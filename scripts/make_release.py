"""Build the files of a section release.

    python scripts/make_release.py equipment 2026.11.0 --out dist \
        [--previous prev/manifest.json] [--min-integration reef_maintenance=0.3.0 ...]

Writes into --out:
  manifest.json   read by the integrations (published on the versioned release
                  and on the rolling '<section>-latest' release)
  notes.md        release notes (added / updated / removed entries)
  upload/*.zip    archives of the entries that changed since --previous, and
                  schema.zip (the JSON Schemas of the section, for tools that
                  edit the catalog) when it changed

An entry whose content did not change keeps pointing to the archive of the
release that built it: nothing is rebuilt nor uploaded again, and consumers
only download what changed. Versioned releases must therefore never be deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from catalog import SCHEMA_VERSION, SECTIONS, Entry, collect, read_json

VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d+$")
# Integrations reading each section, for the min_integration table.
CONSUMERS = {
    "equipment": ("reef_maintenance", "maintenance_hub"),
    "supplements": ("redsea",),
    "parameters": ("reeftank", "redsea"),
    "water_tests": ("redsea", "reeftank"),
}
# JSON Schemas (schema/<kind>.schema.json) of the entries of each section. Data
# only: a release never carries code, which integrations would have to run.
SCHEMA_KINDS = {
    "equipment": ("task", "category", "item"),
    "supplements": ("supplement",),
    "parameters": ("parameter",),
    "water_tests": ("water_test",),
}
SCHEMA_ENTRY = "schema"
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def tag(section: str, version: str) -> str:
    return f"{section}-{version}"


def write_zip(entry: Entry, path: Path) -> None:
    """Flat archive with fixed metadata (same input, same bytes on this zlib)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname in sorted(entry.files):
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entry.files[arcname].read_bytes())


def _summary(root: Path, section: str, entry: Entry) -> dict:
    """Metadata letting a consumer pick entries before downloading them."""
    summary: dict = {"kind": entry.kind}
    if entry.kind == "item":
        item = read_json(entry.files["item.json"])
        category = read_json(
            root / section / "categories" / item["category"] / "category.json"
        )
        summary["category"] = item["category"]
        summary["scopes"] = item.get("scopes", category["scopes"])
    elif entry.kind == "category":
        summary["scopes"] = read_json(entry.files["category.json"])["scopes"]
    elif entry.kind == "supplement":
        data = read_json(entry.files["supplement.json"])
        summary["supplement_kind"] = data["kind"]
        summary["fullname"] = data["fullname"]
        if "uid" in data:
            summary["uid"] = data["uid"]
    elif entry.kind == "parameter":
        data = read_json(entry.files["parameter.json"])
        summary["category"] = data["category"]
        summary["unit"] = data["unit"]
    elif entry.kind == "water_test":
        data = read_json(entry.files["water_test.json"])
        summary["brand"] = data["brand"]
        summary["parameters"] = [test["parameter"] for test in data["tests"]]
    return summary


def _schema_entry(root: Path, section: str) -> Entry:
    """The schemas of a section (shared definitions included) as an entry."""
    entry = Entry(SCHEMA_ENTRY, SCHEMA_ENTRY)
    for kind in ("common", *SCHEMA_KINDS[section]):
        name = f"{kind}.schema.json"
        path = root / "schema" / name
        if not path.is_file():
            raise SystemExit(f"missing schema file {path}")
        entry.files[name] = path
    return entry


def _note_line(name: str) -> str:
    if name == SCHEMA_ENTRY:
        return "- validation schemas"
    kind, _, entry_id = name.partition(".")
    return f"- `{entry_id}` ({kind})"


