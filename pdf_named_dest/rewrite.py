"""Spec 4.2 Step 3 -- point every explicit destination at its name and write out.

Three things happen here:
  - each explicit destination site is rewritten to reference a name
  - /Root/Names/Dests is created or extended with the name -> array mapping
  - /A and /Dest are never left coexisting on the same object
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import pikepdf

from .model import CollectResult, DestKey, DestKind, DestSite


@dataclass
class RewriteStats:
    n_names_created: int = 0
    n_sites_rewritten: int = 0
    n_dest_removed_for_action: int = 0
    n_name_tree_entries: int = 0
    n_existing_names_kept: int = 0
    warnings: list[str] = field(default_factory=list)


def _open_name_tree(pdf: pikepdf.Pdf) -> pikepdf.NameTree:
    """Get the destination name tree, creating it if the file has none.

    An existing tree is extended rather than replaced, so names already in the
    file survive untouched (spec section 5 rule 4).
    """
    root = pdf.Root
    if "/Names" in root and "/Dests" in root.Names:
        return pikepdf.NameTree(root.Names.Dests)

    tree = pikepdf.NameTree.new(pdf)
    if "/Names" in root:
        root.Names.Dests = tree.obj
    else:
        root.Names = pdf.make_indirect(pikepdf.Dictionary(Dests=tree.obj))
    return tree


def _dest_array(pdf: pikepdf.Pdf, site: DestSite) -> pikepdf.Array:
    """Rebuild the destination array for the name tree.

    The page reference is taken from the document's page list, and every
    display parameter after it is carried across exactly as found. Spec
    section 5 rule 6 forbids normalising them, so nothing here inspects,
    rounds or rewrites those values.
    """
    page_obj = pdf.pages[site.page_index].obj
    return pikepdf.Array([page_obj, *site.tail])


def _apply_name(site: DestSite, name: str, stats: RewriteStats) -> None:
    """Replace one site's destination with a reference to ``name``.

    The name is stored as a PDF string, not a name object, as spec 4.2 Step 3
    requires.

    The original carrier is kept: a site that used /A keeps using /A, a site
    that used /Dest keeps using /Dest. That keeps the structural change as
    small as possible. Whichever form is kept, the other is removed, because a
    reader ignores /Dest when /A is present and a file carrying both behaves
    differently depending on the viewer.
    """
    value = pikepdf.String(name)

    if site.slot == "A.D":
        site.carrier.A.D = value
        if "/Dest" in site.carrier:
            del site.carrier["/Dest"]
            stats.n_dest_removed_for_action += 1
    elif site.slot == "Dest":
        site.carrier.Dest = value
        if "/A" in site.carrier:
            # Only reachable if a carrier gained an /A between collection and
            # rewriting; recorded rather than silently tolerated.
            del site.carrier["/A"]
            stats.warnings.append(
                "removed an /A that appeared alongside /Dest during rewriting"
            )
    else:  # pragma: no cover - only EXPLICIT sites reach this function
        raise ValueError(f"cannot rewrite site with slot {site.slot!r}")

    stats.n_sites_rewritten += 1


def rewrite(
    pdf: pikepdf.Pdf,
    result: CollectResult,
    names: dict[DestKey, str],
) -> RewriteStats:
    """Apply Step 3 to an open Pdf. Does not save."""
    stats = RewriteStats(n_existing_names_kept=len(result.existing_names))
    explicit = result.by_kind(DestKind.EXPLICIT)

    # One site per key is enough to build the array; they are identical by
    # construction, which is what the dedup key means.
    representative: dict[DestKey, DestSite] = {}
    for site in explicit:
        if site.key is not None:
            representative.setdefault(site.key, site)

    tree = _open_name_tree(pdf)

    for key, name in names.items():
        site = representative.get(key)
        if site is None:  # pragma: no cover - names come from the same keys
            stats.warnings.append(f"no site found for generated name {name}")
            continue
        tree[name] = _dest_array(pdf, site)
        stats.n_names_created += 1

    for site in explicit:
        if site.key is None:  # pragma: no cover - EXPLICIT always carries a key
            continue
        _apply_name(site, names[site.key], stats)

    stats.n_name_tree_entries = sum(1 for _ in tree.keys())
    return stats


def save(pdf: pikepdf.Pdf, output_path) -> None:
    """Write the converted document.

    ``allow_overwriting_input`` is deliberately left off: the input file must
    come out of the run byte-identical (spec section 5 rule 5), and the config
    layer already refuses an output path equal to the input.
    """
    path = pathlib.Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(str(path))