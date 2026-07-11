# Plan-First Edit Mode and Marker Sizing (Design)

Design notes for the edit-mode UX rework and the icon/background sizing controls.

Status: shipped. Migration: `0017`.

## Why

The detail view had grown a stack of controls above the SVG, so the rendered plan
— the thing operators come to see — landed last and you scrolled past a wall of
chrome to reach it. Two related needs: lead with the plan, and let markers be
sized (both a shared base size, and per-marker overrides).

## Layout

`templates/.../inc/floorplan_svg.html` puts the canvas first inside a
`.floor-plan-stage`; the canvas is a fixed-height hero and the injected SVG scales
to fit. A floating toolbar (`floorplan-tools.css`) holds an **Edit** toggle, the
Layers button, and zoom controls. The editing tools (mode toggle, place picker,
import, and the sizing sliders) live in a drawer revealed by the gear
(`floorplan-tools.js`), so View mode is uncluttered.

**Hard constraint:** the existing JS (`floorplan.js`, `floorplan_editing.js`,
`floorplan-import.js`, `floorplan-layers.js`) binds controls **by id** at
`DOMContentLoaded` and self-mounts on the async-injected SVG. The restructure
reparents/restyles those controls but keeps every id, so nothing rebinds.

The duplicate legend was a bug: the HTML mirror of the in-SVG legend (an
accessibility aid) had lost its `sr-only` class and rendered visibly. It is
`sr-only` again, leaving one visible (in-SVG) legend.

## Sizing model

Marker size is **decoupled from the grid footprint**. In `svg.py`, a marker's
on-screen box is:

```
clamp(MARKER_BASE * plan.icon_scale * tile.icon_scale, MARKER_MIN, MARKER_MAX)
```

computed once in `_marker_size(tile)` and reused by both `_draw_freeform_tile`
and `_drawing_extents`. Because the box no longer derives from `width`/`height`,
every marker matches at a given scale regardless of footprint (the "matching base
sizes" goal), and the glyph is a fixed fraction of the box.

- `FloorPlan.icon_scale` — global, drives the **Icon size** slider.
- `FloorPlanTile.icon_scale` — per-marker, driven by a corner drag handle (or
  `+` / `-` keys) in Place mode.

Both ride the existing PATCH fast-paths: `icon_scale` was added to
`CALIBRATION_FIELDS` (plan) and `TILE_GEOMETRY_FIELDS` (tile) in
`api/serializers.py`, so a slider or resize gesture persists without full
revalidation, exactly like blueprint calibration and marker drags. A **Blueprint
scale** slider reuses the calibration channel (`bg_width`/`bg_height` about
center). Each freeform marker also carries `data-icon-scale` / `data-marker-size`
so the client can draw the resize handle without knowing the base constant.
