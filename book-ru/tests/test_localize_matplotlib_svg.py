from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts.localize_matplotlib_svg import SvgLayoutError, localize_directory
from scripts.translate_svg_text import extract_records

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"


@pytest.mark.parametrize("chapter", [1, 2])
def test_cli_supports_pilot_default_and_explicit_chapter_pattern(
    tmp_path: Path, chapter: int
) -> None:
    source_dir = tmp_path / "source"
    filename = f"figure-{chapter}-test.svg"
    _source(source_dir).rename(source_dir / filename)
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps({f"{filename}:0000": "Видеопамять", f"{filename}:0001": "Путь чтения"}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    command = [
        sys.executable,
        "-m",
        "scripts.localize_matplotlib_svg",
        "--source-dir",
        str(source_dir),
        "--output-dir",
        str(output_dir),
        "--mapping",
        str(mapping),
    ]
    if chapter != 1:
        command.extend(["--file-pattern", rf"figure-{chapter}-.*\.svg"])

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    root = ET.parse(output_dir / filename).getroot()
    assert ["".join(label.itertext()) for label in root.findall(f".//{{{SVG_NS}}}text")] == [
        "Видеопамять",
        "Путь чтения",
    ]


def _source(directory: Path) -> Path:
    directory.mkdir()
    source = directory / "figure.svg"
    source.write_text(
        f'''<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}"
              width="200pt" height="100pt" viewBox="0 0 200 100">
  <g id="patch_1"><path d="M 1 1 L 199 1 L 199 99 z"/></g>
  <g id="text_1">
    <!-- 显存 -->
    <g style="fill: #252525" transform="translate(80 40) scale(0.12 -0.12)">
      <defs><path id="shared" d="M 0 0 L 10 10"/></defs>
      <use xlink:href="#shared"/>
      <use xlink:href="#shared" transform="translate(100 0)"/>
    </g>
    <!-- 读取路径 -->
    <g style="fill: #252525" transform="translate(70 60) scale(0.12 -0.12)">
      <defs><path id="second" d="M 0 0 L 10 10"/></defs>
      <use xlink:href="#second"/>
    </g>
  </g>
  <g id="text_2"><!-- API call -->
    <g transform="translate(50 80) scale(0.12 -0.12)">
      <defs><path id="latin" d="M 0 0 L 20 20"/></defs>
      <use xlink:href="#latin"/>
    </g>
  </g>
  <use xlink:href="#shared" x="5" y="5"/>
</svg>''',
        encoding="utf-8",
    )
    return source


def _localize(
    tmp_path: Path,
    mapping: dict[str, str],
    *,
    layout: Mapping[str, object] | None = None,
) -> ET.Element:
    source_dir = tmp_path / "source"
    _source(source_dir)
    records = extract_records(source_dir, re.compile(r"figure\.svg"))
    output_dir = tmp_path / "output"
    localize_directory(source_dir, output_dir, records, mapping, layout=layout)
    return ET.parse(output_dir / "figure.svg").getroot()


def test_replaces_actual_cjk_glyphs_but_preserves_defs_latin_and_geometry(tmp_path: Path) -> None:
    root = _localize(
        tmp_path,
        {
            "figure.svg:0000": "Видеопамять",
            "figure.svg:0001": "Путь чтения",
        },
    )

    labels = root.findall(f".//{{{SVG_NS}}}text")
    assert ["".join(label.itertext()) for label in labels] == ["Видеопамять", "Путь чтения"]
    assert root.find(f".//{{{SVG_NS}}}g[@id='patch_1']/{{{SVG_NS}}}path") is not None
    assert root.find(f".//{{{SVG_NS}}}g[@id='text_2']/{{{SVG_NS}}}g") is not None
    assert len(root.findall(f".//{{{SVG_NS}}}use")) == 2  # Latin + external shared glyph.
    assert root.find(f".//{{{SVG_NS}}}path[@id='shared']") is not None
    assert all("显存" not in "".join(label.itertext()) for label in labels)


def test_wraps_on_word_boundaries_at_fixed_font_size(tmp_path: Path) -> None:
    root = _localize(
        tmp_path,
        {
            "figure.svg:0000": "Очень длинная подпись",
            "figure.svg:0001": "Путь чтения",
        },
        layout={"figure.svg:0000": {"max_width": 60}},
    )

    label = root.find(f".//{{{SVG_NS}}}text")
    assert label is not None
    assert label.get("font-size") == "12"
    lines = label.findall(f"{{{SVG_NS}}}tspan")
    assert [line.text for line in lines] == ["Очень", "длинная", "подпись"]
    assert " ".join(line.text or "" for line in lines) == "Очень длинная подпись"


def test_rejects_word_that_cannot_fit_without_shrinking(tmp_path: Path) -> None:
    with pytest.raises(SvgLayoutError, match="не помещается"):
        _localize(
            tmp_path,
            {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
            layout={"figure.svg:0000": {"max_width": 10}},
        )
    assert not (tmp_path / "output/figure.svg").exists()


def test_vertical_geometry_expands_boxes_and_arrows_without_scaling_labels(tmp_path: Path) -> None:
    root = _localize(
        tmp_path,
        {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
        layout={"figure.svg": {"geometry": {"scale_y": 1.5}}},
    )

    assert root.get("viewBox") == "0 0 200 150"
    assert root.get("height") == "150pt"
    assert root.find(f".//{{{SVG_NS}}}g[@id='figure_1']") is None
    assert root.find(f".//{{{SVG_NS}}}g[@id='geometry']") is not None
    label = root.find(f".//{{{SVG_NS}}}text[@data-source-id='figure.svg:0000']")
    assert label is not None
    assert label.get("y") == "60"
    assert label.get("font-size") == "12"


def test_moves_complete_legend_group_with_line_samples(tmp_path: Path) -> None:
    root = _localize(
        tmp_path,
        {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
        layout={"figure.svg": {"move_groups": {"text_2": {"dx": 10, "dy": 5}}}},
    )

    moved = root.find(f".//{{{SVG_NS}}}g[@id='text_2']")
    assert moved is not None
    assert moved.get("transform") == "translate(10 5)"
    assert moved.find(f".//{{{SVG_NS}}}use") is not None
    patch = root.find(f".//{{{SVG_NS}}}g[@id='patch_1']")
    assert patch is not None
    assert patch.get("transform") is None


@pytest.mark.parametrize(
    ("scales", "transform"),
    [
        ({"scale_x": 1.2}, "translate(-10 2) scale(1.2 1)"),
        ({"scale_y": 1.5}, "translate(-10 2) scale(1 1.5)"),
        ({"scale_x": 1.2, "scale_y": 1.5}, "translate(-10 2) scale(1.2 1.5)"),
    ],
)
def test_resizes_path_group_without_scaling_or_changing_label(
    tmp_path: Path, scales: dict[str, float], transform: str
) -> None:
    root = _localize(
        tmp_path,
        {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
        layout={"figure.svg": {"move_groups": {"patch_1": {"dx": -10, "dy": 2, **scales}}}},
    )
    patch = root.find(f".//{{{SVG_NS}}}g[@id='patch_1']")
    assert patch is not None
    assert patch.get("transform") == transform
    path = patch.find(f"{{{SVG_NS}}}path")
    assert path is not None and path.get("d") == "M 1 1 L 199 1 L 199 99 z"
    label = root.find(f".//{{{SVG_NS}}}text[@data-source-id='figure.svg:0000']")
    assert label is not None and label.get("font-size") == "12"
    assert "".join(label.itertext()) == "Видеопамять"
    assert label.get("transform") is None


@pytest.mark.parametrize("axis", ["scale_x", "scale_y"])
@pytest.mark.parametrize("scale", [0, -1, True, "2", float("inf"), float("nan")])
def test_rejects_invalid_group_scale(tmp_path: Path, scale: object, axis: str) -> None:
    with pytest.raises(SvgLayoutError, match=axis):
        _localize(
            tmp_path,
            {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
            layout={"figure.svg": {"move_groups": {"patch_1": {"dx": 0, "dy": 0, axis: scale}}}},
        )


@pytest.mark.parametrize("group_id", ["text_1", "text_2"])
@pytest.mark.parametrize("axis", ["scale_x", "scale_y"])
def test_rejects_stretching_native_or_outlined_text(
    tmp_path: Path, group_id: str, axis: str
) -> None:
    with pytest.raises(SvgLayoutError, match=axis):
        _localize(
            tmp_path,
            {"figure.svg:0000": "Видеопамять", "figure.svg:0001": "Путь чтения"},
            layout={"figure.svg": {"move_groups": {group_id: {"dx": 0, "dy": 0, axis: 1.2}}}},
        )


def test_vertical_geometry_keeps_wrapped_line_spacing_unscaled(tmp_path: Path) -> None:
    root = _localize(
        tmp_path,
        {"figure.svg:0000": "Очень длинная подпись", "figure.svg:0001": "Путь чтения"},
        layout={
            "figure.svg": {"geometry": {"scale_y": 1.5}},
            "figure.svg:0000": {"max_width": 60},
        },
    )

    label = root.find(f".//{{{SVG_NS}}}text[@data-source-id='figure.svg:0000']")
    assert label is not None
    assert label.get("y") == "60"
    assert [float(line.get("y", "0")) for line in label.findall(f"{{{SVG_NS}}}tspan")] == [
        45.6,
        60.0,
        74.4,
    ]
