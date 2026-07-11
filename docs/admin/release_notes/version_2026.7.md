# v2026.7 Release Notes (Freeform fork)

This document describes the changes introduced by the `nautobot-floor-plan-freeform` fork on top of the upstream v3.0 line. The fork uses date-based (CalVer) versioning of the form `YYYY.MM.DD`.

## Release Overview

This release adds blueprint-backed freeform placement and a runtime-configurable placement system while keeping the existing grid workflow fully intact. Grid mode remains the default, and both modes are supported on the same plan.

## Added

### Freeform placement and blueprint backgrounds

- Added a **Placement Mode** on Floor Plan (`Grid` or `Freeform`). Grid remains the default.
- Added an optional **blueprint background image** per Floor Plan, rendered behind the grid.
- Added **calibration** of the background image: drag to move, drag handles to scale, and a rotate handle to align the drawing to the grid.
- Added **drag-to-place** and **drag-to-move** of object markers directly on the rendered SVG in freeform mode. Positions persist through the API on drop.
- Added conversion of an existing grid plan to freeform without recreating it.

### Generic, extensible object placement

- Any object type can now be placed, not only Devices, Power Panels, Power Feeds, and Racks. **Locations** are placeable out of the box (campus to building to floor drill-down).
- Added a push-based **placement registry** so other apps can register their own object types as placeable, each with an icon, color, legend order, and a resolver that derives the object's Location, without the floor-plan app importing the owning app.
- Added **per-type marker glyphs, colors, and an on-plan legend** so different object types are distinguishable at a glance.

### Runtime-configurable types from the web UI

- Added the **Floor Plan Object Type** model with full CRUD UI and REST API. Admins can define placeable types, choose a built-in glyph or supply custom SVG paths, set a color and legend order, and override the built-in types, all without a code change.
- Configuration merges into the placement registry and refreshes across workers via a cache-version check, so runtime edits take effect without a restart.

### Import a blueprint from a PDF

- Added **Import from PDF** on the Floor Plan: upload an architectural PDF, and a background **Nautobot Job** (`Render Blueprint PDF Pages`) rasterizes each page to an image. Pick a page from a thumbnail grid, crop the drawing region, rotate it if needed, and it becomes the plan's background image.
- Added `FloorPlan.source_document` and a `BlueprintPage` model (rendered pages persist, so a page can be re-picked or re-cropped without re-uploading), plus `import-pdf` / `pages` / `extract` REST API actions.
- Rendering uses **pypdfium2** (BSD-3-Clause / Apache-2.0) under caps on page count, file size, and resolution, and runs on the platform's job queue via the built-in Nautobot Job (no direct queue dependency in the app).

### Marker-visibility layers

- Added a **Layers** panel on the rendered plan to show, hide, and dim placed-object markers without editing the plan. Markers can be filtered by content type, and by named **Floor Plan Layer** groups.
- Added the **Floor Plan Layer** model with full CRUD UI and REST API. A layer's membership is the union of rule sources — content types, tags, and dynamic groups — plus a hand-picked static set, and a layer is either global (applies to every plan) or scoped to one plan.
- Named layers take precedence over the content-type toggles: a layer's checkbox hides its own members directly, while the type toggles govern only markers that belong to no named layer. Membership is resolved once per render (dynamic groups are evaluated a single time, not per marker).
- Styling is visibility and opacity only. A marker's Status color always wins; a layer can hide or dim its members but never recolors them.

### Plan-first edit mode and marker sizing

- Reworked the Floor Plan detail view so the rendered canvas is the hero and fills the view. All controls moved into a compact floating toolbar, and the editing tools (View/Place/Calibrate, Import from PDF, blueprint and sizing controls) collapse behind an **Edit** toggle so View mode stays uncluttered.
- Added a global **Icon size** slider that sets one base size for every marker on a plan, so icons match regardless of their grid footprint. Marker size is now `FloorPlan.icon_scale` × `FloorPlanTile.icon_scale` × a base size, decoupled from the tile footprint.
- Added **per-marker resizing**: select a marker in Place mode and drag its corner handle (or press `+` / `-`) to scale just that marker. Both the global and per-marker scales persist through the existing drag/calibrate REST fast-paths.
- Added a **Blueprint scale** slider alongside blueprint opacity for quickly sizing the background image.
- Fixed a duplicated marker legend: the accessibility mirror of the in-SVG legend is now screen-reader-only as intended, leaving a single visible legend.

## Housekeeping

- Published to PyPI as `nautobot-floor-plan-freeform` under CalVer.
- Added `pypdfium2` and `Pillow` as dependencies for the PDF import pipeline.
- Added migrations `0016` (Floor Plan Layer) and `0017` (`icon_scale` on Floor Plan and Floor Plan Tile).
- Added design notes under the Developer Guide covering the freeform/blueprint model, generalized object placement, hierarchical Location placement, the (now shipped) PDF blueprint import, marker-visibility layers, and the edit-mode UX / marker sizing.
