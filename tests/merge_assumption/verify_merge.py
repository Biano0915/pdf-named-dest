"""Spec section 6: merge A + B with several tools and check the assumption.

A run counts as PASS only when all three checks hold:
  1. The merged name tree still defines SHARED_NAME (not dropped, not renamed).
  2. A's link annotation survived and its /A/D is still SHARED_NAME
     (not "cleaned up", not rewritten).
  3. SHARED_NAME resolves to page 5 of the merge (B page 2, 0-based index 4).

This is a structural check, not a real click in a viewer. Passing here means a
viewer will almost certainly follow the link, but confirm once by hand in the
target reader before delivery.
"""

from __future__ import annotations

import pathlib
import sys

import pikepdf

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"

SHARED_NAME = "SHARED_NAME"
EXPECTED_PAGE_INDEX = 4  # 3 pages from A + B page 2 -> 0-based index 4


# ---------------------------------------------------------------------------
# Merge tools under test
# ---------------------------------------------------------------------------
def merge_pikepdf_extend(a: pathlib.Path, b: pathlib.Path, out: pathlib.Path) -> None:
    """pages.extend(): pikepdf itself warns this drops named destinations."""
    import warnings

    with pikepdf.Pdf.open(a) as pdf_a, pikepdf.Pdf.open(b) as pdf_b:
        dst = pikepdf.Pdf.new()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dst.pages.extend(pdf_a.pages)
            dst.pages.extend(pdf_b.pages)
        dst.save(out)


def merge_pikepdf_add_pages_from(
    a: pathlib.Path, b: pathlib.Path, out: pathlib.Path
) -> None:
    """add_pages_from(): the documented way, carries the name tree over."""
    with pikepdf.Pdf.open(a) as pdf_a, pikepdf.Pdf.open(b) as pdf_b:
        dst = pikepdf.Pdf.new()
        for label, src in (("A", pdf_a), ("B", pdf_b)):
            res = dst.add_pages_from(src)
            for attr in ("dropped_dests", "renamed_dests"):
                val = getattr(res, attr, None)
                if val:
                    print(f"      ! {label}: {attr} = {val}")
        dst.save(out)


def merge_pypdf(a: pathlib.Path, b: pathlib.Path, out: pathlib.Path) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.append(str(a))
    w.append(str(b))
    w.write(str(out))
    w.close()


def merge_qpdf(a: pathlib.Path, b: pathlib.Path, out: pathlib.Path) -> None:
    """qpdf --pages. Point QPDF_EXE at the binary if it is not on PATH."""
    import os
    import shutil
    import subprocess

    exe = os.environ.get("QPDF_EXE") or shutil.which("qpdf")
    if not exe or not pathlib.Path(exe).exists():
        raise FileNotFoundError("qpdf not found (set QPDF_EXE or put it on PATH)")
    subprocess.run(
        [exe, "--empty", "--pages", str(a), str(b), "--", str(out)],
        check=True,
        capture_output=True,
    )


def merge_reference_union(a: pathlib.Path, b: pathlib.Path, out: pathlib.Path) -> None:
    """Reference merge: concatenate pages and union the name trees verbatim.

    Not a candidate tool. It exists to separate two very different failure
    modes: "a faithful merge cannot express this" versus "the tools we tried
    happen to throw the entries away". If this one passes, the approach is
    sound and the problem is purely tool selection.
    """
    import warnings

    with pikepdf.Pdf.open(a) as pdf_a, pikepdf.Pdf.open(b) as pdf_b:
        dst = pikepdf.Pdf.new()
        merged: dict[str, pikepdf.Object] = {}
        offset = 0
        for src in (pdf_a, pdf_b):
            src_index = {p.obj.objgen: i for i, p in enumerate(src.pages)}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                dst.pages.extend(src.pages)

            for name, dest in _iter_named_dests(src):
                if isinstance(dest, pikepdf.Dictionary) and "/D" in dest:
                    dest = dest.D
                if not isinstance(dest, pikepdf.Array) or not len(dest):
                    continue
                target = dest[0]
                if isinstance(target, int):
                    page_no = int(target)
                else:
                    page_no = src_index.get(target.objgen)
                if page_no is None:
                    continue
                tail = [_import_scalar(dst, x) for x in dest[1:]]
                merged[str(name)] = pikepdf.Array(
                    [dst.pages[offset + page_no].obj, *tail]
                )
            offset += len(src.pages)

        nt = pikepdf.NameTree.new(dst)
        for name, dest in merged.items():
            nt[name] = dest
        dst.Root.Names = dst.make_indirect(pikepdf.Dictionary(Dests=nt.obj))
        dst.save(out)


def _iter_named_dests(pdf: pikepdf.Pdf):
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        yield from pikepdf.NameTree(root.Names.Dests).items()
    if "/Dests" in root:
        for k, v in root.Dests.items():
            yield str(k).lstrip("/"), v


def _import_scalar(dst: pikepdf.Pdf, obj):
    """Carry a destination display parameter across documents unchanged."""
    if obj is None or isinstance(obj, (int, float, str)):
        return obj
    if isinstance(obj, pikepdf.Name):
        return obj
    return dst.copy_foreign(obj)


MERGERS = {
    "pikepdf-extend": merge_pikepdf_extend,
    "pikepdf-addpages": merge_pikepdf_add_pages_from,
    "pypdf": merge_pypdf,
    "qpdf": merge_qpdf,
    "reference-union": merge_reference_union,
}


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------
def lookup_named_dest(pdf: pikepdf.Pdf, name: str):
    """Look the name up in both the name tree and the legacy name dictionary."""
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        nt = pikepdf.NameTree(root.Names.Dests)
        if name in nt:
            return nt[name]
    if "/Dests" in root:  # PDF 1.1 style
        legacy = root.Dests
        if f"/{name}" in legacy:
            return legacy[f"/{name}"]
    return None


