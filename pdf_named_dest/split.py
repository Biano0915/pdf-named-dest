"""Split a converted document into parts, preserving every named destination.

This runs after the conversion, never before. The order matters: once
destinations are names rather than page references, splitting cannot break
them. A link whose target page ends up in another part still carries its name,
and the name resolves again as soon as the parts are merged back.

Each part gets:
  - its slice of pages, with all link annotations copied untouched
  - the whole outline tree, copied wholesale (after conversion no outline node
    references a page object, so there is nothing dangling to copy)
  - a name tree holding exactly the entries whose target page is in this part

The union of the parts' name trees is therefore the original name tree, which
is what makes a merge restore every link.
"""

from __future__ import annotations

import pathlib
import time
import warnings
from dataclasses import dataclass, field

import pikepdf

from .collect import read_existing_names


class SplitError(RuntimeError):
    """Raised when the input is not safe to split."""


# How much of the bookmark tree each part carries. A merge tool concatenates
# whatever it finds, so a full copy in every part shows up N times in the
# merged result.
#
#   first  only the first part carries the tree, complete. The merged document
#          gets exactly the original tree, once. Later parts have no bookmark
#          panel of their own.
#   own    each part keeps the bookmarks pointing at its own pages, plus the
#          ancestors needed to hold them. Standalone parts show only bookmarks
#          that work; a shared ancestor still appears in more than one part.
#   all    every part carries the whole tree. Matches the previous external
#          tool, and duplicates on merge.
#   none   no part carries bookmarks.
OUTLINE_MODES = ("first", "own", "all", "none")


@dataclass
class SplitPart:
    index: int
    start_page: int          # 0-based, inclusive
    end_page: int            # 0-based, exclusive
    path: str
    n_pages: int = 0
    n_link_annots: int = 0
    n_name_tree_entries: int = 0
    n_outline_items: int = 0


@dataclass
class SplitResult:
    input_path: str
    n_pages_in: int
    max_pages_per_file: int
    align: str
    outlines: str = "first"
    # False when the document already fits the page limit, so it was left as a
    # single file instead of being reissued under a "split 1" name.
    split_needed: bool = True
    parts: list[SplitPart] = field(default_factory=list)
    n_names_in: int = 0
    n_names_distributed: int = 0
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def pages_accounted(self) -> bool:
        return sum(p.n_pages for p in self.parts) == self.n_pages_in

    @property
    def names_accounted(self) -> bool:
        """Every name must land in exactly one part, none lost or duplicated."""
        return self.n_names_distributed == self.n_names_in

    @property
    def within_limit(self) -> bool:
        return all(p.n_pages <= self.max_pages_per_file for p in self.parts)

    def self_checks(self) -> list[tuple[str, bool]]:
        return [
            ("all pages accounted for, none lost or duplicated", self.pages_accounted),
            ("all name tree entries distributed exactly once", self.names_accounted),
            (f"every part within {self.max_pages_per_file} pages", self.within_limit),
            ("no warnings", not self.warnings),
        ]

    @property
    def ok(self) -> bool:
        return all(p for _, p in self.self_checks())

    def to_dict(self) -> dict:
        return {
            "input_path": self.input_path,
            "n_pages_in": self.n_pages_in,
            "max_pages_per_file": self.max_pages_per_file,
            "align": self.align,
            "outlines": self.outlines,
            "split_needed": self.split_needed,
            "n_parts": len(self.parts),
            "parts": [p.__dict__ for p in self.parts],
            "n_names_in": self.n_names_in,
            "n_names_distributed": self.n_names_distributed,
            "warnings": self.warnings,
            "elapsed_s": self.elapsed_s,
            "self_checks": {n: p for n, p in self.self_checks()},
            "ok": self.ok,
        }

    def format(self) -> str:
        lines = ["=" * 72, "SPLIT REPORT", "=" * 72]
        lines.append(f"  input                : {self.input_path}")
        lines.append(f"  pages                : {self.n_pages_in}")
        lines.append(f"  max pages per file   : {self.max_pages_per_file}")
        lines.append(f"  boundary alignment   : {self.align}")
        lines.append(f"  parts                : {len(self.parts)}")
        lines.append(f"  outline handling     : {self.outlines}")
        if not self.split_needed:
            lines.append("")
            lines.append(f"  NO SPLIT NEEDED      : {self.n_pages_in} pages is within"
                         f" the {self.max_pages_per_file} page limit")
            lines.append("                         the document is left as one file,"
                         " with no 'split N' suffix")
        lines.append("")
        lines.append("    #   pages          range            names  links  marks  file")
        for p in self.parts:
            lines.append(
                f"    {p.index:<3} {p.n_pages:>6}    "
                f"{p.start_page + 1:>6}-{p.end_page:<6}   "
                f"{p.n_name_tree_entries:>5}  {p.n_link_annots:>5}  "
                f"{p.n_outline_items:>5}  "
                f"{pathlib.Path(p.path).name}"
            )
        lines.append("")
        lines.append(f"  name tree entries    : {self.n_names_in} in input, "
                     f"{self.n_names_distributed} distributed")
        if self.warnings:
            lines.append("")
            lines.append(f"  warnings ({len(self.warnings)})")
            for w in self.warnings:
                lines.append(f"    {w}")
        lines.append("")
        lines.append("  self checks")
        for name, passed in self.self_checks():
            lines.append(f"    [{'PASS' if passed else 'FAIL'}] {name}")
        lines.append("")
        lines.append(f"  result               : {'OK' if self.ok else 'PROBLEMS FOUND'}")
        lines.append(f"  elapsed              : {round(self.elapsed_s, 2)}s")
        lines.append("=" * 72)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Boundary planning
