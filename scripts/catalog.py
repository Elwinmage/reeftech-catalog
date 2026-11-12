"""Catalog layout shared by validate.py and make_release.py.

A folder (or a file name, for tasks) is the entry id: descriptors carry no id,
so an entry can never disagree with its location.

    equipment/tasks/<task_id>.json
    equipment/categories/<category_id>/category.json [+ image.webp, thumb.webp]
    equipment/items/<item_id>/item.json              [+ image.webp, thumb.webp]
    supplements/items/<supplement_id>/supplement.json [+ image.webp, thumb.webp]
    parameters/items/<parameter_id>/parameter.json    [+ image.webp, thumb.webp]
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

# Bumped only on breaking changes: consumers skip releases they cannot read.
SCHEMA_VERSION = 1

SECTIONS = ("equipment", "supplements", "parameters")

# Languages of the ecosystem. English is mandatory, the others are reported
# as missing (warning) so translators know what to fill in.
KNOWN_LANGS = ("en", "fr", "de", "es", "it", "pl", "pt")

# Files allowed next to a descriptor, with their size limit in bytes.
ASSET_FILES = {
    "image.webp": 300_000,
    "image.png": 300_000,
    "thumb.webp": 40_000,
}
DESCRIPTOR_MAX = 32_000


@dataclass
class Entry:
    """One release unit: a set of files archived together."""

    name: str  # release entry name, e.g. "item.eheim-thermocontrol-200"
    kind: str  # tasks | category | item | supplement
    files: dict[str, Path] = field(default_factory=dict)  # archive name -> path
    data: dict = field(default_factory=dict)  # parsed descriptor(s)

    def content_hash(self) -> str:
        """Hash of the archived content, independent of zip encoding."""
        digest = hashlib.sha256()
        for arcname in sorted(self.files):
            digest.update(arcname.encode() + b"\0")
            digest.update(self.files[arcname].read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _folder_entries(base: Path) -> list[tuple[str, Path]]:
    if not base.is_dir():
        return []
    return [
        (folder.name, folder)
        for folder in sorted(base.iterdir())
        if folder.is_dir() and not folder.name.startswith(".")
    ]


def collect(root: Path, section: str) -> list[Entry]:
    """List the release entries of a section (descriptors are not parsed here)."""
    entries: list[Entry] = []
    if section == "equipment":
        tasks_dir = root / "equipment" / "tasks"
        if tasks_dir.is_dir():
            tasks = Entry("tasks", "tasks")
            for path in sorted(tasks_dir.glob("*.json")):
                tasks.files[path.name] = path
            entries.append(tasks)
        layout = (("categories", "category"), ("items", "item"))
    elif section == "supplements":
        layout = (("items", "supplement"),)
    elif section == "parameters":
        layout = (("items", "parameter"),)
    else:
        raise ValueError(f"unknown section {section!r}")

    for folder_name, kind in layout:
        for eid, folder in _folder_entries(root / section / folder_name):
            entry = Entry(f"{kind}.{eid}", kind)
            for path in sorted(folder.iterdir()):
                if path.is_file() and not path.name.startswith("."):
                    entry.files[path.name] = path
            entries.append(entry)
    return entries


def entry_id(entry: Entry) -> str:
    return entry.name.split(".", 1)[1] if "." in entry.name else entry.name
