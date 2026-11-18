"""Write the lists of the catalog into README.md.

The equipment categories, equipment models, maintenance tasks, supplements,
parameters and water tests of the catalog are listed, with their visuals,
between two markers of the README; the rest of the file is left alone:

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


def _details(summary: str, body: list[str]) -> list[str]:
    """A block that folds: closed by default, its summary always shown.

    Blank lines around the body keep the markdown (tables, nested blocks) working
    inside the HTML element, on GitHub as elsewhere.
    """
    return ["<details>", f"<summary>{summary}</summary>", "", *body, "", "</details>"]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A markdown table; the cells are expected to be safe already."""
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _grouped(
    rows: list[tuple[str, str, str, list[str]]],
    header: list[str],
) -> list[str]:
    """Rows folded by type, then by brand: up to three levels with the list.

    @param rows: (type label, brand, sort key of the row, cells), in the order the
        types are listed in
    @param header: the columns of the table of a brand
    """
    lines: list[str] = []
    types = list(dict.fromkeys(kind for kind, _, _, _ in rows))
    for kind in types:
        of_type = [row for row in rows if row[0] == kind]
        brands = sorted({brand for _, brand, _, _ in of_type}, key=str.lower)
        body: list[str] = []
        for brand in brands:
            mine = sorted(
                (row for row in of_type if row[1] == brand), key=lambda r: r[2].lower()
            )
            body += _details(
                f"{_cell(brand)} ({len(mine)})",
                _table(header, [row[3] for row in mine]),
            )
            body.append("")
        lines += _details(f"<b>{_cell(kind)}</b> ({len(of_type)})", body[:-1])
        lines.append("")
    return lines[:-1]


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
    rows = [
        [
            f"`{task_id}`",
            _cell(_name(data, task_id)),
            _cell(_schedule(data.get("schedule"))),
            _cell(data.get("icon", "")),
        ]
        for task_id, data in tasks.items()
    ]
    return _details(
        f"<b>Maintenance tasks</b> ({len(tasks)})",
        _table(["Id", "Task", "Schedule", "Icon"], rows),
    )


def categories_table(root: Path, tasks: dict[str, dict]) -> list[str]:
    folders = _folders(root / "equipment" / "categories")
    rows = []
    for folder in folders:
        data = read_json(folder / "category.json")
        rows.append(
            [
                _visual(root, folder),
                f"`{folder.name}`",
                _cell(_name(data, folder.name)),
                _cell(", ".join(data.get("scopes", []))),
                _cell(_task_names(data.get("maintenance", []), tasks)),
            ]
        )
    return _details(
        f"<b>Equipment categories</b> ({len(folders)})",
        _table(["", "Id", "Category", "Scopes", "Default tasks"], rows),
    )


def items_table(root: Path, tasks: dict[str, dict]) -> list[str]:
    folders = _folders(root / "equipment" / "items")
    categories = root / "equipment" / "categories"
    rows: list[tuple[str, str, str, list[str]]] = []
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
        model = str(data.get("model", ""))
        rows.append(
            (
                _name(category, category_id),
                str(data.get("brand", "")),
                model,
                [visual, _cell(model), power, _cell(_task_names(refs, tasks))],
            )
        )
    rows.sort(key=lambda r: r[0].lower())
    return _details(
        f"<b>Equipment models</b> ({len(folders)}, by category and brand)",
        _grouped(rows, ["", "Model", "Power", "Tasks"]),
    )


def _effects(data: dict) -> str:
    if data.get("kind") == "brine" and "concentration_g_l" in data:
        concentration = data["concentration_g_l"]
        if isinstance(concentration, dict):
            concentration = concentration.get("default")
        return f"salinity ({concentration} g/L)"
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


CATEGORIES = {
    "physical": "physical",
    "alkalinity": "alkalinity",
    "major": "major element",
    "minor": "minor element",
    "trace": "trace element",
    "nutrient": "nutrient",
}

UNITS = {
    "dkh": "dKH",
    "ppm": "ppm",
    "ppb": "ppb",
    "mg_l": "mg/L",
    "celsius": "°C",
    "fahrenheit": "°F",
    "mv": "mV",
    "ph": "pH",
    "sg": "SG",
    "ppt": "ppt",
}