# ---------------------------------------------------------------------------
def plan_pages(n_pages: int, max_pages: int) -> list[tuple[int, int]]:
    """Fixed-size chunks. Matches how the current external tool cuts."""
    return [(s, min(s + max_pages, n_pages)) for s in range(0, n_pages, max_pages)]


def plan_outline_aligned(
    n_pages: int, max_pages: int, boundaries: list[int]
) -> tuple[list[tuple[int, int]], list[str]]:
    """Chunks that end just before a bookmark target, so no section is cut.

    Falls back to a hard cut whenever the next bookmark boundary is further
    than ``max_pages`` away, because the page limit is the harder constraint.
    """
    notes: list[str] = []
    cuts = sorted({b for b in boundaries if 0 < b < n_pages})
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < n_pages:
        limit = start + max_pages
        if limit >= n_pages:
            chunks.append((start, n_pages))
            break
        candidates = [c for c in cuts if start < c <= limit]
        if candidates:
            end = candidates[-1]
        else:
            end = limit
            notes.append(
                f"pages {start + 1}-{end}: no bookmark boundary within the page "
                "limit, cut at the limit instead"
            )
        chunks.append((start, end))
        start = end
    return chunks, notes


# ---------------------------------------------------------------------------
def _read_name_tree(pdf: pikepdf.Pdf) -> dict[str, pikepdf.Object]:
    entries: dict[str, pikepdf.Object] = {}
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        for k, v in pikepdf.NameTree(root.Names.Dests).items():
            entries[str(k)] = v
    if "/Dests" in root:
        for k, v in root.Dests.items():
            entries.setdefault(str(k).lstrip("/"), v)
    return entries


def _dest_page_index(
    dest, page_index: dict[tuple[int, int], int]
) -> int | None:
    if isinstance(dest, pikepdf.Dictionary) and "/D" in dest:
        dest = dest.D
    if not isinstance(dest, pikepdf.Array) or len(dest) == 0:
        return None
    first = dest[0]
    if first is None:
        return None
    if isinstance(first, int):
        return int(first)
    if isinstance(first, pikepdf.Object) and first.is_indirect:
        return page_index.get(first.objgen)
    return None


def _dest_tail(dest) -> list:
    if isinstance(dest, pikepdf.Dictionary) and "/D" in dest:
        dest = dest.D
    return list(dest[1:])