def all_dest_names(pdf: pikepdf.Pdf) -> list[str]:
    names: list[str] = []
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        names += [str(k) for k in pikepdf.NameTree(root.Names.Dests).keys()]
    if "/Dests" in root:
        names += [str(k).lstrip("/") for k in root.Dests.keys()]
    return names


def resolve_dest_to_page_index(pdf: pikepdf.Pdf, dest) -> int | None:
    """Resolve a destination to a 0-based page index."""
    if dest is None:
        return None
    # A named destination may be wrapped in a dictionary under /D.
    if isinstance(dest, pikepdf.Dictionary) and "/D" in dest:
        dest = dest.D
    if not isinstance(dest, pikepdf.Array) or len(dest) == 0:
        return None
    target = dest[0]
    if isinstance(target, int):
        return int(target)
    index = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
    return index.get(target.objgen)


def find_link_dest_strings(pdf: pikepdf.Pdf, page_index: int) -> list[str]:
    """Named destinations referenced by link annotations on one page."""
    page = pdf.pages[page_index]
    found: list[str] = []
    if "/Annots" not in page:
        return found
    for annot in page.Annots:
        if annot.get("/Subtype") != pikepdf.Name.Link:
            continue
        d = None
        if "/A" in annot and annot.A.get("/S") == pikepdf.Name.GoTo:
            d = annot.A.get("/D")
        elif "/Dest" in annot:
            d = annot.Dest
        if isinstance(d, pikepdf.String):
            found.append(str(d))
        elif isinstance(d, pikepdf.Name):
            found.append(str(d).lstrip("/"))
    return found


def check(path: pathlib.Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    with pikepdf.Pdf.open(path) as pdf:
        n_pages = len(pdf.pages)
        suffix = "" if n_pages == 6 else "   <-- expected 6"
        lines.append(f"    pages                : {n_pages}{suffix}")
        if n_pages != 6:
            ok = False

        names = all_dest_names(pdf)
        lines.append(f"    name tree entries    : {len(names)} {sorted(names)}")

        dest = lookup_named_dest(pdf, SHARED_NAME)
        c1 = dest is not None
        ok &= c1
        lines.append(
            f"    [1] name kept        : {'PASS' if c1 else 'FAIL'}"
            f"  ({SHARED_NAME} {'found' if c1 else 'MISSING'})"
        )

        got = find_link_dest_strings(pdf, 0)
        c2 = SHARED_NAME in got
        ok &= c2
        lines.append(
            f"    [2] annot intact     : {'PASS' if c2 else 'FAIL'}"
            f"  (page 1 link dests = {got})"
        )

        idx = resolve_dest_to_page_index(pdf, dest)
        c3 = idx == EXPECTED_PAGE_INDEX
        ok &= c3
        shown = "unresolvable" if idx is None else f"page {idx + 1} (index {idx})"
        lines.append(
            f"    [3] resolves to B p2 : {'PASS' if c3 else 'FAIL'}"
            f"  (-> {shown}, expected page 5 / index 4)"
        )
    return ok, lines


# ---------------------------------------------------------------------------
def run_scenario(scenario: str, b_name: str) -> dict[str, str]:
    a, b = OUT / "A.pdf", OUT / b_name
    print()
    print("-" * 72)
    print(f"  scenario: {scenario}   (A.pdf + {b_name})")
    print("-" * 72)

    results: dict[str, str] = {}
    for name, fn in MERGERS.items():
        merged = OUT / f"merged_{scenario}_{name}.pdf"
        print(f"\n  [{name}]")
        try:
            fn(a, b, merged)
        except FileNotFoundError as e:
            print(f"    SKIP: {e}")
            results[name] = "SKIP"
            continue
        except Exception as e:  # noqa: BLE001 - a failed merge is a valid result
            print(f"    ERROR: {type(e).__name__}: {e}")
            results[name] = "ERROR"
            continue

        ok, lines = check(merged)
        print("\n".join(lines))
        print(f"    => {'PASS' if ok else 'FAIL'}   ({merged.name})")
        results[name] = "PASS" if ok else "FAIL"
    return results


def main() -> int:
    if not (OUT / "A.pdf").exists():
        print("A.pdf not found; run make_ab.py first")
        return 2

    print("=" * 72)
    print("Spec section 6 - merge tool name tree assumption")
    print("=" * 72)

    scenarios = {
        "referenced": "B_referenced.pdf",
        "orphan": "B_orphan.pdf",
    }
    all_results = {s: run_scenario(s, f) for s, f in scenarios.items()}

    print()
    print("=" * 72)
    print("  summary")
    header = "    {:<18}".format("tool") + "".join(
        f"{s:<14}" for s in all_results
    )
    print(header)
    for tool in MERGERS:
        row = "    {:<18}".format(tool) + "".join(
            f"{all_results[s].get(tool, '-'):<14}" for s in all_results
        )
        print(row)
    print("=" * 72)
    print()
    print("  'orphan' is the realistic post-split shape: the name is defined in")
    print("  B but only referenced from A. A tool that passes 'referenced' but")
    print("  fails 'orphan' is not usable for this workflow.")
    print()
    print("  Structural check only. Re-run with the merge tool actually in use")
    print("  and click the page 1 link in the target reader before delivery.")

    orphan = all_results.get("orphan", {})
    return 0 if any(r == "PASS" for r in orphan.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
