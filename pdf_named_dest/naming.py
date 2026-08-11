"""Spec 4.2 Step 2 -- assign one unique name per distinct destination.

The requirements are: derive the name from the page index, carry the
configured prefix, separate multiple destinations on one page by a sequence
number, stay globally unique, avoid every name already in the file, and be
deterministic so that re-running on the same input yields the same names.
"""

from __future__ import annotations

from .model import DestKey

SEQ_WIDTH = 3


def format_name(prefix: str, page_index: int, seq: int, pad_width: int) -> str:
    """PXD_000042_001 -- prefix, zero-padded page index, sequence number."""
    return f"{prefix}{page_index:0{pad_width}d}_{seq:0{SEQ_WIDTH}d}"


def assign_names(
    keys: list[DestKey],
    *,
    prefix: str,
    pad_width: int,
    existing_names: set[str],
) -> dict[DestKey, str]:
    """Map each distinct destination to its generated name.

    ``keys`` is expected in the deterministic order produced by
    ``CollectResult.distinct_keys()`` -- sorted by content, not by the order
    the destinations happened to be discovered in. That is what makes the
    output independent of traversal order, and therefore reproducible.

    Sequence numbers restart at 1 for each page and skip any name already
    taken, whether by the file's own existing destinations or by an earlier
    assignment in this run.
    """
    taken = set(existing_names)
    names: dict[DestKey, str] = {}
    next_seq: dict[int, int] = {}

    for key in keys:
        page = key.page_index
        seq = next_seq.get(page, 1)
        name = format_name(prefix, page, seq, pad_width)
        while name in taken:
            seq += 1
            name = format_name(prefix, page, seq, pad_width)
        taken.add(name)
        names[key] = name
        next_seq[page] = seq + 1

    return names