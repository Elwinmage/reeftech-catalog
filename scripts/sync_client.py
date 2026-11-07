"""Copy the catalog client and a built-in snapshot into the integrations.

Run from the folder holding the checkouts side by side (like reeftank's
regen.sh), or give it with --base:

    python reeftech-catalog/scripts/sync_client.py [--base ..] [--check]

For each integration found it writes, in custom_components/<domain>/:
  catalog_pack.py, catalog_runtime.py  copies of client/ with a header
  catalog_builtin/                     the entries of its section and scopes,
                                       used before the first download and offline

--check changes nothing and fails when a copy or a snapshot is out of date.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

from catalog import collect, entry_id, read_json

REPO = Path(__file__).resolve().parent.parent
CLIENT_FILES = ("catalog_pack.py", "catalog_runtime.py")
HEADER = "# Generated from reeftech-catalog/client/{name} by scripts/sync_client.py: do not edit this copy.\n"

#: checkout folder -> (domain, section, scopes kept or None for all)
TARGETS: dict[str, tuple[str, str, tuple[str, ...] | None]] = {
    "ha-reefbeat-component": ("redsea", "supplements", None),
    "ha-reef-maintenance-component": ("reef_maintenance", "equipment", ("aquarium",)),
    "ha-maintenance-hub": ("maintenance_hub", "equipment", None),
}


def _kept(root: Path, section: str, scopes: tuple[str, ...] | None) -> list:
    """Entries of a section a consumer keeps (an item takes its category's scopes)."""
    entries = collect(root, section)
    if scopes is None:
        return entries
    wanted = set(scopes)
    categories = {
        entry_id(e): set(read_json(e.files["category.json"])["scopes"])
        for e in entries
        if e.kind == "category" and "category.json" in e.files
    }
    kept = []
    for entry in entries:
        if entry.kind == "category":
            entry_scopes = categories.get(entry_id(entry), set())
        elif entry.kind == "item":
            item = read_json(entry.files["item.json"])
            entry_scopes = set(
                item.get("scopes", categories.get(item["category"], set()))
            )
        else:
            kept.append(entry)
            continue
        if wanted & entry_scopes:
            kept.append(entry)
    return kept


def write_snapshot(
    root: Path, section: str, scopes: tuple[str, ...] | None, dest: Path
) -> None:
    """Write the built-in snapshot of a consumer into `dest` (replaced)."""
    shutil.rmtree(dest, ignore_errors=True)
    for entry in _kept(root, section, scopes):
        if entry.kind == "tasks":
            folder = dest / "tasks"
        else:
            folder = (
                dest
                / ("categories" if entry.kind == "category" else "items")
                / entry_id(entry)
            )
        folder.mkdir(parents=True, exist_ok=True)
        for name, path in entry.files.items():
            shutil.copyfile(path, folder / name)


def write_client(dest: Path) -> None:
    """Copy the client modules into an integration folder."""
    for name in CLIENT_FILES:
        text = (REPO / "client" / name).read_text(encoding="utf-8")
        (dest / name).write_text(HEADER.format(name=name) + text, encoding="utf-8")


def _same_tree(left: Path, right: Path) -> bool:
    if not left.is_dir() or not right.is_dir():
        return left.is_dir() == right.is_dir()
    compare = filecmp.dircmp(left, right)
    if compare.left_only or compare.right_only or compare.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(
        left, right, compare.common_files, shallow=False
    )
    if mismatch or errors:
        return False
    return all(_same_tree(left / d, right / d) for d in compare.common_dirs)


def sync(base: Path, check: bool = False, root: Path = REPO) -> list[str]:
    """Sync every integration found under `base`; return what is (or was) out of date."""
    stale: list[str] = []
    for folder, (domain, section, scopes) in TARGETS.items():
        component = base / folder / "custom_components" / domain
        if not component.is_dir():
            print(f"skip {folder}: no custom_components/{domain}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp)
            write_client(fresh)
            write_snapshot(root, section, scopes, fresh / "catalog_builtin")
            for name in CLIENT_FILES:
                target = component / name
                if not target.is_file() or not filecmp.cmp(
                    fresh / name, target, shallow=False
                ):
                    stale.append(f"{folder}/{name}")
                    if not check:
                        shutil.copyfile(fresh / name, target)
            if not _same_tree(fresh / "catalog_builtin", component / "catalog_builtin"):
                stale.append(f"{folder}/catalog_builtin")
                if not check:
                    shutil.rmtree(component / "catalog_builtin", ignore_errors=True)
                    shutil.copytree(
                        fresh / "catalog_builtin", component / "catalog_builtin"
                    )
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--base", type=Path, default=Path.cwd(), help="folder holding the checkouts"
    )
    parser.add_argument(
        "--check", action="store_true", help="only report what is out of date"
    )
    args = parser.parse_args(argv)
    stale = sync(args.base, args.check)
    for line in stale:
        print(("out of date: " if args.check else "updated: ") + line)
    if not stale:
        print("everything up to date")
    return 1 if args.check and stale else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
