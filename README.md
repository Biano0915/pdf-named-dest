# PDF Destination Converter and Splitter

Converts **explicit destinations** in a PDF into **named destinations**, then
splits the document to a page limit, so that links survive the split and come
back to life when the parts are merged again.

| Document | Contents |
|---|---|
| [RUNBOOK.md](RUNBOOK.md) | **Follow this when a new file arrives.** Includes troubleshooting |
| [DESIGN.md](DESIGN.md) | How it works and why. Code comments refer to its section numbers |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Acceptance results |
| [FINDINGS_real_files.md](FINDINGS_real_files.md) | Damage measured on real production files |
| [tests/merge_assumption/FINDINGS.md](tests/merge_assumption/FINDINGS.md) | Merge tool verification results |

---

## Seeing the difference quickly

Take an original file from **before** a split and one of its **post-split**
outputs, and run inspect mode on each:

```powershell
$py = ".\.venv\Scripts\python.exe"

# before the split
& $py -m pdf_named_dest.cli --mode inspect --input "input_files\<original>.pdf" `
      --name-prefix PXD_ --name-pad-width 6 --report "output_files\before"

# after the split (output of the current process)
& $py -m pdf_named_dest.cli --mode inspect --input "input_files\<original> split 1.pdf" `
      --name-prefix PXD_ --name-pad-width 6 --report "output_files\after"
```

Compare the `unresolved` counts. The original should be 0; the post-split file
shows how many links are already dead beyond recovery. Measured on a real
10,587-page document: 0 before, 30 after.

---

## Installation (new machine / handing it over)

```powershell
.\setup.ps1
```

Creates the virtual environment, installs the pinned packages, and runs a self
test so you know it works. Takes about a minute.

### What to copy

| Copy | Do not copy | Why |
|---|---|---|
| `pdf_named_dest/` | | The code |
| `tests/` | | Tests; `setup.ps1` uses them |
| `*.md`, `*.yaml`, `*.ps1`, `requirements.txt` | | Documentation and settings |
| | **`.venv/`** | **Stores absolute paths, so it breaks on a different machine or path** (58 MB) |
| | `input_files/`, `output_files/` | That is data, not the tool |

Simplest approach: zip the folder **excluding `.venv`**, and have the recipient
run `.\setup.ps1` after extracting.

```powershell
# package it up (excludes the venv and the data)
Compress-Archive -Path pdf_named_dest,tests,*.md,*.yaml,*.ps1,requirements.txt `
                 -DestinationPath pdf_named_dest_tool.zip
```

### What the other machine needs

- **Python 3.12** (from [python.org](https://www.python.org/downloads/); tick
  "Add to PATH" during installation)
- A network connection once, so `setup.ps1` can download the packages. Running
  the tool afterwards needs no network
- If PowerShell blocks scripts, run this once:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

If `setup.ps1` cannot find Python 3.12, point it at one:

```powershell
.\setup.ps1 -PythonExe "C:\Program Files\Python312\python.exe"
```

### Packages

Versions are pinned in [requirements.txt](requirements.txt) so every machine
runs identical code.

| Package | Purpose |
|---|---|
| `pikepdf` 10.11.0 | Main PDF library (QPDF backend) |
| `pypdf` 6.15.0 | Cross-check for inspect mode |
| `PyYAML` 6.0.3 | Config files |
| `reportlab` 5.0.0 | Builds test fixtures (tests only; not needed to process files) |

Acceptance also needs the `qpdf` CLI for PDF structure checking
(`winget install qpdf.qpdf`). The tool itself does not require it, and
**all processing happens locally with no outbound connections**.

---

## Everyday use

### One command, whole pipeline

```powershell
.\.venv\Scripts\python.exe -m pdf_named_dest.cli --mode convert+split `
    --input  "input_files\Listings.pdf" `
    --output "output_files\Listings.pdf" `
    --name-prefix PXD_ --name-pad-width 6 `
    --report "output_files\report" `
    --split-max-pages 6999
```

Produces `output_files\Listings split 1.pdf`,
`output_files\Listings split 2.pdf`, and so on, plus
`output_files\report.json` / `.txt` and `output_files\report_split.json`.

### Processing a whole folder

Point `--input` at a directory and every PDF inside is processed:

```powershell
.\.venv\Scripts\python.exe -m pdf_named_dest.cli --mode convert+split `
    --input  "input_files" `
    --output "output_files" `
    --name-prefix PXD_ --name-pad-width 6 `
    --report "output_files\reports" `
    --split-max-pages 6999
