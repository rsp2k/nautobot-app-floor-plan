# Marker-Visibility Layers (Design)

Design notes for the layers feature: named, reusable groups of placed-object
markers that can be shown, hidden, and dimmed on a rendered plan without editing
the plan.

Status: shipped. Migration: `0016`.

## Why

A plan can carry racks, devices, cameras, access points, and power gear at once,
with no way to focus on a subset. Layers add that focus while keeping Status
color authoritative — a layer may hide or dim its members, never recolor them.

## Model

- `FloorPlanLayer` (`PrimaryModel`, `models.py`): `name`, `floor_plan` (FK,
  null = global), `source_content_types` / `source_tags` /
  `source_dynamic_groups` (M2M rule sources), `color` (panel swatch only),
  `opacity`, `default_visible`, `display_order`.
- `FloorPlanLayerObject` (through model): the hand-picked static set — a generic
  `(content_type, object_id)` pair per row, mirroring `FloorPlanTile`'s placement
  pair, with a uniqueness constraint on `(layer, content_type, object_id)`.

A layer's membership is the **union** of its rule sources and its static set. A
plan-scoped layer's static objects are validated against the plan's Location
(reusing `registry.resolve_location`).

## Resolution

`layers.resolve_layers(plan, placed_objects)` returns
`{(label_lower, pk): [layer_id, …]}`, computed **once per render**. Each rule
source is turned into a PK set a single time and intersected with the plan's
placed objects:

- content types → placed objects of that type,
- tags → one `TaggedItem` query over the placed PKs,
- dynamic groups → `dg.members.values_list("pk")` **once per group** (filter-based
  groups evaluate their whole FilterSet on `.members` access, so this must not be
  per-marker), intersected with the placed PKs.

With no layers defined, resolution short-circuits to an empty dict and adds no
overhead. A query-count test (`tests/test_layers.py`) guards the resolve-once
property.

## Render + client

`svg.py` calls the resolver in `render()` and stamps each freeform marker `<g>`
with `data-content-type` and, when it belongs to any layer, `data-layers`. The
plan's applicable layers are exposed at
`GET /api/plugins/floor-plan/floor-plans/<id>/layers/`.

The client (`floorplan-layers.js`) builds the panel and applies visibility purely
in CSS (`display` / `opacity`) so it can't affect the viewBox-based pan/zoom.
Precedence: **a marker in any named layer is governed only by its layers**; a
marker in no named layer follows its content-type toggle. So a layer's checkbox
hides its own members directly, while type toggles sweep up the ungrouped
remainder — and isolation ("show only cameras") is turning the type toggles off
and leaving one layer on. Visibility is OR across a marker's layers; opacity is
the strongest (lowest) dim among the groups showing it.

## CRUD

`FloorPlanLayer` has the full stack (form / filter / table / `NautobotUIViewSet` /
REST viewset / nav item), mirroring `FloorPlanObjectType`. `FloorPlanLayerObject`
is managed through the REST API, not its own view.
