#!/usr/bin/env python3
"""
lac2svg.py - Convert Bambu Studio laser project files (.lac) to SVG.

A .lac file is a ZIP package (Bambu Suite project). The vector geometry
lives in "2D/2dmodel.json" as a scene graph:

  - canvas_list[]: canvases, each with `components` (root placements)
  - AttachedGroup: group node with nested `components` (obj_id + transform)
  - PathObject: leaf with `path_data` (SVG path syntax), `color` "R G B A",
    `is_closed` flag
  - transforms are SVG-style matrices: "a b c d e f"

Process types (LaserLineCut / LaserLineEngrave / LaserFillEngrave) are read
from "Metadata2D/project_settings.json" when present.

Output SVG uses the same Y-down coordinate system as the Bambu Studio 2D
canvas (SVG native), so it renders identically to what you see in the app.

Usage:
  python lac2svg.py input.lac [output.svg]
  python lac2svg.py input.lac out.svg --layout plate   # plate arrangement
  python lac2svg.py input.lac out.svg --stroke-width 0.3 --margin 5
  python lac2svg.py input.lac out.svg --stroke-width 0   # Studio look (default):
                                                          # 1px constant on screen
"""

import argparse
import json
import os
import re
import sys
import zipfile

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

PATH_CMD = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")
NUM_TOKEN = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


# ---------------------------------------------------------------- transforms

def parse_transform(s):
    """Parse 'a b c d e f' into an SVG matrix tuple."""
    if not s or not str(s).strip():
        return IDENTITY
    parts = str(s).replace(",", " ").split()
    if len(parts) != 6:
        raise ValueError("unexpected transform: %r" % s)
    return tuple(float(p) for p in parts)


def mat_mul(parent, child):
    """Return parent * child (child is applied first, like nested SVG groups)."""
    pa, pb, pc, pd, pe, pf = parent
    ca, cb, cc, cd, ce, cf = child
    return (
        pa * ca + pc * cb, pb * ca + pd * cb,
        pa * cc + pc * cd, pb * cc + pd * cd,
        pa * ce + pc * cf + pe, pb * ce + pd * cf + pf,
    )


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


# ------------------------------------------------------------------ path bbox

def path_points(d):
    """
    Yield every coordinate pair of an SVG path (endpoints and Bezier
    control points). Good enough for a viewBox; curves may slightly
    over-estimate, which the output margin absorbs.
    """
    tokens = []
    for m in re.finditer(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)", d):
        if m.group(1):
            tokens.append(m.group(1))
        else:
            tokens.append(float(m.group(2)))

    i, n = 0, len(tokens)
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    cmd = None

    def take(k):
        nonlocal i
        if i + k > n:
            raise ValueError("truncated path data")
        vals = tokens[i:i + k]
        i += k
        return vals

    while i < n:
        if isinstance(tokens[i], str):
            cmd = tokens[i]
            i += 1
        elif cmd is None:
            raise ValueError("path data must start with a command")
        # else: implicit repetition of the previous command

        rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            start = cur
            yield cur
            cmd = "L" if not rel else "l"  # subsequent pairs are line-tos
        elif c == "L":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            yield cur
        elif c == "H":
            (x,) = take(1)
            if rel:
                x += cur[0]
            cur = (x, cur[1])
            yield cur
        elif c == "V":
            (y,) = take(1)
            if rel:
                y += cur[1]
            cur = (cur[0], y)
            yield cur
        elif c == "C":
            x1, y1, x2, y2, x, y = take(6)
            if rel:
                x1 += cur[0]; y1 += cur[1]
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            yield (x1, y1); yield (x2, y2)
            cur = (x, y)
            yield cur
        elif c == "S":
            x2, y2, x, y = take(4)
            if rel:
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            yield (x2, y2)
            cur = (x, y)
            yield cur
        elif c == "Q":
            x1, y1, x, y = take(4)
            if rel:
                x1 += cur[0]; y1 += cur[1]
                x += cur[0]; y += cur[1]
            yield (x1, y1)
            cur = (x, y)
            yield cur
        elif c == "T":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            yield cur
        elif c == "A":
            rx, ry, rot, laf, sf, x, y = take(7)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            yield cur  # endpoints only (approximate for arcs)
        elif c == "Z":
            cur = start
            yield cur
        else:
            raise ValueError("unsupported path command: %r" % cmd)


