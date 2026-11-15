"""Check the catalog before it is merged or released.

Errors make the command fail; warnings (missing translations, missing images,
unverified dosing data) are printed only, unless --strict is given.

    python scripts/validate.py [--root .] [--strict]

The rules themselves live in client/catalog_rules.py, shared with the tools
that edit the catalog; this script reads the repository and applies them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

from catalog_rules import (
    DESCRIPTORS,
    Context,
    Report,
    check_bundles,
    check_files,
    load_validators,
    validate_entry,
    where_of,
)

from catalog import SECTIONS, collect, entry_id, read_json


def _read(report: Report, where: str, path: Path) -> dict | None:
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError) as err:
        report.error(where, f"unreadable JSON ({err})")
        return None


def _sizes(entry) -> dict[str, int]:
    return {name: path.stat().st_size for name, path in entry.files.items()}


def _check_kind(
    root: Path,
    report: Report,
    validators,
    ctx: Context,
    section: str,
    kind: str,
    group: str | None = None,
) -> None:
    """Entries of a kind kept in a folder each; valid ones join `ctx.<group>`."""
    descriptor = DESCRIPTORS[kind]
    for entry in (e for e in collect(root, section) if e.kind == kind):
        eid = entry_id(entry)
        where = where_of(kind, eid)
        check_files(report, where, _sizes(entry), descriptor)
        if descriptor not in entry.files:
            continue
        data = _read(report, where, entry.files[descriptor])
        if data is None:
            continue
        accepted = validate_entry(
            report, kind, eid, data, _sizes(entry), ctx, validators
        )
        if accepted and group is not None:
            getattr(ctx, group)[eid] = data


def _check_equipment(root: Path, report: Report, validators) -> None:
    # Tasks, then categories, then the items that refer to both
    ctx = Context()
    for entry in collect(root, "equipment"):
        if entry.kind != "tasks":
            continue
        for name, path in entry.files.items():
            task_id = name.removesuffix(".json")
            data = _read(report, where_of("task", task_id), path)
            if data is not None and validate_entry(
                report, "task", task_id, data, {}, ctx, validators
            ):
                ctx.tasks[task_id] = data
    _check_kind(root, report, validators, ctx, "equipment", "category", "categories")
    _check_kind(root, report, validators, ctx, "equipment", "item")


def _check_supplements(root: Path, report: Report, validators) -> None:
    ctx = Context()
    _check_kind(
        root, report, validators, ctx, "supplements", "supplement", "supplements"
    )
    check_bundles(report, ctx.supplements)


def _check_parameters(root: Path, report: Report, validators) -> None:
    ctx = Context()
    _check_kind(root, report, validators, ctx, "parameters", "parameter", "parameters")


def validate(root: Path) -> Report:
    report = Report()
    validators = load_validators(root / "schema")
    for section in SECTIONS:
        if not (root / section).is_dir():
            report.error(section, "section folder missing")
    _check_equipment(root, report, validators)
    _check_supplements(root, report, validators)
    _check_parameters(root, report, validators)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as errors"
    )
    args = parser.parse_args(argv)

    report = validate(args.root)
    for line in report.warnings:
        print(f"warning: {line}")
    for line in report.errors:
        print(f"error: {line}")
    failed = report.errors or (args.strict and report.warnings)
    print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
