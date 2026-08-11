"""Spec section 7 -- the processing report.

Two forms are written for every run: a JSON file for machine comparison
(idempotency checks, acceptance criteria) and a text rendering for humans.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .model import CollectResult, DestKind, Source
from .rewrite import RewriteStats


@dataclass
class ConversionReport:
    config: dict[str, Any]
    input_path: str
    output_path: str
    input_sha256: str
    output_sha256: str
    input_sha256_after_run: str
    n_pages_in: int
    n_pages_out: int
    n_link_annots: int
    n_outline_items: int
    counts: dict[str, int]
    counts_by_source: dict[str, dict[str, int]]
    n_names_generated: int
    n_name_tree_entries: int
    n_existing_names_kept: int
    exceptions: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    timestamp: str = ""

    # ------------------------------------------------------------------
    @property
    def input_unchanged(self) -> bool:
        """Spec section 8: the input file must be untouched after the run."""
        return self.input_sha256 == self.input_sha256_after_run

    @property
    def page_count_preserved(self) -> bool:
        """Spec section 5 rule 2: page count and order must not change."""
        return self.n_pages_in == self.n_pages_out

    @property
    def name_tree_matches_report(self) -> bool:
        """Spec section 8: name tree size must equal generated + pre-existing."""
        return (
            self.n_name_tree_entries
            == self.n_names_generated + self.n_existing_names_kept
        )

    def self_checks(self) -> list[tuple[str, bool]]:
        return [
            ("input file unchanged (sha256)", self.input_unchanged),
            ("page count preserved", self.page_count_preserved),
            ("name tree size matches report", self.name_tree_matches_report),
            ("no warnings", not self.warnings),
        ]

    @property
    def ok(self) -> bool:
        return all(passed for _, passed in self.self_checks())

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["self_checks"] = {name: passed for name, passed in self.self_checks()}
        d["ok"] = self.ok
        return d

    def write(self, path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        json_path = path.with_suffix(".json")
        text_path = path.with_suffix(".txt")
        json_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        text_path.write_text(self.format(), encoding="utf-8")
        return json_path, text_path

    # ------------------------------------------------------------------
    def format(self, *, max_exceptions: int = 50) -> str:
        lines: list[str] = []
        add = lines.append

        add("=" * 72)
        add("CONVERSION REPORT")
        add("=" * 72)
        add(f"  timestamp            : {self.timestamp}")
        add("")
        add("  parameters")
        width = max(len(k) for k in self.config)
        for k, v in self.config.items():
            add(f"    {k:<{width}} : {v}")
        add("")
        add("  files")
        add(f"    input              : {self.input_path}")
        add(f"      pages            : {self.n_pages_in}")
        add(f"      sha256 before    : {self.input_sha256}")
        add(f"      sha256 after     : {self.input_sha256_after_run}")
        add(f"    output             : {self.output_path}")
        add(f"      pages            : {self.n_pages_out}")
        add(f"      sha256           : {self.output_sha256}")
        add("")
        add("  content")
        add(f"    link annotations   : {self.n_link_annots}")
        add(f"    outline items      : {self.n_outline_items}")
        add("")
        add("  destinations by kind")
        labels = {
            DestKind.EXPLICIT.value: "explicit (converted)",
            DestKind.NAMED.value: "named (kept)",
            DestKind.EXTERNAL.value: "external (kept)",
            DestKind.UNRESOLVED.value: "unresolved (exception)",
        }
        for kind in DestKind:
            n = self.counts.get(kind.value, 0)
            annot = self.counts_by_source.get("annot", {}).get(kind.value, 0)
            outline = self.counts_by_source.get("outline", {}).get(kind.value, 0)
            add(f"    {labels[kind.value]:<24} {n:>8}"
                f"   (annots {annot}, outline {outline})")
        add("")
        add("  names")
        add(f"    generated (deduped): {self.n_names_generated}")
        add(f"    pre-existing kept  : {self.n_existing_names_kept}")
        add(f"    name tree total    : {self.n_name_tree_entries}")

        if self.exceptions:
            add("")
            add(f"  exceptions ({len(self.exceptions)})")
            for e in self.exceptions[:max_exceptions]:
                add(f"    {e['location']:<48} {e['reason']}")
            if len(self.exceptions) > max_exceptions:
                add(f"    ... and {len(self.exceptions) - max_exceptions} more"
                    " (full list in the JSON report)")

        if self.warnings:
            add("")
            add(f"  warnings ({len(self.warnings)})")
            for w in self.warnings:
                add(f"    {w}")

        add("")
        add("  self checks")
        for name, passed in self.self_checks():
            add(f"    [{'PASS' if passed else 'FAIL'}] {name}")
        add("")
        add(f"  result               : {'OK' if self.ok else 'PROBLEMS FOUND'}")
        add(f"  elapsed              : {self.elapsed_s}s")
        add("=" * 72)
        return "\n".join(lines)


def build(
    *,
    config: Config,
    result: CollectResult,
    stats: RewriteStats,
    n_pages_out: int,
    input_sha256: str,
    output_sha256: str,
    input_sha256_after_run: str,
    elapsed_s: float,
) -> ConversionReport:
    by_source: dict[str, dict[str, int]] = {}
    for src in Source:
        sites = [s for s in result.sites if s.source is src]
        by_source[src.value] = {
            k.value: sum(1 for s in sites if s.kind is k) for k in DestKind
        }

    return ConversionReport(
        config=config.to_dict(),
        input_path=str(config.input_path),
        output_path=str(config.output_path),
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        input_sha256_after_run=input_sha256_after_run,
        n_pages_in=result.n_pages,
        n_pages_out=n_pages_out,
        n_link_annots=result.n_link_annots,
        n_outline_items=result.n_outline_items,
        counts=result.counts(),
        counts_by_source=by_source,
        n_names_generated=stats.n_names_created,
        n_name_tree_entries=stats.n_name_tree_entries,
        n_existing_names_kept=stats.n_existing_names_kept,
        exceptions=[
            {"location": s.location, "source": s.source.value, "reason": s.note}
            for s in result.by_kind(DestKind.UNRESOLVED)
        ],
        warnings=list(stats.warnings),
        elapsed_s=round(elapsed_s, 2),
        timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
    )