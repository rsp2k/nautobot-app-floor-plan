# Blueprint + Freeform Placement — Locked Implementation Spec

Produced by the `floorplan-blueprint-design` ultracode workflow (8 agents: 4 analyze, 1 synthesize, 3 adversarial critique). This is the source of truth for the build; §0 coordinate contract is binding across `svg.py`, `convert_to_freeform`, and `floorplan.js`.

I have enough grounding. Here is the merged spec.

---

# Blueprint + Freeform Placement — Unified Implementation Spec

Branch `feature/blueprint-freeform-placement`. P0 model fields already exist (verified in `models.py`): `FloorPlan.{placement_mode,show_grid,background_image,background_image_width/height,background_opacity,bg_x,bg_y,bg_width,bg_height,bg_rotation}`, `FloorPlanTile.{pos_x,pos_y,width,height,rotation}` (nullable normalized floats; `rotation` default 0). `PlacementModeChoices.{GRID,FREEFORM}` exist. Latest migration is `0011_add_background_and_freeform.py`. `x_origin/y_origin` are still `PositiveSmallIntegerField` **NOT NULL** with `unique_together = [floor_plan, x_origin, y_origin, allocation_type]` live.

## 0. Coordinate Contract (resolves the biggest cross-analysis conflict — read first)

The four analyses disagreed on (a) whether `pos_x/pos_y` anchors top-left or center, (b) whether freeform coords normalize against the blueprint calibration rect or the grid extent, and (c) whether `bg_*` are stored in SVG user units or normalized. These are settled once, here, and every wave inherits it.

**One rectangle, one normalization basis: the CONTENT RECT.**
The content rect is the grid drawing area in SVG user units:
```
content_x = GRID_OFFSET            # 26
content_y = GRID_OFFSET            # 26
content_w = x_size * GRID_SIZE_X
content_h = y_size * GRID_SIZE_Y
```
It is always defined (independent of whether a blueprint exists) and is what conversion-from-grid naturally produces (grid cells → grid extent). The server publishes it as `data-content-x/-y/-w/-h` on the root `<svg>`.

