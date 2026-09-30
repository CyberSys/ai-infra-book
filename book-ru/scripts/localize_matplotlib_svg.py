from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from PIL import ImageFont

from scripts.translate_svg_text import (
    CJK_RE,
    SvgTextRecord,
    extract_records,
    validate_mapping,
)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
TRANSFORM_RE = re.compile(
    r"translate\(\s*([+-]?[\d.]+)[ ,]+([+-]?[\d.]+)\s*\)\s*"
    r"(?:rotate\(\s*([+-]?[\d.]+)\s*\)\s*)?"
    r"scale\(\s*([+-]?[\d.]+)[ ,]+([+-]?[\d.]+)\s*\)"
)
TRANSLATE_RE = re.compile(r"translate\(\s*([+-]?[\d.]+)(?:[ ,]+[+-]?[\d.]+)?\s*\)")

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ET.register_namespace("cc", "http://creativecommons.org/ns#")
ET.register_namespace("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")


class SvgLayoutError(RuntimeError):
    """SVG-подпись невозможно безопасно разместить."""


def _number(value: object, name: str, record_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SvgLayoutError(f"{record_id}: {name} должен быть числом")
    number = float(value)
    if not math.isfinite(number):
        raise SvgLayoutError(f"{record_id}: {name} должен быть конечным")
    return number


def _layout_for(layout: Mapping[str, object], record: SvgTextRecord) -> dict[str, object]:
    merged: dict[str, object] = {}
    for key in (record.path, record.id):
        value = layout.get(key)
        if value is None:
            continue
        allowed = {"align", "max_width", "font_size", "line_height", "x", "y"}
        if key == record.path:
            allowed.update({"geometry", "move_groups"})
        if not isinstance(value, dict):
            raise SvgLayoutError(f"{key}: некорректные параметры layout")
        typed_value = cast(dict[str, object], value)
        if set(typed_value) - allowed:
            raise SvgLayoutError(f"{key}: некорректные параметры layout")
        merged.update(
            (name, setting)
            for name, setting in typed_value.items()
            if name not in {"geometry", "move_groups"}
        )
    return merged


def _original_width(glyphs: ET.Element, font_size: float, source: str) -> float:
    uses = glyphs.findall(f"{{{SVG_NS}}}use")
    if not uses:
        raise SvgLayoutError("Нет контурных глифов у исходной подписи")
    transform = uses[-1].get("transform")
    offset = 0.0
    if transform is not None:
        match = TRANSLATE_RE.fullmatch(transform)
        if match is None:
            raise SvgLayoutError(f"Неизвестное смещение контура: {transform}")
        offset = float(match.group(1))
    last_char = source.rstrip()[-1]
    advance = 100.0 if CJK_RE.search(last_char) else 55.0
    return (offset + advance) * font_size / 100


def _width(text: str, font: ImageFont.FreeTypeFont, font_size: float) -> float:
    return float(font.getlength(text)) * font_size / 1000


def _wrap(
    value: str,
    max_width: float,
    font: ImageFont.FreeTypeFont,
    font_size: float,
    record_id: str,
) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if _width(word, font, font_size) > max_width + 0.01:
            raise SvgLayoutError(f"{record_id}: слово {word!r} не помещается в {max_width:g}")
        candidate = f"{current} {word}" if current else word
        if _width(candidate, font, font_size) <= max_width + 0.01:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _format(number: float) -> str:
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _replace_glyphs(
    glyphs: ET.Element,
    record: SvgTextRecord,
    translated: str,
    options: Mapping[str, object],
    font: ImageFont.FreeTypeFont,
    viewbox: tuple[float, float, float, float],
) -> ET.Element:
    transform = glyphs.get("transform", "")
    match = TRANSFORM_RE.fullmatch(transform)
    if match is None:
        raise SvgLayoutError(f"{record.id}: неизвестное преобразование {transform!r}")
    source_x, source_y = float(match.group(1)), float(match.group(2))
    rotation = float(match.group(3) or 0)
    x_scale, y_scale = float(match.group(4)), float(match.group(5))
    if x_scale <= 0 or not math.isclose(y_scale, -x_scale):
        raise SvgLayoutError(f"{record.id}: неподдерживаемый масштаб {transform!r}")
    source_font_size = x_scale * 100
    font_size = _number(options.get("font_size", source_font_size), "font_size", record.id)
    if font_size <= 0:
        raise SvgLayoutError(f"{record.id}: font_size должен быть положительным")
    original_width = _original_width(glyphs, source_font_size, record.source)
    align = options.get("align", "center")
    if align not in ("start", "center", "end"):
        raise SvgLayoutError(f"{record.id}: align должен быть start, center или end")
    anchor_offset = {"start": 0, "center": original_width / 2, "end": original_width}[align]
    original_x = source_x + (0 if rotation else anchor_offset)
    original_y = source_y - (anchor_offset if rotation == -90 else 0)
    anchor_x = _number(options.get("x", original_x), "x", record.id)
    anchor_y = _number(options.get("y", original_y), "y", record.id)
    min_x, _, view_width, view_height = viewbox
    if rotation == 0:
        if align == "start":
            available = min_x + view_width - anchor_x - 2
        elif align == "end":
            available = anchor_x - min_x - 2
        else:
            available = 2 * min(anchor_x - min_x, min_x + view_width - anchor_x) - 4
    else:
        available = view_height - 8
    max_width = _number(options.get("max_width", available), "max_width", record.id)
    if max_width <= 0:
        raise SvgLayoutError(f"{record.id}: max_width должен быть положительным")
    lines = _wrap(translated, max_width, font, font_size, record.id)
    line_height = _number(options.get("line_height", font_size * 1.2), "line_height", record.id)
    if line_height <= 0:
        raise SvgLayoutError(f"{record.id}: line_height должен быть положительным")

    visible_children = [child for child in glyphs if child.tag != f"{{{SVG_NS}}}defs"]
    if any(child.tag != f"{{{SVG_NS}}}use" for child in visible_children):
        raise SvgLayoutError(f"{record.id}: внутри подписи есть неучтённая геометрия")
    for child in visible_children:
        glyphs.remove(child)
    text = ET.Element(f"{{{SVG_NS}}}text")
    text.set("x", _format(anchor_x))
    text.set("y", _format(anchor_y))
    text.set("font-family", "DejaVu Sans")
    text.set("font-size", _format(font_size))
    text.set("text-anchor", {"start": "start", "center": "middle", "end": "end"}[align])
    fill_match = re.search(r"fill:\s*([^;]+)", glyphs.get("style", ""))
    text.set("fill", fill_match.group(1).strip() if fill_match else "#252525")
    text.set("data-source-id", record.id)
    if rotation:
        text.set(
            "transform",
            f"rotate({_format(rotation)} {_format(anchor_x)} {_format(anchor_y)})",
        )
    if len(lines) == 1:
        text.text = lines[0]
    else:
        for index, line in enumerate(lines):
            tspan = ET.SubElement(text, f"{{{SVG_NS}}}tspan")
            tspan.set("x", _format(anchor_x))
            tspan.set("y", _format(anchor_y + (index - (len(lines) - 1) / 2) * line_height))
            tspan.text = line
    return text


def _expand_geometry(
    root: ET.Element,
    layout: Mapping[str, object],
    filename: str,
    labels: Sequence[tuple[ET.Element, ET.Element]],
    viewbox: tuple[float, float, float, float],
) -> None:
    file_layout = layout.get(filename)
    if not isinstance(file_layout, dict) or "geometry" not in file_layout:
        return
    typed_file_layout = cast(dict[str, object], file_layout)
    raw_geometry = typed_file_layout["geometry"]
    if not isinstance(raw_geometry, dict):
        raise SvgLayoutError(f"{filename}: geometry допускает только scale_y")
    geometry = cast(dict[str, object], raw_geometry)
    if set(geometry) != {"scale_y"}:
        raise SvgLayoutError(f"{filename}: geometry допускает только scale_y")
    scale_y = _number(geometry["scale_y"], "scale_y", filename)
    if scale_y < 1:
        raise SvgLayoutError(f"{filename}: scale_y должен быть >= 1")
    min_x, min_y, width, height = viewbox
    if min_y != 0:
        raise SvgLayoutError(f"{filename}: geometry требует viewBox с y=0")
    raw_height = root.get("height", "")
    size_match = re.fullmatch(r"([\d.]+)(pt|px)", raw_height)
    if size_match is None:
        raise SvgLayoutError(f"{filename}: неизвестная единица высоты {raw_height!r}")
    for parent, label in labels:
        parent.remove(label)
    children = list(root)
    drawing = [
        child
        for child in children
        if child.tag
        not in {
            f"{{{SVG_NS}}}metadata",
            f"{{{SVG_NS}}}defs",
            f"{{{SVG_NS}}}title",
            f"{{{SVG_NS}}}desc",
        }
    ]
    first_index = children.index(drawing[0])
    for child in drawing:
        root.remove(child)
    geometry = ET.Element(
        f"{{{SVG_NS}}}g",
        {"id": "geometry", "transform": f"scale(1 {_format(scale_y)})"},
    )
    geometry.extend(drawing)
    root.insert(first_index, geometry)
    overlay = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "localized_labels"})
    for _, label in labels:
        x = float(label.get("x", "0"))
        source_y = float(label.get("y", "0"))
        y = source_y * scale_y
        label.set("y", _format(y))
        for tspan in label.findall(f"{{{SVG_NS}}}tspan"):
            original_line_y = float(tspan.get("y", "0"))
            tspan.set("y", _format(y + original_line_y - source_y))
        transform = label.get("transform")
        if transform:
            angle = transform.split("(", 1)[1].split(" ", 1)[0]
            label.set("transform", f"rotate({angle} {_format(x)} {_format(y)})")
        overlay.append(label)
    root.set("viewBox", f"{_format(min_x)} 0 {_format(width)} {_format(height * scale_y)}")
    root.set("height", f"{_format(float(size_match.group(1)) * scale_y)}{size_match.group(2)}")


