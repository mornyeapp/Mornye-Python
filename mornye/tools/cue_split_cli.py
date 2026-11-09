"""tools/cue_split_cli.py — the `--split-cue` command.

Reporting and argument handling only; parsing and splitting live in
core/cue_split.py. Same split as tools/library_upgrade_cli.py.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from ..core.cue_split import SplitResult, find_cue_files, split_cue_async


async def run_async(
    path: str,
    *,
    output_dir: str | None = None,
    fmt: str = "flac",
    encoding: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    recursive: bool = True,
    as_json: bool = False,
) -> int:
    sheets = find_cue_files(path, recursive=recursive)
    if not sheets:
        print(f"No .cue files found under {path}", file=sys.stderr)
        return 1

    results: list[SplitResult] = []
    for cue in sheets:
        # With several sheets, each album goes in its own folder under the
        # chosen output directory instead of all their "01 - …" colliding.
        target = output_dir
        if output_dir and len(sheets) > 1:
            target = str(Path(output_dir) / cue.parent.name)
        result = await split_cue_async(
            cue,
            target,
            fmt=fmt,
            encoding=encoding,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        results.append(result)
        if not as_json:
            _print_result(result, dry_run=dry_run)

    if as_json:
        print(
            json.dumps(
                [
                    {
                        "cue": str(r.cue),
                        "written": [str(p) for p in r.written],
                        "skipped": [str(p) for p in r.skipped],
                        "error": r.error,
                    }
                    for r in results
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
    return 2 if any(r.error for r in results) else 0


def _print_result(result: SplitResult, *, dry_run: bool) -> None:
    print(f"\n{result.cue}")
    if result.error:
        print(f"  ✗ {result.error}")
    verb = "would write" if dry_run else "wrote"
    for p in result.written:
        print(f"  {verb} {p.name}")
    for p in result.skipped:
        print(f"  skipped (exists) {p.name}")


def run(path: str, **kwargs) -> int:
    return asyncio.run(run_async(path, **kwargs))
