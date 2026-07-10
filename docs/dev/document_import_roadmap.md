# Import blueprints from PDF / CAD (crop-to-background)

Status: **v1 implemented** (PDF import shipped 2026-07); DXF and the pluggable importer
registry remain planned. This page documents the shipped design and what is still ahead.

A background blueprint can be uploaded directly as a raster image, or **imported from a PDF**:
upload a PDF to a Floor Plan, a background Nautobot Job rasterizes each page, and the user
picks a page and **crops** the region they want as the plan's `background_image`. The existing
calibration (`bg_x/bg_y/bg_width/bg_height`) and freeform placement then work unchanged.

## Why

Real floor plans arrive as multi-page PDF sets (architectural drawings), not pre-cropped PNGs.
Ingesting them directly removes a manual export/crop step in an external tool and keeps the
source document attached to the plan for re-picking or re-cropping later.

## What shipped (v1, PDF)

1. **Keep the source, derive the raster.** `FloorPlan.source_document` (FileField) holds the
   uploaded PDF; `background_image` stays the derived PNG the SVG embeds. A user can re-pick a
   page or re-crop later without re-uploading (`models.py`).

2. **Rendered pages persist.** A `BlueprintPage` model (FK to FloorPlan, `page_number`, `image`,
   `thumbnail`) holds each rendered page so the picker is server-backed and re-pickable
   (`models.py`, migration `0015_add_blueprint_import.py`).

3. **Server-side rasterization in a background Job.** `RenderBlueprintPages`
   (`jobs.py`, built on `nautobot.apps.jobs`) opens `source_document`, clears stale pages, and
   renders each page to a full image + thumbnail under caps (max pages, per-page pixels, DPI,
   upload size). Using a **built-in Nautobot Job** means it runs on whatever queue the platform
   provides (Celery on stock Nautobot, the Procrastinate fork in this deployment) with **no
   direct queue dependency** in the app.
   - **Library: `pypdfium2`** (Google PDFium bindings, BSD-3-Clause / Apache-2.0), NOT PyMuPDF.
     PyMuPDF is AGPL-3.0 and would impose copyleft on this Apache-2.0 package. `pypdfium2` renders
     a page with `page.render(scale=dpi/72).to_pil()`, ships wheels, needs no system deps.
   - **Render the page, don't extract embedded images.** Print-to-PDF sets embed the firm logo,
     not the drawing; rendering the page is the correct default.

4. **Pick + crop UX.** New actions on `FloorPlanViewSet` (`api/views.py`):
   - `POST .../import-pdf/` — save the PDF to `source_document` and enqueue the render Job.
   - `GET .../pages/` — list rendered `BlueprintPage`s (page number, thumbnail + image URLs).
   - `POST .../extract/` — `{page_number, crop_box, rotation}` → **crop then rotate** with Pillow
     → set `background_image`, reset `bg_*` so the SVG auto-fits the new crop.
   The client (`static/.../js/floorplan-import.js`) is a standalone "Import from PDF" modal: a
   thumbnail page-picker grid, then a crop box over the selected page image with 90° rotate.
   Crop is normalized to the original page; the server crops server-side so huge renders never
   hit the browser. Deliberately decoupled from the calibrate drag layer.

5. **After crop** the current calibrate/opacity/freeform flow is unchanged — this only feeds a
   better `background_image`.

## Dependencies & risks

- Deps: `pypdfium2` (PDF) + `Pillow` (crop/thumbnail); both ship wheels, no system deps.
- **Untrusted-input surface.** PDF parsers are an attack surface. v1 caps page count, upload
  size, render DPI, and per-page pixels, and runs extraction in the worker (Job), not the web
  request.
- Storage: the source doc lives on the normal media backend; only the derived crop is embedded
  (base64) in the SVG.

## Still ahead

- **v2 — DXF (CAD)** via `ezdxf` + a render backend (matplotlib or its SVG backend), ideally
  layer-aware so the user can toggle layers before rasterizing. More involved (units/scale,
  entity coverage).
- **v3 — a pluggable importer registry** mirroring `placement/registry.py`
  (`can_handle`/`list_pages`/`render`), made public so other apps can register new formats
  (`.dwg`, image-only TIFF, SVG) without touching the core.
- OCR-based auto floor-naming (v1 pages have no text layer, so the picker is manual).
