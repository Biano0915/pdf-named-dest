# Runbook — what to do when a new file arrives

Follow this for every new output file. About 5 minutes, not counting the merge
and the manual click check.

> This runbook refers to the merge step generically. Substitute whichever merge
> tool your process has verified with `tests/merge_assumption`; see
> [tests/merge_assumption/FINDINGS.md](tests/merge_assumption/FINDINGS.md) for
> why the choice matters.

```powershell
cd "<project folder>"
$py = ".\.venv\Scripts\python.exe"
```

First time on this machine? Run `.\setup.ps1` first (see the installation
section of the README).

---

## Where files go

| Folder | Contents |
|---|---|
| `input_files/` | PDFs to process |
| `output_files/` | Parts and reports produced by the tool |

The tool does not require these locations — `--input` and `--output` use
whatever path you give them. This is only a convention, but following it means
the commands below can be copied verbatim.

(`tests/` holds the test code. It has nothing to do with your files; do not put
anything there.)

---

## Step 0 — Confirm you have the right file

**It must be the complete file from before any split.**

| Symptom | Meaning |
|---|---|
| File name contains `split`, `part`, `_1` | Almost certainly the wrong file; ask for the original |
| `unresolved` above 0 in step 1 | **Definitely** the wrong file: it has already been split and contains links that cannot be recovered |

Running this tool on an already-split file can only preserve the links that are
still alive. The dead ones are gone.

---

## Step 1 — Inspect mode (always do this)

```powershell
& $py -m pdf_named_dest.cli --mode inspect `
    --input "input_files\<new file>.pdf" `
    --name-prefix PXD_ --name-pad-width 6 `
    --report "output_files\check"
```

Writes nothing; reads only. Look at four things:

### 1. `verdict`

| Value | Action |
|---|---|
| `NEEDS_CONVERSION` | Normal; carry on |
| `ALREADY_NAMED` | Already converted. You can run `--mode split` alone |
| `NO_LINKS` | No links and no bookmarks, so conversion achieves nothing. If you only need to split, run `--mode split` |

### 2. `unresolved`

**Should be 0.** Anything above 0 means the file has already been split; see
step 0.

If the source is confirmed correct but a few unresolved entries remain, read
the reasons listed in the report before deciding. Some are pre-existing
problems in the source document (a bookmark pointing at a deleted section, for
example). You can proceed in that case, but note it in the delivery record.

### 3. `encrypted` / `writable`

`encrypted: True` with `writable: False` → **stop** and ask for an unprotected
copy.

### 4. `pages`

Determines the zero-pad width in step 2, and tells you roughly how many parts
to expect.

---

## Step 2 — Confirm the parameters

| Parameter | Value | Changeable? |
|---|---|---|
| `--name-prefix` | `PXD_` | **Do not change** |
| `--name-pad-width` | `6` | **Do not change** |
| `--split-max-pages` | `6999` | Adjust as needed |
| `--split-align` | `pages` | `outline` also available |
| `--split-outlines` | `first` | `own` / `all` / `none` also available |

### Why the first two are fixed

A name is built from prefix + zero-padded page index + sequence number. Change
either and **every name changes**. If one document is processed twice with
different settings, the two sets of parts will not link up, and links break
after merging.

`6` covers up to 999,999 pages, far beyond any real need, so there is no reason
to touch it.

### `pages` or `outline`

| | Behaviour | When to use |
|---|---|---|
| `pages` | Cut at a fixed page count | Matches the external tool currently in use; the default |
| `outline` | Pull the cut back to the nearest bookmark so a listing is not divided | When each file should contain whole sections |

Neither produces a file over the limit. Measured on a 10,587-page document:
`pages` → 6999 + 3588; `outline` → 3733 + 6854.

**A file within the page limit is not split.** The output is simply
`<name>.pdf`, with no ` split 1` suffix. The report prints `NO SPLIT NEEDED`
and the batch summary shows `under page limit, not split`. A file without a
`split 1` is not a missing output — it just did not need splitting.

### How bookmarks are distributed

A merge tool concatenates the bookmark trees it finds, so a full copy in every
part duplicates after merging.

| `--split-outlines` | Each part carries | After merging (original had 32) |
|---|---|---|
| `first` (default) | Part 1 the whole tree, the rest none | **32 entries, no duplication** |
| `own` | Only bookmarks whose target page is in that part | 33 entries (shared root appears twice) |
| `all` | Every part the whole tree | 64 entries (whole tree duplicated) |
| `none` | Nothing | 0 entries |

**Keep the default `first`.** Parts after the first have no bookmark panel when
opened alone, but the merged document is what actually gets read, and that one
matches the original exactly.

---

## Step 3 — Convert and split

```powershell
& $py -m pdf_named_dest.cli --mode convert+split `
    --input  "input_files\<new file>.pdf" `
    --output "output_files\<project code>\<name>.pdf" `
    --name-prefix PXD_ --name-pad-width 6 `
    --report "output_files\<project code>\report" `
    --split-max-pages 6999
```