```

Each file is handled exactly as it would be on its own, with its own report;
output names are derived from the input names. Progress is shown per file, and
a summary is printed and written to `reports\_batch_summary.json` / `.txt`.

**One failing file does not stop the batch** — the reason is recorded, the run
continues, and the failure is listed in the summary. The exit code is 1 if
anything failed.

| Extra option | Purpose |
|---|---|
| `--recursive` | Also process PDFs in sub-folders; the output **mirrors the input folder structure** |
| `--skip-existing` | Skip files whose output already exists, so an interrupted run can be resumed |

The output folder must differ from the input folder, otherwise this run's
output becomes next run's input. Point `--report` at a separate sub-folder so
reports do not sit among the PDFs.

### Several folders

When folders live in different places and each needs its own output, use
[run_batch.ps1](run_batch.ps1):

```powershell
# every sub-folder under one root, each as its own job
.\run_batch.ps1 -InputRoot "D:\studies" -OutputRoot "D:\out"

# folders scattered around
.\run_batch.ps1 -InputFolders "D:\project_a","E:\project_b\pdfs" -OutputRoot "D:\out"

# resume after an interruption
.\run_batch.ps1 -InputRoot "D:\studies" -OutputRoot "D:\out" -SkipExisting
```

Each input folder gets a same-named output folder under `-OutputRoot`, with its
own reports. One failing folder does not affect the others, and a summary is
printed at the end:

```
Folder  Files Ok Skipped Failed Status
------  ----- -- ------- ------ ------
study_a     2  1       0      1 partial
study_b     2  2       0      0 ok
```

The exit code is 1 if anything failed, and the output points at which
`_batch_summary.txt` to read.

| Parameter | Default | Notes |
|---|---|---|
| `-Mode` | `convert+split` | Same as the CLI's `--mode` |
| `-NamePrefix` / `-NamePadWidth` | `PXD_` / `6` | **Do not change**; see the parameter notes below |
| `-MaxPages` | `6999` | Page limit per output file |
| `-Align` / `-Outlines` | `pages` / `first` | Same as the CLI |
| `-Config` | — | Pass a YAML file through; per-folder paths are still overridden by the script |
| `-SkipExisting` / `-Recursive` | off | Same as the CLI |

### Using a config file

Put the parameters in YAML (see [config.example.yaml](config.example.yaml)),
then:

```powershell
.\.venv\Scripts\python.exe -m pdf_named_dest.cli --config config.yaml
```

Command line arguments override the config file.

---

## The four modes

| Mode | Purpose |
|---|---|
| `inspect` | Writes nothing; reports what is in the file. Use before converting to check suitability, and after converting as a spot check |
| `convert` | explicit destinations → named destinations |
| `split` | Split a file that has **already been converted** |
| `convert+split` | Both, in the only safe order |

### Looking inside a file first

```powershell
.\.venv\Scripts\python.exe -m pdf_named_dest.cli --mode inspect `
    --input "file.pdf" --name-prefix PXD_ --name-pad-width 6 --report "output_files\check"
```

Read the `verdict` line:

| verdict | Meaning |
|---|---|
| `NEEDS_CONVERSION` | Explicit destinations found; conversion applies |
| `ALREADY_NAMED` | Already in the target state; nothing to do |
| `NO_LINKS` | No links in the file; this tool has no effect |

Also read the `unresolved` count — those are links that are **already dead and
cannot be recovered**. Anything above 0 means this file has already been split.

---

## Parameters

| Parameter | CLI | Notes |
|---|---|---|
| Mode | `--mode` | See the table above |
| Input | `--input` | A file, or a folder (batch mode) |
| Include sub-folders | `--recursive` | Batch mode |
| Skip finished files | `--skip-existing` | Batch mode; for resuming |
| Output | `--output` | Must differ from the input; in split modes it is the base name for the parts |
| Name prefix | `--name-prefix` | Must be distinguishable from a merge tool's internal naming |
| Zero-pad width | `--name-pad-width` | Must exceed the document's page-count digits. **Changing it changes every name**; pick a value with room to spare and keep it fixed |
| Process bookmarks | `--outlines` / `--no-outlines` | On by default |
| Report path | `--report` | |
| Page limit per file | `--split-max-pages` | Required in split modes |
| Cut alignment | `--split-align pages\|outline` | See below |
| Bookmark distribution | `--split-outlines first\|own\|all\|none` | Default `first`, which avoids duplicated bookmarks after a merge |
| Part naming | `--split-name-pattern` | Default `{stem} split {n}{suffix}` |
| Allow splitting unconverted | `--split-allow-explicit` | Safety switch, off by default |

No parameter has a hard-coded default; a missing required value exits with
code 2.

### Two ways to place a cut

