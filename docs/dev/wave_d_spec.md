# Wave D — Locked Implementation Blueprint

Produced by the `floorplan-waveD-design` ultracode workflow (8 agents). Source of truth for the interactive drag layer + writable place API.

Grounding confirmed. Key facts that pin the contradictions: `isPanning` is initialized `true` (floorplan.js:7); the move handler early-returns on `if (!isPanning && !selectionRect)` (line 307); pan/zoom are property handlers (`onmousedown/move/up`, bubble phase) so a capture-phase `pointerdown` + `preventDefault` suppresses the compat `mousedown` entirely; the API namespace is `plugins-api:nautobot_floor_plan-api:` with `floor-plans`→`FloorPlanViewSet` and `floor-plan-tiles`→`FloorPlanTileViewSet`; `TILE_GEOMETRY_FIELDS` exists but `FloorPlanSerializer` has no calibration fast path; towncrier fragments live in `changes/` as `+name.added`.

Here is the merged blueprint.

---

# Wave D — Integrated Implementation Blueprint

Interactive drag-to-place, drag-to-calibrate, a writable place endpoint, mode toggle, keyboard parity, and accessibility — layered onto the existing viewer without editing its pan/zoom/tooltip/highlight bodies.

## 0. Cross-analysis decisions (contradictions resolved once, up front)

These bind every step below. Where the four analyses disagreed, this is the ruling and why.

1. **Event routing — capture router + state ownership, NOT handler guards.** `drag-calibrate` proposed adding `if (uiMode !== "view") return;` to `onmousedown/move/up`. Rejected: it violates "do not edit pan/zoom," and it is unnecessary. Grounding proves `onmousemove` already early-returns on `!isPanning && !selectionRect` (line 307). So a single capture-phase `pointerdown` router that (a) `preventDefault()`s to kill the compat `mousedown` (so `onmousedown` never runs on a marker/handle) and (b) sets `isPanning = false` + drops `selectionRect` inertizes the untouched handlers for the whole gesture. Adopt `drag-place`'s model. There is **one** router, installed once, that dispatches by `uiMode` — reject `drag-calibrate`'s per-mode add/remove `onCalibratePointerDown`.

2. **Empty-canvas pan stays live in every mode.** The router early-returns (no `preventDefault`) when the gesture hits neither a marker nor a handle, so the compat `mousedown` reaches `onmousedown` and pan works even in place/calibrate. This overrides `drag-calibrate`'s "pan off in calibrate." Box-zoom is the only thing suppressed in edit modes (`forcePanMode` disables the toggle), so box-select can't fight a drag.

3. **Coordinate convention — unified.** Read the content rect once from `<svg data-content-*>`; recompute `getScreenCTM()` **fresh every pointer event**, never cache; `gsap.killTweensOf(svgElement)` on drag start to freeze the animating viewBox. Normalized space is anisotropic: `nx=(ux-cx)/cw`, `ny=(uy-cy)/ch`; keep all math in user units, divide by `cw/ch` only at store time. Both JS analyses already agreed; names unified to `screenToUser` / `userToNorm` / `normToUser` / `clampNorm`.

