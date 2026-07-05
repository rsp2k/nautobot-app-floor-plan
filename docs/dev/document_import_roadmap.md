# Roadmap: import blueprints from PDF / CAD (crop-to-background)

Status: **planned** (captured 2026-07-03). Today a background blueprint is a raster
image uploaded directly. This adds a source-document pipeline: upload a PDF (or CAD
file), have the server rasterize it, let the user pick a page and **crop** the region
they want, and set that crop as the plan's `background_image`. The existing
calibration (`bg_x/bg_y/bg_width/bg_height`) and freeform placement then work unchanged.

## Why

Real floor plans arrive as multi-page PDFs (architectural sets) or CAD files (`.dxf`),
not pre-cropped PNGs. Making the app ingest those directly removes a manual
export/crop step in an external tool and keeps the source of truth attached to the plan.

## Shape of the feature

1. **Keep the source, derive the raster.** Add `source_document` (FileField) to
   `FloorPlan` alongside the existing `background_image`. The source is the uploaded
   PDF/DXF; `background_image` stays the derived PNG that the SVG embeds. This lets a
   user re-crop or re-pick a page later without re-uploading.

2. **Pluggable document importers (mirror the placement registry).** A small registry
   keyed by extension/mimetype, each importer implementing:
   - `can_handle(filename, mimetype) -> bool`
   - `list_pages(file) -> [PagePreview]`  (page index + a thumbnail data-URI)
   - `render(file, page, dpi, crop_box|None) -> png_bytes`
   This is the same push-based, import-free pattern as `placement/registry.py`, so new
   formats (`.dwg`, image-only TIFF, SVG) drop in without touching the core.

3. **Server-side rasterization.**
   - **PDF** — PyMuPDF (`fitz`): render a page to PNG at a chosen DPI
     (`page.get_pixmap(dpi=...)`). Prefer *rendering the page* (keeps vector line art
     crisp) over extracting embedded raster images, though offer both — some scanned
     blueprints are a single embedded image. No poppler system dep.
   - **DXF (CAD)** — `ezdxf` + its drawing add-on (matplotlib or the native SVG
     backend) to rasterize modelspace, ideally layer-aware so the user can toggle
     layers before rasterizing. More involved (units/scale, entity coverage); ship PDF
     first, DXF second.

4. **Pick + crop UX.** New viewset actions on the FloorPlan API:
   - `POST .../document/` — upload the source file.
   - `GET .../document/pages/` — return page thumbnails (multi-page PDF → one per page).
   - `POST .../document/extract/` — `{page, crop_box, dpi}` → renders + crops → sets
     `background_image`, returns the new plan.
   The crop overlay fits the existing editing layer (`floorplan_editing.js`): reuse the
   calibrate-style handle rectangle to draw the crop box in client space, send box in
   normalized coords, crop server-side (Pillow) so huge renders never hit the browser.

5. **After crop** the current calibrate/opacity/freeform flow is unchanged — this only
   feeds a better `background_image`.

## Dependencies & risks

- New deps: `PyMuPDF` (PDF), `ezdxf` (+ a render backend) for DXF. Both ship wheels.
- **Untrusted-input surface.** PDF/CAD parsers are a real attack surface. Cap page
  count, file size, render DPI, and pixel dimensions; run extraction with a timeout;
  consider doing it in the worker (Celery) rather than the web request for big files.
- Storage: keep source docs out of the SVG (embed only the derived crop as base64, as
  today). Source files can be large; store on the normal media backend.

## Suggested slicing

- **v1** PDF only: upload → page thumbnails → pick page → crop → background. Worker-side
  render for files over N pages/MB.
- **v2** DXF via `ezdxf`, layer toggle before rasterize.
- **v3** the importer registry made public so other apps can register formats.
