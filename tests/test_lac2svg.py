"""Tests for lac2svg - run with: python -m pytest tests/ -v"""

import json
import math
import os
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lac2svg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLE_LAC = os.path.join(ROOT, "examples", "gear-flower.lac")


# ------------------------------------------------------------------ transforms

def test_parse_transform_identity():
    assert lac2svg.parse_transform("") == lac2svg.IDENTITY
    assert lac2svg.parse_transform(None) == lac2svg.IDENTITY
    assert lac2svg.parse_transform("1 0 0 1 0 0") == lac2svg.IDENTITY


def test_parse_transform_translation():
    assert lac2svg.parse_transform("1 0 0 1 10 20") == (1, 0, 0, 1, 10, 20)


def test_parse_transform_rejects_bad_input():
    with pytest.raises(ValueError):
        lac2svg.parse_transform("1 2 3")


def test_mat_apply_translation():
    assert lac2svg.mat_apply((1, 0, 0, 1, 10, 20), 5, 5) == (15, 25)


def test_mat_apply_rotation():
    m = lac2svg.parse_transform("0 -1 1 0 0 0")  # 90 degrees
    x, y = lac2svg.mat_apply(m, 10, 0)
    assert x == pytest.approx(0, abs=1e-9)
    assert y == pytest.approx(-10, abs=1e-9)


def test_mat_mul_nested_groups():
    # parent translate (10,0) * child translate (0,20) == translate (10,20)
    m = lac2svg.mat_mul((1, 0, 0, 1, 10, 0), (1, 0, 0, 1, 0, 20))
    assert lac2svg.mat_apply(m, 0, 0) == (10, 20)


# ------------------------------------------------------------------ path data

def test_normalize_path_data_reorders_cubic():
    # .lac stores 'C end c1 c2'; SVG expects 'C c1 c2 end'
    d = lac2svg.normalize_path_data("M 0 0 C 10 10 1 1 2 2")
    assert d == "M 0 0 C 1 1 2 2 10 10"


def test_normalize_path_data_implicit_repetition():
    # implicit repetitions stay implicit, but each 6-tuple is reordered
    d = lac2svg.normalize_path_data("M 0 0 C 9 9 1 1 2 2 8 8 3 3 4 4")
    assert d == "M 0 0 C 1 1 2 2 9 9 3 3 4 4 8 8"


def test_normalize_path_data_relative_c():
    d = lac2svg.normalize_path_data("m 0 0 c 9 9 1 1 2 2")
    assert d == "m 0 0 c 1 1 2 2 9 9"


def test_normalize_path_data_leaves_other_commands():
    d = "M 0 0 L 10 10 Q 5 5 8 8 Z"
    assert lac2svg.normalize_path_data(d) == d


def test_normalize_path_data_empty():
    assert lac2svg.normalize_path_data("") == ""


def test_path_points_line():
    pts = list(lac2svg.path_points("M 1 2 L 3 4"))
    assert pts == [(1, 2), (3, 4)]


def test_path_points_relative():
    pts = list(lac2svg.path_points("M 1 2 l 3 4"))
    assert pts == [(1, 2), (4, 6)]


def test_path_points_cubic_includes_controls():
    pts = list(lac2svg.path_points("M 0 0 C 1 1 2 2 3 3"))
    assert pts == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_path_bbox():
    bb = lac2svg.path_bbox("M 0 0 L 10 4 L 2 8")
    assert bb == (0, 0, 10, 8)


def test_bake_path_identity_returns_verbatim():
    assert lac2svg.bake_path("M 1 2 L 3 4", lac2svg.IDENTITY) == "M 1 2 L 3 4"


def test_bake_path_translation():
    m = lac2svg.parse_transform("1 0 0 1 10 20")
    assert lac2svg.bake_path("M 1 2 L 3 4", m) == "M 11 22 L 13 24"


def test_bake_path_relative_becomes_absolute():
    m = lac2svg.parse_transform("1 0 0 1 10 20")
    assert lac2svg.bake_path("m 1 2 l 3 4", m) == "M 11 22 L 14 26"


def test_bake_path_hv_become_l():
    # identity transform returns the path verbatim (H/V preserved);
    # a real transform converts H/V to L
    m = lac2svg.parse_transform("1 0 0 1 10 20")
    d = lac2svg.bake_path("M 1 2 H 5 V 8", m)
    assert d == "M 11 22 L 15 22 L 15 28"


def test_bake_path_cubic_controls_transformed():
    m = lac2svg.parse_transform("2 0 0 2 0 0")  # scale x2
    d = lac2svg.bake_path("M 0 0 C 1 1 2 2 3 3", m)
    assert d == "M 0 0 C 2 2 4 4 6 6"


# --------------------------------------------------------------------- colors