**Objects (`pos_x/pos_y`) are CENTER-anchored, normalized to the content rect.** `width/height` are the full normalized footprint. Chosen over top-left (svg-render's suggestion) because center-anchoring makes `rotation` pivot about the object's own centroid, matches the design-doc `+0.5*x_size` seed formula, and makes the drag/optimistic transform trivial (`translate(center) rotate(r)` with children drawn relative to origin). This **overrides** svg-render's "top-left" convention lock and its "relative to calibration rect" mapping.
```
centerX = content_x + pos_x * content_w
centerY = content_y + pos_y * content_h
pw = width  * content_w
ph = height * content_h
```

**Blueprint (`bg_x/bg_y/bg_width/bg_height`) is normalized to the SAME content rect** (top-left + size), NOT raw SVG user units. This **overrides** svg-render's and api-persist's "user units" treatment. Rationale: one uniform coordinate space, resolution-independent calibration, and it matches the JS layer that was already built around a normalized content rect. When all four `bg_*` are null → server auto-fits (aspect-correct, letterboxed in the content rect). `bg_rotation` is degrees. Objects and the blueprint live in the same normalized space but are independent: calibrating the blueprint moves the image under the objects; placing objects moves them over the image.

**Help-text fix (Wave B):** the existing P0 help_text ("normalized 0..1 across the blueprint width", `bg_x` "in SVG units") is now wrong. Update to reference the content rect and center-anchor semantics.

**Convention parity is mandatory across `svg.py` (render), `FloorPlan.convert_to_freeform` (seed), and `floorplan.js` (drag).** Any drift = objects render offset from where they were dragged/seeded.

---

## Wave A — SVG rendering of background + freeform tiles

Files: `nautobot_floor_plan/svg.py`, `static/nautobot_floor_plan/css/svg.css`, `static/nautobot_floor_plan/css/dark_svg.css`. Depends only on existing P0 fields. Fully testable via `tests/test_svg.py` with no API/JS present. Grid-mode + no-blueprint output must stay byte-similar to today (upstream regression guard).

### A1. `svg.py` — imports & constants
- Add `import base64, mimetypes, math, json` as needed.
- Add to the existing choices import: `from nautobot_floor_plan.choices import PlacementModeChoices`.
- New class constant `DEFAULT_MARKER_FRAC = 0.04` (fallback normalized footprint when `width/height` null).

### A2. `svg.py` — content rect + background helpers (add methods)
1. `_content_rect()` → `(content_x, content_y, content_w, content_h)` per §0. Wrap as a `cached_property`-style single computation per render.
2. `_background_image_data_uri()` → base64 data URI. Use storage-safe `img.open("rb")/read()/close()` (never `img.path`), `mimetypes.guess_type(img.name)[0] or "image/png"`. Return `None` when no image. Docstring: base64 inflates ~1.33× and is deliberate for self-contained "Save SVG" download.
3. `_background_rect()` → `(bx,by,bw,bh)` in **user units**, derived from the content rect:
   - If all four `bg_*` set: `bx=content_x+bg_x*content_w`, `by=content_y+bg_y*content_h`, `bw=bg_width*content_w`, `bh=bg_height*content_h`. (This is the change vs svg-render: interpret `bg_*` as normalized, not raw.)
   - Else auto-fit: aspect-correct letterbox of `(background_image_width,background_image_height)` inside the content rect; if pixel dims unknown, fill the content rect exactly.

### A3. `svg.py` — `_draw_background_image(drawing)` (add, called FIRST in render)
- No-op if `background_opacity <= 0` or data URI is `None`.
- Build `drawing.image(href=data_uri, insert=(bx,by), size=(bw,bh), class_="background-image", id="blueprint-image")`. svgwrite maps `href`→`xlink:href` (correct, don't "fix"). Also emit `data-bg-x/-y/-width/-height/-rotation` (raw normalized values, for the JS calibrate layer) — set via `image["data-bg-x"]=...` etc.
- `image.stretch()` → `preserveAspectRatio="none"` (safe: auto-fit is aspect-correct, manual is user's explicit choice). Never `.fit()`.
- `image["opacity"] = background_opacity/100.0` (model 0–100 → SVG 0–1).
- If `bg_rotation`: `image.rotate(rot, center=(bx+bw/2, by+bh/2))`.

### A4. `svg.py` — freeform tile drawing
1. Extract `_resolve_tile_object(tile)` → `(obj, dcim_url_type, human_label)` for rack/device/power_panel/power_feed, else `None`. Refactor existing `_draw_object_tile`/`_draw_object_text` to call it (reuse, no behavior change).
2. `_draw_freeform_tile(drawing, tile)`:
   - Guard: skip if `_resolve_tile_object` is None or `pos_x/pos_y` null.
   - Compute `centerX/centerY`, `pw = (width or DEFAULT_MARKER_FRAC)*content_w`, `ph = (height or DEFAULT_MARKER_FRAC)*content_h`.
   - Markup (center-anchored so JS drag can rewrite one transform):
     ```
     <a href=... target="_top" id="{url_type}-{pk}" class="object-tooltip" data-tooltip=...>
       <g class="object" data-tile-id="{pk}"
          data-pos-x data-pos-y data-rotation
          transform="translate(centerX centerY) rotate(rotation)">
         <rect insert=(-pw/2,-ph/2) size=(pw,ph) rx=CORNER_RADIUS class="object" style="fill:#{color}"/>
         <text ...>   # via _draw_freeform_text, centered on (0,0)
       </g>
     </a>
     ```
     Children are drawn relative to the group origin (= object center), so `rotate(rotation)` pivots about the centroid and the JS optimistic update only rewrites the `<g>` transform.
   - `_draw_freeform_text(group, tile, obj, label)`: thin adaptation of `_add_text_element`, centered on `(0,0)`, reusing existing `label-text-primary`/`label-text`/`label-text-grid` classes + `fgcolor(...)`. **Reuse, do not invent new CSS classes** — freeform tiles inherit `svg.css`/`dark_svg.css` automatically.
   - `rotation` supersedes `object_orientation` in freeform (ignore orientation bar, or draw it inside the group so it rotates too).

### A5. `svg.py` — viewBox extents & `_setup_drawing`
- `_drawing_extents()` → union of the default frame box `(0,0,default_W,default_H)` and the (possibly rotated) `_background_rect()` corners, padded by `BORDER_WIDTH`, only when a blueprint is present and opacity>0. Negative min origin is legal and expected when a rotated blueprint spills top-left.
- Change `_setup_drawing(self, width, depth, viewbox=None)`: when `viewbox` given, `drawing.viewbox(vx,vy,vw,vh)`; else current `(0,0,width,depth)`. Keep the frame rect at the original grid box size (frames the grid, not the blueprint).
- Emit `data-content-x/-y/-w/-h` on the root `<svg>` (the JS layer's normalization basis).

### A6. `svg.py` — `render()` order & mode gating
```
extents → _setup_drawing(viewbox=extents)
_draw_background_image(drawing)                 # 1. FIRST → behind everything
for tile: _draw_underlay_tiles(...)             # 2. grid-geometry underlays (skip naturally when no grid coords)
if show_grid: _draw_grid(drawing)               # 3. lines + axis labels + "+" add links, gated
for tile:                                        # 4. object tiles
    if freeform and tile.pos_x is not None: _draw_freeform_tile(...)
    else: _draw_tile(...)                        # existing grid path (fallback for un-converted tiles)
```
Gating rules: `show_grid=False` suppresses grid lines/labels/`+` links (all inside `_draw_grid`). `placement_mode=grid` (default) always takes `_draw_tile`. `placement_mode=freeform` uses freeform for tiles with `pos_x` set, falls back to `_draw_tile` for not-yet-seeded tiles so nothing disappears mid-migration. A grid plan with no `background_image` renders exactly as today.

### A7. CSS (`svg.css` + `dark_svg.css`)
- `.background-image { }` (opacity is a presentation attr; leave styling minimal).
- Cursor affordances keyed off root `svg[data-ui-mode]` (consumed in Wave D but ship the CSS now): `[data-ui-mode="place"] g.object{cursor:grab} .object.dragging{cursor:grabbing}`, `[data-ui-mode="calibrate"] .calibrate-handle{cursor:...}` (nwse/nesw-resize corners, crosshair rotate).
- Accessibility/legibility (model-ux): give object markers a contrasting halo/stroke so they read over a busy blueprint; keep status semantics identical between light/dark (vary luminance, not hue); `:focus-visible` outline on the focused marker `<g>` that survives viewBox transforms. Do NOT rely on hue alone. No purple-dominant palette.

### A8. Wave A tests (`tests/test_svg.py`)
- No blueprint, grid mode → output identical to pre-feature baseline (regression).
- `background_image` set → `<image` present with `xlink:href="data:` and `opacity` attr; `id="blueprint-image"`; `data-bg-*` present.
- Freeform tile `pos_x=0.5,pos_y=0.5,width=0.1` on known content rect → group transform translates to expected content-rect center; rect insert is `-pw/2,-ph/2`.
- `show_grid=False` → no `class="grid"` lines emitted; underlays + objects still present.
- Rotated blueprint (`bg_rotation=30`) → viewBox extends beyond `default_W/default_H`.
- Root `<svg>` carries `data-content-x/-y/-w/-h` equal to `(26,26,x_size*GRID_SIZE_X,y_size*GRID_SIZE_Y)`.

### A8b. Towncrier (Wave A)
`changes/<issue>.added`: "Rendered an optional blueprint image behind the floor plan (embedded, opacity-controlled) and freeform object markers positioned by normalized coordinates with rotation."

---

## Wave B — API persistence, model validation, `convert_to_freeform`

Files: `models.py`, `api/serializers.py`, `api/views.py`, new migration `0012_freeform_validation_nullable.py`, `choices.py` (helper map). `api/urls.py` unchanged (DRF `@action` auto-routes). Depends on Wave A only through the §0 coordinate contract (the seed formula must match the renderer), not on A's code.

### B1. `models.py` — validators & help_text
- `pos_x`, `pos_y`: add `MaxValueValidator(1)` alongside existing `MinValueValidator(0)` (import already present). Anchor must stay on the plan.
- `width`, `height`: **do NOT** add `MaxValueValidator(1)`. Resolves api-persist vs model-ux in favor of model-ux: a large centered footprint near an edge can legitimately push a corner past 1. Enforce `> 0` in `clean()` instead. (Overrides api-persist's max on width/height.)
- Update help_text on all four + `bg_*` to the §0 content-rect / center-anchor wording.

### B2. `models.py` — freeform `clean()` gating (prevents 500s once freeform data exists)
- Add `_validate_freeform()`: if `pos_x` or `pos_y` set → require both non-null (pairing), `0≤pos≤1`, `width>0`/`height>0` if set, and normalize `self.rotation %= 360`.
- Refactor `clean()` so grid-only validators are gated on `x_origin is not None and y_origin is not None`:
  ```
  super().clean(); allocation_type_assignment(self); self._validate_freeform()
  if x_origin is not None and y_origin is not None:
      validate_tile_placement(self); _validate_tile_overlaps(self); _validate_rack_rackgroup(self)
  _validate_installed_objects(); _validate_object_locations(); _validate_single_object_assignment()
  ```
  `bounds` computes `x_origin + x_size - 1` and will `TypeError` on null origins — this gate is what keeps it safe. **Overlap is allowed in freeform** (physical adjacency is intended); surface heavy overlap as advisory UX in JS, never a model error.
- Guard `update_tile_origins`: filter `self.tiles.filter(x_origin__isnull=False)` before `tile.x_origin += delta` (else seed-change save `TypeError`s on freeform tiles).
- `__str__`/`render_axis_origin`: fall back to a `pos_x/pos_y` string when origins are null.

### B3. `models.py` — `FloorPlan.convert_to_freeform` (the method; resolves "logic in model vs view")
Logic lives on the **model** (reusable by API + form); the API action is a thin wrapper. Seeding only — does NOT flip `placement_mode` (that stays a separate field write, which is what makes convert reversible/re-runnable).
```python
def convert_to_freeform(self, *, force=False, save=True):
    """Seed pos_x/pos_y/width/height (center-anchored) + rotation from grid cells.
    Idempotent (force=False skips already-seeded tiles). Reversible: never touches
    x_origin/y_origin. Returns the list of modified tiles."""
    xt, yt = self.x_size, self.y_size          # >=1 via MinValueValidator(1), no divide-by-zero
    modified = []
    qs = self.tiles.filter(x_origin__isnull=False, y_origin__isnull=False)
    for tile in qs:
        if not force and tile.pos_x is not None and tile.pos_y is not None:
            continue
        col = tile.x_origin - self.x_origin_seed    # 0-based; NOT hardcoded -1
        row = tile.y_origin - self.y_origin_seed
        tile.pos_x  = (col + 0.5 * tile.x_size) / xt   # CENTER anchor
        tile.pos_y  = (row + 0.5 * tile.y_size) / yt
        tile.width  = tile.x_size / xt
        tile.height = tile.y_size / yt
        tile.rotation = _ORIENTATION_TO_DEGREES.get(tile.object_orientation, 0)
        modified.append(tile)
    if save:
        with transaction.atomic():
            for tile in modified:
                tile.save(update_fields=["pos_x","pos_y","width","height","rotation"])
    return modified
```
Resolutions baked in: **center anchor** (`+0.5`), **rotation seeded** from `object_orientation` (add `_ORIENTATION_TO_DEGREES` map to `choices.py`; overrides api-persist which left rotation 0), **`save(update_fields=...)` not `validated_save`** (values in-range by construction via `validate_tile_placement` bounds; avoids re-running grid overlap on legal object-on-rackgroup tiles — resolves api-persist vs model-ux toward model-ux), **seed subtraction uses `x_origin_seed`** (note `reset_seed_for_custom_labels` forces seed to 1 on custom-label axes, so `col=x_origin-1` there naturally). Skips pure-freeform tiles (null origins).

### B4. `models.py` — nullability of `x_origin/y_origin` + CheckConstraint (needed only for pure-freeform native create; ship guards together or 500)
Add `null=True, blank=True` to `x_origin`/`y_origin` (keep `PositiveSmallIntegerField` + `MinValueValidator(0)` — validator only runs on non-null). Add:
```python
class Meta:
    constraints = [models.CheckConstraint(
        name="floorplantile_origin_pairing",
        check=models.Q(x_origin__isnull=False, y_origin__isnull=False)
            | models.Q(x_origin__isnull=True, y_origin__isnull=True))]
```
Postgres NULL-distinct semantics mean `unique_together` never fires on freeform rows — that's intended; the real uniqueness guard for freeform is the object `OneToOneField`s (a device/rack sits on one tile). The B2 guards (`clean` gating, `update_tile_origins` filter, `__str__` fallback) are the mandatory companions — without them the first null-origin tile crashes. If native pure-freeform creation is deferred, the nullability step can be deferred with it, and the `x_origin is None` skip in B3/B6 is harmless dead code; but ship nullability + all B2 guards atomically or not at all.

### B5. Migration `0012_freeform_validation_nullable.py`
Do NOT retrofit `0011` (assume applied). Ordered ops: `AlterField(pos_x)`, `AlterField(pos_y)` (add max validator — state-only, no SQL), `AlterField(x_origin)`, `AlterField(y_origin)` (`DROP NOT NULL` — catalog-only on Postgres, no rewrite/backfill), then `AddConstraint(floorplantile_origin_pairing)`. `atomic=True`. Reverse (`null→NOT NULL`) fails if freeform rows exist — document that downgrade requires removing freeform tiles first. Validator-only `AlterField`s keep `makemigrations` clean.

### B6. `api/serializers.py`
- `from rest_framework import serializers`.
- On `FloorPlanTileSerializer` (keep `fields="__all__"`), override to get clean per-field 400s for the debounced client:
  ```python
  pos_x = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=1)
  pos_y = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=1)
  width  = serializers.FloatField(required=False, allow_null=True, min_value=0)   # no max
  height = serializers.FloatField(required=False, allow_null=True, min_value=0)
  ```
  `background_image_width/height` are auto read-only (`editable=False`). `background_image` is a multipart ImageField — never send it in the JSON PATCH loop.
- Add `ConvertToFreeformResultSerializer(serializers.Serializer)` with `placement_mode:CharField`, `tiles_seeded/tiles_skipped/tiles_total:IntegerField` (for drf-spectacular schema).

### B7. `api/views.py` — `convert_to_freeform` action
```python
@extend_schema(request=None, responses={200: serializers.ConvertToFreeformResultSerializer})
@action(detail=True, methods=["post"], url_path="convert-to-freeform")
def convert_to_freeform(self, request, *, pk):
    floor_plan = get_object_or_404(self.queryset, pk=pk)
    # TokenPermissions maps POST->add_floorplan; this MUTATES rows -> require change explicitly
    if not request.user.has_perms(["nautobot_floor_plan.change_floorplan"]):
        raise PermissionDenied("Requires change_floorplan.")
    force = bool(request.data.get("force", False))
    set_mode = request.data.get("set_mode", True)
    with transaction.atomic():
        modified = floor_plan.convert_to_freeform(force=force, save=True)
        skipped = floor_plan.tiles.count() - len(modified)
        if set_mode and floor_plan.placement_mode != PlacementModeChoices.FREEFORM:
            floor_plan.placement_mode = PlacementModeChoices.FREEFORM
            floor_plan.validated_save()
    return Response({"placement_mode": floor_plan.placement_mode,
                     "tiles_seeded": len(modified), "tiles_skipped": skipped,
                     "tiles_total": floor_plan.tiles.count()})
```
Imports: `transaction`, `PermissionDenied`, `Response`, `get_object_or_404`, `PlacementModeChoices`, `extend_schema`. Reverse name `nautobot_floor_plan-api:floorplan-convert-to-freeform`. The explicit `change_floorplan` check resolves the DRF `TokenPermissions` POST→`add` mismatch.

### B8. Debounced-PATCH contract (the JS relies on this)
- Partial PATCH is field-independent for freeform/calibration scalars (no cross-field `clean()` coupling), so sending only changed keys never trips validation on untouched fields. Tile drag: `PATCH .../floor-plan-tiles/{pk}/ {pos_x,pos_y,rotation}`. Calibrate: `PATCH .../floor-plans/{pk}/ {bg_x,bg_y,bg_width,bg_height,bg_rotation}`.
- Never send `background_image` in the JSON loop. Upload is a separate multipart request/`FloorPlanForm`; Pillow fills read-only `background_image_width/height`.
- Errors return `{"<field>":["Ensure this value is less than or equal to 1."]}`; client reverts the dragged handle to last-good and shows a non-blocking toast (no `alert()`). Last-write-wins, no ETag.

### B9. Wave B tests (`tests/test_api.py`, `tests/test_models.py`)
- API: PATCH tile `pos_x/pos_y/rotation` persists; PATCH `pos_x=1.5`→400 field-keyed; PATCH floorplan `bg_*` persists; `convert_to_freeform` seeds exact values per formula; idempotent (2nd call `tiles_seeded==0`, pre-seeded custom `pos_x` preserved); preserves `x_origin/y_origin`; requires `change_floorplan` (view/add-only token→403); skips pure-freeform (`x_origin IS NULL`) tile; `force=true` re-seeds.
- Models: `MaxValueValidator(1)` rejects `pos_x=1.5`/`pos_y=1.5`; `width<=0`/`height<=0` rejected; origin-pairing CheckConstraint rejects half-null; overlap allowed in freeform / still enforced in grid; `update_tile_origins` skips freeform tiles; conversion for 1×1, multi-cell, non-1 numeric seed, custom-label seed, empty plan; `_ORIENTATION_TO_DEGREES` mapping.
- Migrations: apply/rollback `0012` against a DB with grid rows — no data loss, NULL-distinct uniqueness holds.

### B9b. Towncrier (Wave B)
`changes/<issue>.added`: "Added writable freeform placement and blueprint-calibration REST fields plus a `convert_to_freeform` action that seeds tile positions (center-anchored) from the existing grid; freeform tiles allow overlap and support null grid origins."

---

## Wave C — Forms / UI wiring

Files: `forms.py`, `api/views.py` (context only, if needed), `templates/nautobot_floor_plan/inc/floorplan_svg.html`, and the FloorPlan detail template hosting controls. Depends on Wave A (svg `data-*` attributes) and Wave B (API endpoints, `convert_to_freeform`, keyboard-parity fields).

### C1. `forms.py`
- Add a labeled `<fieldset>` "Blueprint" grouping `background_image`, `background_opacity` (slider **plus paired number input**, 0–100), `show_grid` (labeled toggle, not icon-only), `placement_mode`.
- Expose `pos_x/pos_y/width/height/rotation` as optional number inputs on `FloorPlanTileForm` (currently excluded) — keyboard/accessibility parity for placement, step `0.01`, min/max wired to validators. Accept a normalized↔percent unit toggle (percent is more legible than `0.734`).
- Expose `bg_x/bg_y/bg_width/bg_height/bg_rotation` as number fields in the detail/calibration panel + a "Fit to grid" reset (sets `bg_*` to null → auto-fit).

### C2. Templates
- Extend the existing button row in `floorplan_svg.html` with a mode `btn-group` (View / Place objects / Calibrate blueprint), `aria-pressed`, mdi icons. Gate the whole edit UI behind the same permission check the page uses for its edit button (`change_floorplan`/`change_floorplantile`) so read-only users keep today's view-only experience.
- Blueprint opacity `<input type="range">` + number input, shown only `{% if object.background_image %}`.
- On `#floor-plan-svg` add data attributes so JS never hardcodes URLs:
  `data-tile-api="{% url 'plugins-api:nautobot_floor_plan-api:floorplantile-list' %}"`,
  `data-floorplan-api="{% url 'plugins-api:nautobot_floor_plan-api:floorplan-detail' pk=object.pk %}"`,
  `data-convert-api` (the `convert-to-freeform` URL), `data-placement-mode="{{ object.placement_mode }}"`, `data-can-edit` (permission bool).
- Add a "Convert to freeform" button (POSTs the action) and a separately-labeled "Reset freeform layout from grid" (POSTs `{"force":true}`) — never wire `force` to the plain Convert button.

### C3. Accessibility (model-ux)
- Root `<svg>` `role="application"` + `aria-label` describing plan + current mode. Each object `<g>`: `role="button"`, `tabindex="0"`, `aria-label` "Rack R1, Active, at 62% 40%" (reuse Tippy tooltip data). Roving-tabindex so Tab enters the SVG once then Arrow keys move between markers (reading order by `pos_y,pos_x`). `aria-live="polite"` status region announcing committed moves and mode changes. Respect `prefers-reduced-motion` (gate GSAP tweening). Legend of status colors + type glyphs, keyboard-reachable.

### C4. Wave C tests
- `tests/test_forms.py`: FloorPlanForm accepts/persists `background_opacity`, `show_grid`, `placement_mode`; FloorPlanTileForm accepts `pos_x/pos_y/width/height/rotation` and rejects out-of-range with form errors; "Fit to grid" nulls `bg_*`.
- `tests/test_views.py`: detail view renders mode controls only with `change_*` permission; opacity/show_grid controls present when a blueprint exists.

### C4b. Towncrier (Wave C)
`changes/<issue>.added`: "Added blueprint upload, opacity/grid controls, placement-mode toggle, and keyboard-accessible numeric position/calibration entry to the floor plan UI."

---

## Wave D — Drag interaction JS

Files: `static/nautobot_floor_plan/js/floorplan.js`, minor template hooks (calibrate overlay container is JS-built). Depends on A (data attributes, content rect, `#blueprint-image`, `g.object[data-tile-id]`), B (PATCH endpoints, error shape), C (mode buttons, `data-*-api`).

### D1. Reconciliation mechanism — capture-phase router (do NOT edit existing pan/zoom)
Pan/zoom is wired via property handlers (`svgElement.onmousedown/move/up`) with a single slot each, and move/up early-return unless `isPanning`/`selectionRect` is truthy. Attach ONE `svgElement.addEventListener('mousedown', onCaptureMouseDown, true)`. Capture runs before the property handler; if the gesture is a drag (target hit-tests to a draggable and mode allows), call `preventDefault()`+`stopPropagation()` so the pan mousedown never fires and its move/up stay inert for the whole gesture. Otherwise do nothing → event bubbles to pan/zoom exactly as today. **Mode gates interactivity, not panning** — empty-canvas pan/box-zoom keep working in every mode.

### D2. State machine (inside the `initializeSVG` closure — no globals)
`uiMode ∈ {view,place,calibrate}`, orthogonal to existing `zoomMode`. `setMode(next)` is the single authority: idempotent, `cancelDrag()` before switching, set `svgElement.dataset.uiMode`, build/tear the calibrate overlay, sync `aria-pressed` buttons, force pan (grey box-zoom) in place/calibrate and restore in view. `Escape` → `setMode(view)` (and `cancelDrag` first if mid-drag).

### D3. Coordinate helpers
- `screenToUser(clientX,clientY)`: reuse the existing `createSVGPoint()` + `getScreenCTM().inverse()` idiom; recompute CTM every move.
- Read the content rect once from `data-content-*`. `userToNorm`: `nx=(x-content.x)/content.w`; `normToUser` inverse. Clamp `nx,ny` to `[0,1]` (const `NORM_CLAMP_MARGIN=0`). Never derive normalized deltas from pixel deltas (the pan handler's `panFactor` shortcut is wrong for placement).
- Rotation in degrees: `atan2(userY-cy, userX-cx)*180/PI`, normalize `[0,360)`, optional Shift-snap to 15°.

### D4. Drag-to-place (mode `place`)
`hitTestDraggable` → nearest `g.object[data-tile-id]` (ignore links/buttons per existing guard). `startDrag`: record `grabOffsetUser` (pointer minus marker center), `startNorm` snapshot, `.dragging` class, `bringToFront`, disable Tippy, bind `window` `mousemove`/`mouseup{once}` (survives leaving the SVG). `onDragMove`: compute new center → `clampNorm(userToNorm)` → rewrite `el` transform `translate(centerX centerY) rotate(rot)` (direct attribute write, no GSAP) → `schedulePatchTile(id,{pos_x,pos_y})`. Rotate grip appears on click-select; drives `{rotation}`. `onDragEnd`: `flushPatchTile`, write final values back to `dataset.posX/posY/rotation`, cleanup.

### D5. Drag-to-calibrate (mode `calibrate`)
Handles are a JS-built overlay `<g class="calibrate-overlay">` (server stays clean; downloadable SVG has no editing chrome). 4 corner handles + rotate handle, sized `HANDLE_PX/scale` (zoom-invariant, rebuilt on zoom). Corner drag: opposite corner is the fixed anchor, new rect = bbox in normalized space → `bg_x=min nx, bg_y=min ny, bg_width=|Δnx|, bg_height=|Δny|` (Shift locks aspect; un-rotate about center first if `bg_rotation≠0`). Rotate handle: `bg_rotation=atan2(...)`. Body drag (`#blueprint-image`): translate `bg_x/bg_y`. `applyBlueprintTransform` rewrites image x/y/width/height (`bg_*normalized→user`) + `rotate(bg_rot cx cy)` and repositions handles → `schedulePatchFloorPlan({bg_*})`. Opacity slider is separate: live `image.setAttribute('opacity',v)` on `input`, debounced `schedulePatchFloorPlan({background_opacity})` on `change`.

### D6. Debounce / optimism / races
Per-entity debouncer (`tile:{id}` / `floorplan`), trailing ~200–300 ms, coalesce fields into latest payload, `flushWrite` synchronously on pointerup. Each flush: `AbortController.abort()` the superseded in-flight write, send with `X-CSRFToken` (from `csrftoken` cookie). Monotonic `seq` guard in `reconcile`: if `seqAtSend < latestLocalSeq` ignore the server body (prevents snap-back). No periodic re-fetch (nothing to clobber). Guards: block wheel-zoom mid-drag (CTM change makes markers jump), disable Tippy during drag, pause the existing `MutationObserver` around structural overlay changes/`bringToFront` (transform-only attr writes are already filtered by `attributeFilter:['data-tooltip']`). On PATCH failure revert optimistic DOM to session snapshot + toast.

### D7. Keyboard parity (accessibility, model-ux)
Focused marker: Arrow nudges `pos` (Shift = fine), `R`/`Shift+R` rotate 15°/1°, `Enter` commits via the same debounced PATCH, `Esc` reverts. Announce via the `aria-live` region.

### D8. Wave D tests
- JS unit (jsdom, pure functions with a CTM seam): `userToNorm`/`normToUser`/`clampNorm`/angle round-trips against a fixture content rect.
- Playwright e2e: `place` mode drag a marker → exactly one debounced PATCH with expected normalized body (±1px), no snap-back after response, pan still works on empty canvas; `calibrate` corner-drag → expected `bg_*`; keyboard nudge commits.

### D8b. Towncrier (Wave D)
`changes/<issue>.added`: "Added drag-to-place object markers and drag-to-calibrate the blueprint (with keyboard equivalents), persisted via debounced REST PATCH."

*(The four `.added` fragments can ship separately per wave or be consolidated into one feature fragment at merge.)*

---

## Explicit conflict resolutions (audit trail)

1. **Anchor**: CENTER (api-persist + model-ux), overriding svg-render's top-left lock. Rotation pivots about centroid; matches design-doc `+0.5*x_size`.
2. **Normalization basis**: single CONTENT RECT (grid drawing area, user units), overriding svg-render's "relative to calibration rect." Objects independent of blueprint recalibration.
3. **`bg_*` units**: normalized to the content rect (js-interaction), overriding svg-render + api-persist "SVG user units." Uniform, resolution-independent. Requires updating P0 help_text.
4. **`width/height` max validator**: none (model-ux), overriding api-persist's `MaxValueValidator(1)`. Centered footprints legitimately overflow edges; enforce `>0` in `clean()`.
5. **`convert_to_freeform` home**: model method holds logic (model-ux), API action is a permission+response wrapper (api-persist). Method seeds only; action flips mode.
6. **Rotation on conversion**: seeded from `object_orientation` (model-ux), overriding api-persist's leave-at-0.
7. **Conversion save path**: `save(update_fields=...)` (model-ux) over `validated_save()` (api-persist) — in-range by construction, avoids re-validating legal grid overlaps.
8. **Freeform overlap**: allowed at model level (model-ux); advisory-only in UI. Grid overlap still enforced when origins non-null.
9. **`x_origin/y_origin` nullability**: ships in Wave B `0012` with ALL B2 guards (or defer entirely) — never nullability without the `clean`/`update_tile_origins`/`__str__` guards.
10. **Preserve-aspect-ratio**: `stretch()` (`preserveAspectRatio="none"`), never `.fit()`; auto-fit rect is aspect-correct so no distortion.
11. **Migration**: new `0012`, not a retrofit of applied `0011`.

## Risk list

- **Coordinate drift** across `svg.py` / `convert_to_freeform` / `floorplan.js` — the single highest risk. Center-anchor + content-rect normalization must be byte-identical in all three. Mitigate with a shared round-trip test (seed → render coords → drag PATCH → re-render) and the §0 contract as the single source.
- **Grid-mode regression** for upstream contribution — any change to `render()`/`_setup_drawing`/`_draw_grid` can perturb existing output. Mitigate with the A8 baseline-identical test.
- **Null-origin 500s** — enabling `x_origin/y_origin` nullability without the B2 guards crashes `bounds`, `update_tile_origins`, `__str__`, and grid overlap validation on the first freeform tile. Ship them atomically.
- **DRF POST→`add` permission mismatch** — `convert_to_freeform` would be gated on `add_floorplan` by default; explicit `change_floorplan` check required (B7).
- **`background_image` clobber** — sending it in the JSON PATCH loop can null/replace the stored image; JSON channel must be `bg_*`/`background_opacity` only.
- **Base64 payload bloat** — a 2 MB blueprint → ~2.7 MB SVG text on every fetch. Acceptable for self-containment; note a future size-gated `href` fallback.
- **MutationObserver feedback storm** — 60 fps transform writes could re-init Tippy each frame; mitigate via `attributeFilter`, observer pause around structural changes, and Tippy-disable during drag.
- **Out-of-order / stale PATCH** — mitigate with per-entity `AbortController` + monotonic `seq` guard; no periodic re-fetch.
- **CTM shift mid-drag** (wheel/scroll) — block wheel during drag; recompute CTM every move.
- **Reverse migration** (`null→NOT NULL`) fails if freeform rows exist — document downgrade prerequisite.
- **Custom-label seed subtraction** — must use `x_origin_seed` (forced to 1 on custom-label axes), never a hardcoded `-1`.
- **Legibility over busy blueprints** — hue-only status fails for ~8% of users and over noisy scans; require halo/stroke + shape/icon redundancy, no purple-dominant palette.

---

# Adversarial Critiques (must-fix items fold back into the waves)

## Critic 1: Coordinate-math / rendering

I've grounded the review in the actual `svg.py`/`models.py`. The seed/center-anchor round-trip math checks out numerically (verified below), but there are several concrete breakages in the rendering path. Findings ordered most-severe first.

## Coordinate-math / rendering review — blueprint+freeform spec

**Verified sound (so I move past it):** the center-anchor round trip is drift-free. `convert_to_freeform` sets `pos_x=(col+0.5·tile.x_size)/x_size`; the renderer computes `centerX = 26 + pos_x·(x_size·GRID_SIZE_X)` = `26 + (col+0.5·tile.x_size)·GRID_SIZE_X`, which is exactly the grid tile centroid `(x_origin−seed)·GRID_SIZE_X + 26 + (tile.x_size·GRID_SIZE_X)/2`. `content_w = x_size·GRID_SIZE_X` is an exact integer (GRID_SIZE_X is floor-division) and is reused verbatim in grid lines and the content rect, so there's no sub-pixel divergence. Seed subtraction uses `x_origin_seed` in both places (svg.py:290, and the spec's B3), matching the custom-label reset-to-1 behavior. That part is genuinely consistent across render/seed/drag.

---

### 1. (Blocker) `_draw_underlay_tiles` crashes on null-origin freeform tiles — the B2 guard list is incomplete
Location: Wave A6 render loop + `svg.py:284 _draw_underlay_tiles` (line 290 `tile.x_origin - self.floor_plan.x_origin_seed`). Spec B4 makes `x_origin/y_origin` nullable for native pure-freeform tiles; B2 enumerates the guards (`clean`, `update_tile_origins`, `__str__`, `bounds`) but omits the render path.
Failure: the very first pure-freeform tile (null origin) enters `render() → for tile: _draw_underlay_tiles` unconditionally and hits `None - 1` → `TypeError` → 500 on every SVG fetch and the detail page. A6's parenthetical "(skip naturally when no grid coords)" is false: the current code has no such skip; `on_group_tile`/`allocation_type` don't gate on origin nullness.
Fix: guard the underlay loop explicitly — `if tile.x_origin is None or tile.y_origin is None: continue` (or gate the whole underlay pass on `placement_mode == GRID`). Add `_draw_underlay_tiles` and `_draw_defined_rackgroup_tile` (svg.py:307+, also origin-based) to the mandatory B2 guard list and the risk-list "null-origin 500s" item.

### 2. (High) Dragging a converted tile leaves a ghost status/rackgroup underlay at the seed cell
Location: Wave A6 ordering — underlays drawn by grid geometry (`x_origin`), objects drawn by freeform `pos`. Converted tiles keep `x_origin` (B3 is reversible by design, "never touches x_origin/y_origin").
Failure: convert seeds `pos` to equal the grid cell, so initially aligned. User drags the rack across the blueprint → PATCH updates `pos_x/pos_y` only. Next render: the freeform marker is at the new position, but `_draw_underlay_tiles` still paints the status-color box and rackgroup label at `(x_origin−seed)` — the original cell. Every dragged rack orphans its colored underlay + status/rackgroup text at its pre-drag location. On a real floor plan this is a scattering of ghost boxes.
Fix: in `placement_mode == freeform`, either skip underlays for tiles rendered via the freeform path, or draw the status fill/rackgroup label inside the freeform `<g>` (translated/rotated with the marker) so it tracks `pos`. Decide and pin it in §0, since it's another cross-file convention (render + the JS optimistic transform would need to move the underlay too).

### 3. (High) Auto-fit blueprint publishes null `data-bg-*`, so the calibrate overlay can't initialize
Location: A2.3 (auto-fit branch, `bg_*` null) + A3 ("emit `data-bg-x/-y/...` raw normalized values") + D5 (handles built from `bg_*`).
Failure: fresh blueprint upload leaves all four `bg_*` null → server auto-fits in user units but A3 writes the *raw* (null) normalized values to `data-bg-*`. JS enters calibrate mode, reads `data-bg-width` = empty/NaN, and has no concrete rectangle to hang the 4 corner + rotate handles on. First interaction is undefined until the user blindly drags. The image is visible (positioned by the user-unit auto-fit rect) but the handles don't overlay it.
Fix: A3 must publish the **resolved** normalized rect. When `bg_*` are null, convert the computed auto-fit user rect back to normalized (`(bx−content_x)/content_w`, etc.) and emit those in `data-bg-*`. Keep a separate `data-bg-autofit="true"` flag so "Fit to grid" / null-persistence semantics are preserved, but the geometry attributes must always be concrete numbers.

### 4. (Medium) Auto-fit with unknown pixel dims distorts, contradicting the "stretch is safe" claim
Location: A2.3 ("if pixel dims unknown, fill the content rect exactly") vs A3 (`stretch()` = `preserveAspectRatio="none"`, justified as "safe: auto-fit is aspect-correct").
Failure: `background_image_width/height` are Pillow-filled read-only, but they're null for any image Pillow couldn't introspect (corrupt EXIF, SVG upload, truncated file, or a row created before the form populated them). Auto-fit then fills the content rect exactly, and `preserveAspectRatio="none"` stretches the raster to the (generally non-square) content aspect → visibly squashed blueprint. The "auto-fit is aspect-correct so none is safe" invariant only holds when pixel dims are known.
Fix: when pixel dims are unknown, fall back to `preserveAspectRatio="xMidYMid meet"` (or refuse to render the background and surface a "re-upload, couldn't read dimensions" hint) rather than `none`. Alternatively require dims before enabling the background (validate in the upload form).

### 5. (Medium) Freeform markers near edges get clipped when no blueprint is present
Location: A5 `_drawing_extents` — expands the viewBox for the rotated background rect "only when a blueprint is present and opacity>0." Objects are center-anchored with `pos` clamped to `[0,1]` but full `width/height` unbounded (B1 deliberately drops the max validator).
Failure: freeform plan, no blueprint, a rack at `pos_x=0.98, width=0.1`. Its right edge is at normalized `1.03` → user `26 + 1.03·content_w`, i.e. `0.03·content_w` past the content rect. The default viewBox is the frame box `(0,0, content_w+26+20, …)`, giving only `BORDER_WIDTH=10`px of slack on the right; `0.03·content_w` exceeds that for any non-tiny plan, so the marker is clipped by the viewBox edge. The extents union ignores object footprints entirely.
Fix: fold the freeform object footprints (center ± half-footprint, accounting for `rotation`) into `_drawing_extents` regardless of blueprint presence, or clamp the *footprint* (not just the center) into `[0,1]` and document that edge objects are pushed inward.

### 6. (Medium) `width`/`height` boundary is inconsistent between serializer and model
Location: B6 serializer (`min_value=0`, DRF's `min_value` is inclusive) vs B1/B2 model `clean()` ("enforce `> 0`", strict).
Failure: client PATCHes `width=0`. Serializer accepts it (0 ≥ 0), then `validated_save()` → `clean()` rejects strictly → 400, but the error is a model `ValidationError` that may not be keyed cleanly to the `width` field the way DRF field errors are, breaking B8's "revert the dragged handle on `{"<field>":[...]}`" contract. Worse, if any code path saves without `full_clean`, a `width=0` object renders `pw=0` (invisible marker).
Fix: make the serializer boundary exclusive to match the model — a custom validator rejecting `<= 0`, or `min_value` with a small epsilon — so the 0 case is rejected at the serializer with a field-keyed 400, and the model/serializer agree on the invariant.

### 7. (Low) viewBox with negative origin / non-frame size must be honored by the JS reset-to-fit
Location: A5 (negative min origin "legal and expected" for rotated blueprint) + `_setup_drawing` keeps `size=(width,depth)` at the frame size while `viewbox` is the larger extents + floorplan.js pan/zoom reset.
Failure: two issues. (a) SVG intrinsic `size` (frame) differing from `viewBox` (extents) makes the browser apply the default `xMidYMid meet` scale on initial inline render, shrinking the plan before JS takes over. (b) floorplan.js "reset/fit" and pan-bound logic that assumes origin `(0,0)` will mis-fit when the emitted viewBox starts negative. The design-doc contract needs JS to read the server viewBox, not reconstruct `(0,0,w,h)`.
Fix: emit the extents as both `viewBox` and drop/relativize the fixed `size`, or have floorplan.js seed its pan/zoom state from the actual rendered `viewBox` attribute (and `data-content-*`) rather than assuming a zero origin. Add a Wave D test: rotated-blueprint plan resets to the full emitted viewBox.

### 8. (Low) Base64 re-encode cost is per-request on two endpoints, not per-frame — but the model instructs `read()` without a size gate
Location: A2.2 `_background_image_data_uri` + the DRF `svg()` action (uncached).
Failure: not a pan/zoom hot path (floorplan.js fetches SVG once), so the risk-list framing is right, but every detail-page load and every `svg()` API hit reads the full file into memory and base64-encodes it synchronously in the request thread. A 10 MB scan (common for architectural PDFs exported to PNG) = ~13 MB string built per request; concurrent loads multiply it. There's no upper bound.
Fix: enforce a max upload size in the form (e.g. reject > 2–4 MB, or downscale on upload via Pillow), and note the size-gated `href`-to-URL fallback as an actual threshold rather than "future." At minimum stream/encode once and cache on the instance for the request lifetime (the `cached_property` in A2.1 covers the rect but not the data URI).

---

### Things I tried to break and couldn't
- Anisotropic content rect (`GRID_SIZE_X ≠ GRID_SIZE_Y` for non-square tiles) does **not** shear rotated objects: `pw/ph` are computed in user units before `rotate()`, so it's a true rotation of a user-space rectangle. atan2 (D3) and the render `rotate()` both operate in the same y-down user space, so stored degrees match visual angle and are self-consistent for both objects and calibration.
- Divide-by-zero in `convert_to_freeform`: `x_size/y_size` are `MinValueValidator(1)` NOT NULL stored fields, safe.
- NULL-distinct `unique_together` + the origin-pairing CheckConstraint is correct for allowing multiple null-origin freeform rows while blocking half-null.

The two must-fix-before-any-code items are **#1** (render path missing from the null-origin guard set — ship it with B2 or pure-freeform 500s) and **#3** (auto-fit must publish resolved normalized `data-bg-*` or calibrate is dead on arrival). **#2** (ghost underlays after drag) needs a §0-level decision because it's another cross-file convention, not a local fix.

## Critic 2: API / data-model / RBAC

I've grounded the review in the actual `models.py`, `api/views.py`, `serializers.py`, `choices.py`, and migration `0011`. The spec is well-organized and several of its resolutions are genuinely correct, but the API/data-model surface has a cluster of concrete breaks. Ranked most severe first.

---

## 1. [CRITICAL — design contradiction] "Overlap allowed in freeform" is false for the primary freeform population, because the gate discriminates on origin-nullness, not mode

Location: B2 refactored `clean()` gate `if x_origin is not None and y_origin is not None: ... _validate_tile_overlaps(self) ...`; resolution #8.

`convert_to_freeform` (B3) **never nulls `x_origin/y_origin`** — that's explicitly how it stays "reversible." So every converted tile has BOTH origins set AND `pos_*` set. That means converted tiles satisfy the gate `x_origin is not None and y_origin is not None`, so they run the full grid geometry stack including `_validate_tile_overlaps` → `Object tiles cannot overlap` (models.py:583). The stated goal (physical adjacency intended, overlap allowed in freeform) only holds for pure-null-origin tiles, which are the *rare* native-freeform case, not the *common* converted case.

Failure scenario: convert a grid plan to freeform, drag two racks to overlap, PATCH → `validated_save()` → `clean()` → gate is true (origins retained) → `_validate_tile_overlaps` raises `"Object tiles cannot overlap"` → 400. Freeform overlap is silently un-implemented for the exact tiles the feature targets.

Fix: the geometry validators must gate on the *mode/positioning*, not on origin presence. Use `if self.floor_plan.placement_mode != FREEFORM and x_origin is not None and y_origin is not None:` (or `if self.pos_x is None and x_origin is not None`). Keep the `is not None` part strictly for TypeError-avoidance; add mode as the semantic discriminator. As written the spec conflates "don't crash on null" with "skip grid rules," and those are different predicates.

## 2. [CRITICAL — correctness] Partial PATCH re-runs the WHOLE `clean()`; B8's "field-independent, no cross-field coupling" claim is wrong

Location: B8 ("Partial PATCH is field-independent for freeform/calibration scalars … sending only changed keys never trips validation on untouched fields").

`NautobotModelSerializer.update()` calls `validated_save()` → `full_clean()` → `FloorPlanTile.clean()` (models.py:507), which **always** runs `_validate_installed_objects`, `_validate_object_locations`, `_validate_single_object_assignment`, and `allocation_type_assignment` regardless of which fields the PATCH touched. These validate against the tile's *current object assignments and their external state*, not against the incoming payload.

Failure scenario: a rack tile is placed, then someone installs that tile's device into a rack elsewhere (or moves the rack's Location). Later a drag sends `PATCH {pos_x, pos_y}`. `clean()` re-runs `_validate_installed_objects` → `"Device '…' is installed in Rack '…'"` (models.py:534) or `_validate_object_locations` → 400. A pure position change is rejected by an unrelated stale-state rule, the drag snaps back, and the user can never reposition that marker. The debounced drag loop the whole JS layer depends on is not actually decoupled from object validation.

Fix: either (a) route drag/calibration writes through a code path that saves only the geometry fields with `update_fields` and skips `full_clean` (mirroring what convert does), or (b) make `_validate_installed_objects`/`_validate_object_locations` no-op when the object assignment fields are unchanged. Do not claim field-independence while `clean()` validates the full instance.

## 3. [HIGH — security/RBAC] `convert_to_freeform` bypasses object-level permissions

Location: B7 `get_object_or_404(self.queryset, pk=pk)` + `request.user.has_perms(["nautobot_floor_plan.change_floorplan"])`.

`self.queryset` is the raw unrestricted class attribute (`models.FloorPlan.objects.all()`). Nautobot's object-level permissions are enforced by `restrict_queryset(request.user, action)`, which the standard viewset actions apply via `get_queryset()` but the custom action skips (same latent bug as the existing `svg` action, but here it gates a *mutation*). `has_perms([...])` is a **model-level** check; it returns True for a user who has `change_floorplan` constrained to a *different* set of objects.

Failure scenario: user has a Nautobot ObjectPermission granting `change_floorplan` only where `location__name="DC-A"`. They POST convert against a DC-B floor plan's pk. `has_perms` passes (model perm exists), `self.queryset` isn't restricted, so the DC-B plan is fetched and mutated — the object-level constraint is bypassed.

Fix: `floor_plan = get_object_or_404(self.get_queryset().restrict(request.user, "change"), pk=pk)` and drop the manual model-perm check in favor of the queryset restriction (which enforces both model and object level). Fix the `svg` action the same way while you're here.

## 4. [HIGH — security/correctness] The permission "resolution" double-gates: the action ends up requiring BOTH `add` AND `change`

Location: B7 comment "TokenPermissions maps POST->add_floorplan; this MUTATES rows -> require change explicitly"; risk-list "DRF POST→add mismatch."

`NautobotModelViewSet` uses `TokenPermissions` (Nautobot's `DjangoModelPermissions` subclass) whose method map sends POST → `add_floorplan`, and `has_permission` runs **before** the view body. Layering a manual `has_perms(["…change_floorplan"])` inside the body does not *replace* that — it *adds* to it. Net effect: the caller must hold `add_floorplan` (to clear `TokenPermissions`) **and** `change_floorplan` (to clear your manual check).

Failure scenario: the correct principal for this mutation — a user with `change_floorplan` but not `add_floorplan` — is rejected 403 at `TokenPermissions` before ever reaching your check. Meanwhile a user with only `add` (arguably the wrong perm) gets past `TokenPermissions` then fails your manual check. The B9 test "view/add-only token→403" masks this because it never tests the change-only case that *should* pass. This applies to session+CSRF callers too (the JS path), since the method→perm map is auth-independent.

Fix: override the required permission for the action rather than stacking a check. Set `self.get_required_permission()`/a custom permission class so POST-to-this-action maps to `change_floorplan` only, or give the action `permission_classes` that map it to change. Then test the change-only principal succeeds and add-only fails.

## 5. [HIGH — correctness] Half-null origin PATCH → `CheckConstraint` IntegrityError → 500, not 400

Location: B4 `CheckConstraint floorplantile_origin_pairing`; B2 gate; serializer keeps `fields="__all__"` so `x_origin`/`y_origin` stay writable and now nullable.

The pairing invariant for origins is enforced **only** at the DB (CheckConstraint). `_validate_freeform` pairs `pos_x/pos_y` but nothing pairs `x_origin/y_origin` at serializer or `clean()` level. And the B2 gate `if x_origin is not None and y_origin is not None` *skips* grid validation precisely when exactly one is set, so `clean()` passes a half-null instance straight to the DB.

Failure scenario: `PATCH .../floor-plan-tiles/{pk}/ {"x_origin": 3}` on a freeform tile whose `y_origin` is null. `clean()` gate is false → no grid validation → `save()` → Postgres rejects the CheckConstraint → `django.db.utils.IntegrityError` → DRF renders **500** (IntegrityError is not a ValidationError). The debounced client, which is designed to parse `{"field":[...]}` 400 bodies, gets an opaque 500 and can't revert cleanly.

Fix: add an origin-pairing check to `clean()` mirroring `_validate_freeform`'s pos pairing (raise `ValidationError({"x_origin": ...})` when exactly one origin is set), so it surfaces as a field-keyed 400 before hitting the DB.

## 6. [MEDIUM-HIGH — correctness] NaN / Infinity floats slip past `min_value`/`max_value` and persist

Location: B6 serializer `FloatField(min_value=0, max_value=1)` for `pos_x/pos_y`; `rotation`/`bg_*` have no serializer validators at all.

DRF's numeric bounds use `MaxValueValidator`/`MinValueValidator`, which test `value > limit` / `value < limit`. For `NaN`, both comparisons are False, so `NaN` passes *both* bounds. `float('nan')`/`float('inf')` are produced by `FloatField.to_internal_value` and JSON `Infinity`/`NaN` tokens are accepted by Python's default decoder.

Failure scenario: `PATCH {"pos_x": NaN}` (or `Infinity`) clears all bounds, `clean()`'s `%=360` on rotation also yields NaN, value lands in the Postgres `double precision` column, and the renderer emits `transform="translate(NaN NaN) rotate(NaN)"` → the marker (or whole SVG) fails to render for every future viewer. `rotation` and `bg_*` have no bounds at all, so even without NaN a client can PATCH `rotation: 1e308`.

Fix: reject non-finite values in the serializer fields (custom `validate_pos_x` etc. using `math.isfinite`, or a shared validator), and add bounds/finiteness to `rotation` and `bg_*`.

## 7. [MEDIUM — data loss] `background_image` clobber is prevented only by client convention

Location: B6/B8 ("never send `background_image` in the JSON PATCH loop"), `FloorPlanSerializer` `fields="__all__"`.

`background_image` remains writable on the FloorPlan serializer. Nothing server-side stops a JSON `PATCH .../floor-plans/{pk}/ {"background_image": null, "bg_x": ...}` from nulling the stored image (and orphaning `background_image_width/height`). "The JSON channel must be bg_*/opacity only" is a client discipline, not an enforcement — one buggy or malicious payload wipes the blueprint.

Fix: make `background_image` read-only on the serializer used by the calibration PATCH path (upload stays via the multipart `FloorPlanForm`), or split calibration onto a dedicated serializer that whitelists only `bg_*`/`background_opacity`.

## 8. [MEDIUM — validation gap] `width`/`height`: serializer `min_value=0` is inclusive and contradicts `clean()`'s `>0`; no upper bound with center anchor lets footprints be arbitrary

Location: B1 ("do NOT add MaxValueValidator(1)… enforce >0 in clean()"), B6 `FloatField(min_value=0)`, resolution #4.

`min_value=0` (inclusive) lets `width: 0` clear the serializer, then `_validate_freeform`'s `width>0` rejects it — but as a model `ValidationError`, not the clean field-keyed 400 the client expects (and it also contradicts the model field's own `MinValueValidator(0)`). Separately, with center-anchoring and no upper bound, `width: 1000` is accepted; the object covers the entire plan or spills far past both edges. That's a legitimate design choice for "corner past 1," but unbounded is a footgun (accidental fat-finger, or a scale bug in JS, produces a plan-swallowing marker).

Fix: make the serializer bound exclusive of 0 (`min_value` won't do exclusive; use a `validate_width` raising for `<= 0`) so you get a clean field-keyed 400, and add a sane sanity ceiling (e.g. `<= 4` or `<= 2`) rather than unbounded.

## 9. [MEDIUM — data integrity] Nullable origins + `unique_together` NULL-distinct allows unlimited object-less duplicate tiles

Location: B4 (relies on OneToOne object fields as the "real uniqueness guard"), `unique_together = [floor_plan, x_origin, y_origin, allocation_type]` (models.py:459).

The OneToOne guard only exists for tiles that *have* a device/rack/panel/feed. A freeform tile can be RackGroup/status-only (no object). With origins null, Postgres treats `(fp, NULL, NULL, 'rackgroup')` rows as all-distinct, so `unique_together` never fires and there is no OneToOne to fall back on. You can create arbitrarily many identical status-only freeform tiles.

Failure scenario: repeated POSTs of `{floor_plan, status, placement_mode context}` with null origins and no object → N duplicate ghost tiles, each rendered stacked at the same spot. No constraint stops it.

Fix: decide whether object-less freeform tiles are even valid; if not, require an object (or a non-null pos) at `clean()` for freeform tiles. At minimum document that the pairing/OneToOne guards do not cover the object-less case.

## 10. [LOW] `convert_to_freeform` uses `save(update_fields=...)`, bypassing the new `MaxValueValidator(1)` on `pos_x/pos_y`

Location: B3 seed loop; resolution #7.

`save(update_fields=[...])` skips `full_clean`, so the B1 `MaxValueValidator(1)` never runs during seeding. The spec argues values are "in-range by construction via `validate_tile_placement` bounds" — true *only if* existing grid rows already satisfy `validate_tile_placement`. Legacy/corrupt rows (e.g., created before a resize guard) would seed `pos_x > 1` silently, and it persists un-validated until a later PATCH rejects it. Low probability, but the "in-range by construction" justification is load-bearing and unverified against existing data.

Fix: either clamp/assert in the seed loop, or run the seeded values through the field validators before the bulk save.

## 11. [LOW — semantics] `tiles_skipped` conflates "already seeded" with "no grid origin to seed from"

Location: B7 `skipped = floor_plan.tiles.count() - len(modified)`.

`convert_to_freeform` iterates only `tiles.filter(x_origin__isnull=False, y_origin__isnull=False)`, so null-origin (native freeform) tiles are outside the queryset entirely. Subtracting `len(modified)` from the *total* count lumps null-origin tiles into `tiles_skipped` alongside already-seeded grid tiles. Reported metrics become ambiguous for mixed plans.

Fix: compute `skipped` as `qs.count() - len(modified)` over the same filtered queryset, and report null-origin tiles as a separate field if it matters.

---

## Parts that are sound (verified, moving on)

- **Seed formula and `x_origin_seed` subtraction** (B3): `col = x_origin - x_origin_seed`, `pos_x = (col + 0.5*x_size)/x_size_plan`. Given `validate_tile_placement` (models.py:476-489) guarantees `col ∈ [0, plan_size - x_size]`, the center lands in `(0,1)` and `width = x_size/plan_size ≤ 1`. Non-1 numeric seeds and the custom-label forced-seed-of-1 (models.py:242-250) both fall out correctly. Correct.
- **`update_tile_origins` guard** (B2): genuinely required — `self.tiles.all()` (models.py:193) with `tile.x_origin += delta` would `TypeError` on null origins; the `.filter(x_origin__isnull=False)` fix is right and also covers the `reset_seed_for_custom_labels` caller since it's inside the shared method.
- **Migration DROP NOT NULL as catalog-only** (B5): correct on Postgres; no table rewrite. Reverse-fails-with-null-rows is correctly flagged.
- **Nested `transaction.atomic()`** (B3 method + B7 action): savepoint semantics, fine.
- **`background_image_width/height` auto read-only** via `editable=False` (models.py:140-141): correct, no clobber risk on those two.
- **Content-rect normalization math** (§0): internally consistent; center-anchor + single-rect basis is the right call to kill the drift risk, provided §1 above is fixed so the *validation* discriminator matches.

The single highest-leverage fix is #1+#2 together: the gate predicate `x_origin is not None` is being asked to mean three different things (crash-avoidance, grid-vs-freeform, and overlap policy), and it's only correct for the first. Split those predicates explicitly before any of this ships.

## Critic 3: JS / interaction / accessibility

I have enough grounding. Here is my adversarial review, focused on JS interaction robustness (with the Wave A/B items that directly break the JS layer flagged where they bite).

---

## CRITICAL — the D1 reconciliation mechanism is built on a false premise

**Location:** Wave D, D1 ("Capture-phase router — do NOT edit existing pan/zoom").

The spec's central claim is: intercept `mousedown` in capture, `preventDefault()+stopPropagation()`, and "the pan mousedown never fires and its move/up stay inert for the whole gesture." **The move/up handlers are NOT gated on the mousedown having fired.** Look at the actual code:

- `var isPanning = true;` (line 7) — **initialized true at module load.**
- `svgElement.onmousemove` early-returns only `if (!isPanning && !selectionRect)` (line 307).
- `isPanning` is set `false` only in `onmouseup` (line 432) and `onmouseleave` (line 437).

So stopping the pan `mousedown` does nothing to `isPanning`. Concrete failure: on the **first gesture after page load in place mode** (before any mouseup/mouseleave has ever set `isPanning=false`), the user grabs a marker. Your capture router swallows the pan mousedown, but `isPanning` is still `true`. Your drag binds `window.mousemove`, and simultaneously `svgElement.onmousemove` fires on every move (pointer is over the SVG) → the pan branch runs → `gsap.to(svgElement,{viewBox…})` tweens the viewBox **while you're dragging the marker**. The marker and the canvas move at once; the CTM shifts under your own drag math. It self-corrects only after the first real `mouseup`/`mouseleave` flips `isPanning=false`, which is why it'll look "intermittent" and pass a happy-path Playwright test that pans once first.

**Fix:** `cancelDrag`/`startDrag` must own the pan flags, not just rely on stopPropagation. In `startDrag`: `isPanning = false; gsap.killTweensOf(svgElement); if (selectionRect) { selectionRect.remove(); selectionRect = null; }`. Better: add a `uiMode` check as the *first line* of the existing `onmousedown/move/up` handlers (`if (uiMode !== 'view') return;`) so pan/box-zoom are structurally inert in edit modes. The spec's "do NOT edit existing pan/zoom" constraint is the root cause of this bug — a three-line guard in each handler is far safer than a capture-phase side-channel that fights stale state.

---

## CRITICAL — GSAP tweens leave `getScreenCTM()` lying for 300ms after every pan/zoom

**Location:** D3 ("recompute CTM every move"), D6 ("block wheel-zoom mid-drag").

Every pan and zoom in the existing code is a **GSAP tween of the `viewBox` attribute over 0.3s** (lines 242, 360, 410, 519), while the JS `viewBox` variable is set to the *target* synchronously (lines 250, 368). So for 300ms after any pan/wheel, `svgElement.getScreenCTM()` returns a matrix that is mid-animation and does not match the JS `viewBox` state. D3's `screenToUser` reads the live CTM — correct in principle — but if the user pans/zooms and grabs a marker within 300ms (extremely common), the very first `startDrag` samples `grabOffsetUser` against an animating CTM. The marker jumps on grab and again as the tween settles.

D6 says "block wheel-zoom mid-drag," which is necessary but insufficient — the danger is the tween *already in flight from before* the drag started.

**Fix:** `gsap.killTweensOf(svgElement)` at the top of `startDrag` and in `setMode`, and read the settled `viewBox` from the attribute (not the JS var) on drag start. Consider making pan/zoom instantaneous (duration 0) while `uiMode !== 'view'`.

---

## CRITICAL — single-click on a marker in place/calibrate mode navigates away

**Location:** A4 markup (`<a href=... target="_top">` wrapping every freeform marker) + D4 ("Rotate grip appears on click-select").

Markers are anchors with `target="_top"`. D4 relies on *click-to-select* to reveal the rotate grip. `preventDefault()` on `mousedown` does **not** reliably suppress the subsequent `click` → the browser fires a `click` on the `<a>` and navigates the whole page (`_top`) to the rack/device detail. A user who clicks a marker to select it (or whose "drag" moved <1px and is treated as a click) is yanked off the editor, losing any unflushed edits. The existing pan handler dodges this only because it explicitly bails on `e.target.closest('a')` (line 256) and never needs to click anchors.

**Fix:** add a capture-phase `click` listener on `svgElement`: `if (uiMode !== 'view') { e.preventDefault(); e.stopPropagation(); }`. Do it for `click`, not just `mousedown` — anchor activation is a click-level default. Verify with an actual click (mousedown+mouseup, zero movement) in a test, not just a drag.

---

## HIGH — extents with negative origin are unreachable; box-zoom clamps them away

**Location:** A5 ("Negative min origin is legal and expected") vs. the existing pan/zoom clamps.

`updateViewBox` clamps `x` to `Math.min(Math.max(candidate.x, 0), svgActualSize.w - newW)` (line 168), and the pan branch clamps `x` to `[0, maxX]` (line 356). Both assume the viewBox origin is `(0,0)` — true today (`drawing.viewbox(0,0,…)`, line 81). A5 introduces a viewBox whose origin can be **negative** (rotated blueprint spilling top-left) and whose extent exceeds the grid box. The clamps will pin `x,y ≥ 0`, so the spilled region is **permanently unreachable by pan or box-zoom**, and `resetZoom` won't frame it either. The rotated-blueprint feature renders content the user can't scroll to.

**Fix:** parse the actual viewBox origin `(vx,vy)` from `data-*`/the viewBox string and clamp against `[vx, vx+vw-newW]` / `[vy, vy+vh-newH]` throughout, instead of the hardcoded `0`. This is a change to the "do not touch" pan/zoom code — unavoidable once the viewBox origin can move.

---

## HIGH — the deep-link highlight system freezes the whole UI for 20s (pointer-events:none)

**Location:** unaddressed interaction between Wave D and the existing highlight flow (lines 516–564, 556–559).

`highlightElementFromURL()` runs on load, and `disableZoomAndPan()` sets `#floor-plan-svg { pointer-events: none }` for `HIGHLIGHT_DURATION` (default 20s per the code comment, line 536). Anyone arriving via `?highlight_rack=…` (the exact links this app generates from rack pages) and switching to place/calibrate mode hits a **dead canvas for up to 20 seconds** — no drag, no handle grab, nothing, with no visible reason. The spec never mentions the highlight subsystem.

**Fix:** `setMode(place|calibrate)` must call `enableZoomAndPan()` / cancel the outstanding highlight timeouts and clear `pointer-events`. Track the highlight `setTimeout` handles so mode entry can `clearTimeout` them.

---

## HIGH — touch/pointer completely unsupported; the drag-only UX is dead on tablets

**Location:** all of Wave D (uses `mousedown/mousemove/mouseup`; existing code is mouse-only).

Floor plans get used on tablets walking the data-center floor. Every handler in the spec and the existing code is mouse-only. Drag-to-place and drag-to-calibrate will be **entirely non-functional on any touch device**, and D7 keyboard parity doesn't help a tablet with no keyboard. This is both a usability and an accessibility failure for the primary field use case.

**Fix:** use Pointer Events (`pointerdown/move/up`, `setPointerCapture`) instead of mouse events for the new drag layer — `setPointerCapture` also removes the need to bind `window` and neatly solves "pointer leaves the SVG mid-drag." Add `touch-action: none` on draggables so the browser doesn't steal the gesture for scroll.

---

## HIGH — calibrate corner-drag formula wobbles on any rotated blueprint

**Location:** D5 ("un-rotate about center first if `bg_rotation≠0`"... "opposite corner is the fixed anchor").

These two rules are inconsistent. If you un-rotate about the *current* center, then resize by holding the opposite corner fixed, the center **moves** to the midpoint of the new+fixed corners. On the next move event you un-rotate about the *new* center — but the fixed corner was defined in the *old* center's frame. The image drifts/rotates spuriously as you resize. Corner calibration of a rotated blueprint will feel like it's fighting you.

**Fix:** do the entire resize in the un-rotated frame in one consistent step: (1) transform pointer into the image's local un-rotated frame using the rotation about the *drag-start* center (frozen for the gesture), (2) compute the new axis-aligned rect with the opposite corner fixed *in that frozen frame*, (3) derive the new center and `bg_x/bg_y/bg_w/bg_h` from that rect, keeping `bg_rotation` constant. Freeze the pivot for the duration of the gesture; don't recompute it per move.

---

## MEDIUM — mode buttons are live before `initializeSVG` exists (200ms window + fetch latency)

**Location:** D2 ("state machine inside the `initializeSVG` closure") + existing `setTimeout(…,200)` (line 48) after an async `fetch`.

`setMode` is defined only inside `initializeSVG`, which runs 200ms *after* the SVG fetch resolves — potentially a second or more after the page paints and the Wave C mode buttons are clickable. Early clicks either no-op silently or throw `ReferenceError` if wired to a not-yet-defined handler. On a slow SVG fetch the buttons are inert with no feedback.

**Fix:** disable the mode `btn-group` (`aria-disabled`, `disabled`) until `initializeSVG` finishes and explicitly enables them; or attach button listeners inside `initializeSVG` rather than at DOM-ready.

---

## MEDIUM — unflushed debounced PATCH lost on mode-switch/navigation

**Location:** D6 (trailing 200–300ms debounce; flush "synchronously on pointerup").

`onDragEnd` flushes, good. But the opacity slider commits on `change` (debounced), and D7 keyboard nudges commit on a debounce too. If the user nudges with arrow keys or drags the opacity slider and then immediately clicks a link / switches tab / hits browser-back, the trailing timer never fires and the edit is **silently lost** (last-write-wins, no re-fetch to reveal the loss). `setMode`'s `cancelDrag` should also *flush*, not just cancel.

**Fix:** flush all pending per-entity debouncers on `setMode`, on `visibilitychange`/`pagehide`, and before any in-page navigation. `cancelDrag` should distinguish "abort optimistic revert" (Escape) from "commit pending" (mode switch).

---

## MEDIUM — SVG `<g>` focus ring via CSS `outline` is unreliable and scales with zoom

**Location:** A7 (":focus-visible outline on the focused marker `<g>` that survives viewBox transforms") + C3.

CSS `outline` on SVG container elements is inconsistently rendered across browsers (historically unsupported in Firefox for SVG), and any stroke-based ring drawn in user units gets hairline-thin when zoomed out / fat when zoomed in — so the focus indicator either vanishes or dominates depending on zoom. "Survives viewBox transforms" is asserted but not achievable with a plain outline.

**Fix:** draw an explicit focus `<rect>` inside the marker group with `vector-effect="non-scaling-stroke"` (zoom-invariant width) and toggle it on focus, rather than relying on CSS `outline`. Test the ring at min and max zoom.

---

## MEDIUM — `x_origin/y_origin` nullability + partial-index gap for freeform overlap uniqueness

**Location:** B4 (CheckConstraint pairing; "the real uniqueness guard for freeform is the object OneToOneFields").

Two hazards the spec waves past. (1) B4 relies on `OneToOneField`s to prevent a device/rack occupying two tiles — but the spec doesn't confirm those FKs are actually `OneToOne` on `FloorPlanTile` today; if any are `ForeignKey`, freeform mode silently allows duplicate placement of the same object with no DB guard. Verify before asserting it. (2) The reverse migration (`null → NOT NULL`) is documented as "fails if freeform rows exist" — but Django's `AlterField` will attempt it and error mid-migration, leaving a partially-applied downgrade on non-atomic backends. At minimum the reverse `RunPython` should raise a clear guard early, not let Postgres reject the `SET NOT NULL`.

---

## Sound parts (brief)

- **D6 seq/AbortController reconcile** is the right shape; monotonic `seq` guard + per-entity abort correctly prevents stale-response snap-back, and `onDragEnd` writing final values to `dataset` before the response arrives means a re-grab uses correct local baseline. Good.
- **No periodic re-fetch** matches the existing code (SVG injected once; theme toggle only swaps `<style>` textContent, line 754, so it won't clobber drag DOM). Correct call.
- **MutationObserver mitigation** (D6) is warranted: `attributeFilter:['data-tooltip']` (line 461) means `transform`/`data-pos-*` writes won't trigger it, but `bringToFront` (childList reorder) and overlay build/teardown will — pausing around those is the right fix.
- **Center-anchor + single content-rect** (§0) is the correct unification and makes the optimistic transform a one-line rewrite; the anisotropy from `GRID_SIZE_X ≠ GRID_SIZE_Y` is internally consistent because both normalize and render happen in the same user-unit space.

**Top of the fix list, in order:** (1) pan-flag ownership in `startDrag`/`setMode`, (2) `killTweensOf` before sampling CTM, (3) capture-phase `click` suppression on anchors, (4) negative-origin pan clamps, (5) highlight-system `pointer-events` interaction.