def outline_boundaries(
    pdf: pikepdf.Pdf,
    names: dict[str, pikepdf.Object],
    page_index: dict[tuple[int, int], int],
) -> list[int]:
    """Page indices that top-level bookmarks point at.

    After conversion those bookmarks carry names, so each one is resolved
    through the name tree to find the page it opens on.
    """
    root = pdf.Root.get("/Outlines")
    if not isinstance(root, pikepdf.Dictionary):
        return []

    found: list[int] = []
    seen: set[tuple[int, int]] = set()
    node = root.get("/First")
    # Only the first two levels are considered: cutting between top-level
    # sections is the intent, and deep leaves would offer far too many cuts.
    queue = []
    while isinstance(node, pikepdf.Dictionary):
        queue.append((node, 0))
        node = node.get("/Next")

    while queue:
        node, depth = queue.pop(0)
        gen = node.objgen if node.is_indirect else None
        if gen is not None:
            if gen in seen:
                continue
            seen.add(gen)

        dest = node.get("/Dest")
        if dest is None:
            action = node.get("/A")
            if isinstance(action, pikepdf.Dictionary) and \
                    str(action.get("/S")) == "/GoTo":
                dest = action.get("/D")
        if isinstance(dest, (pikepdf.String, pikepdf.Name)):
            dest = names.get(str(dest).lstrip("/"))
        idx = _dest_page_index(dest, page_index)
        if idx is not None:
            found.append(idx)

        if depth < 1:
            child = node.get("/First")
            while isinstance(child, pikepdf.Dictionary):
                queue.append((child, depth + 1))
                child = child.get("/Next")

    return sorted(set(found))


# ---------------------------------------------------------------------------
# Keys that describe a node's place in the tree rather than its content. They
# are rebuilt when the tree is filtered, never copied.
OUTLINE_STRUCTURAL = {"/First", "/Last", "/Next", "/Prev", "/Parent", "/Count"}


def _outline_forest(
    pdf: pikepdf.Pdf,
    names: dict[str, pikepdf.Object],
    page_index: dict[tuple[int, int], int],
) -> list[dict]:
    """Read the outline into plain Python nodes: title, target page, children."""
    root = pdf.Root.get("/Outlines")
    if not isinstance(root, pikepdf.Dictionary):
        return []

    seen: set[tuple[int, int]] = set()

    def read(node) -> list[dict]:
        out: list[dict] = []
        while isinstance(node, pikepdf.Dictionary):
            gen = node.objgen if node.is_indirect else None
            if gen is not None:
                if gen in seen:
                    break
                seen.add(gen)

            dest = node.get("/Dest")
            if dest is None:
                action = node.get("/A")
                if isinstance(action, pikepdf.Dictionary) and \
                        str(action.get("/S")) == "/GoTo":
                    dest = action.get("/D")
            resolved = dest
            if isinstance(dest, (pikepdf.String, pikepdf.Name)):
                resolved = names.get(str(dest).lstrip("/"))

            out.append({
                "obj": node,
                "page": _dest_page_index(resolved, page_index),
                "children": read(node.get("/First")),
            })
            node = node.get("/Next")
        return out

    return read(root.get("/First"))


def _mark_kept(nodes: list[dict], start: int, end: int) -> bool:
    """Flag which nodes belong in a part. Returns True if any node was kept.

    A node is kept when its own target page falls in the range, and also when
    any descendant is kept -- a child cannot exist in the tree without its
    parent, so ancestors come along for structural reasons.
    """
    any_kept = False
    for n in nodes:
        child_kept = _mark_kept(n["children"], start, end)
        own = n["page"] is not None and start <= n["page"] < end
        n["keep"] = own or child_kept
        n["own"] = own
        any_kept |= n["keep"]
    return any_kept


def _copy_node(dst: pikepdf.Pdf, src_node: pikepdf.Dictionary) -> pikepdf.Object:
    """Copy one outline node's content, leaving tree wiring to the caller."""
    fresh = pikepdf.Dictionary()
    for key in src_node.keys():
        if str(key) in OUTLINE_STRUCTURAL:
            continue
        value = src_node[key]
        # Numbers and strings come back as plain Python values and can be
        # assigned straight across; only real PDF objects stored indirectly
        # need importing into the destination document.
        if isinstance(value, pikepdf.Object) and value.is_indirect:
            value = dst.copy_foreign(value)
        fresh[key] = value
    return dst.make_indirect(fresh)


