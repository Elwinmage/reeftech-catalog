"""Copy the catalog client and a built-in snapshot into the integrations.

Run from the folder holding the checkouts side by side (like reeftank's
regen.sh), or give it with --base:

    python reeftech-catalog/scripts/sync_client.py [--base ..] [--check]

For each integration found it writes, in custom_components/<domain>/:
  catalog_pack.py, catalog_runtime.py  copies of client/ with a header
  catalog_rules.py                     (editors of the catalog only) the rules
                                       an entry must follow, shared with validate.py
  catalog_builtin/                     the entries of its section and scopes,
                                       used before the first download and offline
                                       (not for editors, which read the views of
                                       the integrations); a domain reading several
                                       sections has one folder per section after the
                                       first: catalog_builtin_<section>/

--check changes nothing and fails when a copy or a snapshot is out of date.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from catalog import collect, entry_id, read_json

REPO = Path(__file__).resolve().parent.parent
CLIENT_FILES = ("catalog_pack.py", "catalog_runtime.py", "catalog_conversion.py")
HEADER = "# Generated from reeftech-catalog/client/{name} by scripts/sync_client.py: do not edit this copy.\n"

#: Modules only some domains need, besides CLIENT_FILES
EXTRA_FILES: dict[str, tuple[str, ...]] = {
    "catalog_editor": ("catalog_rules.py",),
}
#: Modules of CLIENT_FILES a domain has no use for (it reads no water test)
LEFT_OUT: dict[str, tuple[str, ...]] = {
    "house_view": ("catalog_conversion.py",),
}


@dataclass(frozen=True)
class Reads:
    """A section an integration reads, and what it keeps of it."""

    section: str
    #: scopes kept, None for all
    scopes: tuple[str, ...] | None = None
    #: folders and URLs carry the section (`CatalogSpec.by_section`): from the
    #: second section of a domain on
    by_section: bool = False

    @property
    def snapshot(self) -> str:
        """Folder of the built-in snapshot, next to the modules."""
        return "catalog_builtin" + (f"_{self.section}" if self.by_section else "")


#: checkout folder -> (domain, sections read); a domain reading nothing has no
#: built-in snapshot
TARGETS: dict[str, tuple[str, tuple[Reads, ...]]] = {
    "ha-reefbeat-component": (
        "redsea",
        (
            Reads("supplements"),
            Reads("water_tests", by_section=True),
            Reads("parameters", by_section=True),
        ),
    ),
    "ha-reef-maintenance-component": (
        "reef_maintenance",
        (Reads("equipment", ("aquarium",)),),
    ),
    "ha-maintenance-hub": ("maintenance_hub", (Reads("equipment"),)),
    "catalog-editor": ("catalog_editor", ()),
    # Reads the sections of another catalog (declared with register_section),
    # whose own tools write its snapshots: only the client comes from here
    "ha-house-view-component": ("house_view", ()),
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


def client_files(domain: str) -> tuple[str, ...]:
    """Modules copied into the integration of a domain."""
    left_out = LEFT_OUT.get(domain, ())
    return tuple(
        name
        for name in (*CLIENT_FILES, *EXTRA_FILES.get(domain, ()))
        if name not in left_out
    )


def write_client(dest: Path, domain: str) -> None:
    """Copy the client modules into an integration folder."""
    for name in client_files(domain):
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
    for folder, (domain, reads) in TARGETS.items():
        component = base / folder / "custom_components" / domain
        if not component.is_dir():
            print(f"skip {folder}: no custom_components/{domain}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp)
            write_client(fresh, domain)
            for read in reads:
                write_snapshot(root, read.section, read.scopes, fresh / read.snapshot)
            for name in client_files(domain):
                target = component / name
                if not target.is_file() or not filecmp.cmp(
                    fresh / name, target, shallow=False
                ):
                    stale.append(f"{folder}/{name}")
                    if not check:
                        shutil.copyfile(fresh / name, target)
            for read in reads:
                if _same_tree(fresh / read.snapshot, component / read.snapshot):
                    continue
                stale.append(f"{folder}/{read.snapshot}")
                if not check:
                    shutil.rmtree(component / read.snapshot, ignore_errors=True)
                    shutil.copytree(fresh / read.snapshot, component / read.snapshot)
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
