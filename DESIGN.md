# Design — PDF Destination Converter and Splitter

How this tool works and why it is built the way it is. Read
[README.md](README.md) first if you just want to run it, and
[RUNBOOK.md](RUNBOOK.md) if you are processing a file today.

> **On the section numbering.** Comments in the source still cite sections as
> `spec 4.2 Step 1`, `spec section 6` and so on. Those refer to the numbered
> sections of this document; the wording predates the file name and was left
> alone rather than churning every comment. Keep the numbering stable if you
> fork this.

---

## 1. What this tool does

### 1.1 The problem

Split a large PDF into parts and its internal hyperlinks die. Not "stop working
until you merge it back" — **die**, permanently, in a way no later step can
undo. A 10,587-page document measured here lost 15 of 31 table-of-contents links
and 32 of 64 bookmark entries the moment it was cut in two, and merging the
parts back did not bring a single one of them back.

This tool prevents that. It rewrites every link so that it survives the round
trip, then performs the split itself.

### 1.2 In scope

- Converting explicit destinations to named destinations, in one PDF
- Both places a destination can live: **link annotations** and **outline
  entries** (bookmarks)
- Splitting to a page limit, with each part carrying exactly the name tree
  entries it is responsible for
- Any document size — nothing here is tuned to a particular page count
- Folder-at-a-time batch processing

### 1.3 Out of scope

| Item | Why |
|---|---|
| **Merging the parts back** | Deliberately left to an external tool, and section 6 explains why that choice is the riskiest part of the whole design |
| Page labels (`/PageLabels`) | Not carried into the parts; the split report warns when the input has them |
| Consolidating duplicate bookmarks after a merge | The `--split-outlines first` default avoids creating duplicates in the first place, which is a better answer than cleaning them up afterwards |
| Creating or editing table-of-contents pages | This tool never touches page content |

---

## 2. Background: why explicit destinations die

A hyperlink in a PDF is a **link annotation** carrying a destination. By
default that destination is **explicit** — it points straight at a page object:

```
/Dest [ 16 0 R  /XYZ  0  792  0 ]
        ^^^^^^ an indirect reference to a page object
```

Delete that page — which is exactly what splitting does to half the document —
and the reference dangles. Every tool observed handles this the same way: it
blanks the reference to `null`.

```
healthy:  /A << /S /GoTo /D [ 16 0 R  /FitH  612 ] >>   -> page 2
broken:   /A << /S /GoTo /D [ null    /FitH  612 ] >>   -> nowhere, forever
```

That `null` is unrecoverable. Nothing anywhere in the file records which page
the link was meant to reach, so no amount of merging, repairing or re-linking
can restore it. The information is gone.

A **named destination** stores a string instead:

```
link annotation:  /A << /S /GoTo  /D (PXD_000042_001) >>
name tree:        (PXD_000042_001) -> [ 16 0 R  /XYZ  0  792  0 ]
```

The annotation now holds **a string, not an object reference**. When the target
page lives in a different part, the name tree simply cannot resolve the name and
clicking does nothing — but the annotation is completely intact. Merge the parts
back, the name matches again, and the link works. Nothing was destroyed; it was
only dormant.

This is why the conversion has to happen **before** the split, on the complete
document. Every part's names then derive from the same source document and are
consistent by construction. There is no mapping table to keep, no cross-file
bookkeeping, and no way for two parts to disagree.

Running it in the other order does not produce a worse result — it produces no
result, because by then the links are already `null`. The tool refuses to do it
(section 4.3).

---

## 3. Parameters

Nothing in the list below has a hard-coded fallback. A missing value stops the
run with exit code 2 rather than quietly picking something, because every one of
these changes the output and a silent default would make two runs differ without
anyone noticing.

