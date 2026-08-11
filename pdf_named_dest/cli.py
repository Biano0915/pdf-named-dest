"""Command line entry point.

    python -m pdf_named_dest.cli --config config.yaml
    python -m pdf_named_dest.cli --config config.yaml --mode inspect --input x.pdf

Exit codes:
    0  ran, and every self check passed
    1  ran, but a self check failed or explicit destinations remain
    2  bad parameters, or the input could not be opened
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .batch import find_pdfs, run_batch
from .config import Config, ConfigError
from .convert_mode import convert
from .inspect_mode import format_report, inspect
from .split import SplitError, split


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf_named_dest",
        description="Convert explicit PDF destinations into named destinations.",
    )
    p.add_argument("--config", type=pathlib.Path,
                   help="YAML file holding the spec section 3 parameters")
    p.add_argument("--mode", choices=("inspect", "convert", "split", "convert+split"))
    p.add_argument("--input", dest="input_path", type=pathlib.Path)
    p.add_argument("--output", dest="output_path", type=pathlib.Path)
    p.add_argument("--name-prefix", dest="name_prefix")
    p.add_argument("--name-pad-width", dest="name_pad_width", type=int)
    p.add_argument("--report", dest="report_path", type=pathlib.Path)

    p.add_argument("--recursive", action="store_true",
                   help="batch mode: also process PDFs in sub-folders")
    p.add_argument("--skip-existing", action="store_true",
                   help="batch mode: leave files whose output already exists, "
                        "so an interrupted run can be resumed")

    p.add_argument("--split-max-pages", dest="split_max_pages", type=int,
                   help="maximum pages per output file (split modes)")
    p.add_argument("--split-align", dest="split_align",
                   choices=("pages", "outline"),
                   help="cut at a fixed page count, or pull each cut back to "
                        "the nearest bookmark so no section is divided")
    p.add_argument("--split-outlines", dest="split_outlines",
                   choices=("first", "own", "all", "none"),
                   help="how much of the bookmark tree each part carries. "
                        "'first' (default) puts the whole tree in part 1 only, "
                        "so a merge shows it exactly once")
    p.add_argument("--split-name-pattern", dest="split_name_pattern",
                   help="output file naming, e.g. '{stem} split {n}{suffix}'")
    p.add_argument("--split-allow-explicit", dest="split_allow_explicit",
                   action="store_true", default=None,
                   help="split even though explicit destinations remain; this "
                        "destroys them permanently and is refused by default")

    outlines = p.add_mutually_exclusive_group()
    outlines.add_argument("--outlines", dest="process_outlines",
                          action="store_true", default=None,
                          help="process the bookmark tree (the default)")
    outlines.add_argument("--no-outlines", dest="process_outlines",
                          action="store_false",
                          help="skip the bookmark tree")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    overrides = {
        "mode": args.mode,
        "input_path": args.input_path,
        "output_path": args.output_path,
        "name_prefix": args.name_prefix,
        "name_pad_width": args.name_pad_width,
        "process_outlines": args.process_outlines,
        "report_path": args.report_path,
        "split_max_pages": args.split_max_pages,
        "split_align": args.split_align,
        "split_outlines": args.split_outlines,
        "split_name_pattern": args.split_name_pattern,
        "split_allow_explicit": args.split_allow_explicit,
    }

    try:
        config = Config.load(args.config, overrides)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Spec section 3: every effective value is printed before any work starts.
    print(config.format_effective())
    print()

    try:
        if config.is_batch:
            return _run_batch(config, args.recursive, args.skip_existing)
        if config.mode == "inspect":
            return _run_inspect(config)
        if config.mode == "split":
            return _run_split(config, config.input_path)
        code = _run_convert(config)
        if code == 0 and config.mode == "convert+split":
            print()
            return _run_split(config, config.output_path)
        return code
    except SplitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the CLI must not dump a traceback
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _run_inspect(config: Config) -> int:
    result = inspect(config.input_path, process_outlines=config.process_outlines)
    print(format_report(result))

    report_path = config.report_path.with_suffix(".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  report written: {report_path}")
    return 0


def _run_convert(config: Config) -> int:
    report = convert(config)
    print(report.format())

    json_path, text_path = report.write(config.report_path)
    print(f"\n  report written: {json_path}")
    print(f"                  {text_path}")

    if not report.ok:
        return 1

    # Spec section 8: an output file must inspect clean. Checking it here means
    # a bad conversion cannot pass unnoticed.
    verify = inspect(config.output_path, process_outlines=config.process_outlines)
    remaining = verify.counts.get("explicit", 0)
    if remaining:
        print(f"\n  ERROR: output still holds {remaining} explicit destination(s)",
              file=sys.stderr)
        return 1
    print(f"\n  verified: output has 0 explicit destinations,"
          f" {verify.n_existing_names} named destinations")
    return 0


def _run_batch(config: Config, recursive: bool, skip_existing: bool) -> int:
    found = find_pdfs(config.input_path, recursive)
    if not found:
        where = "including sub-folders" if recursive else "top level only"
        print(f"error: no PDF files in {config.input_path} ({where})",
              file=sys.stderr)
        return 2

    print(f"BATCH: {len(found)} PDF file(s) in {config.input_path}")
    print()

    def progress(item, n, total):
        mark = {"ok": " ok ", "skipped": "skip", "failed": "FAIL"}[item.status]
        print(f"  [{n}/{total}] [{mark}] {item.name}"
              f"  ({item.elapsed_s}s)"
              + (f"  {item.detail}" if item.detail else ""))

    result = run_batch(
        config, recursive=recursive, skip_existing=skip_existing,
        on_progress=progress,
    )

    print()
    print(result.format())

    summary = config.report_path / "_batch_summary"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.with_suffix(".json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary.with_suffix(".txt").write_text(result.format(), encoding="utf-8")
    print(f"\n  summary written: {summary.with_suffix('.json')}")
    print(f"                   {summary.with_suffix('.txt')}")

    return 0 if result.ok else 1


def _run_split(config: Config, source: pathlib.Path) -> int:
    result = split(
        source,
        config.output_path,
        max_pages=config.split_max_pages,
        align=config.split_align,
        outlines=config.split_outlines,
        pattern=config.split_name_pattern,
        allow_explicit=config.split_allow_explicit,
    )
    print(result.format())

    report_path = config.report_path.with_name(
        config.report_path.stem + "_split"
    ).with_suffix(".json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n  report written: {report_path}")

    if not result.ok:
        return 1

    # Every part must inspect clean: the names it holds must resolve, and the
    # links pointing into other parts must still be present and named.
    print("\n  per-part verification")
    failed = False
    for part in result.parts:
        v = inspect(pathlib.Path(part.path),
                    process_outlines=config.process_outlines)
        explicit = v.counts.get("explicit", 0)
        named = v.counts.get("named", 0)
        status = "ok"
        if explicit:
            status = f"FAIL {explicit} explicit remain"
            failed = True
        elif not v.has_name_tree and part.n_name_tree_entries:
            status = "FAIL name tree missing"
            failed = True
        print(f"    part {part.index}: {v.n_pages:>6} pages, "
              f"{v.n_existing_names:>4} names, {named:>4} named dests, "
              f"{explicit} explicit  [{status}]")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())