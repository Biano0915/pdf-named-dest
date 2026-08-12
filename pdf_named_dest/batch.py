"""Process every PDF in a folder instead of one file at a time.

Triggered by pointing --input at a directory. Each file is handled exactly as
it would be on its own, with its own report; one bad file is recorded and the
run continues, because a batch of fifty should not stop on the third.
"""

from __future__ import annotations

import dataclasses
import json
import os.path
import pathlib
import time
from dataclasses import dataclass, field

from .config import Config
from .convert_mode import convert
from .inspect_mode import inspect
from .split import split

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# Summary table name column. Files produced together tend to share a long
# generated prefix and differ only at the tail, so a head-preserving truncation
# hides the one part of the name that identifies the row.
NAME_COL_MAX = 72
# Below this, stripping the shared head costs more in explanation than it saves.
MIN_SHARED_PREFIX = 8


def _shared_prefix(names: list[str]) -> str:
    """The head every name has in common, trimmed back to a word boundary.

    Returns "" when there is nothing worth stripping, so the caller can treat
    "no shared prefix" and "prefix too short to bother" identically.
    """
    if len(names) < 2:
        return ""
    prefix = os.path.commonprefix(names)
    # Cutting mid-word reads as corruption; back up to a natural break.
    cut = max((prefix.rfind(c) for c in " -_/\\"), default=-1)
    prefix = prefix[:cut + 1] if cut >= 0 else ""
    if len(prefix) < MIN_SHARED_PREFIX:
        return ""
    # Never strip a name down to nothing.
    if any(len(n) <= len(prefix) for n in names):
        return ""
    return prefix


def _shorten(name: str, width: int) -> str:
    """Drop characters from the middle, because the tail is the identifying end."""
    if len(name) <= width:
        return name
    if width <= 3:
        return name[:width]
    keep_tail = (width - 3) * 2 // 3
    keep_head = width - 3 - keep_tail
    return name[:keep_head] + "..." + name[len(name) - keep_tail:]


@dataclass
class BatchItem:
    name: str
    status: str
    detail: str = ""
    n_pages: int = 0
    n_names: int = 0
    n_parts: int = 0
    n_unresolved: int = 0
    elapsed_s: float = 0.0


@dataclass
class BatchResult:
    mode: str
    input_dir: str
    output_dir: str
    items: list[BatchItem] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def n_ok(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_OK)

    @property
    def n_failed(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_FAILED)

    @property
    def n_skipped(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_SKIPPED)

    @property
    def ok(self) -> bool:
        return self.n_failed == 0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "n_files": len(self.items),
            "n_ok": self.n_ok,
            "n_skipped": self.n_skipped,
            "n_failed": self.n_failed,
            "elapsed_s": round(self.elapsed_s, 2),
            "items": [i.__dict__ for i in self.items],
        }

    def format(self) -> str:
        lines = ["=" * 72, "BATCH SUMMARY", "=" * 72]
        lines.append(f"  mode                 : {self.mode}")
        lines.append(f"  input folder         : {self.input_dir}")
        lines.append(f"  output folder        : {self.output_dir}")
        lines.append(f"  files                : {len(self.items)}")

        # Strip the shared head so the column carries only what differs. The
        # full name still goes to the JSON and to the failures block below.
        prefix = _shared_prefix([i.name for i in self.items])
        shown = [i.name[len(prefix):] for i in self.items]
        if prefix:
            lines.append(f"  shared name prefix   : {prefix}")
            lines.append("                         (trimmed from the table below)")
        lines.append("")

        width = max((len(n) for n in shown), default=4)
        width = min(width, NAME_COL_MAX)
        header = f"    {'file':<{width}}  {'pages':>7} {'names':>6} {'parts':>6} {'dead':>5}  status"
        lines.append(header)
        for i, display in zip(self.items, shown):
            name = _shorten(display, width)
            dead = str(i.n_unresolved) if i.status == STATUS_OK else "-"
            # The row keeps the detail short; the failures block below prints
            # it in full.
            detail = i.detail if len(i.detail) <= 44 else i.detail[:41] + "..."
            lines.append(
                f"    {name:<{width}}  {i.n_pages:>7} {i.n_names:>6} "
                f"{i.n_parts:>6} {dead:>5}  {i.status}"
                + (f"  {detail}" if detail else "")
            )

        lines.append("")
        lines.append(f"  ok {self.n_ok}   skipped {self.n_skipped}   "
                     f"failed {self.n_failed}")
        if self.n_failed:
            lines.append("")
            lines.append("  failures")
            for i in self.items:
                if i.status == STATUS_FAILED:
                    lines.append(f"    {i.name}")
                    lines.append(f"      {i.detail}")
        lines.append("")
        lines.append(f"  elapsed              : {round(self.elapsed_s, 2)}s")
        lines.append("=" * 72)
        return "\n".join(lines)


