"""End-to-end proof: convert -> split -> merge must restore every destination.

This is the test the whole project exists for. It takes an original document,
runs the real conversion and the real split, merges the parts back, and then
checks that every link and bookmark in the merged result lands on exactly the
same page and scroll position as in the original.

The merge here is a faithful reference merge (pages concatenated, name trees
unioned), not the merge tool the real process uses. It answers "did convert and
split preserve enough information to make recovery possible", which is what this
project controls. Whether the real merge tool is equally faithful is a separate
question, answered by tests/merge_assumption.

    python roundtrip.py ORIGINAL.pdf WORKDIR [max_pages]
"""

from __future__ import annotations

import pathlib
import sys
import warnings

import pikepdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pdf_named_dest.config import Config  # noqa: E402
from pdf_named_dest.convert_mode import convert  # noqa: E402
from pdf_named_dest.split import split  # noqa: E402
from verify_equivalence import Resolver, iter_links, iter_outline  # noqa: E402


def merge_parts(paths: list[pathlib.Path], out: pathlib.Path) -> None:
    """Concatenate pages and union the name trees, changing nothing else."""
    dst = pikepdf.Pdf.new()
    merged: dict[str, tuple[int, list]] = {}
    offset = 0
    outline_taken = False

    for path in paths:
        with pikepdf.Pdf.open(path) as src:
            index = {p.obj.objgen: i for i, p in enumerate(src.pages)}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                dst.pages.extend(src.pages)

            if not outline_taken and "/Outlines" in src.Root:
                # Each part carries the full outline tree. Taking it from the
                # first part only avoids the duplicate bookmark panel that a
                # naive merge produces.
                dst.Root.Outlines = dst.copy_foreign(src.Root.Outlines)
                outline_taken = True

            root = src.Root
            if "/Names" in root and "/Dests" in root.Names:
                for name, dest in pikepdf.NameTree(root.Names.Dests).items():
                    if not isinstance(dest, pikepdf.Array) or not len(dest):
                        continue
                    first = dest[0]
                    if first is None or not first.is_indirect:
                        continue
                    local = index.get(first.objgen)
                    if local is None:
                        continue
                    merged[str(name)] = (offset + local, list(dest[1:]))
            offset += len(src.pages)

    tree = pikepdf.NameTree.new(dst)
    for name, (page_no, tail) in merged.items():
        tree[name] = pikepdf.Array([dst.pages[page_no].obj, *tail])
    dst.Root.Names = dst.make_indirect(pikepdf.Dictionary(Dests=tree.obj))
    dst.save(str(out))
    dst.close()


def compare(original: pathlib.Path, merged: pathlib.Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    with pikepdf.Pdf.open(original) as a, pikepdf.Pdf.open(merged) as b:
        ra, rb = Resolver(a), Resolver(b)
        lines.append(f"  pages                : {len(a.pages)} -> {len(b.pages)}")
        if len(a.pages) != len(b.pages):
            lines.append("  FAIL page count changed")
            ok = False

        for label, it in (("link annotations", iter_links),
                          ("outline items", iter_outline)):
            items_a, items_b = list(it(a)), list(it(b))
            if len(items_a) != len(items_b):
                lines.append(f"  FAIL {label}: {len(items_a)} -> {len(items_b)}")
                ok = False
                continue

            alive_before = alive_after = recovered = broken = mismatch = 0
            details: list[str] = []
            for (loc, na), (_, nb) in zip(items_a, items_b):
                da, db = ra.resolve(na), rb.resolve(nb)
                a_ok = da[0] == "page"
                b_ok = db[0] == "page"
                alive_before += a_ok
                alive_after += b_ok
                if a_ok and b_ok:
                    if da != db:
                        mismatch += 1
                        ok = False
                        if len(details) < 10:
                            details.append(f"    {loc}\n      was: {da}\n      now: {db}")
                elif b_ok and not a_ok:
                    recovered += 1
                elif a_ok and not b_ok:
                    broken += 1
                    ok = False
                    if len(details) < 10:
                        details.append(f"    {loc} BROKEN: {da} -> {db}")

            lines.append(
                f"  {label:<20} : {len(items_a)} compared, "
                f"{alive_before} resolvable before, {alive_after} after, "
                f"{mismatch} mismatched, {broken} broken"
            )
            if recovered:
                lines.append(f"    {recovered} were dead in the original and now"
                             " resolve (input had pre-existing damage)")
            lines.extend(details)

    return ok, lines


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    original = pathlib.Path(sys.argv[1])
    work = pathlib.Path(sys.argv[2])
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 6999
    work.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("ROUND TRIP: convert -> split -> merge")
    print("=" * 72)
    print(f"  original             : {original}")
    print(f"  max pages per part   : {max_pages}")
    print()

    converted = work / "converted.pdf"
    cfg = Config(
        input_path=original,
        output_path=converted,
        mode="convert",
        name_prefix="PXD_",
        name_pad_width=6,
        process_outlines=True,
        report_path=work / "roundtrip_report",
    )
    rep = convert(cfg)
    print(f"  [1] convert          : {rep.n_names_generated} names generated, "
          f"{rep.counts.get('explicit', 0)} explicit converted, "
          f"{rep.counts.get('unresolved', 0)} exceptions, "
          f"{rep.elapsed_s}s")
    if not rep.ok:
        print("      FAIL conversion self checks did not pass")
        return 1

    res = split(converted, work / "part.pdf", max_pages=max_pages)
    print(f"  [2] split            : {len(res.parts)} parts "
          f"({', '.join(str(p.n_pages) for p in res.parts)} pages), "
          f"{res.n_names_distributed}/{res.n_names_in} names distributed, "
          f"{round(res.elapsed_s, 2)}s")
    if not res.ok:
        print("      FAIL split self checks did not pass")
        return 1

    merged = work / "merged.pdf"
    merge_parts([pathlib.Path(p.path) for p in res.parts], merged)
    print(f"  [3] merge            : {merged.name}")
    print()

    ok, lines = compare(original, merged)
    print("\n".join(lines))
    print()
    print(f"  => {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