Output:

```
output_files\<project code>\
  <name> split 1.pdf
  <name> split 2.pdf
  <name>.pdf              converted, pre-split intermediate
  report.json             audit record, keep
  report.txt              human-readable version
  report_split.json       split record, keep
```

**The input file is never modified.** The report carries the sha256 from before
and after the run as evidence.

---

### Processing several files at once

Put all the PDFs in `input_files/` and point `--input` at the folder:

```powershell
& $py -m pdf_named_dest.cli --mode convert+split `
    --input  "input_files" `
    --output "output_files" `
    --name-prefix PXD_ --name-pad-width 6 `
    --report "output_files\reports" `
    --split-max-pages 6999
```

Progress is shown per file, followed by a summary:

```
  [1/3] [ ok ] Listings.pdf  (12.4s)
  [2/3] [FAIL] Tables.pdf  (0.1s)  PdfError: unable to find trailer dictionary
  [3/3] [ ok ] Figures.pdf  (8.9s)

    file          pages  names  parts  dead  status
    Listings.pdf  10587     32      2     0  ok
    Tables.pdf        0      0      0     -  failed  PdfError: unable to fin...
    Figures.pdf    4210     18      1     0  ok

  ok 2   skipped 0   failed 1
```

**One failing file does not stop the batch.** The reason is recorded, the file
is skipped, and it is listed in the summary and in
`reports\_batch_summary.txt`. The exit code is 1 if anything failed.

| Extra option | Purpose |
|---|---|
| `--recursive` | Also process sub-folders; the output mirrors the input folder structure |
| `--skip-existing` | Skip files that already have output. Add this to resume an interrupted run |

### Several folders

When each folder needs its own output, use `run_batch.ps1`:

```powershell
# every sub-folder under one root
.\run_batch.ps1 -InputRoot "D:\studies" -OutputRoot "D:\out"

# folders scattered around
.\run_batch.ps1 -InputFolders "D:\project_a","E:\project_b\pdfs" -OutputRoot "D:\out"
```

Each input folder gets a same-named output folder under `-OutputRoot`. One
failing folder does not affect the others; a summary is printed at the end
pointing at which `_batch_summary.txt` to read. Add `-SkipExisting` to resume
after an interruption.

In batch mode the per-file reports from step 4 are still written to the
`--report` folder; only the screen output changes to a summary. Open a file's
`.txt` to see its detail.

---

## Step 4 — Read the report (always do this)

It is printed on screen and saved to `report.txt`. Four things:

### 1. Conversion self checks — all four must PASS

```
    [PASS] input file unchanged (sha256)
    [PASS] page count preserved
    [PASS] name tree size matches report
    [PASS] no warnings
```

### 2. `exceptions` — should be 0

If there are any, each is listed with its location and reason. Read them before
deciding whether the output is deliverable.

### 3. Split self checks — all four must PASS

```
    [PASS] all pages accounted for, none lost or duplicated
    [PASS] all name tree entries distributed exactly once
    [PASS] every part within 6999 pages
    [PASS] no warnings
