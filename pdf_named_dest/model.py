"""Data model shared by the collect, naming and rewrite stages.

Spec section 4.2 Step 1 classifies every destination-bearing site into one of
four kinds and requires a deduplication key built from the *full* destination
content, not just the target page.
"""

from __future__ import annotations

import decimal
import enum
from dataclasses import dataclass, field
from typing import Any


def canon(x: Any) -> str:
    """Canonical text form of one destination display parameter.

    Keeps ``null`` distinct from ``0`` -- ``/XYZ null null null`` is extremely
    common and is not the same destination as ``/XYZ 0 0 0``. Numbers are
    normalised so that the integer 612 and the real 612.0 hash alike, since PDF
    draws no semantic distinction between them; anything that cannot be
    interpreted falls back to repr(), which errs towards treating two values as
    different. Over-splitting only costs an extra generated name, whereas
    under-splitting would send links to the wrong place.
    """
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (int, float, decimal.Decimal)):
        try:
            return "num:" + str(decimal.Decimal(str(x)).normalize())
        except (ValueError, ArithmeticError):
            return "num:" + repr(x)
    text = str(x)
    if text.startswith("/"):  # a PDF name such as /XYZ or /FitH
        return text
    return repr(x)


class DestKind(str, enum.Enum):
    """Spec 4.2 Step 1 destination classification."""

    EXPLICIT = "explicit"      # array destination -> must be converted
    NAMED = "named"            # already a string/name -> keep untouched
    EXTERNAL = "external"      # /GoToR, /URI, ... -> keep untouched
    UNRESOLVED = "unresolved"  # missing or undecodable -> report, never skip


class Source(str, enum.Enum):
    ANNOT = "annot"
    OUTLINE = "outline"


@dataclass(frozen=True)
class DestKey:
    """Deduplication key for an explicit destination.

    Spec 4.2 Step 1 forbids deduplicating on the page index alone: one page can
    carry several destinations that differ only in their scroll coordinates, and
    collapsing them would silently send links to the wrong spot.

    ``params`` holds the canonical form of every array element after the page
    reference; see :func:`canon`.

    This key is for identity only. Rewriting always copies the original objects,
    because spec section 5 rule 6 forbids normalising display parameters.
    """

    page_index: int
    params: tuple[str, ...]

    @classmethod
    def build(cls, page_index: int, tail: list[Any]) -> DestKey:
        return cls(page_index, tuple(canon(x) for x in tail))

    @property
    def display_type(self) -> str:
        """The /XYZ, /Fit, ... element, for reporting only."""
        return self.params[0] if self.params else "(none)"


@dataclass
class DestSite:
    """One place in the document that carries a destination.

    A site is where a destination is *referenced*; several sites can share the
    same DestKey and will then share one generated name.
    """

    source: Source
    location: str                  # human readable, for the exception report
    carrier: Any                   # the annotation or outline node object
    slot: str                      # "Dest" or "A.D" -- where the value lives
    kind: DestKind
    page_index: int | None = None
    key: DestKey | None = None
    tail: list[Any] = field(default_factory=list)
    existing_name: str | None = None  # for NAMED sites
    action_type: str | None = None    # for EXTERNAL sites: /GoToR, /URI, ...
    note: str = ""                    # for UNRESOLVED sites: why it failed


@dataclass
class CollectResult:
    """Everything Step 1 found."""

    n_pages: int
    sites: list[DestSite] = field(default_factory=list)
    n_link_annots: int = 0
    n_outline_items: int = 0
    existing_names: set[str] = field(default_factory=set)
    has_name_tree: bool = False
    has_outlines: bool = False

    def by_kind(self, kind: DestKind) -> list[DestSite]:
        return [s for s in self.sites if s.kind is kind]

    def counts(self) -> dict[str, int]:
        return {k.value: len(self.by_kind(k)) for k in DestKind}

    def distinct_keys(self) -> list[DestKey]:
        """Unique explicit destinations, ordered deterministically.

        Sorting by content rather than by discovery order means the generated
        names depend only on the input, which is what spec 4.2 Step 2 requires
        ("same input must produce the same names").
        """
        keys = {s.key for s in self.by_kind(DestKind.EXPLICIT) if s.key is not None}
        return sorted(keys, key=lambda k: (k.page_index, k.params))