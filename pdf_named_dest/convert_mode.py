"""Spec 4.2 -- convert mode: collect, name, rewrite, save, report."""

from __future__ import annotations

import hashlib
import pathlib
import time

import pikepdf

from .collect import collect
from .config import Config
from .naming import assign_names
from .report import ConversionReport, build
from .rewrite import rewrite, save


def sha256(path: pathlib.Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def convert(config: Config) -> ConversionReport:
    started = time.perf_counter()
    input_sha_before = sha256(config.input_path)

    with pikepdf.Pdf.open(config.input_path) as pdf:
        result = collect(pdf, process_outlines=config.process_outlines)
        config.check_pad_width(result.n_pages)

        names = assign_names(
            result.distinct_keys(),
            prefix=config.name_prefix,
            pad_width=config.name_pad_width,
            existing_names=result.existing_names,
        )
        stats = rewrite(pdf, result, names)
        save(pdf, config.output_path)

    with pikepdf.Pdf.open(config.output_path) as out:
        n_pages_out = len(out.pages)

    return build(
        config=config,
        result=result,
        stats=stats,
        n_pages_out=n_pages_out,
        input_sha256=input_sha_before,
        output_sha256=sha256(config.output_path),
        input_sha256_after_run=sha256(config.input_path),
        elapsed_s=time.perf_counter() - started,
    )