def path_bbox(d, m=IDENTITY):
    xs, ys = [], []
    for (x, y) in path_points(d):
        px, py = mat_apply(m, x, y)
        xs.append(px)
        ys.append(py)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def bake_path(d, m=IDENTITY):
    """
    Re-emit path `d` with every coordinate transformed by matrix `m`,
    as absolute commands. Affine transforms map Bezier control points
    exactly, so M/L/C/S/Q/T stay exact; arcs keep their radii and only
    have their endpoint transformed (approximation - .lac files use
    Bezier curves, not arcs).
    """
    if all(abs(m[i] - IDENTITY[i]) < 1e-12 for i in range(6)):
        return d

    tokens = []
    for t in re.finditer(
            r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)",
            d):
        tokens.append(t.group(1) or float(t.group(2)))

    i, n = 0, len(tokens)
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    cmd = None
    out = []

    def take(k):
        nonlocal i
        if i + k > n:
            raise ValueError("truncated path data")
        vals = tokens[i:i + k]
        i += k
        return vals

    def pt(x, y):
        px, py = mat_apply(m, x, y)
        return "%s %s" % (fmt(px), fmt(py))

    while i < n:
        if isinstance(tokens[i], str):
            cmd = tokens[i]
            i += 1
        elif cmd is None:
            raise ValueError("path data must start with a command")

        rel = cmd.islower()
        c = cmd.upper()

        if c == "M":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            start = cur
            out.append("M " + pt(x, y))
            cmd = "L" if not rel else "l"
        elif c == "L":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("L " + pt(x, y))
        elif c == "H":
            (x,) = take(1)
            if rel:
                x += cur[0]
            cur = (x, cur[1])
            out.append("L " + pt(*cur))
        elif c == "V":
            (y,) = take(1)
            if rel:
                y += cur[1]
            cur = (cur[0], y)
            out.append("L " + pt(*cur))
        elif c == "C":
            x1, y1, x2, y2, x, y = take(6)
            if rel:
                x1 += cur[0]; y1 += cur[1]
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("C %s %s %s" % (pt(x1, y1), pt(x2, y2), pt(x, y)))
        elif c == "S":
            x2, y2, x, y = take(4)
            if rel:
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("S %s %s" % (pt(x2, y2), pt(x, y)))
        elif c == "Q":
            x1, y1, x, y = take(4)
            if rel:
                x1 += cur[0]; y1 += cur[1]
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("Q %s %s" % (pt(x1, y1), pt(x, y)))
        elif c == "T":
            x, y = take(2)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("T " + pt(x, y))
        elif c == "A":
            rx, ry, rot, laf, sf, x, y = take(7)
            if rel:
                x += cur[0]; y += cur[1]
            cur = (x, y)
            out.append("A %s %s %s %d %d %s"
                       % (fmt(rx), fmt(ry), fmt(rot), laf, sf, pt(x, y)))
        elif c == "Z":
            cur = start
            out.append("Z")
        else:
            raise ValueError("unsupported path command: %r" % cmd)

    return " ".join(out)


# --------------------------------------------------------------------- colors

def parse_color(s):
    """'244 67 54 255' -> ('#f44336', 1.0). Defaults to black on bad input."""
    try:
        r, g, b, a = (int(float(v)) for v in str(s).split()[:4])
    except (ValueError, TypeError):
        return "#000000", 1.0
    alpha = max(0.0, min(1.0, a / 255.0)) if a is not None else 1.0
    return "#%02x%02x%02x" % (r, g, b), alpha


