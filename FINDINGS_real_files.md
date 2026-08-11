# Inspection of a real split output pair — split 1 + split 2

**Files**
`large_listings split 1.pdf` (21.9 MB, 6,999 pages)
`large_listings split 2.pdf` (11.2 MB, 3,588 pages)

Both are real production output from an existing split process, taken from the
same source document. File names are generalised here; the measurements are
unchanged.

**Date** 2026-08-10

**Conclusion in one line**
After the split, **15 of 31 table-of-contents links and 32 of 64 bookmark
entries are permanently dead**. Cross-checking confirms that **the target page
of every dead entry still exists in the other file** — there is simply no
mechanism left to connect them. This is exactly the problem named destinations
solve.

---

## Inspect mode output

| Item | split 1 | split 2 |
|---|---|---|
| Pages | 6,999 | 3,588 |
| Encrypted / permission-restricted | No | No |
| Name tree | **Absent** | **Absent** |
| Outline tree | 32 entries | 32 entries |
| Link annotations | **31** | **0** |
| Explicit destinations | 33 (16 annotations + 17 bookmarks) | 15 (all bookmarks) |
| Already named / external | 0 / 0 | 0 / 0 |
| **Unresolvable (dead)** | **30** (15 annotations + 15 bookmarks) | **17** (all bookmarks) |
| Display type | All `/FitH 612` | All `/FitH 612` |
| Scan time | 1.09 s | 0.63 s |

The original is therefore **10,587 pages** (6,999 + 3,588).

---

## Key findings

### 1. The failure mode: destinations blanked to `null`

```
healthy:  /A << /S /GoTo /D [ 16 0 R  /FitH  612 ] >>     -> page 2
broken:   /A << /S /GoTo /D [ null    /FitH  612 ] >>     -> null
```

That `null` is exactly what spec section 2 describes. **These links cannot be
recovered** — nothing in the file records which page they were meant to reach.

### 2. The two files' bookmarks are perfectly complementary

Cross-checking the survival state of all 32 bookmark entries across both files:

| State | Count |
|---|---|
| Alive only in split 1 | 17 |
| Alive only in split 2 | 15 |
| Alive in both | 0 |
| **Dead in both** | **0** |

Zero overlap, zero jointly dead. **Every bookmark's target page still exists
somewhere in the pair.**

The implication is direct: had the destinations been named, merging the two
files back would restore **100% of the 32 bookmarks**. They cannot be restored
now purely because they point at object references rather than names.

### 3. All table-of-contents links are in split 1; split 2 has none

The table of contents is on pages 1-2 of the original and stayed entirely in
split 1. Split 2 therefore has **no clickable contents at all**, only the
bookmark panel.

Of split 1's 31 links, 15 point at pages that ended up in split 2 and were all
blanked to null. Those 15 target pages are sitting intact in split 2.

### 4. The splitter preserved the annotations (spec section 6 assumption 1 holds)

The splitter **did not delete any annotation**; it only set the first element
of the destination array to `null`. All 31 link annotations in split 1 are
still there.

This means that had the links been named destinations, the annotation would
have held a **string** rather than an object reference, the splitter would have
had nothing to blank, and the links would have survived intact.
**Assumption 1 holds for this splitter.**

Assumption 2 (preserving name tree entries) still cannot be verified here,
because the original has no name tree. It requires the converter to produce a
file that has one, then running a real split on it.

### 5. Performance is not a concern

10,587 pages scanned in 1.7 seconds in total. The "memory and processing time
on large files" risk in spec section 9 can be downgraded at this scale.

### 6. The bookmark tree is duplicated in full across both files

Each file carries the complete 32-entry bookmark tree, so merging them back
produces 64 entries with one full duplicate. This is **a pre-existing property
of the current split process, not something this tool causes**, but the merged
bookmark panel will show duplicates and whoever receives the merged file should
know. Per spec 1.3, merging and bookmark consolidation are out of scope for this
tool.

---

## What these two files cannot test

| Uncovered scenario | Why it matters |
|---|---|
| Multiple destinations with different coordinates on one page | Spec section 9 lists this as the high risk that is "hard to notice"; every display type here is `/FitH 612`, so it cannot be exercised |
| Pre-existing named destinations | Spec section 5 rule 4 forbids changing existing names; there is no name tree here |
| External links `/GoToR`, `/URI` | Spec section 5 requires preserving them exactly; the count here is 0 |
| The `/XYZ null null null` form | Extremely common in practice; deduplication must distinguish `null` from `0` |

All four are covered separately by fixtures from `tests/make_fixtures.py`.

---

## Still needed

**The complete original from before the split (10,587 pages).**
That is the converter's real input. Running the conversion on split 1 or
split 2 is already too late: the tool can only preserve what is still alive,
and anything already turned into `null` is unrecoverable.

Once the original is available, one inspect run gives the total link count in
the complete file and confirms whether 31 is the whole picture (the table of
contents may run to more pages).

---

## How to re-run

```powershell
.\.venv\Scripts\python.exe -m pdf_named_dest.cli --config config.yaml --mode inspect --input "input_files\<file>.pdf"
```
