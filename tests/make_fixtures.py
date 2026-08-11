"""Build test PDFs for the cases the real production files do not cover.

The two real split files use a single display type (/FitH 612) throughout, have
no name tree and no external links, so on their own they exercise only the
happy path. This module builds a document that deliberately contains every
awkward case the spec calls out:

  - several destinations on one page differing only in their coordinates
    (spec section 9 flags getting this wrong as "hard to notice")
  - /XYZ null null null alongside /XYZ 0 0 0, which must not be merged
  - a destination that is already named, which must survive untouched
  - /GoToR and /URI actions, which must survive untouched
  - a dangling destination whose page reference is null
  - destinations carried on /Dest as well as on /A /GoTo
  - an object carrying both /A and /Dest, which must not stay that way
  - a nested bookmark tree, so the outline walk is exercised past level 1
"""

from __future__ import annotations

import pathlib

import pikepdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = pathlib.Path(__file__).parent
OUT = HERE / "fixtures"

N_PAGES = 12
PAGE_W, PAGE_H = letter

EXISTING_NAME = "PRE_EXISTING_DEST"


def _base_pdf(path: pathlib.Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(N_PAGES):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, PAGE_H - 90, f"page {i + 1} of {N_PAGES}")
        c.setFont("Helvetica", 11)
        for row, y in enumerate((640, 560, 480, 400)):
            c.drawString(72, y, f"link row {row} on page {i + 1}  (y={y})")
        c.showPage()
    c.save()


def _link(pdf: pikepdf.Pdf, rect: list, **entries) -> pikepdf.Object:
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=pikepdf.Array(rect),
            Border=pikepdf.Array([0, 0, 1]),
            **entries,
        )
    )


def _goto(dest) -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=dest)


def _rect(row: int) -> list:
    y = (640, 560, 480, 400)[row]
    return [70, y - 4, 420, y + 16]


