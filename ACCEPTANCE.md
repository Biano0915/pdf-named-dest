# Acceptance results

Measured results against the acceptance criteria the tool was built to meet.
The verification approach behind them is described in
[DESIGN.md](DESIGN.md) section 8.

**Date** 2026-08-10
**File under test** `large_listings.pdf`
(33.0 MB, 10,587 pages, the complete original before any split; a real
production document, named generically here)
**Output** `output_files/Listings_named.pdf` (33.2 MB, 10,587 pages)

---

## Item by item

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Every section 3 parameter externally configurable, no hard-coded values | PASS | `config.py` exits with `ConfigError` (code 2) if any parameter is missing; see `config.example.yaml` |
| 2 | All effective parameter values printed at run time | PASS | Every run opens with `EFFECTIVE PARAMETERS`, seven entries |
| 3 | Input unmodified after the run (hash comparison) | PASS | sha256 before and after both `ad84f094…6f15` |
| 4 | Output page count identical to the input | PASS | 10,587 → 10,587 |
| 5 | Output passes a PDF structure checking tool | PASS | `qpdf --check` exit 0, `No syntax or stream encoding errors found` |
| 6 | Inspect mode on the output reports 0 explicit destinations | PASS | Automatic re-check after conversion: `0 explicit, 32 named` |
| 7 | Output name tree entry count equals the report's name count | PASS | Both 32 |
| 8 | **At least 30 sampled links jump to the same page and scroll position** | PASS — **exhaustive, not sampled** | `verify_equivalence.py`: all 31 links compared, 31 identical / 0 mismatched |
| 9 | At least 5 sampled bookmarks jump to the same position | PASS — **exhaustive, not sampled** | All 32 bookmarks compared, 32 identical / 0 mismatched |
| 10 | Pre-existing named destinations keep their names | PASS | This file has none (0); verified with `tricky.pdf`: 1 pre-existing name preserved exactly |
| 11 | Links to external files unchanged | PASS | This file has none (0); verified with `tricky.pdf`: `/GoToR` + `/URI`, 2 in total, byte-identical |
| 12 | **Multiple destinations on one page still jump to their own positions** | PASS | This file uses a single display type throughout (all `/FitH 612`), so it cannot exercise this; verified with `tricky.pdf`: 4 different coordinates on one page → 4 separate names, no merging |
| 13 | Two runs in succession: second reports 0 explicit and generates no new names (idempotency) | PASS | Second run: explicit 0, named kept 63, generated 0, name tree still 32 |
| 14 | One run on a realistically sized file, with time and memory recorded | PASS | See below |

**All 14 criteria passed.**

---

## Measured performance

| Item | Value |
|---|---|
| File | 10,587 pages / 33.0 MB |
| Inspect mode | **1.91 s** |
| Convert mode (read, rewrite, write out, re-check) | **6.18 s** |
| Second conversion (idempotency check) | 4.06 s |
| Output size | 33.2 MB (+0.6%, i.e. the added name tree) |

The "memory and processing time on large files" risk in spec section 9 can be
downgraded.

---

## What was converted

| Item | Count |
|---|---|
| Link annotations | 31 |
| Outline entries | 32 |
| Explicit destinations (converted) | **63** (31 annotations + 32 bookmarks) |
| Already named (preserved) | 0 |
| External (preserved) | 0 |
| Exceptions | **0** |
| Names generated after deduplication | **32** |
| Final name tree entries | 32 |

63 destinations deduplicate to 32 names because the table of contents links and
the bookmarks mostly point at the same targets and therefore share names. The
original file contains **no dead destinations at all**, which confirms what
spec section 2 states: links die at the moment of the split, not before.

---

## Note on how criteria 8 and 9 were met

The spec asks for "at least 30 sampled links" and "at least 5 sampled
bookmarks" to be compared by clicking. This was done instead as an
**exhaustive automated comparison** in `tests/verify_equivalence.py`:

1. Pair every link annotation and outline node in the input with its
   counterpart in the output
2. Resolve both destinations down to `(page index, all display parameters)` —
   the output side **follows the name through the name tree**, so a link only
   passes if its name really does lead back to the same page and the same
   scroll position
3. Additionally check the mapping in both directions:
   - destinations that differ in the input must not be merged into one in the
     output (guards against over-deduplication)
   - destinations that are identical in the input must not split into several
     in the output

This is stronger than sampling: 100% coverage, and it catches deduplication
errors that a sample could easily miss — the failure spec section 9 describes
as hard to notice.

**It does not replace clicking by hand.** Structural correctness is not the
same as correct behaviour in a real PDF reader. Clicking a few links in the
target reader before delivery is still recommended.

---

# Split functionality

Splitting was originally left to a downstream tool and was brought into this one
partway through. The results below cover that addition.

Reason for the change: performing the split here turns spec section 6
assumption 2 (whether the splitter preserves the name tree) from an
unverifiable external risk into behaviour this tool guarantees. That leaves the
merge side as the only external dependency in the chain, and that side has been
verified against the merge tool actually in use.

## Safety design