| `--split-align` | Behaviour | Measured (10,587 pages, limit 6999) |
|---|---|---|
| `pages` | Cut at a fixed page count | 6999 + 3588 |
| `outline` | Pull the cut back to the nearest bookmark so no section is divided | 3733 + 6854 |

**A document within the page limit is not split.** The file name gets no
` split 1` suffix; it is simply `<name>.pdf`. The report says
`NO SPLIT NEEDED` and the batch summary shows `under page limit, not split`, so
a missing part file is never mistaken for a missing output.

### How bookmarks are distributed

A merge tool concatenates the bookmark trees it finds, so a full copy in every
part shows up once per part in the merged document. `--split-outlines`
controls this:

| Value | Each part carries | After merging (original had 32) | Opening a part on its own |
|---|---|---|---|
| `first` (default) | Part 1 the whole tree, the rest none | **32 entries, no duplication** | Parts after the first have no bookmark panel |
| `own` | Only bookmarks whose target page is in that part | 33 entries (the shared root appears twice) | Shows only bookmarks that work |
| `all` | Every part the whole tree | 64 entries (the whole tree duplicated) | All show 32, half of them inert |
| `none` | Nothing | 0 entries | No bookmarks anywhere |

`first` is the default because the merged document is what actually gets read.
Bookmark destinations are named too, so the part-1 bookmarks pointing into
part 2 are simply inert until the merge, and all recover afterwards.

---

## Safety design

**Split mode refuses to split a file that has not been converted:**

```
error: input still holds 63 explicit destination(s); splitting now would
destroy them permanently. Run convert mode first.
```

Doing it in the wrong order kills links permanently. Overriding requires an
explicit `--split-allow-explicit`.

Also: the input file is never modified (sha256 compared before and after); an
output path equal to the input is rejected outright; and after every conversion
the output is re-inspected to confirm its explicit count is 0.

---

## Tests

```powershell
$py = ".\.venv\Scripts\python.exe"

# end to end: convert -> split -> merge, comparing where every link lands
& $py tests\roundtrip.py "input_files\your_file.pdf" "output_files\roundtrip" 6999

# awkward-case fixture (same page multiple coordinates, pre-existing names,
# external links, dangling destinations)
& $py tests\make_fixtures.py
& $py -m pdf_named_dest.cli --mode convert --input tests\fixtures\tricky.pdf `
      --output output_files\tricky.pdf --name-prefix PXD_ --name-pad-width 6 --report output_files\t
& $py tests\verify_equivalence.py tests\fixtures\tricky.pdf output_files\tricky.pdf

# merge tool verification (re-run when changing or upgrading the merge tool)
& $py tests\merge_assumption\make_ab.py
& $py tests\merge_assumption\verify_merge.py
```

`verify_equivalence.py` pairs up every link in the input and the output,
resolves both down to `(page index, all display parameters)` and compares them;
the output side follows the name through the name tree. This is the automated
form of acceptance criteria 8 and 9, at 100% coverage rather than a sample.

---

## Important limitation — verify your merge tool first

**This tool is only half the job. The merge at the other end has to cooperate,
and most tools do not.** All four Python merge tools tested failed in the
realistic scenario: some discard the entire name tree, others delete links they
cannot resolve as if they were invalid. Preserving the name tree is **not**
general merge tool behaviour.

So: before relying on any of this, run `tests/merge_assumption` against your own
merge tool, and re-run it whenever that tool is changed or upgraded. A GUI tool
that cannot be driven from Python is merged by hand and checked with
`check_one.py`. See
[tests/merge_assumption/FINDINGS.md](tests/merge_assumption/FINDINGS.md) for the
procedure and the results for each tool tested.

---

## Project layout

```
setup.ps1           first-time installation on a new machine
requirements.txt    pinned package versions
run_batch.ps1       several folders in one run
input_files/        PDFs to process go here
output_files/       results: parts and reports
pdf_named_dest/
  config.py         parameter loading and effective-value output (spec 3)
  model.py          DestKey / DestSite -- the deduplication key lives here
  collect.py        scanning annotations and the bookmark tree (spec 4.2 Step 1)
  naming.py         name assignment (Step 2)
  rewrite.py        rewriting and writing the name tree (Step 3)
  split.py          splitting (spec 4.3)
  inspect_mode.py   inspect mode (spec 4.1)
  convert_mode.py   convert mode
  batch.py          whole-folder processing (spec 4.4)
  report.py         processing report (spec 7)
  cli.py            entry point
tests/
  make_fixtures.py       awkward-case fixture
  verify_equivalence.py  exhaustive comparison of where links land
  roundtrip.py           convert -> split -> merge, end to end
  merge_assumption/      merge tool verification (spec 6)
```