def build(path: pathlib.Path) -> dict[str, int]:
    """Write the fixture and return the counts a correct run must produce."""
    raw = OUT / "_base.pdf"
    _base_pdf(raw)

    with pikepdf.Pdf.open(raw) as pdf:
        pages = pdf.pages

        # -- page 1: four destinations that all land on page 5 ---------------
        # Same target page, four different scroll positions. Deduplicating on
        # the page index alone would collapse these into one name and send
        # three of the four links to the wrong place.
        p5 = pages[4].obj
        page1 = [
            _link(pdf, _rect(0), A=_goto(pikepdf.Array([p5, pikepdf.Name.XYZ, 0, 700, 0]))),
            _link(pdf, _rect(1), A=_goto(pikepdf.Array([p5, pikepdf.Name.XYZ, 0, 400, 0]))),
            _link(pdf, _rect(2), A=_goto(pikepdf.Array([p5, pikepdf.Name.XYZ, 0, 100, 0]))),
            _link(pdf, _rect(3), A=_goto(pikepdf.Array([p5, pikepdf.Name.FitH, 700]))),
        ]
        pages[0].Annots = pikepdf.Array(page1)

        # -- page 2: null versus zero, and a duplicate --------------------
        # /XYZ null null null and /XYZ 0 0 0 are different destinations and
        # must get different names. The third link repeats the first exactly
        # and must share its name.
        p6 = pages[5].obj
        page2 = [
            _link(pdf, _rect(0),
                  A=_goto(pikepdf.Array([p6, pikepdf.Name.XYZ, None, None, None]))),
            _link(pdf, _rect(1),
                  A=_goto(pikepdf.Array([p6, pikepdf.Name.XYZ, 0, 0, 0]))),
            _link(pdf, _rect(2),
                  A=_goto(pikepdf.Array([p6, pikepdf.Name.XYZ, None, None, None]))),
        ]
        pages[1].Annots = pikepdf.Array(page2)

        # -- page 3: destinations on /Dest instead of /A ------------------
        page3 = [
            _link(pdf, _rect(0),
                  Dest=pikepdf.Array([pages[6].obj, pikepdf.Name.Fit])),
            _link(pdf, _rect(1),
                  Dest=pikepdf.Array([pages[7].obj, pikepdf.Name.FitR, 0, 0, 300, 300])),
            # Both /A and /Dest present. A reader ignores /Dest here; the tool
            # must not leave the file in that ambiguous state.
            _link(pdf, _rect(2),
                  A=_goto(pikepdf.Array([pages[8].obj, pikepdf.Name.FitV, 100])),
                  Dest=pikepdf.Array([pages[9].obj, pikepdf.Name.Fit])),
        ]
        pages[2].Annots = pikepdf.Array(page3)

        # -- page 4: things that must not be touched ---------------------
        page4 = [
            # Already named: the name must come out unchanged.
            _link(pdf, _rect(0), A=_goto(pikepdf.String(EXISTING_NAME))),
            # External document.
            _link(pdf, _rect(1), A=pikepdf.Dictionary(
                S=pikepdf.Name.GoToR,
                F=pikepdf.String("other.pdf"),
                D=pikepdf.Array([2, pikepdf.Name.XYZ, 0, 700, 0]),
            )),
            # Web link.
            _link(pdf, _rect(2), A=pikepdf.Dictionary(
                S=pikepdf.Name.URI, URI=pikepdf.String("https://example.invalid/x"),
            )),
            # Dangling: the shape a split tool leaves behind.
            _link(pdf, _rect(3),
                  A=_goto(pikepdf.Array([None, pikepdf.Name.FitH, 612]))),
        ]
        pages[3].Annots = pikepdf.Array(page4)

        # -- a pre-existing name tree ------------------------------------
        nt = pikepdf.NameTree.new(pdf)
        nt[EXISTING_NAME] = pikepdf.Array(
            [pages[10].obj, pikepdf.Name.XYZ, 0, 500, 0]
        )
        pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(Dests=nt.obj))

        # -- a nested bookmark tree --------------------------------------
        _build_outline(pdf)

        pdf.save(path)

    raw.unlink()

    return {
        # explicit sites: 10 from annots (4 + 3 + 3) plus 4 outline nodes
        # distinct names: 4 from page 1, 2 from page 2 (the duplicate
        #   collapses), 3 from page 3, plus 2 new ones from the outline
        #   (its other 2 targets repeat page-1 destinations and share names)
        # page 4 contributes none: named, external, external, dangling
        "expected_distinct_names": 11,
        "expected_explicit_sites": 14,
        "expected_named_sites": 1,
        "expected_external_sites": 2,
        "expected_unresolved_sites": 1,
        "expected_pages": N_PAGES,
        "expected_existing_names": 1,
    }


def _build_outline(pdf: pikepdf.Pdf) -> None:
    """A three-level bookmark tree, so the walk cannot stop at level 1."""
    pages = pdf.pages

    def node(title: str, dest) -> pikepdf.Object:
        return pdf.make_indirect(
            pikepdf.Dictionary(Title=pikepdf.String(title), Dest=dest)
        )

    # Two of these repeat destinations already used by page 1 links, so they
    # must share the generated names rather than create new ones.
    leaf_a = node("L2 leaf a", pikepdf.Array(
        [pages[4].obj, pikepdf.Name.XYZ, 0, 700, 0]))
    leaf_b = node("L2 leaf b", pikepdf.Array(
        [pages[11].obj, pikepdf.Name.XYZ, 0, 250, 0]))
    child = node("L1 child", pikepdf.Array(
        [pages[4].obj, pikepdf.Name.XYZ, 0, 400, 0]))
    top = node("L0 top", pikepdf.Array([pages[0].obj, pikepdf.Name.Fit]))

    leaf_a.Next = leaf_b
    leaf_b.Prev = leaf_a
    child.First = leaf_a
    child.Last = leaf_b
    child.Count = 2
    for n in (leaf_a, leaf_b):
        n.Parent = child

    top.First = child
    top.Last = child
    top.Count = 1
    child.Parent = top

    outlines = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Outlines, First=top, Last=top, Count=1)
    )
    top.Parent = outlines
    pdf.Root.Outlines = outlines


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "tricky.pdf"
    expected = build(path)
    print(f"wrote {path}")
    for k, v in expected.items():
        print(f"  {k:<28} {v}")


if __name__ == "__main__":
    main()