def fmt(v):
    s = ("%.5f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def svg_matrix(m):
    return "matrix(%s)" % " ".join(fmt(v) for v in m)


# ---------------------------------------------------------------- scene graph

def load_lac(path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    model = json.loads(z.read("2D/2dmodel.json"))

    proc_map = {}
    if "Metadata2D/project_settings.json" in names:
        settings = json.loads(z.read("Metadata2D/project_settings.json"))

        def walk(node):
            if isinstance(node, dict):
                if "obj_id" in node and "process_type" in node:
                    proc_map[node["obj_id"]] = node["process_type"]
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(settings)

    entry = {}
    if "2D/entry.json" in names:
        try:
            entry = json.loads(z.read("2D/entry.json"))
        except ValueError:
            entry = {}

    return z, model, proc_map, entry


def normalize_path_data(d):
    """
    .lac path_data stores each cubic Bezier as
    'C end_x end_y c1_x c1_y c2_x c2_y' (endpoint first), while SVG
    expects 'C c1_x c1_y c2_x c2_y end_x end_y'. Reorder the values of
    every C/c segment (including implicit repetitions); all other
    commands pass through unchanged.
    """
    if not d:
        return d
    tokens = [t.group(1) or t.group(2) for t in re.finditer(
        r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)",
        d)]

    def is_cmd(tok):
        return len(tok) == 1 and tok.isalpha()

    out = []
    i, n = 0, len(tokens)
    while i < n:
        cmd = tokens[i]
        i += 1
        j = i
        while j < n and not is_cmd(tokens[j]):
            j += 1
        vals = tokens[i:j]
        out.append(cmd)
        if cmd in ("C", "c"):
            k = 0
            while k + 6 <= len(vals):
                out.extend(vals[k + 2:k + 6])  # control points 1, 2
                out.extend(vals[k:k + 2])     # endpoint
                k += 6
            out.extend(vals[k:])
        else:
            out.extend(vals)
        i = j
    return " ".join(out)


def build_object_map(model):
    objs = {}
    for canvas in model.get("canvas_list", []):
        for o in canvas.get("obj_list", []):
            if o.get("path_data"):
                o["path_data"] = normalize_path_data(o["path_data"])
            objs[o.get("obj_id")] = o
    return objs


def plate_layouts(model, settings_json):
    """Yield (name, components) for each making plate in project_settings."""
    for cs in settings_json.get("canvas_settings", []):
        for batch in cs.get("making_batch_list", []):
            for plate in batch.get("making_plate_list", []):
                label = "plate%d" % plate.get("index", 1)
                yield label, plate.get("components", [])


def walk_components(objs, comps, matrix, visit):
    """Depth-first walk of the scene graph, accumulating transforms."""
    for comp in comps or []:
        obj = objs.get(comp.get("obj_id"))
        if obj is None:
            continue
        m = mat_mul(matrix, parse_transform(comp.get("transform")))
        visit(obj, m)
        if obj.get("type") == "AttachedGroup":
            walk_components(objs, obj.get("components", []), m, visit)


# ------------------------------------------------------------------ SVG write

def style_for(obj, proc, stroke_width):
    color, alpha = parse_color(obj.get("color"))
    opacity = fmt(alpha) if alpha < 1.0 else None
    if proc == "LaserFillEngrave":
        parts = ["fill:%s" % color, "stroke:none", "fill-rule:evenodd"]
        if opacity:
            parts.append("fill-opacity:%s" % opacity)
    else:
        # LaserLineCut / LaserLineEngrave / unknown -> outline.
        # Match Bambu Studio's SVG export: square caps, bevel joins
        # (default miter joins produce spikes on sharp corners).
        # stroke_width == 0 -> Studio look: 1px constant on screen
        # (vector-effect="non-scaling-stroke"), never scaled by zoom.
        sw = 1 if stroke_width == 0 else stroke_width
        parts = ["fill:none", "stroke:%s" % color,
                 "stroke-width:%s" % fmt(sw),
                 "stroke-linecap:square", "stroke-linejoin:bevel"]
        if opacity:
            parts.append("stroke-opacity:%s" % opacity)
    return ";".join(parts)


def path_attrs_extra(obj, stroke_width):
    """Suite-style constant-width stroke: 1px on screen regardless of zoom
    or transform scale (vector-effect="non-scaling-stroke"). Only used when
    the caller asked for the Suite look (stroke_width == 0)."""
    if stroke_width == 0:
        return ' vector-effect="non-scaling-stroke"'
    return ""


def path_d(obj):
    # Emit path_data verbatim. Bambu Studio's own SVG export never closes a
    # path that lacks an explicit Z, even when is_closed is true (start/end
    # gaps up to a few mm are left open). Adding Z would draw a spurious
    # chord across the gap.
    return (obj.get("path_data") or "").strip()


def emit_svg(objs, comps, proc_map, title, stroke_width, margin):
    """Build the SVG document string for one layout (list of components)."""
    bboxes = []

    def measure(obj, m):
        if "path_data" in obj and obj.get("path_data"):
            bb = path_bbox(obj["path_data"], m)
            if bb:
                bboxes.append(bb)

    walk_components(objs, comps, IDENTITY, measure)
    if not bboxes:
        return None

    x0 = min(b[0] for b in bboxes) - margin
    y0 = min(b[1] for b in bboxes) - margin
    x1 = max(b[2] for b in bboxes) + margin
    y1 = max(b[3] for b in bboxes) + margin
    w, h = x1 - x0, y1 - y0

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="%smm" height="%smm" viewBox="%s %s %s %s">'
        % (fmt(w), fmt(h), fmt(x0), fmt(y0), fmt(w), fmt(h))
    )
    if title:
        out.append("  <title>%s</title>" % re.sub(r"[<>&]", "", str(title)))

    def emit(obj, m, indent):
        pad = "  " * indent
        oid = obj.get("obj_id")
        name = obj.get("name") or ""
        attrs = 'id="obj-%s"' % oid
        if name:
            attrs += ' inkscape:label="%s"' % re.sub(r"[\"<>&]", "", name)
        if obj.get("type") == "AttachedGroup":
            out.append('%s<g %s>' % (pad, attrs))
            for comp in obj.get("components", []):
                child = objs.get(comp.get("obj_id"))
                if child is None:
                    continue
                cm = mat_mul(m, parse_transform(comp.get("transform")))
                emit(child, cm, indent + 1)
            out.append("%s</g>" % pad)
        elif "path_data" in obj and obj.get("path_data"):
            proc = proc_map.get(oid, "")
            d = bake_path(path_d(obj), m)
            style = style_for(obj, proc, stroke_width)
            extra = ' data-process="%s"' % proc if proc else ""
            out.append(
                '%s<path %s d="%s" style="%s"%s%s/>'
                % (pad, attrs, d, style, path_attrs_extra(obj, stroke_width), extra)
            )

    for comp in comps or []:
        obj = objs.get(comp.get("obj_id"))
        if obj is None:
            continue
        m = parse_transform(comp.get("transform"))
        emit(obj, m, 1)

    out.append("</svg>")
    return "\n".join(out)


