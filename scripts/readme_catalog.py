"""Write the lists of the catalog into README.md.

The equipment categories, equipment models, maintenance tasks and supplements
of the catalog are listed, with their visuals, between two markers of the
README; the rest of the file is left alone:

    <!-- catalog:begin -->
    ...generated...
    <!-- catalog:end -->

    python scripts/readme_catalog.py [--root .] [--check]

--check writes nothing and fails when the README is not up to date (CI and
pre-commit), so a new entry never lands without its line in the README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from catalog import read_json

BEGIN = "<!-- catalog:begin -->"
END = "<!-- catalog:end -->"

# Size of the visuals in the tables, in pixels
THUMB_WIDTH = 48

KINDS = {
    "kh": "KH",
    "ca": "Ca",
    "mg": "Mg",
    "trace": "trace elements",
    "nutrient": "nutrients",
    "food": "food",
    "bundle": "bundle",
    "brine": "brine",
    "other": "other",
}


# Parameters as written on the test kits
PARAMETERS = {
    "kh": "KH",
    "ca": "Ca",
    "mg": "Mg",
    "k": "K",
    "sr": "Sr",
    "no3": "NO3",
    "po4": "PO4",
    "i": "I",
    "fe": "Fe",
}


def _cell(text: object) -> str:
    """Text safe in a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _name(data: dict, fallback: str) -> str:
    """English name of a translated descriptor."""
    name = data.get("name")
    if isinstance(name, dict):
        return str(name.get("en") or fallback)
    return str(name or fallback)


def _visual(root: Path, folder: Path) -> str:
    """The thumbnail (else the image) of an entry folder, as an <img>."""
    for file in ("thumb.webp", "image.webp", "image.png"):
        path = folder / file
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            return f'<img src="{rel}" width="{THUMB_WIDTH}" alt="">'
    return ""


def _schedule(schedule: dict | None) -> str:
    """A schedule in a few words: "1 months (1-3)", "yearly, 03/01"."""
    if not isinstance(schedule, dict):
        return ""
    if schedule.get("type") == "interval":
        every = schedule.get("every", {})
        unit = schedule.get("unit", "")
        text = f"{every.get('default')} {unit}"
        if "min" in every and "max" in every and every["min"] != every["max"]:
            text += f" ({every['min']}-{every['max']})"
        return text
    if schedule.get("type") == "seasonal":
        # Day/month, the order of every language of the ecosystem but en
        text = f"yearly, {schedule.get('day', 1):02d}/{schedule.get('month', 1):02d}"
        if "window_days" in schedule:
            text += f" (-{schedule['window_days']} d)"
        return text
    return str(schedule.get("type", ""))