def _parse_min(values: list[str], section: str) -> dict[str, str]:
    """Minimum integration versions of a section, from name=version values.

    Names of the consumers of another section are skipped, so that one list
    can be given when both sections are released together.
    """
    known = {name for names in CONSUMERS.values() for name in names}
    result = {}
    for value in values:
        name, _, version = value.partition("=")
        if name not in known or not version:
            raise SystemExit(
                f"--min-integration expects one of {sorted(known)} as name=version, got {value!r}"
            )
        if name in CONSUMERS[section]:
            result[name] = version
    return result


def build(
    root: Path,
    section: str,
    version: str,
    out: Path,
    previous: dict | None = None,
    min_integration: dict[str, str] | None = None,
) -> dict:
    if section not in SECTIONS:
        raise SystemExit(f"unknown section {section!r}")
    if not VERSION_RE.match(version):
        raise SystemExit(f"version must look like 2026.11.0, got {version!r}")
    if previous and previous.get("section") != section:
        raise SystemExit("previous manifest belongs to another section")

    upload = out / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    old_entries = (previous or {}).get("entries", {})
    entries: dict[str, dict] = {}
    added, updated = [], []

    for entry in collect(root, section):
        content = entry.content_hash()
        old = old_entries.get(entry.name)
        record = _summary(root, section, entry) | {"content_hash": content}
        if old and old["content_hash"] == content:
            record |= {k: old[k] for k in ("archive", "sha256", "size", "since")}
        else:
            name = f"{entry.name}.zip"
            path = upload / name
            write_zip(entry, path)
            data = path.read_bytes()
            record |= {
                "archive": f"{tag(section, version)}/{name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "since": version,
            }
            (updated if old else added).append(entry.name)
        entries[entry.name] = record

    # The schemas travel like an entry, but apart from them: integrations reading
    # the entries of the section ignore this record.
    schema = _schema_entry(root, section)
    schema_hash = schema.content_hash()
    old_schema = (previous or {}).get("schema")
    schema_record: dict = {"content_hash": schema_hash}
    if old_schema and old_schema["content_hash"] == schema_hash:
        schema_record |= {
            k: old_schema[k] for k in ("archive", "sha256", "size", "since")
        }
    else:
        name = f"{SCHEMA_ENTRY}.zip"
        write_zip(schema, upload / name)
        data = (upload / name).read_bytes()
        schema_record |= {
            "archive": f"{tag(section, version)}/{name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "since": version,
        }
        (updated if old_schema else added).append(SCHEMA_ENTRY)

    removed = sorted(set(old_entries) - set(entries))
    minimum = dict((previous or {}).get("min_integration", {}))
    minimum.update(min_integration or {})

    lines = [f"# {section} {version}", ""]
    for title, names in (("Added", added), ("Updated", updated), ("Removed", removed)):
        if names:
            lines += [f"## {title}", ""] + [_note_line(n) for n in names] + [""]
    if not (added or updated or removed):
        lines += ["No content change.", ""]
    notes = "\n".join(lines)
    (out / "notes.md").write_text(notes, encoding="utf-8")

    manifest = {
        "format": SCHEMA_VERSION,
        "section": section,
        "version": version,
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_integration": minimum,
        # Shown by the update entities of the integrations
        "notes": notes,
        "entries": entries,
        "schema": schema_record,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("section", choices=SECTIONS)
    parser.add_argument("version")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--previous", type=Path)
    parser.add_argument(
        "--min-integration", action="append", default=[], metavar="NAME=VERSION"
    )
    args = parser.parse_args(argv)

    previous = None
    if args.previous and args.previous.is_file():
        previous = read_json(args.previous)
        if previous.get("format", 0) > SCHEMA_VERSION:
            raise SystemExit("previous manifest has a newer format than this script")
    manifest = build(
        args.root,
        args.section,
        args.version,
        args.out,
        previous,
        _parse_min(args.min_integration, args.section),
    )
    changed = sum(
        1
        for e in (*manifest["entries"].values(), manifest["schema"])
        if e["since"] == args.version
    )
    print(
        f"{args.section} {args.version}: {len(manifest['entries'])} entries, {changed} to upload"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
