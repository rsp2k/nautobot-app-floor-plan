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

## Housekeeping

- Published to PyPI as `nautobot-floor-plan-freeform` under CalVer.
- Added `pypdfium2` and `Pillow` as dependencies for the PDF import pipeline.
- Added design notes under the Developer Guide covering the freeform/blueprint model, generalized object placement, hierarchical Location placement, and the (now shipped) PDF blueprint import.
