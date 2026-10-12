#!/usr/bin/env python3
"""
make_example.py - Build examples/gear-flower.lac, an original sample project
for the lac2svg converter (no third-party models involved).

The .lac format is a ZIP package (OPC) produced by Bambu Suite:

  [Content_Types].xml
  _rels/.rels
  2D/entry.json                     project metadata (Title, ...)
  2D/2dmodel.json                   scene graph: canvases -> objects
  Metadata2D/project_settings.json  process types per object, making plates

This script writes a small project with:

  - a gear outline (LaserLineCut, closed, red #f44336)
  - a "flower" of 6 petals built from cubic Beziers (LaserLineEngrave, blue)
  - a fill-engraved hexagon (LaserFillEngrave, blue)
  - an AttachedGroup nesting the hexagon with a rotated transform
  - cubic segments stored in the native .lac order (endpoint first) to
    exercise lac2svg's normalize_path_data()

Run:  python make_example.py [out.lac]
"""

import json
import math
import os
import sys
import zipfile

CUT_COLOR = "244 67 54 255"      # red, like Bambu Studio line cuts
ENGRAVE_COLOR = "3 4 253 255"   # blue, like Bambu Studio engravings


def fmt(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def gear_path(r_out, r_in, teeth):
    """Gear outline: alternating outer/inner radii (slanted flanks)."""
    pts = []
    n = teeth * 4
    for i in range(n):
        ang = 2 * math.pi * i / n
        r = r_out if i % 4 in (0, 1) else r_in
        pts.append((r * math.cos(ang), r * math.sin(ang)))
    d = "M %s %s" % (fmt(pts[0][0]), fmt(pts[0][1]))
    for x, y in pts[1:]:
        d += " L %s %s" % (fmt(x), fmt(y))
    return d + " Z"


def flower_path(r_petal, r_center):
    """
    Six-petal flower made of cubic Bezier segments, stored in the native
    .lac order: 'C end_x end_y c1_x c1_y c2_x c2_y' (endpoint FIRST).
    lac2svg reorders these to SVG order when converting.
    """
    d = []
    for i in range(6):
        a0 = 2 * math.pi * i / 6
        a1 = 2 * math.pi * (i + 0.5) / 6
        a2 = 2 * math.pi * (i + 1) / 6
        tip = (r_petal * math.cos(a1), r_petal * math.sin(a1))
        p0 = (r_center * math.cos(a0), r_center * math.sin(a0))
        # control points bulge outward from the petal base
        rc = r_petal * 0.55 + r_center * 0.45
        c1 = (rc * math.cos(a0 + 0.35), rc * math.sin(a0 + 0.35))
        c2 = (rc * math.cos(a2 - 0.35), rc * math.sin(a2 - 0.35))
        if i == 0:
            d.append("M %s %s" % (fmt(p0[0]), fmt(p0[1])))
        # .lac order: endpoint first, then the two control points
        d.append("C %s %s %s %s %s %s" % (
            fmt(tip[0]), fmt(tip[1]),
            fmt(c1[0]), fmt(c1[1]),
            fmt(c2[0]), fmt(c2[1])))
    return " ".join(d) + " Z"


def hexagon_path(r):
    pts = [(r * math.cos(2 * math.pi * i / 6 + math.pi / 6),
            r * math.sin(2 * math.pi * i / 6 + math.pi / 6))
           for i in range(6)]
    d = "M %s %s" % (fmt(pts[0][0]), fmt(pts[0][1]))
    for x, y in pts[1:]:
        d += " L %s %s" % (fmt(x), fmt(y))
    return d + " Z"


def build_model():
    gear = {
        "color": CUT_COLOR,
        "flags": ["FreeAspectRatio"],
        "is_closed": True,
        "name": "Gear outline",
        "obj_id": 1,
        "path_data": gear_path(40, 30, 10),
        "type": "PathObject",
    }
    flower = {
        "color": ENGRAVE_COLOR,
        "flags": ["FreeAspectRatio"],
        "is_closed": False,
        "name": "Flower engrave",
        "obj_id": 2,
        "path_data": flower_path(34, 12),
        "type": "PathObject",
    }
    hexagon = {
        "color": ENGRAVE_COLOR,
        "flags": ["FreeAspectRatio"],
        "is_closed": True,
        "name": "Hex fill",
        "obj_id": 3,
        "path_data": hexagon_path(8),
        "type": "PathObject",
    }
    # Group nesting the hexagon with a rotation, to exercise transform
    # baking (SVG matrix "a b c d e f").
    group = {
        "components": [{"obj_id": 3, "transform": "0.866 -0.5 0.5 0.866 0 0"}],
        "flags": [],
        "name": "Hex group",
        "obj_id": 10,
        "type": "AttachedGroup",
    }
    canvas = {
        "components": [
            {"obj_id": 1, "transform": "1 0 0 1 60 60"},
            {"obj_id": 2, "transform": "1 0 0 1 60 60"},
            {"obj_id": 10, "transform": "1 0 0 1 60 60"},
        ],
        "index": 1,
        "name": "",
        "obj_list": [gear, flower, hexagon, group],
        "type_count": {},
    }
    return {
        "Application": "Bambu Suite",
        "FileVersion": "1.0",
        "canvas_list": [canvas],
    }


def build_settings():
    return {
        "canvas_settings": [
            {
                "index": 1,
                "making_batch_list": [
                    {
                        "auto_arranged": True,
                        "batch_settings": {
                            "classVersion": 3,
                            "material_thickness": 3,
                            "processing_mode": "PLANE",
                        },
                        "making_plate_list": [
                            {
                                # Plate arrangement differs from the design
                                # canvas: gear on the left, flower + hex on
                                # the right, like an auto-arranged cut plate.
                                "components": [
                                    {"obj_id": 1, "transform": "1 0 0 1 50 50"},
                                    {"obj_id": 2, "transform": "1 0 0 1 150 50"},
                                    {"obj_id": 10, "transform": "1 0 0 1 150 50"},
                                ],
                                "index": 1,
                                "name": "Plate 1",
                                "obj_id": 100,
                                "plate_mirror": False,
                                "plate_settings": {
                                    "classVersion": 3,
                                    "laser_cut_colors": "#F44336",
                                },
                            }
                        ],
                        "material_id": "B-YA-A-A-X0",
                        "material_name": "3mm Basswood Plywood",
                        "name": "Batch 1",
                        "obj_id": 101,
                        "process_category": "laser_process",
                    }
                ],
                "object_settings": [
                    {"obj_id": 1, "process_type": "LaserLineCut"},
                    {"obj_id": 2, "process_type": "LaserLineEngrave"},
                    {"obj_id": 3, "process_type": "LaserFillEngrave"},
                ],
            }
        ],
        "project_settings": {},
    }


def build_entry():
    return {
        "Application": "Bambu Suite",
        "Cover": "",
        "CreationDate": "2026-05-08",
        "Description": "Original sample project for the lac2svg converter",
        "Title": "Gear Flower Sample",
        "FileVersion": "1.0",
        "LicenseTerms": "CC0-1.0",
    }


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/json"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Default Extension="gcode" ContentType="text/plain"/>'
    "</Types>"
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships '
    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/2D/design_thumbnail.png" Id="rel-1" Type=""/>'
    "</Relationships>"
)


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    out = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gear-flower.lac")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("2D/entry.json", json.dumps(build_entry(), indent=2))
        z.writestr("2D/2dmodel.json", json.dumps(build_model(), indent=2))
        z.writestr("Metadata2D/project_settings.json",
                   json.dumps(build_settings(), indent=2))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