def _move_groups(root: ET.Element, layout: Mapping[str, object], filename: str) -> None:
    file_layout = layout.get(filename)
    if not isinstance(file_layout, dict) or "move_groups" not in file_layout:
        return
    typed_file_layout = cast(dict[str, object], file_layout)
    if "geometry" in typed_file_layout:
        raise SvgLayoutError(f"{filename}: move_groups нельзя совмещать с geometry")
    raw_moves = typed_file_layout["move_groups"]
    if not isinstance(raw_moves, dict):
        raise SvgLayoutError(f"{filename}: move_groups должен быть object")
    moves = cast(dict[str, object], raw_moves)
    for group_id, move in moves.items():
        if not isinstance(move, dict):
            raise SvgLayoutError(f"{filename}: некорректный move_groups")
        typed_move = cast(dict[str, object], move)
        if not {"dx", "dy"} <= set(typed_move) <= {"dx", "dy", "scale_x", "scale_y"}:
            raise SvgLayoutError(f"{filename}: некорректный move_groups")
        dx = _number(typed_move["dx"], "dx", group_id)
        dy = _number(typed_move["dy"], "dy", group_id)
        scale_x = _number(typed_move.get("scale_x", 1), "scale_x", group_id)
        scale_y = _number(typed_move.get("scale_y", 1), "scale_y", group_id)
        if scale_x <= 0:
            raise SvgLayoutError(f"{filename}: scale_x должен быть положительным")
        if scale_y <= 0:
            raise SvgLayoutError(f"{filename}: scale_y должен быть положительным")
        matches = [
            node
            for node in root.iter()
            if node.tag == f"{{{SVG_NS}}}g" and node.get("id") == group_id
        ]
        if len(matches) != 1:
            raise SvgLayoutError(f"{filename}: группа {group_id!r} не уникальна или отсутствует")
        group = matches[0]
        scaled = bool({"scale_x", "scale_y"} & set(typed_move))
        if scaled and any(
            node.tag not in {f"{{{SVG_NS}}}g", f"{{{SVG_NS}}}path"} for node in group.iter()
        ):
            raise SvgLayoutError(f"{filename}: scale_x/scale_y разрешены только для групп g/path")
        original = group.get("transform", "")
        transform = f"translate({_format(dx)} {_format(dy)})"
        if scaled:
            transform += f" scale({_format(scale_x)} {_format(scale_y)})"
        group.set("transform", f"{transform} {original}".strip())