# Order parameters are listed in, grouped by category.
PARAMETER_ORDER = ("physical", "alkalinity", "major", "minor", "trace", "nutrient")


def _band(data: dict) -> str:
    """The reference band in a few words: "7-8.5 (8)"."""
    band = data.get("range")
    if not isinstance(band, dict):
        return ""
    low, high, optimal = band.get("low"), band.get("high"), band.get("optimal")
    if low is None and high is None:
        return f"target {optimal}" if optimal is not None else ""
    text = f"{low}-{high}"
    if optimal is not None:
        text += f" ({optimal})"
    return text


def parameters_table(root: Path) -> list[str]:
    folders = _folders(root / "parameters" / "items")
    entries = [(folder, read_json(folder / "parameter.json")) for folder in folders]
    entries.sort(
        key=lambda e: (
            PARAMETER_ORDER.index(e[1]["category"])
            if e[1].get("category") in PARAMETER_ORDER
            else len(PARAMETER_ORDER),
            _name(e[1], e[0].name).lower(),
        )
    )
    body: list[str] = []
    categories = list(dict.fromkeys(str(d.get("category", "")) for _, d in entries))
    for category in categories:
        mine = [(f, d) for f, d in entries if str(d.get("category", "")) == category]
        rows = [
            [
                _cell(data.get("symbol", "")),
                _cell(_name(data, folder.name)),
                UNITS.get(str(data.get("unit", "")), str(data.get("unit", ""))),
                _cell(_band(data)),
            ]
            for folder, data in mine
        ]
        body += _details(
            f"<b>{CATEGORIES.get(category, category)}</b> ({len(mine)})",
            _table(["Symbol", "Parameter", "Unit", "Reef range"], rows),
        )
        body.append("")
    return _details(f"<b>Water parameters</b> ({len(entries)}, by category)", body[:-1])


def supplements_table(root: Path) -> list[str]:
    folders = _folders(root / "supplements" / "items")
    entries = [(folder, read_json(folder / "supplement.json")) for folder in folders]
    brands = {str(data.get("brand", "")) for _, data in entries}
    rows: list[tuple[str, str, str, list[str]]] = []
    for folder, data in entries:
        kind = str(data.get("kind", ""))
        name = _name(data, folder.name)
        rows.append(
            (
                KINDS.get(kind, kind),
                str(data.get("brand", "")),
                name,
                [_visual(root, folder), _cell(name), _cell(_effects(data))],
            )
        )
    order = list(KINDS.values())
    rows.sort(key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    return _details(
        f"<b>Supplements</b> ({len(entries)}, {len(brands)} brands, by kind and brand)",
        _grouped(rows, ["", "Supplement", "1 mL in 100 L"]),
    )


def _methods(data: dict) -> str:
    """The tests of a kit, e.g. 'KH (titration), NO3 (direct)'."""
    return ", ".join(
        f"{PARAMETERS.get(str(t.get('parameter')), t.get('parameter'))} "
        f"({t.get('method')})"
        for t in data.get("tests", [])
    )


def water_tests_table(root: Path) -> list[str]:
    folders = _folders(root / "water_tests" / "items")
    entries = [(folder, read_json(folder / "water_test.json")) for folder in folders]
    brands = {str(data.get("brand", "")) for _, data in entries}
    rows: list[tuple[str, str, str, list[str]]] = []
    for folder, data in entries:
        tests = data.get("tests") or [{}]
        parameter = str(tests[0].get("parameter", ""))
        name = _name(data, folder.name)
        rows.append(
            (
                PARAMETERS.get(parameter, parameter),
                str(data.get("brand", "")),
                name,
                [_visual(root, folder), _cell(name), _cell(_methods(data))],
            )
        )
    order = list(PARAMETERS.values())
    rows.sort(key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    return _details(
        f"<b>Water tests</b> ({len(entries)}, {len(brands)} brands, "
        "by parameter and brand)",
        _grouped(rows, ["", "Test", "Parameters"]),
    )


def render(root: Path) -> str:
    """The generated block, markers included."""
    tasks = _tasks(root)
    blocks = [
        categories_table(root, tasks),
        items_table(root, tasks),
        tasks_table(root, tasks),
        supplements_table(root),
        parameters_table(root),
        water_tests_table(root),
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
