"""Spec 4.2 Step 1 -- find and classify every destination in the document.

Two sources must both be scanned:
  1. /Link annotations in every page's /Annots
  2. the outline tree, walked to every level and every sibling

and in each of those, a destination can sit in either of two slots:
  - the object's /Dest
  - the object's /A, when /A/S is /GoTo, under /D
"""

from __future__ import annotations

from typing import Any, Iterator

import pikepdf

from .model import CollectResult, DestKey, DestKind, DestSite, Source

# Actions that point outside this document. Spec 4.2 Step 1 says to leave them
# alone and only count them.
EXTERNAL_ACTIONS = {"/GoToR", "/URI", "/Launch", "/GoToE", "/Named", "/JavaScript"}


def build_page_index(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    """Map page object -> 0-based page index.

    Keyed on objgen rather than on the object itself: objgen is a plain
    (number, generation) tuple, so it is hashable and stable across lookups.
    """
    return {page.obj.objgen: i for i, page in enumerate(pdf.pages)}


def _as_text(obj: Any) -> str:
    """Best-effort decode of a PDF text string, for report labels only."""
    try:
        return str(obj)
    except Exception:  # noqa: BLE001 - a bad title must not abort the scan
        return "<undecodable>"


def _find_dest_slot(carrier: Any) -> tuple[str | None, Any, str | None]:
    """Locate the destination on an annotation or outline node.

    Returns (slot, value, action_type):
      slot        "A.D" or "Dest", or None when there is nothing to convert
      value       the destination value found at that slot
      action_type the /A/S name when the carrier holds an action

    /A wins over /Dest here because the PDF spec says a reader ignores /Dest
    when /A is present.

    pikepdf resolves indirect references on access, so nothing here needs an
    explicit dereference step even though /A and /Dest are often indirect.
    """
    action = carrier.get("/A")
    if isinstance(action, pikepdf.Dictionary):
        subtype = action.get("/S")
        name = str(subtype) if subtype is not None else None
        if name == "/GoTo":
            return "A.D", action.get("/D"), name
        # Anything else leaves the document; not ours to touch.
        return None, None, name

    if "/Dest" in carrier:
        return "Dest", carrier.get("/Dest"), None

    return None, None, None


def _classify(
    carrier: Any,
    source: Source,
    location: str,
    page_index_map: dict[tuple[int, int], int],
) -> DestSite:
    slot, value, action_type = _find_dest_slot(carrier)

    if slot is None:
        if action_type in EXTERNAL_ACTIONS:
            return DestSite(
                source=source,
                location=location,
                carrier=carrier,
                slot="A.D",
                kind=DestKind.EXTERNAL,
                action_type=action_type,
            )
        return DestSite(
            source=source,
            location=location,
            carrier=carrier,
            slot="",
            kind=DestKind.UNRESOLVED,
            note=(
                f"no /Dest and no /GoTo action (action /S = {action_type})"
                if action_type
                else "no /Dest and no /A"
            ),
        )

    # Already a named destination: keep the name exactly as it is.
    if isinstance(value, (pikepdf.String, pikepdf.Name)):
        return DestSite(
            source=source,
            location=location,
            carrier=carrier,
            slot=slot,
            kind=DestKind.NAMED,
            existing_name=_as_text(value).lstrip("/"),
        )

    if isinstance(value, pikepdf.Array):
        return _classify_array(carrier, source, location, slot, value, page_index_map)

    return DestSite(
        source=source,
        location=location,
        carrier=carrier,
        slot=slot,
        kind=DestKind.UNRESOLVED,
        note=f"destination is {type(value).__name__}, expected array or string",
    )


def _classify_array(
    carrier: Any,
    source: Source,
    location: str,
    slot: str,
    value: pikepdf.Array,
    page_index_map: dict[tuple[int, int], int],
) -> DestSite:
    def unresolved(note: str) -> DestSite:
        return DestSite(
            source=source,
            location=location,
            carrier=carrier,
            slot=slot,
            kind=DestKind.UNRESOLVED,
            note=note,
        )

    if len(value) == 0:
        return unresolved("empty destination array")

    target = value[0]
    if target is None:
        # The classic post-split corpse: the page object is gone and the tool
        # that removed it blanked the reference instead of deleting the link.
        # Nothing here can be recovered -- which is exactly why this conversion
        # has to run before splitting, not after.
        return unresolved("dangling: page reference is null (target page removed)")

    if isinstance(target, int):
        # Integer page numbers are the /GoToR form, but they do turn up inside
        # plain /GoTo destinations in the wild.
        page_index = int(target)
        if not 0 <= page_index < len(page_index_map):
            return unresolved(f"page number {page_index} out of range")
    elif isinstance(target, pikepdf.Object) and target.is_indirect:
        page_index = page_index_map.get(target.objgen)
        if page_index is None:
            return unresolved(
                f"target {target.objgen} is not a page of this document "
                "(dangling reference)"
            )
    else:
        return unresolved(
            f"first element is {type(target).__name__}, expected a page reference"
        )

    tail = list(value[1:])
    return DestSite(
        source=source,
        location=location,
        carrier=carrier,
        slot=slot,
        kind=DestKind.EXPLICIT,
        page_index=page_index,
        key=DestKey.build(page_index, tail),
        tail=tail,
    )


def _iter_outline_nodes(
    root: Any, max_nodes: int = 2_000_000
) -> Iterator[tuple[Any, int]]:
    """Depth-first walk of the outline tree, yielding (node, depth).

    Siblings are followed with a loop rather than recursion because a document
    can have tens of thousands of them at one level. A visited set guards
    against malformed files whose /Next or /First cycles back on itself.
    """
    visited: set[tuple[int, int]] = set()
    stack: list[tuple[Any, int]] = []

    first = root.get("/First") if root is not None else None
    if isinstance(first, pikepdf.Dictionary):
        stack.append((first, 0))

    count = 0
    while stack:
        node, depth = stack.pop()
        while isinstance(node, pikepdf.Dictionary):
            gen = node.objgen if node.is_indirect else None
            if gen is not None:
                if gen in visited:
                    break
                visited.add(gen)

            count += 1
            if count > max_nodes:
                return
            yield node, depth

            child = node.get("/First")
            if isinstance(child, pikepdf.Dictionary):
                stack.append((child, depth + 1))

            node = node.get("/Next")


def collect(pdf: pikepdf.Pdf, *, process_outlines: bool = True) -> CollectResult:
    page_index_map = build_page_index(pdf)
    result = CollectResult(n_pages=len(pdf.pages))

    root = pdf.Root
    result.has_name_tree = "/Names" in root and "/Dests" in root.Names
    result.has_outlines = "/Outlines" in root
    result.existing_names = read_existing_names(pdf)

    # --- source 1: link annotations -------------------------------------
    for page_no, page in enumerate(pdf.pages):
        annots = page.get("/Annots")
        if annots is None:
            continue
        for i, annot in enumerate(annots):
            if not isinstance(annot, pikepdf.Dictionary):
                continue
            if annot.get("/Subtype") != pikepdf.Name.Link:
                continue
            result.n_link_annots += 1
            result.sites.append(
                _classify(
                    annot,
                    Source.ANNOT,
                    f"page {page_no + 1} annot {i}",
                    page_index_map,
                )
            )

    # --- source 2: outline tree -----------------------------------------
    if process_outlines and result.has_outlines:
        outlines = root.get("/Outlines")
        if isinstance(outlines, pikepdf.Dictionary):
            for node, depth in _iter_outline_nodes(outlines):
                result.n_outline_items += 1
                title = _as_text(node.get("/Title")) if "/Title" in node else "(untitled)"
                if len(title) > 60:
                    title = title[:57] + "..."
                result.sites.append(
                    _classify(
                        node,
                        Source.OUTLINE,
                        f"outline L{depth} {title!r}",
                        page_index_map,
                    )
                )

    return result


def read_existing_names(pdf: pikepdf.Pdf) -> set[str]:
    """Every destination name already used in the file.

    Spec 4.2 Step 2 requires generated names to avoid these. Both the modern
    name tree and the PDF 1.1 legacy /Root/Dests dictionary are read, because
    an old file can carry either or both.
    """
    names: set[str] = set()
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        try:
            names |= {str(k) for k in pikepdf.NameTree(root.Names.Dests).keys()}
        except Exception:  # noqa: BLE001 - a broken tree must not abort inspection
            pass
    if "/Dests" in root:
        try:
            names |= {str(k).lstrip("/") for k in root.Dests.keys()}
        except Exception:  # noqa: BLE001
            pass
    return names