| Parameter | CLI | Notes |
|---|---|---|
| Mode | `--mode` | `inspect` / `convert` / `split` / `convert+split` |
| Input | `--input` | A file, or a folder for batch mode |
| Output | `--output` | Must differ from the input; the base name for parts in split modes |
| Name prefix | `--name-prefix` | Must not resemble the internal naming merge and authoring tools generate, which tends to be bare `G1`, `page3`, `Dest7` |
| Zero-pad width | `--name-pad-width` | Must exceed the document's page-count digits |
| Process bookmarks | `--outlines` / `--no-outlines` | On by default |
| Report path | `--report` | Writes `.json` and `.txt` |
| Page limit | `--split-max-pages` | Required in split modes |
| Cut alignment | `--split-align` | `pages` or `outline`; section 4.3 |
| Bookmark distribution | `--split-outlines` | `first` / `own` / `all` / `none`; section 4.3 |
| Part naming | `--split-name-pattern` | Must contain `{n}` |
| Allow unconverted split | `--split-allow-explicit` | Safety override, off by default |
| Recurse | `--recursive` | Batch mode |
| Skip finished | `--skip-existing` | Batch mode, for resuming |

Every effective value is printed at startup and written into the report. That
matters more than it sounds: the two naming parameters silently determine
whether parts produced on different days will link up.

### Why the naming parameters must stay fixed

A name is `prefix + zero-padded page index + sequence number`. Change the prefix
or the pad width and **every name in the document changes**. Process one
document with `PXD_` and its sibling with `PX_`, merge them, and nothing
resolves — with no error message anywhere, because a name that does not resolve
is indistinguishable from a link into a part that is not present.

Pick values with room to spare and never touch them again. `6` covers 999,999
pages.

---

## 4. How it works

### 4.1 Inspect

Reads only; writes nothing. Reports page count, whether a name tree and outline
tree exist, link and bookmark counts, destinations broken down by kind, and
whether the file is encrypted or permission-restricted.

Two numbers decide what to do next:

| Reading | Meaning |
|---|---|
| `explicit > 0` | Conversion applies |
| `explicit = 0`, `named > 0` | Already converted; splitting alone is safe |
| `links = 0` | No hyperlinks; this tool has nothing to do |
| **`unresolved > 0`** | **This file has already been split.** Those links are dead and this tool cannot bring them back — go and find the pre-split original |

That last row is the reason inspect mode exists as a separate mode. Running the
converter on an already-split file produces a clean-looking output and a report
full of successes, while silently preserving only the links that happened to
survive. Checking first costs two seconds.

### 4.2 Convert

**Step 1 — collect.** Build a page-object → page-index map keyed on `objgen`,
then scan two sources, neither optional:

1. every page's `/Annots`, for entries with `/Subtype /Link`
2. the outline tree, walked in full — down through `/First` and *across* through
   `/Next`, at every level, with a visited set because malformed files do
   contain cycles

Each item can carry its destination in either of two places, and both are
checked: the object's `/Dest`, and its `/A` → `/D` when `/S` is `/GoTo`. If both
are present, `/A` wins, because that is what readers do.

| Kind found | Handling |
|---|---|
| Array (explicit) | Convert. **Every display parameter after the page reference is preserved byte for byte** — `/XYZ`, `/Fit`, `/FitH`, `/FitV`, `/FitR`, `/FitB` and all their coordinates. None are dropped, reordered or normalised |
| String or name (already named) | Left completely alone, counted only |
| `/GoToR`, `/URI`, other external actions | Left completely alone, counted only |
| Missing, or a `null` page reference | Recorded as an exception with its location and reason. Never skipped silently |

**Deduplication is the part most likely to go wrong.** The identity key is the
target page index plus *every* display parameter, canonicalised. Deduplicating
on page index alone is the obvious shortcut and it is wrong: one page routinely
carries several destinations that differ only in scroll position, and collapsing
them sends links to the wrong place on the right page. That failure is close to
invisible — the link works, it just lands somewhere slightly wrong, and nobody
notices until a reader complains.

The canonicalisation also distinguishes `null` from `0`. `/XYZ null null null`
means "keep the current view" and `/XYZ 0 0 0` means "top-left corner"; they are
different destinations and must not merge. Numbers are normalised through
`Decimal` so `612` and `612.0` do collapse, since those genuinely are the same.

**Step 2 — assign names.** One name per distinct destination:

```
PXD_000042_001
^^^^ ^^^^^^ ^^^
 |     |     sequence within that page, restarting at 1 per page
 |     zero-padded 0-based page index
 prefix
```

The sequence restarts per page so a name stays readable — you can tell at a
glance where `PXD_000042_003` points. Names already present in the document are
read first and skipped over, never reassigned. Assignment order comes from the
sorted key, not from discovery order, so the same input always produces the same
names: two runs are comparable and re-running is safe.

**Step 3 — rewrite.** Point each converted destination at its name, write the
`/Root/Names/Dests` name tree (extending an existing one rather than replacing
it), and save to a new file.

Two details that cause reader-dependent behaviour if you get them wrong:

- Names go into the name tree as **strings**, not as PDF name objects. Some
  readers accept both; the specification says strings.
- `/Dest` is ignored when `/A` is present. Rather than rely on every reader
  agreeing, the tool keeps one and removes the other, so there is no ambiguity
  left in the file.

### 4.3 Split

Runs after conversion, never before.

**The precondition.** If the input still contains explicit destinations, the
split is refused with exit code 2 and an explanation. This is not a warning that
can be clicked through by accident — splitting an unconverted file is precisely
the operation that destroys links permanently, and it is an easy mistake to make
because it produces plausible-looking output. `--split-allow-explicit` overrides
it, and exists only so the old behaviour can be reproduced deliberately.

**Placing the cuts.**

| `--split-align` | Behaviour |
|---|---|
| `pages` | Cut every time the page limit is reached |
| `outline` | Pull each cut back to the last bookmark target within the limit, so no section is divided. Falls back to a hard cut, recorded in the report, when the next bookmark is further away than the limit allows |

Neither can produce a part over the limit. On the 10,587-page test document with
a 6999 limit: `pages` gives 6999 + 3588, `outline` gives 3733 + 6854 — the
latter because everything from page 3734 on is a single 3,200-page section, so
3733 is the last cut that keeps it whole.

**A document already within the limit is not split at all.** It is written once,
under the plain output name, with no ` split 1` suffix — a split that did not
happen should not show up in the file name. The report says `NO SPLIT NEEDED` so
that a missing part file is never mistaken for a missing output.

**What each part gets.**

| | Rule |
|---|---|
| Pages | Its own range, order and content untouched |
| Link annotations | **All of them, exactly**, including those pointing outside this part. Deleting them is what breaks the round trip |
| Name tree | **Only names whose target page is in this part.** The union across parts equals the input exactly — nothing lost, nothing assigned twice |
| Bookmarks | Per `--split-outlines`, below |

**Bookmark distribution** exists because merge tools concatenate whatever
bookmark trees they find. A complete tree in every part therefore appears once
per part in the merged document.

| Value | Each part carries | Merged (original had 32) | Part opened alone |
|---|---|---|---|
| `first` (default) | Part 1 the whole tree, rest none | **32, no duplication** | Later parts have no bookmark panel |
| `own` | Only bookmarks targeting its own pages, plus ancestors | 33 (shared root twice) | Only working bookmarks |
| `all` | Every part the whole tree | 64 (fully duplicated) | All show 32, half inert |
| `none` | Nothing | 0 | No bookmarks |

`first` is the default because the merged document is the one people read.
Bookmarks in part 1 pointing into part 2 are inert until the merge and then all
recover — the same dormancy that makes the links work.

### 4.4 Batch

Point `--input` at a folder and every PDF inside is processed, in file-name
order so runs are reproducible. Each file is handled exactly as it would be
alone and gets its own report.

Three behaviours worth knowing:

- **One failure does not stop the run.** The reason is recorded, the file is
  marked failed, and the batch continues. The exit code is 1 if anything failed.
- **`--recursive` mirrors the input folder structure into the output.** It has
  to: two sub-folders containing a same-named file would otherwise overwrite
  each other silently, which is data loss disguised as success.
- **The output folder must differ from the input folder**, or this run's output
  becomes next run's input.

For folders scattered across different locations, `run_batch.ps1` wraps this and
gives each its own output folder.

---

## 5. Invariants — what the tool never does