| Behaviour | Description |
|---|---|
| **Refuses to split an unconverted file** | If the input still holds explicit destinations, execution is refused with exit code 2 and an explanation. This is precisely the operation that kills links permanently |
| Name distribution is auditable | Each name goes into exactly one part; the report compares "input name count = names distributed" |
| Page conservation | The report compares "sum of part page counts = input page count" |
| Per-part re-check | Each part is automatically run through inspect mode to confirm the name tree is present and explicit count is 0 |

## Two ways to split

| `split_align` | Behaviour | Measured (limit 6999 pages) |
|---|---|---|
| `pages` | Fixed page count, matching the existing external tool | 6999 + 3588 |
| `outline` | Cut pulled back to the nearest bookmark boundary so no listing is divided | 3733 + 6854 |

`outline` mode cuts at page 3733 because everything after page 3734 is a single
listing of more than 3,200 pages; cutting at 3733 keeps it whole in one file.

## Bookmark distribution (`split_outlines`)

A merge tool concatenates the bookmark trees it finds, so a complete tree in
every part duplicates after merging.

| Value | Each part carries | After merging (original had 32) |
|---|---|---|
| `first` (default) | Part 1 complete, the rest none | **32 entries, no duplication** |
| `own` | Only bookmarks whose target page is in that part | 33 entries (shared root duplicated once) |
| `all` | Every part complete | 64 entries (whole tree duplicated) |
| `none` | Nothing | 0 entries |

Default is `first`. Bookmark destinations are named after conversion, so
bookmarks in part 1 pointing into part 2 are inert before the merge and all
recover afterwards — the end-to-end check confirms all 32 bookmarks resolve
correctly.

## Measured split (`pages` mode, limit 6999, `split_outlines=first`)

```
    #   pages          range            names  links  marks  file
    1     6999         1-6999        17     31     32  Listings split 1.pdf
    2     3588      7000-10587       15      0      0  Listings split 2.pdf

  name tree entries    : 32 in input, 32 distributed
```

**Exactly the same shape as the existing external tool produces** (6999 + 3588,
name tree 17 + 15), but both files have **0** explicit destinations and **0**
dead links. The old process produced 30 dead links at this same point.

| | Old process | This tool |
|---|---|---|
| split 1 dead links | 15 / 31 | **0 / 31** |
| split 1 dead bookmarks | 15 / 32 | **0 / 32** |
| split 2 dead bookmarks | 17 / 32 | **0 / 32** |

---

# End-to-end verification: convert → split → merge

`tests/roundtrip.py` runs the full pipeline on the real file and compares the
result:

```
  [1] convert          : 32 names generated, 63 explicit converted, 0 exceptions, 5.51s
  [2] split            : 2 parts (6999, 3588 pages), 32/32 names distributed, 7.21s
  [3] merge            : merged.pdf

  pages                : 10587 -> 10587
  link annotations     : 31 compared, 31 resolvable before, 31 after, 0 mismatched, 0 broken
  outline items        : 32 compared, 32 resolvable before, 32 after, 0 mismatched, 0 broken

  => PASS
```

**All 63 destinations from the original return to exactly the same page and
scroll position after conversion, splitting and merging. Zero failures, zero
position shifts.**

The merge used here is a faithful reference merge (pages concatenated, name
trees unioned). It answers the question this tool controls: did conversion and
splitting preserve enough information for the links to be recoverable? Whether
the merge tool in use merges equally faithfully is a separate question, verified
independently in `tests/merge_assumption` (passed).

---

## Still requires manual confirmation

1. **Merge this tool's parts with the merge tool actually in use** and confirm
   by clicking in the target reader. Structural correctness is not the same as
   correct reader behaviour.
2. **Page labels.** If the input carries `/PageLabels`, they are not carried
   into the parts and the report issues a warning. Per spec 1.3 this is handled
   by a downstream tool.

---

## How to reproduce

```powershell
$py = ".\.venv\Scripts\python.exe"

# convert
& $py -m pdf_named_dest.cli --mode convert `
    --input "input_files\large_listings.pdf" `
    --output "output_files\Listings_named.pdf" `
    --name-prefix "PXD_" --name-pad-width 6 --report "output_files\full_report"

# equivalence check (criteria 8, 9, 12)
& $py "tests\verify_equivalence.py" `
    "input_files\large_listings.pdf" `
    "output_files\Listings_named.pdf"

# awkward cases (criteria 10, 11, 12)
& $py "tests\make_fixtures.py"
& $py -m pdf_named_dest.cli --mode convert --input "tests\fixtures\tricky.pdf" `
    --output "output_files\tricky_converted.pdf" --name-prefix "PXD_" --name-pad-width 6 `
    --report "output_files\tricky_report"
& $py "tests\verify_equivalence.py" "tests\fixtures\tricky.pdf" "output_files\tricky_converted.pdf"

# idempotency (criterion 13)
& $py -m pdf_named_dest.cli --mode convert --input "output_files\Listings_named.pdf" `
    --output "output_files\Listings_named_pass2.pdf" --name-prefix "PXD_" `
    --name-pad-width 6 --report "output_files\pass2_report"

# structure check (criterion 5) — requires the qpdf CLI to be installed
qpdf --check "output_files\Listings_named.pdf"
```