def _folders(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [
        folder
        for folder in sorted(base.iterdir())
        if folder.is_dir() and not folder.name.startswith(".")
    ]


def _tasks(root: Path) -> dict[str, dict]:
    tasks_dir = root / "equipment" / "tasks"
    if not tasks_dir.is_dir():
        return {}
    return {path.stem: read_json(path) for path in sorted(tasks_dir.glob("*.json"))}


def _task_names(refs: list, tasks: dict[str, dict]) -> str:
    names = []
    for ref in refs or []:
        task_id = ref.get("task", "") if isinstance(ref, dict) else str(ref)
        names.append(_name(tasks.get(task_id, {}), task_id))
    return ", ".join(names)


def tasks_table(root: Path, tasks: dict[str, dict]) -> list[str]:
    lines = [
        f"### Maintenance tasks ({len(tasks)})",
        "",
        "| Id | Task | Schedule | Icon |",
        "|---|---|---|---|",
    ]
    for task_id, data in tasks.items():
        lines.append(
            f"| `{task_id}` | {_cell(_name(data, task_id))} "
            f"| {_cell(_schedule(data.get('schedule')))} "
            f"| {_cell(data.get('icon', ''))} |"
        )
    return lines


def categories_table(root: Path, tasks: dict[str, dict]) -> list[str]:
    folders = _folders(root / "equipment" / "categories")
    lines = [
        f"### Equipment categories ({len(folders)})",
        "",
        "| | Id | Category | Scopes | Default tasks |",
        "|---|---|---|---|---|",
    ]
    for folder in folders:
        data = read_json(folder / "category.json")
        lines.append(
            f"| {_visual(root, folder)} | `{folder.name}` "
            f"| {_cell(_name(data, folder.name))} "
            f"| {_cell(', '.join(data.get('scopes', [])))} "
            f"| {_cell(_task_names(data.get('maintenance', []), tasks))} |"
        )
    return lines


def items_table(root: Path, tasks: dict[str, dict]) -> list[str]:
    folders = _folders(root / "equipment" / "items")
    categories = root / "equipment" / "categories"
    lines = [
        f"### Equipment models ({len(folders)})",
        "",
        "| | Brand | Model | Category | Power | Tasks |",
        "|---|---|---|---|---|---|",
    ]
    for folder in folders:
        data = read_json(folder / "item.json")
        category_id = str(data.get("category", ""))
        category_file = categories / category_id / "category.json"
        category = read_json(category_file) if category_file.is_file() else {}
        # Own visual, else the one of its category
        visual = _visual(root, folder) or _visual(root, categories / category_id)
        refs = list(data.get("maintenance", []))
        if data.get("inherit_maintenance", True):
            own = {r.get("task") for r in refs if isinstance(r, dict)}
            refs += [
                r
                for r in category.get("maintenance", [])
                if isinstance(r, dict) and r.get("task") not in own
            ]
        power = f"{data['power_w']} W" if "power_w" in data else ""
        lines.append(
            f"| {visual} | {_cell(data.get('brand', ''))} "
            f"| {_cell(data.get('model', ''))} "
            f"| {_cell(_name(category, category_id))} | {power} "
            f"| {_cell(_task_names(refs, tasks))} |"
        )
    return lines


def _effects(data: dict) -> str:
    if data.get("kind") == "brine" and "concentration_g_l" in data:
        return f"salinity ({data['concentration_g_l']} g/L)"
    parts = []
    for effect in data.get("effects", []):
        parameter = str(effect.get("parameter", ""))
        parts.append(
            f"+{effect.get('per_ml_per_100l')} {effect.get('unit', '')} "
            f"{PARAMETERS.get(parameter, parameter)}"
        )
    if not parts:
        return ""
    return ", ".join(parts) + ("" if data.get("verified") else " (unverified)")


def supplements_table(root: Path) -> list[str]:
    folders = _folders(root / "supplements" / "items")
    entries = [(folder, read_json(folder / "supplement.json")) for folder in folders]
    entries.sort(
        key=lambda e: (str(e[1].get("brand", "")).lower(), _name(e[1], "").lower())
    )
    brands = sorted({str(data.get("brand", "")) for _, data in entries})
    lines = [
        f"### Supplements ({len(entries)}, {len(brands)} brands)",
        "",
        "| | Brand | Supplement | Kind | 1 mL in 100 L |",
        "|---|---|---|---|---|",
    ]
    for folder, data in entries:
        kind = str(data.get("kind", ""))
        lines.append(
            f"| {_visual(root, folder)} | {_cell(data.get('brand', ''))} "
            f"| {_cell(_name(data, folder.name))} | {KINDS.get(kind, kind)} "
            f"| {_cell(_effects(data))} |"
        )
    return lines


def render(root: Path) -> str:
    """The generated block, markers included."""
    tasks = _tasks(root)
    blocks = [
        categories_table(root, tasks),
        items_table(root, tasks),
        tasks_table(root, tasks),
        supplements_table(root),
    ]
    lines = [
        BEGIN,
        "<!-- Generated by scripts/readme_catalog.py: do not edit by hand -->",
        "",
    ]
    for block in blocks:
        lines += [*block, ""]
    lines.append(END)
    return "\n".join(lines)


def update(text: str, block: str) -> str:
    """The README with its generated block replaced (appended when missing).

    @raise ValueError: markers out of order
    """
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 and end == -1:
        return text.rstrip("\n") + "\n\n## Catalog content\n\n" + block + "\n"
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"README markers {BEGIN} / {END} are broken")
    return text[:start] + block + text[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--check", action="store_true", help="fail if README.md is not up to date"
    )
    args = parser.parse_args(argv)
    readme = args.root / "README.md"
    text = readme.read_text(encoding="utf-8")
    try:
        new = update(text, render(args.root))
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    if new == text:
        print("README.md is up to date")
        return 0
    if args.check:
        print(
            "README.md is not up to date: run python scripts/readme_catalog.py",
            file=sys.stderr,
        )
        return 1
    readme.write_text(new, encoding="utf-8")
    print("README.md updated")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