def _render_file(
    source: Path,
    records: Sequence[SvgTextRecord],
    mapping: Mapping[str, str],
    layout: Mapping[str, object],
    font: ImageFont.FreeTypeFont,
) -> bytes:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.parse(source, parser=parser).getroot()
    except ET.ParseError as error:
        raise SvgLayoutError(f"{source.name}: некорректный XML: {error}") from error
    try:
        viewbox = tuple(float(part) for part in root.get("viewBox", "").split())
    except ValueError as error:
        raise SvgLayoutError(f"{source.name}: некорректный viewBox") from error
    if len(viewbox) != 4:
        raise SvgLayoutError(f"{source.name}: нужен viewBox из четырёх чисел")
    parents = {child: parent for parent in root.iter() for child in parent}
    comments = [
        node
        for node in root.iter()
        if node.tag is ET.Comment and CJK_RE.search(html.unescape(node.text or ""))
    ]
    if len(comments) != len(records):
        raise SvgLayoutError(f"{source.name}: число CJK-комментариев изменилось")
    labels: list[tuple[ET.Element, ET.Element]] = []
    for comment, record in zip(comments, records, strict=True):
        if record.kind != "comment" or html.unescape((comment.text or "").strip()) != record.source:
            raise SvgLayoutError(f"{record.id}: исходная подпись не совпала с mapping")
        parent = parents[comment]
        if not (parent.get("id") or "").startswith("text_"):
            raise SvgLayoutError(f"{record.id}: комментарий вне text_N")
        siblings = list(parent)
        index = siblings.index(comment)
        if index + 1 == len(siblings) or siblings[index + 1].tag != f"{{{SVG_NS}}}g":
            raise SvgLayoutError(f"{record.id}: нет контурных глифов после комментария")
        glyphs = siblings[index + 1]
        text = _replace_glyphs(
            glyphs, record, mapping[record.id], _layout_for(layout, record), font, viewbox
        )
        comment.text = f" {mapping[record.id]} "
        parent.insert(index + 2, text)
        labels.append((parent, text))
    _move_groups(root, layout, source.name)
    _expand_geometry(root, layout, source.name, labels, viewbox)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def localize_directory(
    source_dir: Path,
    output_dir: Path,
    records: Sequence[SvgTextRecord],
    raw_mapping: Mapping[str, object],
    *,
    layout: Mapping[str, object] | None = None,
) -> tuple[Path, ...]:
    mapping = validate_mapping(records, raw_mapping)
    overrides = layout or {}
    known_keys = {record.id for record in records} | {record.path for record in records}
    if set(overrides) - known_keys:
        raise SvgLayoutError(f"Неизвестные layout keys: {sorted(set(overrides) - known_keys)}")
    font = ImageFont.truetype("DejaVuSans.ttf", 1000)
    files = sorted({record.path for record in records})
    rendered: dict[Path, bytes] = {}
    for filename in files:
        target = output_dir / filename
        if target.exists():
            raise SvgLayoutError(f"Output уже существует: {target}")
        file_records = [record for record in records if record.path == filename]
        rendered[target] = _render_file(
            source_dir / filename, file_records, mapping, overrides, font
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    for target, data in rendered.items():
        target.write_bytes(data)
    return tuple(rendered)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Заменить контурные CJK-подписи Matplotlib SVG")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mapping", required=True, action="append", type=Path)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--file-pattern", default=r"figure-1-.*\.svg")
    arguments = parser.parse_args(argv)
    try:
        mapping: dict[str, object] = {}
        for path in cast(list[Path], arguments.mapping):
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise SvgLayoutError(f"{path}: mapping некорректен или ключи повторяются")
            typed_raw = cast(dict[str, object], raw)
            if set(mapping) & set(typed_raw):
                raise SvgLayoutError(f"{path}: mapping некорректен или ключи повторяются")
            mapping.update(typed_raw)
        raw_layout: object = (
            json.loads(arguments.layout.read_text(encoding="utf-8")) if arguments.layout else {}
        )
        if not isinstance(raw_layout, dict):
            raise SvgLayoutError("layout должен быть JSON object")
        layout = cast(dict[str, object], raw_layout)
        records = extract_records(arguments.source_dir, re.compile(arguments.file_pattern))
        outputs = localize_directory(
            arguments.source_dir, arguments.output_dir, records, mapping, layout=layout
        )
    except (OSError, ValueError, SvgLayoutError) as error:
        print(f"svg-layout-error: {error}", file=sys.stderr)
        return 1
    print(f"SVG localized: {len(outputs)} файлов, {len(records)} подписей")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