def _build_outline(dst: pikepdf.Pdf, nodes: list[dict], parent) -> tuple:
    """Recreate the kept nodes under ``parent``. Returns (first, last, count)."""
    made = []
    for n in nodes:
        if not n.get("keep"):
            continue
        node = _copy_node(dst, n["obj"])
        node.Parent = parent
        first, last, count = _build_outline(dst, n["children"], node)
        if first is not None:
            node.First = first
            node.Last = last
            # A negative count means the node shows collapsed. The source's
            # sign is honoured so parts open the same way the original did.
            original = n["obj"].get("/Count")
            closed = original is not None and int(original) < 0
            node.Count = -count if closed else count
        made.append(node)

    if not made:
        return None, None, 0
    for a, b in zip(made, made[1:]):
        a.Next = b
        b.Prev = a
    return made[0], made[-1], len(made)


def _write_outline(
    dst: pikepdf.Pdf,
    src: pikepdf.Pdf,
    forest: list[dict],
    mode: str,
    start: int,
    end: int,
    is_first_part: bool,
) -> int:
    """Put the appropriate slice of the outline into one part.

    Returns the number of nodes written.
    """
    if mode == "none" or not forest:
        return 0

    if mode == "all" or (mode == "first" and is_first_part):
        # Whole tree, copied as-is. Safe because after conversion no outline
        # node holds a page reference.
        dst.Root.Outlines = dst.copy_foreign(src.Root.Outlines)
        return _count_nodes(forest)

    if mode == "first":
        return 0  # every part after the first gets no outline

    _mark_kept(forest, start, end)
    outlines = dst.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Outlines))
    first, last, count = _build_outline(dst, forest, outlines)
    if first is None:
        return 0
    outlines.First = first
    outlines.Last = last
    outlines.Count = count
    dst.Root.Outlines = outlines
    return _count_kept(forest)


def _count_links(pdf: pikepdf.Pdf) -> int:
    total = 0
    for page in pdf.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        total += sum(1 for a in annots
                     if isinstance(a, pikepdf.Dictionary)
                     and a.get("/Subtype") == pikepdf.Name.Link)
    return total


def _count_nodes(nodes: list[dict]) -> int:
    return sum(1 + _count_nodes(n["children"]) for n in nodes)


def _count_kept(nodes: list[dict]) -> int:
    return sum(1 + _count_kept(n["children"]) for n in nodes if n.get("keep"))


def part_path(output_base: pathlib.Path, pattern: str, n: int, total: int) -> pathlib.Path:
    """Where part ``n`` of ``total`` is written.

    A document that already fits the page limit produces a single part, and
    that part is the document itself -- calling it "split 1" would imply a
    split that never happened. So the base name is used unchanged.
    """
    if total == 1:
        return output_base
    name = pattern.format(stem=output_base.stem, n=n, total=total,
                          suffix=output_base.suffix or ".pdf")
    return output_base.parent / name