def find_pdfs(folder: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(
        p for p in folder.glob(pattern)
        if p.is_file() and not p.name.startswith(".")
    )


def _per_file_config(config: Config, src: pathlib.Path) -> Config:
    """Derive the single-file config for one member of the batch.

    With --recursive the input tree is mirrored under the output folder rather
    than flattened, so two sub-folders holding a file of the same name do not
    overwrite each other.
    """
    stem = src.stem
    try:
        sub = src.relative_to(config.input_path).parent
    except ValueError:  # pragma: no cover - src always comes from input_path
        sub = pathlib.Path(".")

    out_dir = config.output_path / sub if config.output_path else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    report_dir = config.report_path / sub
    report_dir.mkdir(parents=True, exist_ok=True)

    return dataclasses.replace(
        config,
        input_path=src,
        output_path=(out_dir / f"{stem}.pdf") if out_dir else None,
        report_path=report_dir / stem,
    )


def run_batch(
    config: Config,
    *,
    recursive: bool = False,
    skip_existing: bool = False,
    on_progress=None,
) -> BatchResult:
    started = time.perf_counter()

    sources = find_pdfs(config.input_path, recursive)
    result = BatchResult(
        mode=config.mode,
        input_dir=str(config.input_path),
        output_dir=str(config.output_path) if config.output_path else "(none)",
    )
    if not sources:
        return result

    if config.output_path is not None:
        config.output_path.mkdir(parents=True, exist_ok=True)
    config.report_path.mkdir(parents=True, exist_ok=True)

    for src in sources:
        item = _process_one(config, src, skip_existing)
        # Show the sub-folder when recursing, so two files of the same name in
        # different folders are distinguishable in the summary.
        item.name = str(src.relative_to(config.input_path))
        result.items.append(item)
        if on_progress is not None:
            on_progress(item, len(result.items), len(sources))

    result.elapsed_s = time.perf_counter() - started
    return result


def _process_one(config: Config, src: pathlib.Path, skip_existing: bool) -> BatchItem:
    started = time.perf_counter()
    item = BatchItem(name=src.name, status=STATUS_OK)
    cfg = _per_file_config(config, src)

    try:
        if skip_existing and cfg.output_path is not None and cfg.output_path.exists():
            item.status = STATUS_SKIPPED
            item.detail = "output already exists"
            return item

        if config.mode == "inspect":
            r = inspect(src, process_outlines=config.process_outlines)
            cfg.report_path.with_suffix(".json").write_text(
                json.dumps(r.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            item.n_pages = r.n_pages
            item.n_names = r.n_distinct_explicit
            item.n_unresolved = r.counts.get("unresolved", 0)
            item.detail = r.verdict.split(":")[0]
            return item

        source_for_split = src

        if config.mode in ("convert", "convert+split"):
            report = convert(cfg)
            report.write(cfg.report_path)
            item.n_pages = report.n_pages_in
            item.n_names = report.n_names_generated
            item.n_unresolved = report.counts.get("unresolved", 0)
            if not report.ok:
                failed = [n for n, ok in report.self_checks() if not ok]
                item.status = STATUS_FAILED
                item.detail = "self checks failed: " + ", ".join(failed)
                return item
            source_for_split = cfg.output_path

        if config.mode in ("split", "convert+split"):
            sr = split(
                source_for_split,
                cfg.output_path,
                max_pages=config.split_max_pages,
                align=config.split_align,
                outlines=config.split_outlines,
                pattern=config.split_name_pattern,
                allow_explicit=config.split_allow_explicit,
            )
            path = cfg.report_path.with_name(cfg.report_path.name + "_split")
            path.with_suffix(".json").write_text(
                json.dumps(sr.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            item.n_pages = sr.n_pages_in
            item.n_parts = len(sr.parts)
            if not sr.split_needed:
                item.detail = "under page limit, not split"
            if not sr.ok:
                failed = [n for n, ok in sr.self_checks() if not ok]
                item.status = STATUS_FAILED
                item.detail = "split checks failed: " + ", ".join(failed)
                return item

        return item

    except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
        # Library errors tend to repeat the full path, which is already the
        # row label here and only makes the message harder to read.
        message = str(e).replace(str(src), src.name).replace(f"{src.name}: ", "")
        item.status = STATUS_FAILED
        item.detail = f"{type(e).__name__}: {message}"
        return item
    finally:
        item.elapsed_s = round(time.perf_counter() - started, 2)
