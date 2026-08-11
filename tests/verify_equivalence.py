"""Spec section 8 -- prove input and output jump to exactly the same places.

The acceptance list asks for at least 30 sampled links and 5 sampled bookmarks
to be checked by hand. This does the same comparison exhaustively and
automatically: every link annotation and every outline node in the input is
paired with its counterpart in the output, both destinations are resolved all
the way down to (page index, display parameters), and the two must match.

Resolution follows named destinations through the name tree, so a converted
link only passes if the name it now carries really does lead back to the same
page and the same scroll position.

    python verify_equivalence.py INPUT.pdf OUTPUT.pdf
"""

from __future__ import annotations

import pathlib
import sys

import pikepdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pdf_named_dest.collect import build_page_index  # noqa: E402
from pdf_named_dest.model import canon  # noqa: E402

EXTERNAL_ACTIONS = {"/GoToR", "/URI", "/Launch", "/GoToE", "/Named", "/JavaScript"}


class Resolver:
    """Resolves any destination form to a comparable tuple."""

    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.page_index = build_page_index(pdf)
        self.names: dict[str, object] = {}
        root = pdf.Root
        if "/Names" in root and "/Dests" in root.Names:
            for k, v in pikepdf.NameTree(root.Names.Dests).items():
                self.names[str(k)] = v
        if "/Dests" in root:
            for k, v in root.Dests.items():
                self.names.setdefault(str(k).lstrip("/"), v)

    def resolve(self, carrier) -> tuple:
        """Return a tuple describing where this carrier actually lands.

        The tuple is the thing being compared, so it deliberately says nothing
        about whether the destination was reached by name or by page reference
        -- that is exactly the difference the conversion is allowed to make.
        """
        action = carrier.get("/A")
        if isinstance(action, pikepdf.Dictionary):
            subtype = str(action.get("/S")) if action.get("/S") is not None else None
            if subtype != "/GoTo":
                # External actions are compared verbatim: they must not change.
                return ("external", subtype, repr(action))
            value = action.get("/D")
        elif "/Dest" in carrier:
            value = carrier.get("/Dest")
        else:
            return ("none",)

        return self._resolve_value(value)

    def _resolve_value(self, value, depth: int = 0) -> tuple:
        if depth > 4:
            return ("unresolved", "name indirection too deep")

        if isinstance(value, (pikepdf.String, pikepdf.Name)):
            name = str(value).lstrip("/")
            target = self.names.get(name)
            if target is None:
                return ("unresolved", f"name {name!r} not in name tree")
            if isinstance(target, pikepdf.Dictionary) and "/D" in target:
                target = target.D
            return self._resolve_value(target, depth + 1)

        if isinstance(value, pikepdf.Array):
            if len(value) == 0:
                return ("unresolved", "empty array")
            first = value[0]
            if first is None:
                return ("unresolved", "null page reference")
            if isinstance(first, int):
                page = int(first)
            elif isinstance(first, pikepdf.Object) and first.is_indirect:
                page = self.page_index.get(first.objgen)
                if page is None:
                    return ("unresolved", "page not in this document")
            else:
                return ("unresolved", f"bad first element {type(first).__name__}")
            return ("page", page, tuple(canon(x) for x in value[1:]))

        return ("unresolved", f"bad destination type {type(value).__name__}")


def iter_links(pdf: pikepdf.Pdf):
    for page_no, page in enumerate(pdf.pages):
        annots = page.get("/Annots")
        if annots is None:
            continue
        for i, annot in enumerate(annots):
            if not isinstance(annot, pikepdf.Dictionary):
                continue
            if annot.get("/Subtype") != pikepdf.Name.Link:
                continue
            yield f"page {page_no + 1} annot {i}", annot


def iter_outline(pdf: pikepdf.Pdf):
    root = pdf.Root.get("/Outlines")
    if not isinstance(root, pikepdf.Dictionary):
        return
    visited: set[tuple[int, int]] = set()
    stack = []
    first = root.get("/First")
    if isinstance(first, pikepdf.Dictionary):
        stack.append((first, 0))
    n = 0
    while stack:
        node, depth = stack.pop()
        while isinstance(node, pikepdf.Dictionary):
            gen = node.objgen if node.is_indirect else None
            if gen is not None:
                if gen in visited:
                    break
                visited.add(gen)
            title = str(node.get("/Title")) if "/Title" in node else "(untitled)"
            yield f"outline #{n} L{depth} {title[:40]!r}", node
            n += 1
            child = node.get("/First")
            if isinstance(child, pikepdf.Dictionary):
                stack.append((child, depth + 1))
            node = node.get("/Next")