def split(
    input_path: pathlib.Path,
    output_base: pathlib.Path,
    *,
    max_pages: int,
    align: str = "pages",
    outlines: str = "first",
    pattern: str = "{stem} split {n}{suffix}",
    allow_explicit: bool = False,
) -> SplitResult:
    started = time.perf_counter()

    if outlines not in OUTLINE_MODES:
        raise SplitError(
            f"outlines must be one of {OUTLINE_MODES}, got {outlines!r}"
        )

    with pikepdf.Pdf.open(input_path) as src:
        n_pages = len(src.pages)
        page_index = {p.obj.objgen: i for i, p in enumerate(src.pages)}
        names = _read_name_tree(src)

        result = SplitResult(
            input_path=str(input_path),
            n_pages_in=n_pages,
            max_pages_per_file=max_pages,
            align=align,
            outlines=outlines,
            n_names_in=len(names),
        )

        _guard_explicit(src, page_index, allow_explicit, result)

        if "/PageLabels" in src.Root:
            result.warnings.append(
                "input carries /PageLabels; page numbering is out of scope "
                "(spec section 1.3) and is not carried into the parts"
            )

        if align == "outline":
            bounds = outline_boundaries(src, names, page_index)
            chunks, notes = plan_outline_aligned(n_pages, max_pages, bounds)
            result.warnings.extend(notes)
        elif align == "pages":
            chunks = plan_pages(n_pages, max_pages)
        else:
            raise SplitError(f"align must be 'pages' or 'outline', got {align!r}")

        # Which part each name belongs to, decided once so the accounting can
        # prove no name is dropped or duplicated.
        name_home: dict[int, dict[str, tuple[int, list]]] = {
            i: {} for i in range(len(chunks))
        }
        for name, dest in names.items():
            idx = _dest_page_index(dest, page_index)
            if idx is None:
                result.warnings.append(
                    f"name {name!r} does not resolve to a page and is not "
                    "carried into any part"
                )
                continue
            for i, (start, end) in enumerate(chunks):
                if start <= idx < end:
                    name_home[i][name] = (idx - start, _dest_tail(dest))
                    break

        outputs = [part_path(output_base, pattern, i + 1, len(chunks))
                   for i in range(len(chunks))]
        output_base.parent.mkdir(parents=True, exist_ok=True)

        result.split_needed = len(chunks) > 1

        # In convert+split the converted file and the split target are the same
        # path. When no split is needed there is nothing left to do: that file
        # already is the finished single-part output.
        if not result.split_needed and \
                input_path.resolve() == outputs[0].resolve():
            result.parts.append(SplitPart(
                index=1,
                start_page=0,
                end_page=n_pages,
                path=str(outputs[0]),
                n_pages=n_pages,
                n_link_annots=_count_links(src),
                n_name_tree_entries=len(names),
                n_outline_items=_count_nodes(
                    _outline_forest(src, names, page_index)
                ),
            ))
            result.n_names_distributed = len(names)
            result.elapsed_s = time.perf_counter() - started
            return result

        forest = _outline_forest(src, names, page_index)

        for i, ((start, end), out_path) in enumerate(zip(chunks, outputs)):
            part = _write_part(
                src, start, end, out_path, name_home[i],
                forest=forest, outlines=outlines, is_first_part=(i == 0),
            )
            part.index = i + 1
            result.parts.append(part)
            result.n_names_distributed += part.n_name_tree_entries

    result.elapsed_s = time.perf_counter() - started
    return result


def _guard_explicit(
    src: pikepdf.Pdf,
    page_index: dict[tuple[int, int], int],
    allow_explicit: bool,
    result: SplitResult,
) -> None:
    """Refuse to split a document that still holds explicit destinations.

    Splitting one is exactly the operation that kills links permanently: the
    target page object disappears and nothing in the file records where the
    link was meant to go. Running the conversion first is the whole point.
    """
    from .collect import collect
    from .model import DestKind

    found = collect(src)
    n_explicit = len(found.by_kind(DestKind.EXPLICIT))
    if not n_explicit:
        return

    message = (
        f"input still holds {n_explicit} explicit destination(s); splitting now "
        "would destroy them permanently. Run convert mode first."
    )
    if not allow_explicit:
        raise SplitError(message)
    result.warnings.append("OVERRIDDEN: " + message)


def _write_part(
    src: pikepdf.Pdf,
    start: int,
    end: int,
    out_path: pathlib.Path,
    names: dict[str, tuple[int, list]],
    *,
    forest: list[dict],
    outlines: str,
    is_first_part: bool,
) -> SplitPart:
    dst = pikepdf.Pdf.new()

    # pages.extend warns that it does not carry named destinations; that is
    # expected and handled, because the name tree is rebuilt below from the
    # source rather than copied.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dst.pages.extend(src.pages[start:end])

    n_outline = _write_outline(
        dst, src, forest, outlines, start, end, is_first_part
    )

    if names:
        tree = pikepdf.NameTree.new(dst)
        for name, (local_index, tail) in names.items():
            tree[name] = pikepdf.Array([dst.pages[local_index].obj, *tail])
        dst.Root.Names = dst.make_indirect(pikepdf.Dictionary(Dests=tree.obj))

    n_links = 0
    for page in dst.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        n_links += sum(1 for a in annots
                       if isinstance(a, pikepdf.Dictionary)
                       and a.get("/Subtype") == pikepdf.Name.Link)

    dst.save(str(out_path))
    dst.close()

    with pikepdf.Pdf.open(out_path) as check:
        n_tree = len(read_existing_names(check))

    return SplitPart(
        index=0,
        start_page=start,
        end_page=end,
        path=str(out_path),
        n_pages=end - start,
        n_link_annots=n_links,
        n_name_tree_entries=n_tree,
        n_outline_items=n_outline,
    )
