# The `.lac` file format (reverse-engineered)

This document describes the parts of the Bambu Suite `.lac` project format
that lac2svg reads. It was reverse-engineered from real files produced by
Bambu Studio; it is incomplete (G-code, thumbnails and 3D data are not
covered) but sufficient to extract the 2D vector geometry faithfully.

> `.lac` is not an official public format. Details may change between
> Bambu Studio versions.

## Package layout

A `.lac` file is an OPC package — a ZIP with content types and
relationships:

```
project.lac (ZIP)
├── [Content_Types].xml
├── _rels/.rels
├── 2D/
│   ├── entry.json              project metadata
│   ├── 2dmodel.json            2D scene graph (the vector geometry)
│   └── design_thumbnail.png    preview image
└── Metadata2D/
    └── project_settings.json   process types, making plates, materials
```

`[Content_Types].xml` declares default content types for `rels`, `model`,
`png` and `gcode` extensions. `_rels/.rels` points at the design thumbnail.

## `2D/entry.json` — project metadata

```json
{
  "Application": "Bambu Suite",
  "Cover": "",
  "CreationDate": "2026-05-08",
  "Description": "…",
  "Title": "My Project",
  "FileVersion": "1.0",
  "LicenseTerms": "…"
}
```

Only `Title` is used by the converter (it becomes the SVG `<title>`).

## `2D/2dmodel.json` — the scene graph

Top level:

```json
{
  "Application": "Bambu Suite",
  "FileVersion": "1.0",
  "canvas_list": [ …canvases… ]
}
```

Each **canvas** holds the objects and the root placements:

```json
{
  "components": [ { "obj_id": 121, "transform": "1 0 0 1 28.7652 52.1693" } ],
  "index": 1,
  "name": "",
  "obj_list": [ …objects… ],
  "type_count": {}
}
```

- `components` — root placements: which object sits where on the canvas.
- `obj_list` — the object definitions (flat; referenced by `obj_id`).

### PathObject

A leaf node with actual geometry:

```json
{
  "color": "244 67 54 255",
  "flags": ["FreeAspectRatio"],
  "is_closed": true,
  "name": "Outline",
  "obj_id": 16,
  "path_data": "M 0 0 L 10 0 …",
  "type": "PathObject"
}
```

- `color` — `"R G B A"` with 0–255 components.
- `path_data` — SVG path syntax, **with one crucial difference** (below).
- `is_closed` — whether the path is meant to be closed. Note: Bambu
  Studio's own SVG export does **not** add `Z` for closed paths that lack
  an explicit `Z` (start/end gaps up to a few mm are left open), so lac2svg
  emits `path_data` verbatim and never adds `Z` either.

### AttachedGroup

A group node nesting other objects:

```json
{
  "components": [ { "obj_id": 3, "transform": "0.866 -0.5 0.5 0.866 0 0" } ],
  "flags": [],
  "name": "My Group",
  "obj_id": 10,
  "type": "AttachedGroup"
}
```

Groups can nest groups. Transforms compose like nested SVG `<g>` elements
(child transform applied first).

### Transforms

Stored as SVG-style matrix strings `"a b c d e f"`:

```
| a c e |
| b d f |
| 0 0 1 |
```

## ⚠️ The Bezier gotcha

`.lac` `path_data` uses SVG path syntax, **but** cubic Bezier segments store
the **endpoint first**:

```
.lac:  C end_x end_y c1_x c1_y c2_x c2_y
SVG:   C c1_x c1_y c2_x c2_y end_x end_y
```

This applies to implicit repetitions too. Any converter that naively
copies `path_data` into an SVG will render every curve wrong. lac2svg
reorders the six values of every `C`/`c` segment
(`normalize_path_data`).

## `Metadata2D/project_settings.json` — processes and plates

Top level:

```json
{
  "canvas_settings": [ … ],
  "project_settings": {}
}
```

Each canvas settings entry:

```json
{
  "index": 1,
  "making_batch_list": [ …batches… ],
  "object_settings": [ … ]
}
```

### Process types

`object_settings` maps each object to its laser process:

```json
{ "obj_id": 1, "process_type": "LaserLineCut" }
```

| Process type | Meaning | SVG styling |
|---|---|---|
| `LaserLineCut` | vector cut | outline stroke |
| `LaserLineEngrave` | vector engraving | outline stroke |
| `LaserFillEngrave` | filled engraving | solid fill, `fill-rule:evenodd` |

lac2svg attaches the process to each path as a `data-process` attribute.

### Making plates

Each batch contains `making_plate_list`; each plate has its own
`components` (the auto-arranged placement for cutting):

```json
{
  "components": [ { "obj_id": 1, "transform": "1 0 0 1 50 50" } ],
  "index": 1,
  "name": "Plate 1",
  "obj_id": 100,
  "plate_mirror": false,
  "plate_settings": { "classVersion": 3, "laser_cut_colors": "#F44336" }
}
```

`--layout plate` uses these placements instead of the design canvas ones.

### Materials

Batches also carry material info (not used by the converter):

```json
{
  "material_id": "B-YA-A-A-X0",
  "material_name": "3mm Basswood Plywood",
  "batch_settings": { "material_thickness": 3, "processing_mode": "PLANE" }
}
```

## Coordinate system

The 2D canvas uses **Y-down** millimeters — the same as SVG's native user
space — so coordinates map 1:1 with no flipping. `width`/`height` in the
output SVG are real millimeters.