These hold for every mode. Several are checked at run time rather than merely
intended.

| # | Never | Why it matters |
|---|---|---|
| 1 | Modifies page content streams | Only the annotation layer and the document catalogue are touched. Visible content cannot change |
| 2 | Adds, removes or reorders pages | Page count is compared before and after |
| 3 | Deletes a link annotation, however broken it looks | An annotation pointing outside its part is the *design*, not damage. Tools that "clean up" these are the ones that break the round trip |
| 4 | Renames an existing named destination | It may already be referenced from another document, and there is no way to know |
| 5 | Modifies the input file | sha256 is taken before and after and compared. An output path equal to the input is rejected outright |
| 6 | Normalises display parameters | Turning `/XYZ 0 792 0` into `/Fit` would change where the reader lands. Coordinates are copied through untouched |

---

## 6. The downstream merge dependency

**This is the weakest point in the design, and it is external.**

The whole approach rests on one assumption: that the merge tool at the far end
preserves the name tree without renaming. If it does not, everything this tool
does is wasted — the parts are correct, the merged result is broken, and the
failure looks identical to having done nothing at all.

That assumption cannot be taken on trust. `tests/merge_assumption/` tests it
with a deliberately minimal pair of files:

| File | Contents |
|---|---|
| `A.pdf` | A link pointing at `SHARED_NAME`, which **A's own name tree does not define** |
| `B_orphan.pdf` | Defines `SHARED_NAME` → its page 2, and **nothing inside B references it** |

The orphan condition is the whole test. It reproduces the real post-split shape:
the name is defined in the content file while the only link using it lives in
the table-of-contents file. A variant where B also references the name
(`B_referenced.pdf`) is kept alongside it precisely because a merge tool passed
that one and failed the realistic one — a false PASS that would have sent this
design into production broken.

Results, all measured:

| Merge tool | referenced | orphan | Failure mode |
|---|---|---|---|
| Target tool (commercial GUI editor) | — | **PASS** | Verified by hand |
| reference-union (in-house control) | **PASS** | **PASS** | — |
| pikepdf `add_pages_from()` | PASS | FAIL | Carries only names referenced by the copied pages; drops the orphan |
| pikepdf `pages.extend()` | FAIL | FAIL | Carries no name tree at all |
| pypdf `PdfWriter.append()` | FAIL | FAIL | Keeps the name tree but **deletes the unresolvable annotation** |
| qpdf `--pages` | FAIL | FAIL | Name tree vanishes entirely |

Four of four off-the-shelf tools fail the realistic case. The in-house
`reference-union` control passes, which is the important part: it proves the
approach is expressible in PDF terms and the failures are tool behaviour, not a
flaw in the idea. Every failing tool fails by being *clever* — cleaning up links
it judges invalid, or carrying across only the names it can see a use for.

**So: verify your own merge tool before relying on any of this**, and re-verify
after every upgrade. Which tool works is a property of that specific tool and
version and does not transfer. `tests/merge_assumption/FINDINGS.md` has the
procedure, including the manual path for GUI tools via `check_one.py`.

---

## 7. The report

Every run writes `.json` and `.txt`. Both contain:

- every effective parameter value
- input path and sha256 before and after, output path, page counts
- link and bookmark totals
- destinations by kind: converted / preserved-named / preserved-external /
  exceptions
- names generated after deduplication, and final name tree size
- every exception individually, with its location and reason
- self-checks, each PASS or FAIL

The JSON is meant to be kept. It is the record of what a given file was
processed with, which matters when a document has to be reprocessed months later
and has to produce identical names.

Split runs add `_split.json`: per-part page ranges and name counts, the
"input names = names distributed" reconciliation, the page-conservation check,
and any place where bookmark alignment fell back to a hard cut.

---

## 8. Verification

### What is checked automatically, every run

| Check | Method |
|---|---|
| Input untouched | sha256 before and after |
| Page count preserved | Direct comparison |
| Name tree size matches the report | Direct comparison |
| Output has no explicit destinations left | The output is re-inspected after writing |
| Pages conserved across parts | Sum of parts = input |
| Names distributed exactly once | Union of parts = input |
| No part over the limit | Direct comparison |
| Each part is well-formed | Every part is re-inspected |