# ----------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert Bambu Studio .lac laser projects to SVG.")
    ap.add_argument("input", help="input .lac file")
    ap.add_argument("output", nargs="?", help="output SVG (default: <input>.svg)")
    ap.add_argument("--layout", choices=["design", "plate"], default="design",
                    help="'design' = 2D canvas arrangement (default); "
                         "'plate' = making-plate arrangement from project settings")
    ap.add_argument("--stroke-width", type=float, default=0.0,
                    help="stroke width in mm for cut/engrave lines "
                         "(default 0 = Bambu Studio look: 1px constant "
                         "on screen, not scaled by zoom)")
    ap.add_argument("--margin", type=float, default=2.0,
                    help="viewBox margin in mm (default 2)")
    args = ap.parse_args(argv)

    z, model, proc_map, entry = load_lac(args.input)
    objs = build_object_map(model)
    if not objs:
        sys.exit("error: no objects found in 2D/2dmodel.json")

    base_out = args.output or os.path.splitext(args.input)[0]
    if os.path.isdir(base_out):
        base_out = os.path.join(base_out, os.path.basename(args.input) + ".svg")

    title = entry.get("Title", "")

    if args.layout == "design":
        layouts = []
        for canvas in model.get("canvas_list", []):
            comps = canvas.get("components", [])
            if comps:
                layouts.append(("canvas%s" % canvas.get("index", 1), comps))
    else:
        settings = json.loads(z.read("Metadata2D/project_settings.json"))
        layouts = list(plate_layouts(model, settings))

    if not layouts:
        sys.exit("error: no components found for layout %r" % args.layout)

    written = []
    multi = len(layouts) > 1
    for label, comps in layouts:
        svg = emit_svg(objs, comps, proc_map, title,
                       args.stroke_width, args.margin)
        if svg is None:
            print("warning: %s has no geometry, skipped" % label)
            continue
        out_path = base_out if not multi else "%s_%s.svg" % (
            os.path.splitext(base_out)[0], label)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)
        written.append(out_path)
        print("wrote %s (%d objects)" % (out_path, svg.count("<path")))

    if not written:
        sys.exit("error: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