4. **Debounce ownership — one factory, one channel per entity key.** `drag-place` used a keyed `pending` Map; `drag-calibrate` used a `makePatchChannel(url)` factory. Merge: a `makePatchChannel(url, {delay})` factory (calibrate's encapsulation) plus a lazy registry `channelFor(key, url)` so there is exactly one channel per `tile:<id>` and one per `floorplan`. Each channel owns its own trailing debounce, `AbortController`, and monotonic `seq`. `flushAll()` iterates the registry. Default `delay = 200ms`.

5. **Place endpoint — `detail=False` on `FloorPlanTileViewSet`** (`drag`-`place-api`'s design), reverse `floorplantile-place`. Reject `controls-a11y`'s `floorplan-place` naming: putting it on the tile viewset makes `POST → add_floorplantile` (semantically "create a tile") and avoids the `convert-to-freeform` `POST → add_floorplan` mismatch. `floor_plan` travels in the **body**, not the URL. `placeable-types` stays `detail=True` on `FloorPlanViewSet` (`floorplan-placeable-types`) — both analyses agree.

6. **Permission model.** Model-level via Nautobot `TokenPermissions` (POST→`add_floorplantile`; PATCH→`change_floorplantile`; GET placeable-types→`view_floorplan`). Object-level in `validate()`: `change` on the target `FloorPlan` and `view` on the placed object, both via `.restrict(user, …).filter().first()` so missing and forbidden are indistinguishable (no existence leak). Template gating is UX-only; the server is authoritative.

7. **Focus ring — server-drawn, JS-toggled.** `svg.py` emits a hidden `.focus-ring` rect inside each marker `<g>` with `vector-effect="non-scaling-stroke"`; JS toggles `.is-focused`. Reject `drag-place`'s JS-created-on-demand ring (diverges from server render, untestable in `test_svg.py`). Dynamically placed markers get their ring because **place re-renders via the server** (next point).

8. **After a successful place, re-fetch the SVG rather than hand-building a `<g>`.** The place endpoint returns tile JSON, not SVG. Client-constructing a marker that matches `_draw_freeform_tile` (icon, backing rect, focus ring, aria-label) is fragile. Resolve: `reloadSvgPreservingMode()` re-runs the existing fetch+inject, restores `uiMode`, re-arms the roving tabindex, then programmatically `startDrag`s the new marker by `data-tile-id`. Correct and reuses server rendering; the cost is one extra GET per placement, which is acceptable.

9. **Highlight-freeze release — minimal lift, not a refactor.** Reject `controls-a11y`'s `isHighlighting`-flag rewrite of the pan handlers (edits forbidden code). Adopt `drag-place`'s minimal change: push `highlightElement`'s two `setTimeout` handles (lines 529, 537) into an outer-scope `highlightTimers` array (recording only, no logic change), and `clearHighlightFreeze()` calls `clearTimeout` on them + the existing `enableZoomAndPan()`. Additionally disable the mode buttons while a highlight is active so calibrate can't be entered into a frozen canvas.

10. **`FloorPlanSerializer` needs its own geometry fast path.** `drag-calibrate` flagged it; `place-api` didn't cover it. It is required — calibrate PATCHes the plan detail, and without a fast path every frame re-runs `full_clean` and can reject a pure reposition on `placement_mode`/`background_image`. Add `CALIBRATION_FIELDS` mirroring `TILE_GEOMETRY_FIELDS`.

---

## Build Step 1 — Server: place endpoint, placeable-types, calibration fast path, registry field

Independently testable via DRF tests with zero client. Ship first; everything else consumes these URLs.

### 1.1 `nautobot_floor_plan/placement/registry.py`
- Add `location_field: str = "location"` to `PlacementType` (dataclass field, default-safe). Thread it through `register()` / `register_variant()`. Set `"location"` for Device/Rack/PowerPanel, `"power_panel__location"` for PowerFeed, `"device__location"` for cross-app Phone/Printer. This is the single source for the picker's location filter param (prevents picker/eligibility divergence).

### 1.2 `nautobot_floor_plan/api/serializers.py`
- **`CALIBRATION_FIELDS = {"bg_x","bg_y","bg_width","bg_height","bg_rotation","background_opacity"}`** module constant.
- **`FloorPlanSerializer.update()`**: mirror the tile fast path — `if validated_data and set(validated_data).issubset(CALIBRATION_FIELDS): setattr + instance.save(update_fields=list(validated_data)); return instance`. Else fall through to `super().update()`.
- **`FloorPlanTilePlacementSerializer(serializers.Serializer)`** (input-only, NOT a ModelSerializer, so the auto round-trip harness never touches it):
  - Fields: `floor_plan` (PK), `placed_content_type` (`ContentTypeField`, queryset bound to `registry.allowed_content_types()` in `__init__`), `placed_object_id` (UUID), `pos_x`/`pos_y` (`FloatField` min 0 max 1 + `validate_finite`), `width`/`height` (optional, `>0`, finite), `rotation` (optional default 0, finite), `status` (optional PK).
  - `validate(attrs)`: (a) plan `change` via `FloorPlan.objects.restrict(user,"change").filter(pk=…).exists()` → 400 on `floor_plan`; (b) object `view` via `model_cls.objects.restrict(user,"view").filter(pk=…).first()` → 400 on `placed_object_id` if `None` (missing≡forbidden); (c) `registry.resolve(obj) is None` → 400 on `placed_content_type`; (d) `registry.resolve_location(obj)` missing/≠`plan.location` → 400 on `placed_object_id`; (e) `FloorPlanTile.objects.for_object(obj).exists()` → 400 on `placed_object_id`. Stash `attrs["_object"]`.
  - `create(validated)`: build `FloorPlanTile` with the generic pair only (never a typed FK, so `_sync_placed_object_from_typed()` is a no-op and the pair survives), origins left NULL (pure-freeform, satisfies `floorplantile_origin_pairing`), `status = validated.get("status") or _default_tile_status()`. `tile.validated_save()`; map `DjangoValidationError → serializers.ValidationError(exc.message_dict)` (preserves field anchoring), `IntegrityError → 400` on `placed_object_id` (uniqueness race).
- **`_default_tile_status()`** helper: first `Status` whose `content_types` include `FloorPlanTile`; if none, field-anchored 400 on `status`.
- **`PlaceableTypeSerializer(serializers.Serializer)`**: read-only, schema-only (`key`, `content_type`, `label`, `icon`, `color`, `legend_order`, `object_source`).
- Leave `FloorPlanTileSerializer` and its `read_only_fields` (line 62) unchanged.

### 1.3 `nautobot_floor_plan/api/views.py`
- **`FloorPlanTileViewSet.place`** — `@action(detail=False, methods=["post"])`, `@extend_schema(request=FloorPlanTilePlacementSerializer, responses={201: FloorPlanTileSerializer})`. Validate with context `{"request": request}`, save, return **`FloorPlanTileSerializer(tile).data`** at 201 (client gets the shape it already knows, incl. `placed_label`).
- **`FloorPlanViewSet.placeable_types`** — `@action(detail=True, url_path="placeable-types")`. `get_object_or_404(self.queryset.restrict(request.user,"view"), pk=pk)` (same pattern as `svg`, line 30). Enumerate `entry.base` per registered CT (base types, not per-role variants), build each row incl. `object_source = _object_source_for(pt, plan.location)`, sort by `(legend_order, label)`, return `{floor_plan, location, placeable_types:[…]}`.
- Helpers `_object_source_for(pt, location)` → `{list_url: <model default api list>, params: {pt.location_field: location.pk, "nautobot_floor_plan_has_floor_plan_tile":"false"}}` (emit the `has_floor_plan_tile` param only once the G3 filter lands; until then just the location param, documented best-effort), and `_registered_base_types()`.
- No URL wiring (auto-routed by `OrderedDefaultRouter`), no migration (generic pair + freeform columns already exist).

### Step 1 tests — `tests/test_api.py`
- Place happy path per builtin type (device/rack/power-panel/power-feed) + one mocked cross-app CT → 201, NULL origins, `placed_label` set, generic pair set.
- Rejections asserting the exact field key: unregistered CT; missing object; wrong-location; already-placed; `pos_x=1.5`; `pos_x=NaN`; `width=0`.
- Permissions: object-level `change_floorplan` scoped to another location → 400 on `floor_plan`; no `view` on target → 400 on `placed_object_id` (≡missing); token with only `view_floorplantile` → 403.
- Uniqueness race: pre-create tile → place → 400 not 500.
- Move stays on fast path: PATCH `{pos_x,pos_y}` on a tile whose object was since moved to another location → 200; PATCH incl. `placed_object_id` → falls through to full validation.
- **Calibration fast path**: PATCH plan `{bg_x,bg_y}` while `background_image`/`placement_mode` would otherwise fail `full_clean` → 200; PATCH incl. a non-calibration field → full validation.
- placeable-types: shape/keys, sorted by `legend_order`, `object_source.params` carries the plan location via each type's `location_field` (incl. PowerFeed's `power_panel__location`); requires `view_floorplan`.

---

## Build Step 2 — Template controls, `data-*` wiring, and `svg.py` a11y attributes

Inert without Wave D JS (buttons ship `disabled`, panels ship `hidden`), so this lands without breaking today's viewer. `template_content.py` needs **no change** (`perms` comes from the auth context processor; the include already renders with `object`).

### 2.1 `templates/nautobot_floor_plan/inc/floorplan_svg.html`
- Permission bootstrap wrapping the block: `{% with can_calibrate=perms.nautobot_floor_plan.change_floorplan can_place=perms.nautobot_floor_plan.add_floorplantile can_move=perms.nautobot_floor_plan.change_floorplantile %}{% with can_edit=can_calibrate|default:can_place %}`.
- **Mode `btn-group`** (only if `can_edit`): `role="group"`, buttons `#mode-view/#mode-place/#mode-calibrate` each `data-mode=…`, `aria-pressed`, and **`disabled aria-disabled="true"`** (JS enables at end of `initializeSVG`). Calibrate button only when `object.background_image`. View is `active`/`aria-pressed=true` on load.
- **Opacity control** (`can_calibrate and object.background_image`): paired `<input type=range #blueprint-opacity-range>` + `<input type=number #blueprint-opacity-number>`, both seeded `object.background_opacity|default:100`, mirror on `input`, live-set image opacity on `input`, debounced PATCH on `change`.
- **Place panel** `#place-object-panel` (`can_place`, `hidden`): `<select #place-type-select>` (populated at runtime from `placeable-types`), ARIA combobox `#place-object-input` + `role="listbox" #place-object-listbox`, `#place-commit` button, `#place-hint`.
- **Extend `#floor-plan-svg`** (line 50) with `data-*` (edit URLs emitted only when the matching permission is held, so a read-only page cannot construct a write URL):
  - `data-tile-api="{% url 'plugins-api:nautobot_floor_plan-api:floorplantile-list' %}"`
  - `data-floorplan-api="{% url '…:floorplan-detail' pk=object.pk %}"`
  - `{% if can_place %}data-place-api="{% url '…:floorplantile-place' %}"` (**no pk**) `data-placeable-types-api="{% url '…:floorplan-placeable-types' pk=object.pk %}"{% endif %}`
  - `{% if can_calibrate %}data-convert-api="{% url '…:floorplan-convert-to-freeform' pk=object.pk %}"{% endif %}`
  - `data-placement-mode`, `data-location-id`, `data-can-edit`, `data-can-move`, `data-can-calibrate` (`|yesno:'true,false'`).
- **Live region + legend mirror** (always present): `<div id="floor-plan-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true">` and `<div id="floor-plan-legend" role="group" aria-label="Marker legend">`.
- Close both `{% endwith %}` before the file's final `{% endif %}`.

### 2.2 `nautobot_floor_plan/svg.py`
- `_setup_drawing` (add near line 235): root `<svg>` `role="application"`, `aria-label="Floor plan for <location>, <mode> placement"`, `tabindex="0"`.
- `_draw_freeform_tile` (near line 955): on the `<g>` add `role="button"`, `tabindex="-1"` (roving), `aria-label=self._marker_aria_label(obj,label,tile)`, `data-can-move` (optional per-object). Append hidden `.focus-ring` rect inside the group with `vector-effect="non-scaling-stroke"`.
- New `_marker_aria_label(obj,label,tile)`: reuse `_get_tooltip_data` → `"<Name>, <label>, <Status>, at 62% 40%"`.
- `_draw_legend`: set the in-SVG legend group `aria-hidden="true"` (HTML mirror is the SR channel).

### 2.3 CSS — `css/svg.css` + `css/dark_svg.css` (+ `light.css` for `.sr-only` if absent)
- `.object .focus-ring{display:none;fill:none;stroke:#1c7ed6;stroke-width:2px}` `.object.is-focused .focus-ring{display:inline}`.
- `svg[data-ui-mode="place"] g.object{touch-action:none}` and calibrate handles `touch-action:none` (browser must not claim the gesture for scroll).
- `.calibrate-overlay/.calibrate-handle/.calibrate-body` styles + `.calibrate-handle:focus-visible{outline:…}`.
- `@media (prefers-reduced-motion: reduce){.spotlight-effect,.highlight-border,.indicator-arrow{animation:none!important}}`.

### Step 2 tests — `tests/test_svg.py`
- Grid-mode / no-blueprint baseline assertions stay byte-identical (only freeform markers touched).
- New: freeform `<g>` carries `role="button"`, `tabindex="-1"`, non-empty `aria-label` with a `%` position, one `.focus-ring` child with `vector-effect="non-scaling-stroke"`; root `<svg>` has `role="application"` + `tabindex="0"`; legend group `aria-hidden="true"`.

---

## Build Step 3 — Shared JS scaffold (mode machine, capture router, coords, patch channel)

All code lives inside the existing `initializeSVG(svgElement)` closure (line 60) so it reaches `isPanning`, `selectionRect`, `zoomMode`, and the local `viewBox`. Organize the file into labeled sections; no existing handler body is edited.

**File: `static/nautobot_floor_plan/js/floorplan.js`** — section layout:

```
// ── Wave D constants ──
DRAG_THRESHOLD_PX=3, PATCH_DEBOUNCE_MS=200, ROTATE_SNAP_DEG=15, HANDLE_PX=12,
ROTATE_ARM_PX=28, MIN_NORM=0.02, NORM_CLAMP_MARGIN=0
let uiMode='view'; let activeDrag=null; const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── §A Coordinate helpers (pure; unit-testable) ──
readContentRect(svg) → {x,y,w,h} from data-content-*
screenToUser(clientX,clientY)   // createSVGPoint + getScreenCTM().inverse(), FRESH each call
userToNorm(x,y) / normToUser(nx,ny)   // anisotropic
clampNorm(n)=min(1,max(0,n))
angleDeg(cx,cy,ux,uy,snap)
frame(rotDeg) → {u,v}; makeLocalXforms(pivot,rotDeg) → {toLocal,toWorld}

// ── §B Patch channel factory + registry (debounce/abort/seq) ──
makePatchChannel(url,{delay=PATCH_DEBOUNCE_MS}) → {schedule(partial), flush(), abort()}
   coalesce payload; trailing setTimeout; on flush abort prior AbortController, ++seq,
   fetch PATCH {Content-Type json, X-CSRFToken}, keepalive:true;
   drop response if mySeq<applied; reconcile/onError field-keyed (no alert()).
channelFor(key,url)  // lazy Map<key,channel>: 'tile:<id>' | 'floorplan'
flushAllPatches() / abortPatch(key)

// ── §C Mode state machine ──
setMode(next): idempotent; if(activeDrag) cancelDrag({commit:true}); flushAllPatches();
   clearHighlightFreeze(); uiMode=next; svgElement.dataset.uiMode=next;
   view→restorePanControls()+teardownCalibrate(); else forcePanMode()+ (calibrate? buildCalibrate() : teardownCalibrate());
   syncModeButtons(next); announce(`${next} mode`); rewrite root aria-label.
forcePanMode(): zoomMode=false; disable box-zoom toggle.
announce(msg): write #floor-plan-status.

// ── §D Capture-phase input router (single, installed once) ──
svgElement.addEventListener('pointerdown', onPointerDownCapture, true)
svgElement.addEventListener('click', onClickCapture, true)      // anchor-nav + post-drag killer
svgElement.addEventListener('wheel', e=>{ if(activeDrag){e.preventDefault();e.stopPropagation();} }, true)
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ if(activeDrag) cancelDrag({commit:false}); setMode('view'); } })

// ── §E Highlight-freeze lift (minimal) ──
let highlightTimers=[];   // highlightElement pushes its 2 setTimeout ids here
clearHighlightFreeze(): highlightTimers.forEach(clearTimeout); highlightTimers=[]; enableZoomAndPan();

// ── §F Roving tabindex + focus ring ──  (Step 6)
// ── §G Place module ──                    (Step 4)
// ── §H Calibrate module ──                (Step 5)

// last lines of initializeSVG: enable mode buttons + wire click→setMode(btn.dataset.mode)
```

`onPointerDownCapture(e)` (the crux, Decision 1/2): return if `uiMode==='view'` or non-primary button. `tile = uiMode==='place' ? e.target.closest('g.object[data-tile-id]') : null`; `handle = uiMode==='calibrate' ? e.target.closest('.calibrate-handle,.calibrate-body') : null`. **If neither → return (no preventDefault)** so pan still works on empty canvas. Else `e.preventDefault(); e.stopPropagation(); isPanning=false; if(selectionRect){selectionRect.remove();selectionRect=null;} gsap.killTweensOf(svgElement);` then `startDrag(tile,e)` or `startCalibrate(handle,e)`.

Minimal edits to existing code, all additive/recording-only: push the two `highlightElement` `setTimeout` handles (lines 529, 537) into `highlightTimers`; if `reduceMotion`, pass `duration:0` where new code calls gsap (existing tweens untouched — reduced-motion belt-and-suspenders is the CSS in Step 2.3).

### Step 3 tests — JS unit (pure transforms; e.g. a small `tests/js/` with vitest/node)
- `userToNorm`/`normToUser` round-trip; `clampNorm` bounds; `angleDeg` wraps 0–360 and snaps; `makeLocalXforms` `toLocal∘toWorld = identity`; `frame` orthonormal.
- `makePatchChannel`: coalescing (two `schedule` before flush → one body with merged keys); `seq` guard (stale response dropped); `abort()` cancels in-flight (mock fetch).
- `setMode` idempotency + `flushAllPatches` called on transition (spy).

---

## Build Step 4 — Drag-to-place (`uiMode==='place'`)

`§G` in the closure. No GSAP on markers — direct one-attribute `transform` rewrite. `setPointerCapture` on the marker so leaving the SVG mid-drag needs no `window` binding.

- `startDrag(el,e)`: snapshot `{id, pointerId, moved:false, startClient, grab:{dx,dy} in user units, snap:{nx,ny,rot}, rot}`. `el.setPointerCapture(pointerId)`, `el.classList.add('dragging')`, `disableTooltipFor(el)`, `bringToFront(el, pauseObserver=true)`, attach `pointermove/up/cancel` on `el`.
- `onDragMove(e)`: `DRAG_THRESHOLD_PX` gate flips `moved`; compute center `= screenToUser − grab`, `userToNorm`, `clampNorm` **center** to [0,1], rewrite `transform="translate(cx cy) rotate(rot)"`, `channelFor('tile:'+id, tileUrl).schedule({pos_x,pos_y})`.
- `onDragEnd()`: if moved, commit dataset (`data-pos-x/y/rotation`), `channelFor(...).flush()`, `announce("Moved …")`. `selectMarker(el)` reveals rotate grip. Clear `activeDrag`.
- `cancelDrag({commit})`: commit→flush; else `abortPatch('tile:'+id)` + restore snapshot transform.
- **Rotate grip**: small `<g>` added on `selectMarker`; drag computes `angleDeg(center,pointerUser,e.shiftKey)`, rewrites `rotate(...)`, `schedule({rotation})`.
- **Place flow**: type+object arm placement; next canvas click (or `#place-commit`) POSTs `data-place-api` with `{floor_plan, placed_content_type, placed_object_id, pos_x, pos_y, rotation:0}`; on 201 → `reloadSvgPreservingMode()` then `startDrag` the new marker by returned `id`. `#place-type-select` populated from `data-placeable-types-api`; object search hits the type's `object_source.list_url` + `params`.
- MutationObserver interplay: wrap `bringToFront` / ring insert in `observer.disconnect()…observe()`; per-frame `transform` doesn't trip the `attributeFilter:['data-tooltip']` observer.

### Blocker coverage (place)
- **Single-click navigates away** → `onClickCapture`: if a drag just moved OR `uiMode!=='view' && target.closest('a')` for a select → `preventDefault()+stopPropagation()`; sub-threshold "drag" is treated as select.
- **Wheel mid-drag CTM shift** → capture `wheel` blocked while `activeDrag`.
- **GSAP-CTM-lie** → `gsap.killTweensOf` in router + `setMode`; CTM read fresh each move.
- **Touch/tablet** → Pointer Events + `setPointerCapture`; `touch-action:none` (Step 2.3).
- **Stale-true `isPanning` driving the move branch** → router sets `isPanning=false`.

### Step 4 tests — Playwright e2e (`tests/`, use `browser_drag`/pointer)
- Enter place mode, drag a marker; assert `transform` updated and a PATCH `{pos_x,pos_y}` (geometry-only) fired; reload → position persisted.
- Click a marker (no move) in place mode → does NOT navigate (URL unchanged), marker selected.
- Wheel during drag → viewBox unchanged.
- Place a new object via picker → 201, marker appears, immediately draggable.

---

## Build Step 5 — Drag-to-calibrate (`uiMode==='calibrate'`)

`§H`. Overlay is JS-built, appended last child of `svgElement`, tagged `data-calibrate-chrome`, zoom-invariant via a rAF loop reading fresh CTM. Lives only while in calibrate mode; `serializableSvg()` strips `[data-calibrate-chrome]` before any Save-SVG download.

- `buildCalibrate()`: read `content` + `bg` (`{x,y,w,h,rot}` from `data-bg-*`, resolved even on autofit — fall back to computing the autofit rect from `#blueprint-image` x/y/width/height if attrs empty). Build `<g.calibrate-overlay>` with body polygon + 4 corner handles + rotate handle (`role`/`tabindex=0`/`aria-label`). `startOverlayRaf()`.
- `startCalibrate(handle,e)` via the shared router: `gsap.killTweensOf`, capture, branch on `handle.dataset.calibrateKind`:
  - **corner** — freeze snapshot `{xf=makeLocalXforms(startCenter,bg.rot), oppLocal (fixed opposite corner), aspect, startBg}`; move recomputes rect **absolutely** (opposite corner fixed, no accumulation → no wobble); `MIN_NORM` floor; Shift = `lockAspect`.
  - **rotate** — `startAngle` snapshot; move `atan2`; Shift snaps 15°.
  - **body** — `startPointer`+`startBg`; move = absolute delta.
- Every move: `applyBlueprintTransform()` (rewrite image x/y/w/h + `rotate(deg cx cy)`, `preserveAspectRatio="none"`, mirror back to `data-bg-*`, set `data-bg-autofit="false"`, `positionOverlay()`) + `channelFor('floorplan', floorplanUrl).schedule(bgPayload(bg))`.
- On pointerup: `channelFor('floorplan').flush()`, `suppressNextClick()` if moved.
- **Opacity slider** wires to the **same** `floorplan` channel: `input` live-sets image opacity (no network); `change` → `schedule({background_opacity})` (coalesces with in-flight `bg_*`).
- `teardownCalibrate()`: `flush()`, `stopOverlayRaf()`, remove overlay.

### Blocker coverage (calibrate)
- **Capture router** — the single shared router (Step 3) dispatches; no per-mode listener churn.
- **Wobble** — immutable dragStart snapshot + absolute recompute + `MIN_NORM`.
- **GSAP-CTM-lie** — `killTweensOf` on start; rAF reads live CTM so handles track mid-animation.
- **Single-click navigates away** — `suppressNextClick()` swallows the synthetic click after a moved drag.
- **Save-SVG chrome leak** — `data-calibrate-chrome` + `serializableSvg()` strip + removal on exit.
- **Calibration full_clean rejection** — the Step 1.2 `CALIBRATION_FIELDS` fast path.
- **MutationObserver thrash** — observer callback guard: `if (mutations.every(m => m.target.closest?.('.calibrate-overlay'))) return;`.

### Step 5 tests — Playwright e2e
- Drag a corner on a non-rotated blueprint → `bg_width/bg_height/bg_x/bg_y` PATCH; opposite corner pixel-fixed (assert its screen coord unchanged).
- Rotate handle → `bg_rotation` PATCH; Shift snaps to 15°.
- Body drag → `bg_x/bg_y` only.
- Opacity slider `input` changes image opacity with no network; `change` fires one PATCH.
- Enter calibrate, Save-SVG download → serialized string contains no `data-calibrate-chrome`.
- Rotated blueprint corner drag → no wobble across 20 frames (bg center drift < ε).

---

## Build Step 6 — Keyboard parity + ARIA

`§F` + handlers. Roving tabindex, arrow parity, live announcements, reduced motion, legend mirror, highlight-freeze release.

- **Roving tabindex**: on init, collect `g.object[data-tile-id]`, sort by `(data-pos-y, data-pos-x)`, first→`tabindex=0`, rest→`-1`. In **view** mode Arrows move focus between markers (roving). In **place** mode Arrows nudge the focused marker.
- **Focus ring**: JS toggles `.is-focused` on `focus`/`blur` (server drew the `.focus-ring` rect in Step 2.2); `:focus-visible` as progressive enhancement.
- **Place keyboard** (place mode, marker focused): Arrow = 1% nudge, Shift+Arrow = 0.1%, `R`/`Shift+R` = +15°/+1°, `Enter` = flush, `Esc` = revert snapshot + `setMode('view')`. Every nudge repaints the transform and routes through the **same** `channelFor('tile:'+id).schedule(...)` — one write channel with the pointer path.
- **Calibrate keyboard**: overlay `keydown` — corner arrows move that corner (opposite fixed, `nudgeCorner` reuses the pointer geometry), rotate handle arrows = ±1°/±15° (Shift), `Esc` reverts to `startBg`; commits via the `floorplan` channel.
- **aria-live**: `announce()` writes `#floor-plan-status` on committed move, mode change, placement, and PATCH failure ("Move rejected, reverted. <field msg>"). Never `alert()`.
- **Legend mirror**: after SVG injection, mirror the in-SVG legend into `#floor-plan-legend` (HTML flow, `role="group"`); the in-SVG legend is `aria-hidden` (Step 2.2).
- **Reduced motion**: `reduceMotion` (Step 3) makes new gsap calls `duration:0`; CSS media query (Step 2.3) disables highlight animations.
- **Highlight-freeze release**: `highlightElement` pushes its two timers into `highlightTimers`; `setMode('place'|'calibrate')`→`clearHighlightFreeze()` cancels them + `enableZoomAndPan()`; mode buttons disabled while a highlight is active so calibrate can't be entered into a `pointer-events:none` canvas. Resolves the "20 s dead canvas."

### Step 6 tests
- JS unit: roving sort order from `(pos_y,pos_x)`; nudge/rotate produce the same normalized geometry as the pointer path (shared function).
- Playwright: Tab enters SVG → first marker focused (ring visible at min and max zoom); Arrow nudges in place mode fire debounced PATCH; `Esc` reverts + returns to view; `#floor-plan-status` text updates; deep-link `?highlight_rack=…` then switch to place mode → canvas immediately interactive (no 20 s wait).

---

## JS critics' blockers → where each is handled

| Blocker | Handled in |
|---|---|
| Capture-phase router / legacy double-fire | Step 3 §D single router + `preventDefault` kills compat `mousedown` |
| Stale-`true` `isPanning` drives move branch | Step 3 router sets `isPanning=false` (Decision 1) |
| GSAP-CTM-lie (300 ms) | Step 3 `killTweensOf` in router+`setMode`; fresh CTM every event; `reduceMotion` |
| Single-click navigates away | Step 4 `onClickCapture` + `DRAG_THRESHOLD_PX`; Step 5 `suppressNextClick` |
| Touch/tablet unsupported | Steps 4/5 Pointer Events + `setPointerCapture`; `touch-action:none` (2.3) |
| Calibrate corner wobble | Step 5 frozen dragStart snapshot + absolute recompute + `MIN_NORM` |
| Debounce/abort/seq | Step 3 §B `makePatchChannel` + registry; flush on pointerup/mode-exit/pagehide |
| 20 s highlight freeze | Step 3 §E lift + Step 6 `clearHighlightFreeze` + buttons disabled during highlight |
| Focus ring survives viewBox transforms | Step 2.2 server `.focus-ring` + `non-scaling-stroke`; Step 6 `.is-focused` |
| Buttons live during 200 ms + fetch window | Step 2.1 `disabled`; enabled at end of `initializeSVG` (Step 3) |
| Unflushed debounce lost on mode-switch/unload | Step 3 `flushAllPatches` in `setMode`; `pagehide`/`visibilitychange` + `keepalive` |
| Wheel-zoom mid-drag | Step 4 capture `wheel` block |
| Save-SVG chrome leak | Step 5 `data-calibrate-chrome` + `serializableSvg` |
| MutationObserver thrash | Step 4 disconnect/observe wrap; Step 5 overlay guard |
| Negative-origin viewBox unreachable by pan clamps | Out of scope for the drag layer; **companion edit to `updateViewBox` (line 160) + pan branch (line 356)** gated behind "a rotated blueprint is present" — the one place the no-edit rule is deliberately lifted, and only then |

---

## File-by-file change summary

- `nautobot_floor_plan/placement/registry.py` — `location_field` on `PlacementType`.
- `nautobot_floor_plan/api/serializers.py` — `CALIBRATION_FIELDS` + `FloorPlanSerializer.update` fast path; `FloorPlanTilePlacementSerializer`; `PlaceableTypeSerializer`; `_default_tile_status`. `FloorPlanTileSerializer` untouched.
- `nautobot_floor_plan/api/views.py` — `FloorPlanTileViewSet.place`; `FloorPlanViewSet.placeable_types`; `_object_source_for`/`_registered_base_types`.
- `templates/nautobot_floor_plan/inc/floorplan_svg.html` — permission bootstrap, mode `btn-group`, opacity control, place panel, `data-*-api`, live region, legend mirror.
- `nautobot_floor_plan/svg.py` — root `role/aria-label/tabindex`; per-marker `role/tabindex/aria-label/data-can-move` + `.focus-ring`; `_marker_aria_label`; legend `aria-hidden`.
- `static/nautobot_floor_plan/js/floorplan.js` — all Wave D sections §A–§H, additive; only edits to existing code are recording the two `highlightElement` timer handles and (companion, gated) negative-origin clamps.
- `static/nautobot_floor_plan/css/svg.css` + `dark_svg.css` (+ `light.css` for `.sr-only`) — focus ring, calibrate overlay, `touch-action`, reduced motion.
- `tests/test_api.py`, `tests/test_svg.py`, `tests/js/…`, Playwright e2e.

## Towncrier fragment

`changes/+wave-d-freeform-placement.added`:

```
Added interactive freeform editing to the floor plan viewer: drag object markers to reposition them, drag the blueprint background to calibrate its size, position, rotation, and opacity, and a view/place/calibrate mode toggle with full keyboard and screen-reader parity. A new REST place action drops any registered object type onto a plan at a normalized position, and a placeable-types endpoint lists the registered types scoped to a plan's location. Moving a marker and calibrating the blueprint both use geometry-only PATCH fast paths.
```

Build order is strictly dependency-first: Step 1 (server) is testable with DRF alone; Step 2 (template + svg.py) renders today's viewer unchanged with edit chrome disabled/hidden; Step 3 (shared scaffold) is unit-testable in isolation; Steps 4–6 layer place, calibrate, and keyboard/ARIA on top, each with its own e2e coverage.

---

# Adversarial Critiques

## Critic 1: interaction/event correctness

I have what I need. I traced the actual event mechanics against the blueprint's load-bearing claims. Findings below, most severe first; sound parts confirmed briefly at the end.

---

## Wave D adversarial review — event/interaction correctness

### BLOCKER 1 — The core "preventDefault kills the compat mousedown" premise is false for mouse, and calibrate handles pan while you drag them

This is the load-bearing claim of Decisions 1 and 2: a capture-phase `pointerdown` that calls `preventDefault()` "kills the compat `mousedown` (so `onmousedown` never runs on a marker/handle)."

That is not how mouse input works. `pointerdown.preventDefault()` suppresses *compatibility* mouse events, which only exist for **touch/pen**. For `pointerType === "mouse"`, `mousedown` is the native event, not a compatibility event, and it fires regardless (Chrome does not suppress it; behavior is not even consistent cross-browser). And `stopPropagation()` on the capture-phase `pointerdown` does nothing to `mousedown` — they are different event types entirely.

So walk the calibrate case concretely. In `calibrate` mode you `pointerdown` on a `.calibrate-handle` (which svg.py-style code appends directly under `<svg>`, **not** inside an `<a>`):

1. Router (capture `pointerdown`): `preventDefault()`, `stopPropagation()`, `isPanning=false`, `startCalibrate()`. Pointer captured on the handle.
2. The native `mousedown` still fires and bubbles to `svgElement.onmousedown` (floorplan.js:254). Line 256 guard is `e.target.closest('a') || closest('button')` — a calibrate handle is neither, so it does **not** return. `zoomMode` is false (forced), so it takes the PAN branch and sets `isPanning = true` again (line 289), capturing `startPoint`.
3. Now `pointermove` runs your calibrate resize **and** the native `mousemove` hits `onmousemove` (line 306): `isPanning` is true → it pans the viewBox via `gsap.to`.

Result: dragging a calibrate corner both resizes the blueprint and pans the canvas underneath it. The overlay's rAF loop then chases the moving CTM. Unusable.

Markers only escape this by *accident*: they render as `<a><g data-tile-id>` (svg.py:945-951), so `onmousedown`'s `closest('a')` guard (line 256) returns early and `isPanning` stays false. That is the real mechanism that saves markers — not the router's `preventDefault`. Any marker that isn't `<a>`-wrapped, or any non-anchored handle, is unprotected.

**Fix:** install a **capture-phase `mousedown`** router on `svgElement` (not just `pointerdown`). When `uiMode !== 'view'` and the target is a marker/handle (or `activeDrag` is set), call `e.stopPropagation()`. Because the target is a descendant of `svgElement`, `stopPropagation()` in the capture phase halts before the bubble phase, so the property handler `svgElement.onmousedown` never runs. That is the only reliable way to inertize the untouched bubble-phase handlers without editing them. The `pointerdown` capture listener is the wrong event type to gate `mousedown`.

---

### BLOCKER 2 — "Next canvas click places" collides with empty-canvas pan; every pan-release drops a marker

Decision 2 keeps empty-canvas pan live in every mode (router early-returns without `preventDefault` when the gesture hits neither marker nor handle). Step 4 arms placement so the "next canvas click POSTs" at the click point.

Concrete failure: in `place` mode with a type+object armed, the user drags the empty background to reposition the view before dropping. Router early-returns → `onmousedown` runs the pan → `onmouseup` sets `isPanning=false` → a `click` fires on the `<svg>` background (mousedown and mouseup on the same background element ⇒ click, even after movement). `onClickCapture`'s suppression fires only when "a drag just moved OR (`uiMode!=='view'` && `target.closest('a')`)". An empty-canvas pan has no `activeDrag` (the router forgot about it) and no `<a>` ancestor, so the click is **not** suppressed → it commits a placement at the pan-end coordinate. Panning to frame the shot silently drops an object.

**Fix:** the router must not "return and forget" on empty canvas in edit modes — record the pointerdown client position even when it lets pan proceed, then in `onClickCapture` suppress the place-commit when the pointer traveled beyond `DRAG_THRESHOLD_PX` between down and click. Placement disambiguation cannot rely on `activeDrag` alone because the pan path is invisible to Wave D state.

---

### BLOCKER 3 — `reloadSvgPreservingMode()` leaks document-level listeners on every placement

Decision 8 re-fetches the SVG and re-runs the existing init after each successful place. But `initializeSVG` (and Step 3 §D) attaches listeners at two scopes:

- On `svgElement`: `addEventListener('wheel')` (line 216), the `MutationObserver`, plus Wave D's capture `pointerdown`/`click`/`wheel`. These die with the replaced element — fine.
- On `document`: Step 3 §D's `keydown` (Escape) and Step 3's `pagehide`/`visibilitychange` flush hooks. **These are not GC'd when the SVG is swapped.**

Re-running init per placement stacks the document-level listeners. After N placements, one Escape calls `cancelDrag()` + `setMode('view')` N+1 times, and `pagehide` runs `flushAllPatches()` N+1 times racing each other's `AbortController`s. The original code called `initializeSVG` exactly once, so this is a new defect the reload introduces.

**Fix:** register all `document`-scoped listeners once, outside `initializeSVG` (module/DOMContentLoaded scope), or have `reloadSvgPreservingMode` explicitly `removeEventListener` them before re-init. Also confirm the line-216 `wheel` listener isn't re-added to a persistent node.

---

### FINDING 4 — The rotated-blueprint corner-drag test asserts the wrong invariant; the corner math is under-specified

Step 5's tests: for a **non-rotated** corner drag, "opposite corner pixel-fixed" (correct). For a **rotated** corner drag, "no wobble across 20 frames (**bg center drift < ε**)". Those are contradictory. A corner resize with the opposite corner held fixed *must move the center* — the center is the midpoint of the two diagonal corners, and changing one corner moves the midpoint. This is true rotated or not. If the rotated implementation actually keeps the center fixed (drift < ε), then it is resizing symmetrically about the center, which means the opposite corner is **not** fixed — i.e., it wobbles, the exact bug Step 5 claims to solve.

This also exposes that the blueprint never specifies the world-space reconstruction step. With rotation applied as `rotate(deg, cx, cy)` about the center, once width/height change the center moves, so pinning the opposite *world* corner requires: transform pointer + fixed corner into the unrotated local frame (using the dragStart pivot/angle), compute the new local AABB and local center, then set world center = `fixedCornerWorld + R(rot)·(localCenter − fixedCornerLocal)`. "recomputes rect absolutely (opposite corner fixed)" hand-waves past this; if bg_x/bg_y are just set to the local-AABB min while rotating about the new center, the opposite corner drifts on-screen.

**Fix:** state the world-reconstruction explicitly, and change the rotated-blueprint assertion to "the dragged corner's opposite **screen** corner is invariant" (the same invariant as the non-rotated test), not center drift.

---

### FINDING 5 — `touch-action: none` on inner SVG elements is unreliable; touch parity is not actually delivered

Step 2.3 sets `svg[data-ui-mode="place"] g.object{touch-action:none}` and the same on calibrate handles. `touch-action` is honored on the root `<svg>` (and HTML boxes) but is historically ignored on **inner** SVG elements in Chromium. With it ignored, a touch-drag on a marker/handle lets the browser claim the gesture for scroll before your `pointermove`/`setPointerCapture` engage → the page scrolls instead of dragging, and you may get a `pointercancel`. Since the blueprint claims touch parity as a resolved blocker, this is a gap, not a win.

**Fix:** apply `touch-action: none` to the **root `<svg>`** (or the `#floor-plan-svg` container) while `uiMode !== 'view'`, rather than per-marker. Keep `setPointerCapture` + `preventDefault` as the second line of defense. (On touch, `pointerdown.preventDefault()` *does* suppress compat mouse events, so Blocker 1 doesn't recur on touch — but only if the gesture reaches you, which requires the root-level `touch-action`.)

---

### FINDING 6 (low confidence) — `killTweensOf` leaves the JS `viewBox` variable ahead of the frozen attribute

Pan/zoom set the closure `viewBox` to the *target* immediately (lines 250, 368) while GSAP tweens the attribute over 300ms. `gsap.killTweensOf(svgElement)` on drag start freezes the attribute mid-tween, but the `viewBox` variable still holds the target. Drag math itself is safe (it reads `getScreenCTM()` fresh — good). But the *next* pan after a drag does `endPoint = {viewBox.x, viewBox.y}` from the stale target, so the first post-drag pan jumps by the leftover tween delta. Edge case, pre-existing latent, newly reachable via the router's `killTweensOf`. Worth a one-line resync of `viewBox` from the actual attribute after `killTweensOf`.

---

### Confirmed sound (grounded, no objection)

- Reverse URL names all resolve: no explicit `basename`, so DRF derives `floorplantile` / `floorplan` from the querysets → `floorplantile-place`, `floorplantile-list`, `floorplan-placeable-types`, `floorplan-detail`, `floorplan-convert-to-freeform` are correct. The template `{% url %}`s won't 500 the page.
- `place` on `FloorPlanTileViewSet` (`detail=False`, POST→`add_floorplantile`) is the right home; the `convert-to-freeform`-on-`FloorPlan` POST→`add_floorplan` mismatch is real and avoided.
- `CALIBRATION_FIELDS` fast path on `FloorPlanSerializer.update` is genuinely required — calibrate PATCHes the plan detail and would otherwise re-run `full_clean` per frame and reject a pure reposition on `background_image`/`placement_mode`. Mirrors the existing `TILE_GEOMETRY_FIELDS` pattern correctly.
- Server-drawn `.focus-ring` with `vector-effect="non-scaling-stroke"`, toggled by `.is-focused`, survives the viewBox transforms and stays testable in `test_svg.py`. Correct call over JS-created rings.
- The `makePatchChannel` seq/abort/trailing-debounce + `keepalive` + `flushAll` on mode-exit/`pagehide` is the right shape (payloads are tiny, under the keepalive cap).
- Reading `getScreenCTM()` fresh every event + `killTweensOf` correctly neutralizes the "GSAP CTM lie" for the drag math (the CTM reflects the live attribute at each instant; freezing the tween removes the moving target).
- The existing wheel handler is already shift-gated (line 217 `if (!e.shiftKey) return`), so "wheel-zoom mid-drag" is a narrow case; the capture-phase wheel block is still correct and harmless.
- MutationObserver won't thrash from per-frame handle repositioning: its `attributeFilter` is `['data-tooltip']` only (line 461), and handle x/y/width rewrites don't touch it. The overlay `childList` add does re-run `initTooltips` once on build — negligible.

The single highest-leverage fix is Blocker 1: replace the `pointerdown`-preventDefault gating with a capture-phase `mousedown` `stopPropagation()` router, because the blueprint's stated mechanism for coexisting with the untouched pan/zoom handlers does not hold for mouse input, and everything in place/calibrate rides on it.

## Critic 2: persistence + security

I've grounded myself in `serializers.py`, `views.py`, `models.py`, and `registry.py`. Here's my adversarial review through the persistence + security lens. Findings ranked most-severe first; concrete break scenarios with fixes, then the sound parts confirmed briefly.

---

## BREAK 1 — Reload-after-place races an outstanding geometry PATCH → stale position persisted and displayed (HIGH)

**Where:** Decision 8 + Step 4 "Place flow": on 201, `reloadSvgPreservingMode()` re-runs fetch+inject. `flushAllPatches()` is only invoked in `setMode` transitions (Decision 4, Step 3 §C), and placing is *not* a mode transition.

**Scenario:** In place mode, drag marker A. `onDragMove` calls `channelFor('tile:A').schedule({pos_x,pos_y})` — a trailing 200 ms debounce. Before it fires (or before its fetch resolves), the user picks object B and clicks commit. The place POST returns 201, and `reloadSvgPreservingMode()` immediately issues the SVG GET. That GET renders from the DB, where A's PATCH has **not yet committed**. The GET returns A at its old position; then A's PATCH commits. The injected DOM now shows A stale, and there is no reconcile because the seq guard only compares *PATCH* responses on A's channel — it knows nothing about a GET on a different endpoint. The user sees A snap back to where it was.

**Fix:** `reloadSvgPreservingMode()` must `await flushAllPatches()` where flush **awaits each channel's in-flight fetch to fully resolve** (not just fire the trailing `setTimeout`) before issuing the reload GET. The blueprint's flush is fire-and-forget; it needs to return a promise that resolves after the server has acknowledged every pending write.

---

## BREAK 2 — Debounce + AbortController + client-side seq does NOT guarantee server write ordering; out-of-order commits clobber silently (HIGH)

**Where:** Step 3 §B `makePatchChannel`: "on flush abort prior AbortController, ++seq … drop response if mySeq<applied."

**Scenario:** The seq guard is purely client-side — it decides which *response* to apply to client state. It does nothing about the order writes land on the server. `AbortController.abort()` is best-effort: it cancels the client's read of the response, but if the request already reached the server, the server keeps processing it. Sequence: drag A ends → `flush1` sends `PATCH{pos:P1}` (slow RTT). User immediately drags A again and ends → debounce fires `flush2`, which calls `abort()` on controller1 (server already received request1) and sends `PATCH{pos:P2}`. Server commits P2, then commits P1 (reorder / P1 was mid-transaction). Final DB = **P1 (stale)**. Client shows P2 and never reconciles, because the response it *does* apply is flush2's (P2). Client and server diverge permanently with no error surfaced. Keyboard nudges (Step 6, auto-repeat arrow) and rapid re-drags are the realistic triggers, especially when `PATCH_DEBOUNCE_MS` (200) is near or below RTT.

**Fix:** Serialize per channel — do not send a new PATCH until the prior one's response is received (queue the coalesced payload, send on resolve), OR carry the client `seq` in the request body and add a server-side monotonic guard on the tile (reject/ignore a geometry write whose seq is < the last applied). AbortController + a client-only seq cannot order server writes.

---

## BREAK 3 — `location_field` (picker filter) and `location_resolver` (server validation) are independent sources of truth; the picker offers objects the place endpoint then rejects (HIGH)

**Where:** Step 1.1 adds `location_field` to `PlacementType` "as the single source for the picker's location filter." But server-side eligibility is `registry.resolve_location(obj)` via the `location_resolver` callable (models.py:676, `_validate_generic_placement`). These are two unconnected descriptors.

**Scenario (PowerFeed / any cross-app type):** A registrant sets `location_field="power_panel__location"` (so `placeable_types.object_source.params` filters candidates by `power_panel__location=<plan.location>`), but leaves the default `location_resolver=_default_location`, which does `getattr(obj,"location",None)`. A PowerFeed's own `.location` is null (its location comes through its power panel). Picker shows all correct PowerFeeds; user picks one; place POST → serializer/model `resolve_location` returns `None` → 400 "has no resolvable Location." **Every candidate the picker offers is rejected.** The blueprint even claims `location_field` "prevents picker/eligibility divergence" — it does the opposite: it introduces a *second* location descriptor with nothing tying it to the resolver.

**Fix:** Derive one from the other, or assert parity at registration time (fail loudly if `location_field` and `location_resolver` disagree for a sample), and add a per-registered-type test that asserts, for a real object, the `location_field` ORM filter and `resolve_location(obj)` yield the same Location. Step 1's test only checks that PowerFeed's *param string* is `power_panel__location`; it never checks the resolver agrees.

---

## BREAK 4 — Every place-endpoint tile is mislabeled `allocation_type=RACKGROUP` (MED)

**Where:** `create()` calls `tile.validated_save()` → `clean()` → `allocation_type_assignment()` (models.py:549-562).

**Scenario:** That method sets `OBJECT` only when a **typed FK** (`rack/device/power_panel/power_feed`) is set. Generic-placed tiles have all four null but always carry a `status` (place sets a default), so the first branch `if self.status is not None: allocation_type = RACKGROUP` fires, and the typed-FK branch never overrides it. Result: a device/rack placed through the generic pair is persisted with `allocation_type=RACKGROUP`. It doesn't corrupt uniqueness (null origins make the `unique_together` NULL-distinct) and overlap checks are suppressed in freeform, but any filter/report/legend that distinguishes object vs rack-group tiles now misclassifies every freeform placement, and any future overlap logic that keys on `allocation_type` will treat these as rack-group regions.

**Fix:** Teach `allocation_type_assignment` about the generic pair — set `OBJECT` when `self.placed_object_id is not None` (and no rack_group), mirroring the typed-FK branch.

---

## BREAK 5 — Object-level authorization for placement lives *only* in `serializer.validate()`; no defense-in-depth (MED, design fragility)

**Where:** Decision 5 puts `place` on `@action(detail=False)`. A `detail=False` action never calls `get_object()`, so NautobotModelViewSet's usual `get_queryset().restrict(user, …)` object-level gate does not run. All object-level enforcement (change on the FloorPlan, view on the placed object) rests entirely on `FloorPlanTilePlacementSerializer.validate()`.

**Assessment:** The validate() design itself is *sound* — `.restrict(user,"change").filter(pk=…).exists()` for the plan and `.restrict(user,"view").filter(pk=…).first()` with missing≡forbidden for the object both avoid existence leaks, and re-checking location in the model `clean()` closes the TOCTOU. The fragility is that the model layer (`_validate_generic_placement`) does **no** permission check at all, so any code path that constructs a placement without going through this exact serializer (a future bulk-import serializer, a management command, a nested writable serializer) silently bypasses authorization. 

**Fix:** Keep the serializer gate, but document it as the sole authorization boundary with a loud comment, and add a regression test that a token with `add_floorplantile` but no object `view`/plan `change` is rejected — plus consider a thin object-perm assert in the model or a shared `authorize_placement(user, plan, obj)` helper both paths call.

---

## BREAK 6 — Concurrent legacy typed-FK write racing a generic placement returns 500, not 400 (MED)

**Where:** `create()` maps `IntegrityError → 400`. The standard `FloorPlanTileSerializer` (typed-FK path, unchanged) does not.

**Scenario:** Two concurrent requests target the same object X: (1) `place` via generic pair; (2) a normal tile create/PATCH setting `device=X`. Path (2) mirrors X into the generic pair on save (`_sync_placed_object_from_typed`). Both `full_clean`s pass (neither sees the other's uncommitted row), both insert; the DB unique constraint `floorplantile_unique_placed_object` fires on the second commit. The `place` request degrades gracefully (400); the typed-FK request surfaces a raw `IntegrityError` → 500. Pre-existing for typed writes, but Wave D increases the collision surface by adding a second concurrent writer for the same pair.

**Fix:** Wrap the mirrored-pair unique violation on the typed path too (or add a DRF `UniqueTogetherValidator`-equivalent for the generic-pair `UniqueConstraint` on the shared model serializer).

---

## Lower-severity notes (LOW)

- **`_default_tile_status()` is nondeterministic.** "First Status whose content_types include FloorPlanTile" depends on default ordering (name). A plan could silently place devices as e.g. "Decommissioning" if that sorts first. Prefer an explicit default (a configured status, or the same default the typed create path uses).
- **CSRF token source unspecified.** The `X-CSRFToken` header is correct and necessary (session auth enforces CSRF; the header is present — this part is sound). But the blueprint never says where the token comes from; hardcoding cookie name `csrftoken` breaks under a non-default `CSRF_COOKIE_NAME`. Read it from the cookie name Nautobot exposes, or from a DOM-rendered token.
- **"Already placed" existence leak.** `create()`/validate reject with a message naming the object when `for_object(obj).exists()` — and `for_object` uses the unrestricted default manager, so it reports a conflict even for a tile on a plan the user cannot view. Minor info leak (confirms the object is placed somewhere). Keep the check global (uniqueness must be global) but consider a generic "already placed" message.
- **Calibration fast path allows degenerate `bg_width`/`bg_height`.** `CALIBRATION_FIELDS` fields are raw `FloatField(validators=[validate_finite])` with no min, and the fast path skips `full_clean`. A `bg_width<=0` persists and renders an inverted/collapsed blueprint. Add `>0` validation for bg_width/bg_height even on the fast path.

---

## Confirmed sound

- **Geometry fast-path bounds are still enforced.** DRF field validators (`min_value=0/max_value=1`, `validate_finite`) run in `is_valid()` before `update()`, so `pos_x=1.5`/`NaN` are rejected even though the fast path skips `full_clean`. The fast-path subset check `set(validated_data).issubset(TILE_GEOMETRY_FIELDS)` correctly falls through to full validation when `placed_object_id` or any non-geometry field is present.
- **Place validate() permission design** (plan `change` via `.exists()`, object `view` via `.restrict().first()` with missing≡forbidden, location re-checked in model `clean()` to close TOCTOU) is the right shape and leak-free — the only gap is defense-in-depth (BREAK 5).
- **Generic-pair uniqueness holds across both write paths** because the typed FK mirrors into the pair on save; the `UniqueConstraint` is the single global guard. The only rough edge is the 500-vs-400 on the typed path under concurrency (BREAK 6).
- **CSRF is handled** for the session-authenticated browser flow via the `X-CSRFToken` header; `keepalive` fetches on `pagehide` remain same-origin and carry the cookie.
- **Model perms** map correctly (POST→`add_floorplantile`, PATCH→`change_floorplantile`, placeable-types GET→`view_floorplan`) via Nautobot `TokenPermissions`.

The two findings I'd treat as merge-blockers are **BREAK 1** (reload/PATCH read-after-write race) and **BREAK 2** (client-only ordering can't prevent server-side clobber) — both are silent-divergence bugs with no user-visible error, exactly the class the debounce/seq machinery was supposed to eliminate. **BREAK 3** is a near-certain "PowerFeed placement is completely broken on day one" if the resolver/field parity isn't tied together and tested.

## Critic 3: accessibility + regression

I read the actual viewer (`floorplan.js`), the include template, and the freeform marker renderer (`svg.py:_draw_freeform_tile`). The blueprint gets the big-picture routing right, but several a11y/regression claims break against the real DOM and the real scope graph. Grounded findings below, most-severe first.

---

## BREAKS

### 1. Roving tabindex targets the wrong node — every marker's `<a href>` stays in the tab order (HIGH, confirmed)
`_draw_freeform_tile` (svg.py:945-955) wraps each marker as `drawing.a(href=…, id="{model}-{pk}")` containing `<g class="object" data-tile-id=…>`. The **anchor is the focusable**, and an `<a href>` is *natively* in the tab order.

The blueprint's roving plan (Step 6 §F) collects `g.object[data-tile-id]` and sets `tabindex=0/-1` on the inner `<g>`. That does nothing to the anchors. Concrete failure: view mode, 40 markers, press Tab repeatedly — you stop on all 40 `<a>` elements in **DOM order** (not the intended `(pos_y,pos_x)` sort), and the Arrow-key roving handler fights native Tab because both are live. Roving is fully defeated; a keyboard user tabs through every marker plus the synthetic roving stop.

Fix: manage the **anchor** for roving — set `tabindex="-1"` on the `<a>` of every non-active marker and `tabindex="0"` on the active one (Step 2.2 must move `role`/`tabindex`/`aria-label` onto the `<a>`, or drop the `href` in freeform mode and synthesize navigation). You cannot leave the anchors tabbable and claim roving.

### 2. `Enter` on a focused marker in place mode navigates away instead of committing (HIGH, confirmed)
Same anchor wrapper. Step 6 says place-mode `Enter` = flush the pending PATCH. But the focused element is inside an `<a href="{device url}">`; native `Enter` on/inside an anchor **activates the link**. Concrete: place mode, nudge a marker with arrows, press Enter to commit → browser navigates to the device detail page, losing the session and the pending move. The blueprint never mentions `preventDefault()` on Enter here. Fix: in place/calibrate mode the marker keydown handler must `preventDefault()` on Enter (and Space, which also activates), and only then flush.

### 3. `highlightTimers` is mis-scoped — the 20s-freeze fix silently no-ops (HIGH, confirmed)
Step 3 §E declares `let highlightTimers=[]` in the "Wave D constants" block, which Step 3 explicitly places *inside `initializeSVG`*. But `highlightElement` (floorplan.js:466) is a **sibling** of `initializeSVG` (both indented 4 spaces directly under the `DOMContentLoaded` callback) — it is NOT nested inside `initializeSVG`. So `highlightElement` pushing its two `setTimeout` handles (lines 529, 537) into an `initializeSVG`-local `highlightTimers` is a `ReferenceError` (or, with `var`, writes a *different* binding). `clearHighlightFreeze()` then clears an empty array and the freeze never releases. The headline "20s dead canvas" fix does nothing.
Fix: declare `highlightTimers` in the outer `DOMContentLoaded` scope right beside `var isPanning` (line 7), which both `highlightElement` and the in-`initializeSVG` `clearHighlightFreeze` can see. (Note also the blueprint's Step 3 rationale — "code inside `initializeSVG` reaches `isPanning`, `selectionRect`, `zoomMode`" — is only true because those are *outer*-scope vars; `highlightTimers` needs the same treatment and the blueprint puts it in the wrong place.)

### 4. "Disable mode buttons during highlight" contradicts its own deep-link test and has no re-enable trigger (HIGH)
Decision 9 / Step 6 say to disable the mode buttons while a highlight is active "so calibrate can't be entered into a frozen canvas." But the Step 6 acceptance test says: "deep-link `?highlight_rack=…` then switch to place mode → canvas immediately interactive (no 20s wait)." You cannot switch to place mode if the button is `disabled`. The design and its test directly conflict. Worse, `enableZoomAndPan()` fires at `ZOOM_DURATION` (line 533, ~5s) but nothing in the blueprint re-enables the mode buttons — they're disabled at highlight start with no stated re-enable, so after any deep-link highlight, place/calibrate is locked out for `HIGHLIGHT_DURATION` (20s) or indefinitely.
Fix: drop the "disable mode buttons" idea. The correct release path is already present — `setMode()` calls `clearHighlightFreeze()`, so *entering* place/calibrate is what should cancel the freeze timers and call `enableZoomAndPan()`. Let the buttons stay live; the mode transition releases the freeze.

### 5. Global `document` `keydown` Escape hijacks the page and breaks the place combobox (HIGH/MED)
Step 3 §D: `document.addEventListener('keydown', e => { if (e.key==='Escape'){ if(activeDrag) cancelDrag(...); setMode('view'); }})`. Two problems. (a) It's on `document`, so Escape *anywhere* on a Nautobot detail page — including inside the place-panel combobox `#place-object-input` — fires `setMode('view')`. Standard combobox Escape should only close the listbox; here it yanks the user out of place mode and discards the in-progress search. (b) `reloadSvgPreservingMode()` re-runs `initializeSVG` (see #6), so this `document` listener **stacks** — after N placements, N Escape handlers fire per keypress.
Fix: scope the Escape handler to the SVG region (attach to `svgElement`, or guard `if (document.activeElement is inside the place panel) return;` and let the combobox handle its own Escape), and install document/global listeners exactly once outside `initializeSVG`.

### 6. `reloadSvgPreservingMode()` re-runs `initializeSVG` and leaks listeners/observers every placement (MED)
The existing load path (lines 29-58) calls `initializeSVG` from the fetch `.then`. `initializeSVG` does `svgElement.addEventListener("wheel", …)` (line 216), `new MutationObserver(...)` + `observer.observe(...)` (lines 453-462), and the blueprint adds a `document` keydown and a capture `pointerdown`/`click`/`wheel` router. Re-running the whole thing on every successful place (Decision 8) means: the old `MutationObserver` is never `disconnect()`-ed (it stays registered on the detached old SVG), and any `document`-scoped listeners accumulate (see #5). The wheel/pointer listeners on `svgElement` die with the replaced node, but the document ones do not.
Fix: split `initializeSVG` into "install once" (document/global listeners) vs "per-SVG wiring," and `observer.disconnect()` before re-injecting. Or reload by swapping only the SVG subtree and re-running just the per-SVG portion.

### 7. Focus ring is sub-pixel at low zoom and focused markers are never scrolled into view (MED — WCAG 2.4.7)
The proposed `.focus-ring` rect is drawn inside the marker `<g>` sized in **user units** (the marker is `pw×ph = frac*cw`). `non-scaling-stroke` keeps the *stroke* 2px, but the rect *area* scales with the viewBox. Concrete: whole-plan zoom (viewBox = full content), a `DEFAULT_MARKER_FRAC` marker renders a few px across, so the focus ring is a ~2px-stroked dot — not a discernible focus indicator, failing "focus visible." Second half: roving/Tab can move focus to a marker that is **outside the current panned/zoomed viewBox**; nothing scrolls it into view (unlike `highlightElement`, which animates the viewBox). Concrete: zoom into the top-left, Arrow-roving to a bottom-right marker → focus is off-screen, sighted keyboard user sees no focus at all. Step 6's test asserting "ring visible at min and max zoom" will fail at min zoom and for off-viewport markers.
Fix: render the focus indicator as CTM-positioned overlay chrome (fixed screen size, like the calibrate overlay) rather than an in-content rect; and on marker `focus`, pan the viewBox so the focused marker is within the visible region.

### 8. `prefers-reduced-motion` only silences highlight CSS animations, not the gsap viewBox tweens (MED)
Step 2.3's media query disables `.spotlight-effect/.highlight-border/.indicator-arrow`. But the primary motion is the gsap `viewBox` animation on pan (line 360, 0.3s), wheel-zoom (line 242), and the 1s highlight zoom (line 519) — none touched (the blueprint says existing tweens stay). So a reduced-motion user deep-linking a highlight still gets a 1s animated zoom, and every pan animates. The blueprint claims reduced-motion coverage but delivers it only for decorative overlays.
Fix: gate the gsap `duration` in `highlightElement` (line 519/549) and, if acceptable, the pan/zoom tweens behind `reduceMotion` → `duration:0`. `highlightElement` is not a "pan/zoom handler body," so editing its duration is within the no-edit rule; the pan tween is the genuine tension worth calling out explicitly rather than papering over.

### 9. `disableTooltipFor(el)` targets the `<g>`, but tippy is bound to the `<a>` parent (MED, confirmed)
`_draw_freeform_tile` puts the tooltip on the anchor: `link["data-tooltip"]=…; link["class"]="object-tooltip"` (svg.py:983-984). `initTooltips` (line 98) binds tippy to `.object-tooltip` — i.e., the `<a>`, not the inner `<g class="object">`. Step 4's `disableTooltipFor(el)` receives the drag target `g.object[data-tile-id]`. Disabling/hiding tippy on the `<g>` misses the instance on the ancestor `<a>`, so the tooltip pops open mid-drag over the moving marker.
Fix: resolve the tippy instance from the nearest `.object-tooltip` ancestor (`el.closest('.object-tooltip')`) and call `instance.hide(); instance.disable()` on it; re-enable on drag end.

---

## SOUND (brief)
- Decisions 1–2 (single capture-phase router, `preventDefault` to kill the compat `mousedown`, keeping empty-canvas pan live by *not* preventing when the gesture misses a marker/handle) are correct against the real handlers: `onmousemove` early-returns on `!isPanning && !selectionRect` (line 307) and `onmousedown` bails on `closest('a')`/`closest('button')` (line 256), so setting `isPanning=false` in the router genuinely inertizes them without editing them.
- Server-drawn `.focus-ring` toggled by JS (Decision 7) is the right instinct over JS-created rings for testability — the flaw is only its geometry (finding #7), not the render-on-server choice.
- `CALIBRATION_FIELDS` fast path on `FloorPlanSerializer` (Decision 10) is genuinely required; the tile fast path already exists, and calibrate PATCHing plan detail would otherwise re-run `full_clean` and reject pure repositions.
- Legend mirrored to HTML with the in-SVG legend `aria-hidden="true"` is the correct SR channel (the in-SVG `<text>` legend is not reliably navigable) — just make sure the mirror is rebuilt/cleared on each `reloadSvgPreservingMode`, or it duplicates.

## Two smaller notes
- `role="application"` on the root `<svg>` (Step 2.2) is a known SR footgun: it suppresses the virtual cursor, so non-editing SR users lose ordinary reading of marker labels and must enter the roving flow. Given the HTML legend mirror exists, consider `role="group"`/`img` + an explicit "press Enter to edit" affordance instead.
- Decision 8's "programmatically `startDrag` the new marker" after place has no `pointerId`, so `el.setPointerCapture(pointerId)` (Step 4 `startDrag`) can't run — a synthetic call has no active pointer. After reload it should `selectMarker()` (focus + reveal rotate grip), not `startDrag()`.