```

### 4. Per-part verification — every line must say `[ok]`

```
    part 1:   6999 pages,   17 names,   63 named dests, 0 explicit  [ok]
    part 2:   3588 pages,   15 names,    0 named dests, 0 explicit  [ok]
```

`explicit` must be 0.

> **Part 2 showing 0 for `links` and `named dests` is normal.**
> The table of contents sits on pages 1-2 and stays in part 1 after the split;
> with the default `first` mode the bookmarks are only in part 1 as well.

---

## Step 5 — End-to-end verification (optional)

**Not needed** for routine processing. Run it when:

- processing output from a document-generation process for the first time
- step 1 shows a `display type` you have not seen before
- anything in step 4 did not PASS and you want to know the impact

```powershell
& $py tests\roundtrip.py "input_files\<new file>.pdf" "output_files\rt" 6999
```

Runs convert → split → merge, compares where every link lands, and prints
`PASS` or `FAIL`. About 30 seconds on a large file.

---

## Step 6 — Delivery and merging

1. Hand `output_files\<project code>\*split*.pdf` to the downstream process
2. **Merging must be done with the verified merge tool** — see the warning in
   step 2 of `tests/merge_assumption/FINDINGS.md`. Most merge tools destroy the
   name tree and undo everything this tool did
3. After merging, **click a few links by hand** in the target reader — one each
   from the start, middle and end of the table of contents

Keep `report.json` and `report_split.json` as the audit record.

---

## Troubleshooting

| Situation | Cause | Fix |
|---|---|---|
| `error: input still holds N explicit destination(s)` | `--mode split` used on an unconverted file | Use `--mode convert+split` |
| `error: output_path must differ from input_path` | Output would overwrite the input | Change the output path |
| `error: missing required parameter 'X'` | Parameter not supplied | Add it, or use a config file |
| `error: name_pad_width=N is too small` | Pad width too small for this document's page count | Do not simply raise it — check whether the page count is unexpected. If it genuinely must change, this document must use the new value every time from then on |
| `inspect` shows many `unresolved` | The file has already been split | Ask for the pre-split original |
| `inspect` shows `encrypted: True` | The file has permission protection | Ask for an unprotected copy |
| Split report warns about `/PageLabels` | The input carries page labels, which are not carried into the parts | Handled downstream per DESIGN 1.3; raise it separately if downstream needs them |
| Links do nothing after a merge | See below | |

### Links dead after a merge

First confirm the parts themselves are fine:

```powershell
& $py -m pdf_named_dest.cli --mode inspect `
    --input "output_files\<project code>\<name> split 1.pdf" `
    --name-prefix PXD_ --name-pad-width 6 --report "output_files\recheck"
```

`name tree present: True` and `explicit: 0` → the parts are fine, so the
problem is on the merge side.

Then re-run the merge tool verification:

```powershell
& $py tests\merge_assumption\make_ab.py
```

Merge `tests\merge_assumption\out\A.pdf` + **`B_orphan.pdf`** by hand in the
merge tool (it must be `B_orphan.pdf`, not `B_referenced.pdf`), save as
`merged_manual.pdf`, then:

```powershell
& $py tests\merge_assumption\check_one.py "tests\merge_assumption\out\merged_manual.pdf"
```

It reports which of the three checks failed and what that implies.

**If this check fails, the merge tool's version or its settings have changed.**
This is the critical dependency of the whole approach: report it immediately and
do not continue delivering. See `tests/merge_assumption/FINDINGS.md`.

---

## Simplifying with a config file

Typing a long argument list every time invites mistakes. Keep one YAML file per
project:

```yaml
mode: convert+split
input_path: input_files/project_a Listings.pdf
output_path: output_files/project_a/Listings.pdf
name_prefix: PXD_
name_pad_width: 6
process_outlines: true
report_path: output_files/project_a/report
split_max_pages: 6999
split_align: pages
split_name_pattern: "{stem} split {n}{suffix}"
split_allow_explicit: false
```

```powershell
& $py -m pdf_named_dest.cli --config configs\project_a.yaml
```

The config file doubles as an audit record and guarantees the same document is
always processed with the same parameters. Command line arguments override it,
so a one-off adjustment does not require editing the file.
