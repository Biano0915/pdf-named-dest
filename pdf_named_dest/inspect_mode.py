"""Spec section 4.1 -- inspect mode. Writes no files.

Used before conversion to confirm a file is a candidate, and after conversion
as a spot check (an output file must report zero explicit destinations).
"""

from __future__ import annotations

import collections
import pathlib
import time
from dataclasses import asdict, dataclass, field

import pikepdf

from .collect import collect
from .model import DestKind, Source


@dataclass
class InspectResult:
    path: str
    n_pages: int
    is_encrypted: bool
    permissions: dict[str, bool]
    has_name_tree: bool
    n_existing_names: int
    has_outlines: bool
    n_link_annots: int
    n_outline_items: int
    counts: dict[str, int]
    counts_by_source: dict[str, dict[str, int]]
    n_distinct_explicit: int
    display_types: dict[str, int]
    exceptions: list[dict[str, str]] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def verdict(self) -> str:
        """Spec 4.1 reading of the numbers."""
        if self.n_link_annots == 0 and self.n_outline_items == 0:
            return "NO_LINKS: nothing to do, this file has no links or bookmarks"
        if self.counts.get(DestKind.EXPLICIT.value, 0) > 0:
            return "NEEDS_CONVERSION: explicit destinations found"
        if self.counts.get(DestKind.NAMED.value, 0) > 0:
            return "ALREADY_NAMED: already in the target state, nothing to do"
        return "NO_DESTINATIONS: links exist but none carry a usable destination"

    @property
    def writable(self) -> bool:
        return not self.is_encrypted or self.permissions.get("modify_annotation", False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["writable"] = self.writable
        return d


def inspect(path: pathlib.Path, *, process_outlines: bool = True) -> InspectResult:
    started = time.perf_counter()
    with pikepdf.Pdf.open(path) as pdf:
        res = collect(pdf, process_outlines=process_outlines)

        perms = {}
        try:
            allow = pdf.allow
            perms = {
                "modify_annotation": bool(allow.modify_annotation),
                "modify_other": bool(allow.modify_other),
                "extract": bool(allow.extract),
                "print_highres": bool(allow.print_highres),
            }
        except Exception:  # noqa: BLE001 - permissions are advisory here
            perms = {}

        by_source: dict[str, dict[str, int]] = {}
        for src in Source:
            sites = [s for s in res.sites if s.source is src]
            by_source[src.value] = {
                k.value: sum(1 for s in sites if s.kind is k) for k in DestKind
            }

        display = collections.Counter(
            key.display_type for key in res.distinct_keys()
        )

        exceptions = [
            {"location": s.location, "source": s.source.value, "reason": s.note}
            for s in res.by_kind(DestKind.UNRESOLVED)
        ]

        return InspectResult(
            path=str(path),
            n_pages=res.n_pages,
            is_encrypted=pdf.is_encrypted,
            permissions=perms,
            has_name_tree=res.has_name_tree,
            n_existing_names=len(res.existing_names),
            has_outlines=res.has_outlines,
            n_link_annots=res.n_link_annots,
            n_outline_items=res.n_outline_items,
            counts=res.counts(),
            counts_by_source=by_source,
            n_distinct_explicit=len(res.distinct_keys()),
            display_types=dict(display),
            exceptions=exceptions,
            elapsed_s=round(time.perf_counter() - started, 2),
        )


def format_report(r: InspectResult, *, max_exceptions: int = 20) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 72)
    add("INSPECT MODE (no files written)")
    add("=" * 72)
    add(f"  file                 : {r.path}")
    add(f"  pages                : {r.n_pages}")
    add(f"  encrypted            : {r.is_encrypted}")
    if r.permissions:
        allowed = [k for k, v in r.permissions.items() if v]
        add(f"  permissions allowed  : {', '.join(allowed) if allowed else '(none)'}")
    add(f"  writable             : {r.writable}")
    add("")
    add(f"  name tree present    : {r.has_name_tree}"
        f"  ({r.n_existing_names} existing names)")
    add(f"  outline tree present : {r.has_outlines}")
    add(f"  link annotations     : {r.n_link_annots}")
    add(f"  outline items        : {r.n_outline_items}")
    add("")
    add("  destinations by kind")
    for kind in DestKind:
        n = r.counts.get(kind.value, 0)
        annot = r.counts_by_source.get("annot", {}).get(kind.value, 0)
        outline = r.counts_by_source.get("outline", {}).get(kind.value, 0)
        add(f"    {kind.value:<12} {n:>8}   (annots {annot}, outline {outline})")
    add("")
    add(f"  distinct explicit    : {r.n_distinct_explicit}"
        f"   <- names that would be generated")
    if r.display_types:
        add("  display types        : "
            + ", ".join(f"{k} x{v}" for k, v in sorted(r.display_types.items())))

    if r.exceptions:
        add("")
        add(f"  exceptions ({len(r.exceptions)})")
        for e in r.exceptions[:max_exceptions]:
            add(f"    {e['location']:<44} {e['reason']}")
        if len(r.exceptions) > max_exceptions:
            add(f"    ... and {len(r.exceptions) - max_exceptions} more")

    add("")
    add(f"  verdict              : {r.verdict}")
    add(f"  elapsed              : {r.elapsed_s}s")
    add("=" * 72)
    return "\n".join(lines)