# Blueprint Floor Plans and Freeform Placement (Design)

Working design doc for the fork's headline feature: turn the abstract tile
grid into a real, blueprint-backed floor plan where racks, devices, power
panels and power feeds are placed at freeform coordinates on top of a scanned
or CAD blueprint image, aligned with a drag-to-calibrate interaction.

Status: proposal, pending review. Branch: `feature/blueprint-freeform-placement`.

## Why

The stock app renders a discrete tile grid. Operators want to see equipment on
the actual building drawing, positioned where it physically sits, not snapped to
a coarse grid. This also gives every other data source in the wider Nautobot
estate (SSOT discovery from Cisco, Fortinet, FMC, Kea, MS-DHCP, the scanner, L2
trace, the DNS/DHCP/firewall models) a real place on a map, and lets the MCP
server answer "where is this device" with a physical location.

## Current architecture (as-is)

One server-rendered SVG. `FloorPlanSVG.render()` in `svg.py` draws a frame, then
status underlays, then grid lines and labels, then object tiles. Every position
is computed as `coord * GRID_SIZE + GRID_OFFSET`. The API action
`/api/plugins/floor-plan/floor-plans/{pk}/svg/` returns it as `image/svg+xml`.
`static/.../js/floorplan.js` fetches that markup, injects it into a div, and
adds GSAP pan/zoom plus Tippy tooltips by manipulating the SVG `viewBox`.

`FloorPlanTile` stores integer grid origins (`x_origin`, `y_origin`), has a
`unique_together` on `(floor_plan, x_origin, y_origin, allocation_type)`, and
validates overlaps by grid cell.

## Target architecture (to-be)

### Coordinate model

Objects store a normalized position in `[0, 1]` relative to the blueprint image
extent, rather than a grid cell. Normalized coordinates are independent of the
image pixel size and survive re-calibration or replacing the blueprint with a
higher-resolution scan. Real-world distances, when needed, come from the image
aspect ratio plus a known scale.

### Data model changes

`FloorPlan` gains:

- `background_image` (`ImageField`, nullable): the blueprint.
- `background_image_width`, `background_image_height` (px, auto-filled from
  Pillow on save): needed for aspect-correct rendering and calibration math.
- Calibration transform, stored as the placement rectangle of the blueprint in
  SVG user units: `bg_x`, `bg_y`, `bg_width`, `bg_height`, plus `bg_rotation`
  (degrees). Drag handles update these; persisted through the API.
- `background_opacity` (0 to 100, default 100).
- `placement_mode` (`grid` or `freeform`, default `grid` so existing plans keep
  their exact current behavior on upgrade; new plans may choose either).
- `show_grid` (bool): lets the grid fade out so the blueprint reads cleanly.

`FloorPlanTile` gains (all nullable, additive, non-destructive):

- `pos_x`, `pos_y` (float, normalized 0 to 1): freeform position.
- `width`, `height` (float, normalized): object footprint. Optional; defaults to
  a fixed marker size when null.
- `rotation` (degrees): object orientation, superseding the four-way
  `object_orientation` for freeform mode.

The existing `x_origin` / `y_origin` become nullable so grid and freeform tiles
coexist permanently. Postgres treats NULLs as distinct in a unique index, so
multiple freeform tiles (null grid coords) do not collide on the existing
`unique_together`.

Both modes are first-class and supported side by side. Grid is the default so
plans upgrading from the stock app keep working unchanged. A plan can be
switched to freeform, and its existing grid tiles converted in place.

### Grid to freeform conversion

Converting a plan seeds each tile's `pos_x` / `pos_y` from its grid origin,
normalized against the grid extent, so the freeform layout starts as a faithful
copy of the grid the operator already built:

```
pos_x = (x_origin - x_origin_seed + 0.5 * x_size) / x_size_total
pos_y = (y_origin - y_origin_seed + 0.5 * y_size) / y_size_total
width  = x_size / x_size_total
height = y_size / y_size_total
```

Exposed as a `convert_to_freeform` action (idempotent, only fills tiles whose
`pos_x` / `pos_y` are still null) plus a button in the UI. Grid coordinates are
retained, so a plan can be switched back to grid mode without data loss.

### Rendering (svg.py)

- `_draw_background_image(drawing)` called first in `render()`, embedding the
  image as a base64 data URI at the calibration rectangle with the configured
  opacity and rotation. Base64 keeps the SVG self-contained so the "Save SVG"
  download and cross-context fetch both work.
- Freeform tiles render at `pos * blueprint_rect + offset`, drawn as rack/device
  rectangles with a rotation transform, reusing the existing object styling and
  tooltip data.
- `show_grid` gates the grid lines and labels.
- The SVG `viewBox` grows to encompass the blueprint plus margins.

### Interactive drag (the "full" ask), floorplan.js and endpoints

Two drag interactions layered onto the existing GSAP viewer with a small mode
state machine (`view`, `place`, `calibrate`):

1. Place or move objects. Drag an object marker to set `pos_x` / `pos_y` (and
   `rotation`), persisted with a debounced PATCH to
   `/api/.../floor-plan-tiles/{pk}/`.
2. Calibrate the blueprint. A calibrate mode shows corner handles to move,
   scale and rotate the background image, persisting the transform to the
   `FloorPlan` (either PATCH of the new writable fields or a dedicated action
   endpoint).

Serializers add the new fields so PATCH works; RBAC rides on Nautobot's existing
object permissions. Saves are debounced with optimistic UI.

### Forms and UI

- `FloorPlanForm`: background image upload, opacity slider, `placement_mode`,
  `show_grid`.
- Detail panel: calibration controls and a "Calibrate blueprint" button that
  enters calibrate mode.
- Tile add/edit: freeform coordinate entry as a keyboard fallback to dragging.

### Migration

`0011_add_background_and_freeform`: AddField for the nullable columns above. No
backfill. `x_origin` / `y_origin` altered to nullable. The existing
`unique_together` stays and only bites grid-mode tiles.

## Phases

- P0 backend, non-destructive, verifiable by unit tests and inspection: model
  fields, migration, serializers, SVG background embed, SVG freeform rendering.
  No JS yet.
- P1 drag-to-place objects: `pos_x` / `pos_y` persist from the viewer.
- P2 drag-to-calibrate the blueprint: handles plus transform persistence.
- P3 polish: blueprint-mode styling (faint grid, translucent tiles, subtle
  shadows), optional full grid removal, docs, towncrier fragment, integration
  tests.

## Testing

- `test_models`: new field validation, freeform bounds (0 to 1).
- `test_svg`: background embed present, freeform tile positioned correctly.
- `test_api`: PATCH of `pos_x` / `pos_y` and the calibration transform.
- `test_integration`: drag interactions via Selenium/Splinter (P2, P3).

## Open decisions

1. Coordinate storage: normalized `[0, 1]` (recommended) vs real-world units vs
   SVG pixels.
2. Object footprint: fixed marker size vs a scaled real-world footprint derived
   from rack dimensions.
3. Rotation support in v1.

Resolved: grid and freeform are both permanent, first-class modes. Grid stays
the default for upgrade safety; freeform is opt-in per plan with a
grid-to-freeform conversion that seeds positions from the existing layout.