def test_parse_color():
    assert lac2svg.parse_color("244 67 54 255") == ("#f44336", 1.0)


def test_parse_color_alpha():
    color, alpha = lac2svg.parse_color("244 67 54 128")
    assert color == "#f44336"
    assert alpha == pytest.approx(128 / 255.0)


def test_parse_color_bad_input():
    assert lac2svg.parse_color("garbage") == ("#000000", 1.0)


# ------------------------------------------------------------------ .lac load

def test_load_lac_example():
    z, model, proc_map, entry = lac2svg.load_lac(EXAMPLE_LAC)
    assert "canvas_list" in model
    assert proc_map[1] == "LaserLineCut"
    assert proc_map[2] == "LaserLineEngrave"
    assert proc_map[3] == "LaserFillEngrave"
    assert entry["Title"] == "Gear Flower Sample"
    z.close()


def test_build_object_map_normalizes_c():
    z, model, _, _ = lac2svg.load_lac(EXAMPLE_LAC)
    objs = lac2svg.build_object_map(model)
    # flower path_data was stored endpoint-first; after build_object_map it
    # must be in SVG order: controls first, endpoint last.
    d = objs[2]["path_data"]
    assert "C " in d
    first_c = d[d.index("C "):]
    nums = [float(t) for t in first_c[2:].split()[:6]]
    # first petal tip is at angle 30deg, radius 34 -> (29.44, 17)
    assert nums[4] == pytest.approx(34 * math.cos(math.pi / 6), abs=0.01)
    assert nums[5] == pytest.approx(34 * math.sin(math.pi / 6), abs=0.01)
    z.close()


# ------------------------------------------------------------------- styling

def test_style_for_cut():
    obj = {"color": "244 67 54 255"}
    s = lac2svg.style_for(obj, "LaserLineCut", 0)
    assert "stroke:#f44336" in s
    assert "stroke-linecap:square" in s
    assert "stroke-linejoin:bevel" in s
    assert "stroke-width:1" in s


def test_style_for_fill_engrave():
    obj = {"color": "3 4 253 255"}
    s = lac2svg.style_for(obj, "LaserFillEngrave", 0)
    assert "fill:#0304fd" in s
    assert "stroke:none" in s
    assert "fill-rule:evenodd" in s


def test_style_for_explicit_width():
    obj = {"color": "244 67 54 255"}
    s = lac2svg.style_for(obj, "LaserLineCut", 0.3)
    assert "stroke-width:0.3" in s


# ------------------------------------------------------------------ end-to-end

def run_converter(args):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "lac2svg.py")] + args,
        capture_output=True, text=True, encoding="utf-8")


def test_cli_design_layout(tmp_path):
    out = tmp_path / "design.svg"
    r = run_converter([EXAMPLE_LAC, str(out), "--layout", "design"])
    assert r.returncode == 0, r.stderr
    svg = out.read_text(encoding="utf-8")
    assert svg.startswith('<?xml version="1.0"')
    assert "<title>Gear Flower Sample</title>" in svg
    assert svg.count("<path") == 3
    assert 'data-process="LaserLineCut"' in svg
    assert 'data-process="LaserLineEngrave"' in svg
    assert 'data-process="LaserFillEngrave"' in svg
    assert 'vector-effect="non-scaling-stroke"' in svg
    # AttachedGroup becomes a <g>
    assert "<g " in svg


def test_cli_plate_layout(tmp_path):
    out = tmp_path / "plate.svg"
    r = run_converter([EXAMPLE_LAC, str(out), "--layout", "plate"])
    assert r.returncode == 0, r.stderr
    svg = out.read_text(encoding="utf-8")
    assert svg.count("<path") == 3


def test_cli_stroke_width(tmp_path):
    out = tmp_path / "wide.svg"
    r = run_converter([EXAMPLE_LAC, str(out), "--stroke-width", "0.5"])
    assert r.returncode == 0, r.stderr
    svg = out.read_text(encoding="utf-8")
    assert "stroke-width:0.5" in svg
    assert "vector-effect" not in svg


def test_cli_viewbox_margin(tmp_path):
    out0 = tmp_path / "m0.svg"
    out5 = tmp_path / "m5.svg"
    assert run_converter([EXAMPLE_LAC, str(out0), "--margin", "0"]).returncode == 0
    assert run_converter([EXAMPLE_LAC, str(out5), "--margin", "5"]).returncode == 0

    def width_of(p):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("<svg"):
                return float(line.split('width="')[1].split("mm")[0])
    assert width_of(out5) - width_of(out0) == pytest.approx(10.0)


def test_cli_missing_file():
    r = run_converter([os.path.join(tmp_path if False else HERE, "nope.lac")])
    assert r.returncode != 0
