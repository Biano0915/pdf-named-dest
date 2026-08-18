# Merge assumption verification (DESIGN 6)

**Date** 2026-08-10
**Status** **Target merge tool verified manually — implementation proceeded**

**Conclusion in one line**
The approach is structurally sound in PDF terms. All four off-the-shelf Python
merge tools tested failed in the realistic scenario, but **the target merge tool
— a commercial GUI PDF editor — passed manual verification of the `orphan`
scenario** (2026-08-10). DESIGN 6 assumption 3 holds and the project can
proceed.

> The details of every failing tool are kept below. Their significance is no
> longer "the approach does not work" but "**the merge tool cannot be swapped
> casually**" — this test must be re-run before replacing it.

> **If you are adopting this tool, this test is the first thing to run.** Which
> merge tool works is a property of that specific tool and version, not
> something you can carry over from someone else's results.

---

## Test design

Minimal test files were built per DESIGN 6, split into two scenarios:

| File | Contents |
|---|---|
| `A.pdf` | 3 pages. Page 1 holds a link annotation pointing at `SHARED_NAME`. A's own name tree defines only the unrelated `A_OWN_NAME` and **does not define** `SHARED_NAME` |
| `B_referenced.pdf` | 3 pages. Name tree defines `SHARED_NAME` → B page 2. **B also has its own link pointing at it** |
| `B_orphan.pdf` | 3 pages. Name tree defines `SHARED_NAME` → B page 2. **Nothing inside B references it** |

`B_orphan` is the realistic post-split shape: the name is defined in the
content file while the link referencing it lives in the table-of-contents file.
The design originally described only the `referenced` case, and **this
distinction only emerged from testing**.

After merging A + B, all three structural checks must pass:

1. The merged name tree still defines `SHARED_NAME` (not discarded, not
   renamed)
2. A's link annotation is still present and its `/A/D` is still `SHARED_NAME`
   (not cleaned up, not rewritten)
3. `SHARED_NAME` resolves to page 5 of the merged file (= B page 2)

---

## Results

| Merge tool | referenced | orphan | Notes |
|---|---|---|---|
| **Target merge tool (commercial GUI editor)** | — | **PASS** | Verified manually, 2026-08-10 |
| pikepdf `pages.extend()` | FAIL | FAIL | Carries no name tree at all (the library warns about this itself) |
| pikepdf `add_pages_from()` | **PASS** | FAIL | Carries only names referenced by the copied pages' annotations; the orphan name is dropped |
| pypdf `PdfWriter.append()` | FAIL | FAIL | Keeps the name tree, but **deletes A's unresolvable link annotation** |
| qpdf 12.3.2 `--pages` | FAIL | FAIL | Name tree vanishes entirely (0 entries) |
| reference-union (in-house control) | **PASS** | **PASS** | Used to prove the approach itself is sound |

### Failure details

**pypdf** rebuilds links per source file in `pypdf/generic/_link.py`; a named
destination that cannot be resolved within its source file is judged invalid
and discarded outright. This is exactly the top risk in DESIGN 9
("the splitter proactively cleans up invalid links"), only occurring on the
merge side.

**qpdf `--pages`** produces a file with 0 name tree entries. The annotations
survive, but there is nothing left to resolve them against.

**pikepdf `add_pages_from()`** — the documentation states that "named
destinations **referenced by the copied pages' annotations** are carried into
this document". In the orphan scenario nothing inside B references B's name,
and the call reports `dropped_dests = ['SHARED_NAME']`.

**reference-union** is an in-house control that does nothing but concatenate
the pages and union the two name trees, repointing them at the new page
objects. It passes both scenarios.

---

## Interpretation

`reference-union` passing means **the problem is not with the approach** — a
faithful merge genuinely can express this, and it is entirely valid in PDF
terms. The problem is that the off-the-shelf tools all add "clever" cleanup.

Status of the three downstream assumptions in DESIGN 6:

| Assumption | Status |
|---|---|
| 1. Link annotations pointing at deleted pages are preserved during the split | Guaranteed by this tool, which now performs the split itself |
| 2. Name tree entries for the remaining pages are preserved during the split | Guaranteed by this tool (as above) |
| 3. The merge tool preserves the name tree without renaming | **The target tool passed; the other 4 tools tested all failed in the realistic scenario** |

---

## Manual verification procedure (passed; kept for future regression testing)

The target merge tool is a commercial GUI PDF editor that cannot be driven from
Python here, so this is run by hand. **It must be re-run whenever that tool is
upgraded or the process moves to a different merge tool** — and by anyone
adopting this project, against their own merge tool.

Roughly 10 minutes:

1. Open `tests/merge_assumption/out/A.pdf` in the merge tool and merge
   `tests/merge_assumption/out/B_orphan.pdf` into it (A first, B second)
   - **It must be `B_orphan.pdf`, not `B_referenced.pdf`.** Using the wrong
     file yields a false PASS — that is exactly how `pikepdf-addpages` fooled
     the first round of testing.
2. Save the merged result as `tests/merge_assumption/out/merged_manual.pdf`
3. Open the merged file in the reader the process actually uses and
   **click the blue text link on page 1**
   - Jumps to page 5 (showing `B - page 2 of 3`) → the assumption holds
   - Nothing happens, or it jumps elsewhere → the assumption fails
4. Return here and run the structural check to confirm the manual reading:

   ```powershell
   .\.venv\Scripts\python.exe tests\merge_assumption\check_one.py tests\merge_assumption\out\merged_manual.pdf
   ```

Step 4 states exactly which of the three checks failed, which determines what
to do next:

| Failed check | Meaning | Possible response |
|---|---|---|
| [1] The name is gone | The merge tool discards the name tree | The approach is unworkable with this tool and needs re-evaluation |
| [2] The annotation was deleted | The merge tool cleans up "invalid" links | Look for an option to disable that behaviour |
| [3] Resolves to the wrong page | The merge tool renamed or re-mapped destinations | Check whether a naming prefix can avoid the collision |

---

## Residual risks

1. **The merge tool gets replaced.** This is the project's largest residual
   risk. The target tool passed, but all four comparable tools failed, showing
   that preserving the name tree is not general merge tool behaviour but a
   property of that specific tool and version.
   **Process documentation must name the verified merge tool and require this
   test to be re-run before any change.**
2. **DESIGN 6 assumption 2 with an external splitter is untested and now
   moot.** The existing split 1 file had no name tree to begin with, so it
   could not be checked. The split is now performed by this tool, which
   guarantees the behaviour; see `FINDINGS_real_files.md`.
3. Assumption 1 (a splitter preserving link annotations) **was confirmed
   empirically** — all 31 annotations in the split 1 file were intact; the
   splitter only set the destination's page reference to null rather than
   deleting the annotation.

---

## How to re-run

```powershell
.\.venv\Scripts\python.exe tests\merge_assumption\make_ab.py
.\.venv\Scripts\python.exe tests\merge_assumption\verify_merge.py
```

When qpdf is not on PATH, point at it with an environment variable:

```powershell
$env:QPDF_EXE = "C:\path\to\qpdf.exe"
```

To test a new merge tool, add a function to `MERGERS` in `verify_merge.py`. For
a GUI tool, merge by hand and then call `check()` on the resulting file.
