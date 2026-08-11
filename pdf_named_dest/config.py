"""Spec section 3 -- run parameters.

Nothing in section 3 may be hard-coded. Every value comes from a config file,
a command line argument, or both, and the effective values are printed at
startup so a run can be traced afterwards.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, fields
from typing import Any

import yaml

# Naming prefixes used internally by common merge and authoring tools. A
# generated name that looks like one of these risks being rewritten or
# reused by downstream software, so the prefix is checked against this list.
RESERVED_PREFIXES = ("G", "P", "Dest", "page", "Bookmark", "JR_", "LT_", "__")

# Splitting is done here rather than left to a downstream tool. That removes the
# one unverified link in the chain: a splitter that discards the name tree would
# undo the conversion, and this way that behaviour is ours to guarantee.
MODES = ("inspect", "convert", "split", "convert+split")

SPLIT_MODES = ("split", "convert+split")

# How a chunk boundary is chosen. "pages" cuts at a fixed page count, matching
# the external tool currently in use. "outline" pulls each cut back to the
# nearest bookmark target so a section is not divided across two files.
ALIGNMENTS = ("pages", "outline")

# See pdf_named_dest.split.OUTLINE_MODES for what each value does.
OUTLINE_MODES = ("first", "own", "all", "none")


class ConfigError(ValueError):
    """Raised for a missing or invalid parameter; the CLI turns this into exit 2."""


@dataclass
class Config:
    """Effective run parameters (spec section 3)."""

    input_path: pathlib.Path
    output_path: pathlib.Path | None   # not needed in inspect mode
    mode: str
    name_prefix: str
    name_pad_width: int
    process_outlines: bool
    report_path: pathlib.Path

    # Split parameters. Only consulted in the split modes.
    split_max_pages: int | None = None
    split_align: str = "pages"
    split_outlines: str = "first"
    split_name_pattern: str = "{stem} split {n}{suffix}"
    split_allow_explicit: bool = False

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, config_file: pathlib.Path | None, overrides: dict[str, Any]) -> Config:
        """Merge a YAML config file with command line overrides.

        Overrides win. A value of None in overrides means "not supplied on the
        command line" and never masks the file.
        """
        data: dict[str, Any] = {}
        if config_file is not None:
            if not config_file.exists():
                raise ConfigError(f"config file not found: {config_file}")
            loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ConfigError(f"config file must contain a mapping: {config_file}")
            data.update(loaded)

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(
                f"unknown key(s) in config file: {', '.join(sorted(unknown))}"
            )

        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls._build(data)

    @classmethod
    def _build(cls, data: dict[str, Any]) -> Config:
        def required(key: str) -> Any:
            if key not in data or data[key] in (None, ""):
                raise ConfigError(
                    f"missing required parameter '{key}' "
                    "(spec section 3 forbids hard-coded defaults; "
                    "supply it in the config file or on the command line)"
                )
            return data[key]

        mode = str(required("mode")).lower()
        if mode not in MODES:
            raise ConfigError(f"mode must be one of {MODES}, got {mode!r}")

        input_path = pathlib.Path(str(required("input_path"))).expanduser()

        output_raw = data.get("output_path")
        output_path = (
            pathlib.Path(str(output_raw)).expanduser() if output_raw else None
        )
        if mode != "inspect" and output_path is None:
            raise ConfigError(f"{mode} mode requires 'output_path'")

        split_max_pages = data.get("split_max_pages")
        if mode in SPLIT_MODES:
            if split_max_pages in (None, ""):
                raise ConfigError(f"{mode} mode requires 'split_max_pages'")
            try:
                split_max_pages = int(split_max_pages)
            except (TypeError, ValueError):
                raise ConfigError("split_max_pages must be an integer") from None
            if split_max_pages < 1:
                raise ConfigError("split_max_pages must be at least 1")
        elif split_max_pages is not None:
            split_max_pages = int(split_max_pages)

        align = str(data.get("split_align", "pages")).lower()
        if align not in ALIGNMENTS:
            raise ConfigError(f"split_align must be one of {ALIGNMENTS}, got {align!r}")

        outline_mode = str(data.get("split_outlines", "first")).lower()
        if outline_mode not in OUTLINE_MODES:
            raise ConfigError(
                f"split_outlines must be one of {OUTLINE_MODES}, "
                f"got {outline_mode!r}"
            )

        pattern = str(data.get("split_name_pattern", "{stem} split {n}{suffix}"))
        for token in ("{n}",):
            if token not in pattern:
                raise ConfigError(
                    f"split_name_pattern must contain {token} so the parts get "
                    "distinct file names"
                )

        try:
            pad = int(required("name_pad_width"))
        except (TypeError, ValueError):
            raise ConfigError("name_pad_width must be an integer") from None

        # process_outlines is the one parameter spec section 3 gives a default
        # for ("process by default"); it stays overridable so the exception
        # case is reachable without editing code.
        outlines = data.get("process_outlines", True)
        if isinstance(outlines, str):
            outlines = outlines.strip().lower() in ("1", "true", "yes", "on")

        cfg = cls(
            input_path=input_path,
            output_path=output_path,
            mode=mode,
            name_prefix=str(required("name_prefix")),
            name_pad_width=pad,
            process_outlines=bool(outlines),
            report_path=pathlib.Path(str(required("report_path"))).expanduser(),
            split_max_pages=split_max_pages,
            split_align=align,
            split_outlines=outline_mode,
            split_name_pattern=pattern,
            split_allow_explicit=bool(data.get("split_allow_explicit", False)),
        )
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------
    @property
    def is_batch(self) -> bool:
        """A folder as input means process every PDF inside it."""
        return self.input_path.is_dir()

    def validate(self) -> None:
        if not self.input_path.exists():
            raise ConfigError(f"input not found: {self.input_path}")

        if self.output_path is not None:
            # Spec section 5 rule 5: the input must survive the run untouched.
            # Compare resolved paths so that a relative path, a different case
            # or a symlink cannot slip past. In batch mode these are folders,
            # and writing outputs into the input folder would feed them back in
            # on the next run.
            if self.input_path.resolve() == self.output_path.resolve():
                raise ConfigError(
                    "output_path must differ from input_path "
                    + ("(outputs would be picked up as inputs next run)"
                       if self.is_batch
                       else "(the input file must not be modified)")
                )

        if self.name_pad_width < 1:
            raise ConfigError("name_pad_width must be at least 1")

        if not self.name_prefix:
            raise ConfigError("name_prefix must not be empty")
        if self.name_prefix.startswith("/"):
            raise ConfigError("name_prefix must not start with '/'")
        for reserved in RESERVED_PREFIXES:
            if self.name_prefix == reserved:
                raise ConfigError(
                    f"name_prefix {self.name_prefix!r} collides with a prefix used "
                    "internally by common merge tools; pick something distinctive"
                )

    def check_pad_width(self, n_pages: int) -> None:
        """Spec section 3: the pad width must cover the document's page count.

        Called once the page count is known, because the config alone cannot
        know it.
        """
        needed = len(str(max(n_pages - 1, 0)))
        if self.name_pad_width < needed:
            raise ConfigError(
                f"name_pad_width={self.name_pad_width} is too small for a "
                f"{n_pages}-page document (needs at least {needed}); "
                "raising it changes every generated name, so pick a width with "
                "room to spare and keep it stable across runs"
            )

    # ------------------------------------------------------------------
    def format_effective(self) -> str:
        """Spec section 3: print every effective parameter value at startup."""
        rows = [
            ("mode", self.mode),
            ("input_path", self.input_path),
            ("output_path", self.output_path if self.output_path else "(none)"),
            ("name_prefix", self.name_prefix),
            ("name_pad_width", self.name_pad_width),
            ("process_outlines", self.process_outlines),
            ("report_path", self.report_path),
        ]
        if self.mode in SPLIT_MODES:
            rows += [
                ("split_max_pages", self.split_max_pages),
                ("split_align", self.split_align),
                ("split_outlines", self.split_outlines),
                ("split_name_pattern", self.split_name_pattern),
                ("split_allow_explicit", self.split_allow_explicit),
            ]
        width = max(len(k) for k, _ in rows)
        lines = ["EFFECTIVE PARAMETERS (spec section 3)"]
        lines += [f"  {k:<{width}} : {v}" for k, v in rows]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "mode": self.mode,
            "input_path": str(self.input_path),
            "output_path": str(self.output_path) if self.output_path else None,
            "name_prefix": self.name_prefix,
            "name_pad_width": self.name_pad_width,
            "process_outlines": self.process_outlines,
            "report_path": str(self.report_path),
        }
        if self.mode in SPLIT_MODES:
            d.update(
                split_max_pages=self.split_max_pages,
                split_align=self.split_align,
                split_outlines=self.split_outlines,
                split_name_pattern=self.split_name_pattern,
                split_allow_explicit=self.split_allow_explicit,
            )
        return d