def compare(in_path: pathlib.Path, out_path: pathlib.Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    with pikepdf.Pdf.open(in_path) as a, pikepdf.Pdf.open(out_path) as b:
        ra, rb = Resolver(a), Resolver(b)

        if len(a.pages) != len(b.pages):
            lines.append(f"  FAIL page count {len(a.pages)} -> {len(b.pages)}")
            return False, lines
        lines.append(f"  pages                : {len(a.pages)} (unchanged)")

        for label, iterator in (("link annotations", iter_links),
                                ("outline items", iter_outline)):
            items_a = list(iterator(a))
            items_b = list(iterator(b))
            if len(items_a) != len(items_b):
                lines.append(
                    f"  FAIL {label}: {len(items_a)} in input,"
                    f" {len(items_b)} in output -- nothing may be added or removed"
                )
                ok = False
                continue

            same = diff = carried = 0
            mismatches: list[str] = []
            for (loc_a, node_a), (_, node_b) in zip(items_a, items_b):
                da, db = ra.resolve(node_a), rb.resolve(node_b)
                if da == db:
                    same += 1
                    if da[0] == "unresolved":
                        carried += 1
                else:
                    diff += 1
                    ok = False
                    if len(mismatches) < 10:
                        mismatches.append(f"    {loc_a}\n      in : {da}\n      out: {db}")

            lines.append(f"  {label:<20} : {len(items_a)} compared,"
                         f" {same} identical, {diff} MISMATCHED")
            if carried:
                lines.append(f"    {carried} were already unresolvable in the input"
                             " and stayed that way (correctly not invented)")
            lines.extend(mismatches)

        # Distinctness: destinations that differed in the input must still
        # differ in the output. This is the check that catches over-eager
        # deduplication, the failure spec section 9 calls hard to notice.
        sites_a = list(iter_links(a)) + list(iter_outline(a))
        sites_b = list(iter_links(b)) + list(iter_outline(b))
        groups: dict[tuple, set[tuple]] = {}
        for (_, na), (_, nb) in zip(sites_a, sites_b):
            groups.setdefault(ra.resolve(na), set()).add(rb.resolve(nb))
        collapsed = {k: v for k, v in groups.items() if len(v) > 1}
        if collapsed:
            ok = False
            lines.append(f"  FAIL {len(collapsed)} input destination(s) map to"
                         " more than one output destination")
        else:
            lines.append(f"  distinct destinations: {len(groups)}"
                         " input groups, each maps to exactly one output group")

        merged: dict[tuple, set[tuple]] = {}
        for (_, na), (_, nb) in zip(sites_a, sites_b):
            merged.setdefault(rb.resolve(nb), set()).add(ra.resolve(na))
        over = {k: v for k, v in merged.items() if len(v) > 1 and k[0] == "page"}
        if over:
            ok = False
            lines.append(f"  FAIL {len(over)} output destination(s) collapsed"
                         " several distinct input destinations into one")
            for k, v in list(over.items())[:5]:
                lines.append(f"    {k} <- {v}")
        else:
            lines.append("  no distinct input destinations were collapsed together")

        # Spec 4.2 Step 3 format rule: a reader ignores /Dest when /A is
        # present, so the output must never carry both on one object.
        both = [loc for loc, node in sites_b if "/A" in node and "/Dest" in node]
        if both:
            ok = False
            lines.append(f"  FAIL {len(both)} output object(s) carry both /A and"
                         f" /Dest: {both[:5]}")
        else:
            lines.append("  no output object carries both /A and /Dest")

        # Spec section 5 rule 4: names that were already in the file must come
        # out with the same spelling.
        kept = set(ra.names) & set(rb.names)
        lost = set(ra.names) - set(rb.names)
        if lost:
            ok = False
            lines.append(f"  FAIL {len(lost)} pre-existing name(s) missing from"
                         f" the output: {sorted(lost)[:5]}")
        elif ra.names:
            lines.append(f"  pre-existing names   : {len(kept)} kept unchanged")

    return ok, lines


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    in_path, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    for p in (in_path, out_path):
        if not p.exists():
            print(f"not found: {p}")
            return 2

    print("=" * 72)
    print("DESTINATION EQUIVALENCE CHECK (spec section 8)")
    print("=" * 72)
    print(f"  input                : {in_path}")
    print(f"  output               : {out_path}")
    ok, lines = compare(in_path, out_path)
    print("\n".join(lines))
    print()
    print(f"  => {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())