### `tests/verify_equivalence.py`

The one that actually matters. It pairs every link annotation and outline node
in the input with its counterpart in the output, resolves both down to
`(page index, all display parameters)` — **following names through the name tree
on the output side** — and compares. A link passes only if its name really does
lead back to the same page and the same scroll position.

It also checks both directions, which is what catches deduplication bugs:

- destinations that differ in the input must not become one in the output
  (over-deduplication)
- destinations identical in the input must not become several

100% coverage, not a sample. On the 10,587-page document: 31 links and 32
bookmarks, all identical, zero mismatches.

### `tests/roundtrip.py`

End to end: convert → split → merge → compare. The merge here is the faithful
reference implementation, not a real merge tool, so it answers only the question
this project controls — *did convert and split preserve enough information for
recovery to be possible?* Whether a real merge tool is equally faithful is
section 6's question.

### `tests/make_fixtures.py`

Real documents tend to exercise one code path. The test document from which the
measurements here come uses `/FitH 612` throughout, has no name tree and no
external links — it never touches the cases most likely to break. `tricky.pdf`
is built to contain them deliberately: four destinations on one page differing
only in coordinates, `/XYZ null null null` next to `/XYZ 0 0 0`, a pre-existing
named destination, `/GoToR` and `/URI` actions, a dangling destination, an object
carrying both `/A` and `/Dest`, and a three-level outline.

`setup.ps1` runs this fixture through convert and equivalence checking as its
self-test, so a fresh install proves itself before touching anything real.

### Measured

| | |
|---|---|
| Document | 10,587 pages / 33.0 MB |
| Inspect | 1.9 s |
| Convert | 6.2 s |
| Split | 8.8 s |
| Output growth | +0.6% (the name tree) |
| Destinations converted | 63 → 32 names after deduplication |
| Exceptions | 0 |

---

## 9. Known risks and limitations

| Risk | Status |
|---|---|
| **The merge tool changes** | **Live, and the largest.** Section 6. Nothing in this repository can detect it happening; only re-running the test can |
| Deduplication collapses distinct destinations | Addressed. Full-parameter key, `null` ≠ `0`, and exhaustive rather than sampled verification. Called out because the symptom — a link landing slightly wrong — is easy to miss |
| Convert and split done in the wrong order | Addressed. The split refuses to run on a file with explicit destinations |
| A split tool cleans up "invalid" links | Addressed by bringing the split in-house. It used to be an unverifiable external dependency |
| Duplicate bookmarks after merging | Addressed. `--split-outlines first` by default |
| Time and memory on large documents | Not a concern at the scale measured; see section 8 |
| Encrypted or permission-restricted input | Not handled. Inspect mode reports it; get an unprotected copy |
| `/PageLabels` are not carried into parts | Known gap. The split report warns when the input has them |
| Qualifying an in-house tool for an audited process | Not a technical question. Whoever owns the process decides the classification and evidence level |

---

## 10. Environment

- Python 3.12
- pikepdf (QPDF backend) for all PDF work — it handles compressed
  cross-reference streams, object streams and FlateDecode. Parsing any of that
  by hand would be a mistake
- pypdf as an independent cross-check in inspect mode
- reportlab for building test fixtures only; not needed to process files
- `qpdf` CLI optional, for external structure checking
- No network access at any point. Everything runs locally

Versions are pinned in `requirements.txt` so every machine runs identical code.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| link annotation | An interactive region on a page, carrying coordinates and a destination |
| explicit destination | Points directly at a page object. Dies permanently the moment that page is removed |
| named destination | Points at a name string, resolved through the name tree |
| name tree | `/Root/Names/Dests`, mapping names to destination arrays |
| outline | The bookmark tree shown in a reader's side panel |
| display parameters | `/XYZ`, `/Fit`, `/FitH`… — the view mode and scroll position after a jump |
| dangling reference | The `null` left where a page reference used to be. Unrecoverable |
| objgen | pikepdf's (object number, generation) identity for an indirect object |
