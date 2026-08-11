"""Spec section 6: minimal test files for the merge-tool assumption.

Builds:
  A.pdf   Page 1 carries a link annotation pointing at the name SHARED_NAME,
          but A's own name tree deliberately does NOT define that name. This
          mimics the post-split state where the target page lives elsewhere.
  B_*.pdf Name tree defines SHARED_NAME -> B page 2.

After merging A + B, A's link should jump to B page 2 (page 5 of the merged
file). If it jumps, the assumption holds; if not, the whole approach needs
to be re-evaluated before any implementation work.
"""

from __future__ import annotations

import pathlib

import pikepdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"

SHARED_NAME = "SHARED_NAME"
DECOY_NAME = "A_OWN_NAME"

PAGE_W, PAGE_H = letter
LINK_RECT = [70, 686, 430, 716]  # covers the hint text drawn below


def _base_pdf(path: pathlib.Path, tag: str, n_pages: int, link_label: str) -> None:
    """Draw the visible content only; annotations are injected later."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(n_pages):
        c.setFont("Helvetica-Bold", 28)
        c.drawString(72, PAGE_H - 100, f"{tag} - page {i + 1} of {n_pages}")
        if i == 0:
            c.setFont("Helvetica", 16)
            c.drawString(72, 694, link_label)
        c.setFont("Helvetica", 10)
        c.drawString(72, 60, f"[{tag}{i + 1}]")
        c.showPage()
    c.save()


def _link_annot(pdf: pikepdf.Pdf, name: str) -> pikepdf.Object:
    """Build a link annotation targeting a named destination.

    Uses the /A << /S /GoTo /D (name) >> form on purpose, matching what the
    conversion tool will emit. /D is a string object, not a PDF name object.
    """
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=pikepdf.Array([*LINK_RECT]),
            Border=pikepdf.Array([0, 0, 1]),
            C=pikepdf.Array([0, 0, 1]),
            A=pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=pikepdf.String(name)),
        )
    )


def _set_dests(pdf: pikepdf.Pdf, entries: dict[str, pikepdf.Object]) -> None:
    """Create the /Root/Names/Dests name tree."""
    nt = pikepdf.NameTree.new(pdf)
    for name, dest in entries.items():
        nt[name] = dest
    if "/Names" in pdf.Root:
        pdf.Root.Names.Dests = nt.obj
    else:
        pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(Dests=nt.obj))


def build_a(path: pathlib.Path) -> None:
    raw = OUT / "_a_base.pdf"
    _base_pdf(raw, "A", 3, f"CLICK ME -> should jump to B page 2  ({SHARED_NAME})")
    with pikepdf.Pdf.open(raw) as pdf:
        pdf.pages[0].Annots = pikepdf.Array([_link_annot(pdf, SHARED_NAME)])
        # A has a name tree, but it only holds an unrelated name:
        # SHARED_NAME is undefined here.
        _set_dests(
            pdf,
            {
                DECOY_NAME: pikepdf.Array(
                    [pdf.pages[2].obj, pikepdf.Name.XYZ, 0, PAGE_H, 0]
                )
            },
        )
        pdf.save(path)
    raw.unlink()


def build_b(path: pathlib.Path, *, self_referenced: bool) -> None:
    """B defines SHARED_NAME.

    self_referenced=True   B also carries its own link to that name.
    self_referenced=False  Nothing inside B references the name. This is the
                           realistic post-split state: the definition lives in
                           B while the only reference lives in A. Some merge
                           tools carry over just the name tree entries that are
                           actually referenced by the copied pages, and this
                           variant exists to catch that behaviour.
    """
    raw = OUT / "_b_base.pdf"
    label = (
        f"(B's own link -> B page 2, {SHARED_NAME})"
        if self_referenced
        else "(no link in B; the name is only referenced from A)"
    )
    _base_pdf(raw, "B", 3, label)
    with pikepdf.Pdf.open(raw) as pdf:
        if self_referenced:
            pdf.pages[0].Annots = pikepdf.Array([_link_annot(pdf, SHARED_NAME)])
        _set_dests(
            pdf,
            {
                SHARED_NAME: pikepdf.Array(
                    [pdf.pages[1].obj, pikepdf.Name.XYZ, 0, PAGE_H, 0]
                )
            },
        )
        pdf.save(path)
    raw.unlink()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    build_a(OUT / "A.pdf")
    build_b(OUT / "B_referenced.pdf", self_referenced=True)
    build_b(OUT / "B_orphan.pdf", self_referenced=False)
    for f in ("A.pdf", "B_referenced.pdf", "B_orphan.pdf"):
        print(f"wrote {OUT / f}")
    print()
    print(f"  A            : 3 pages, link on page 1 -> ({SHARED_NAME}),"
          f" not defined in A")
    print(f"                 A's name tree defines only ({DECOY_NAME})")
    print(f"  B_referenced : defines ({SHARED_NAME}) -> B page 2,"
          f" B also links to it")
    print(f"  B_orphan     : defines ({SHARED_NAME}) -> B page 2,"
          f" nothing in B references it")
    print()
    print("  With either B, A's page 1 link must land on page 5 of the merge.")
    print("  B_orphan is the realistic post-split case and the one that decides"
          " this.")


if __name__ == "__main__":
    main()