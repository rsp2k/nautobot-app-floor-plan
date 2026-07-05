# Generalized Object Placement — Locked Implementation Spec

Produced by the `floorplan-generic-objects-design` ultracode workflow (8 agents: 4 analyze, 1 synthesize, 3 adversarial critique). Source of truth for the generic-FK epic (G1-G4). Composes on committed Wave A/B.

# Generalized Object Placement: Merged Implementation Spec

Tech-lead synthesis of the four expert analyses (model-gfk, registry-icons, forms-api, cross-app) into one ordered, conflict-free build plan. Composes on top of already-committed Wave A/B (blueprint background + freeform placement). Geometry fields (`pos_x/pos_y/width/height/rotation`), the `floorplantile_origin_pairing` constraint, and `convert_to_freeform()` are untouched by this epic; the generic pair answers "what object," the geometry fields answer "where," and they are orthogonal.

## Resolved contradictions (decisions binding on all waves)

1. **Keep vs drop the 4 typed FKs.** KEEP `rack/device/power_panel/power_feed` as declared, fully writable and readable, through G1 to G4. They are the source of truth for legacy rows until the 0013 backfill mirrors them into the generic pair; every new read path prefers `placed_object` and falls back to the typed FKs during transition. Dropping them is a separate post-G4 migration, out of scope here. All four analyses agree.

2. **One registry module, one public import surface.** Canonical location is a package `nautobot_floor_plan/placement/` (from registry-icons), because it is the richest and is what cross-app siblings import. The four analyses each named it differently (`placement_registry.py`, `registry.py`, `placement.py`, `placement/`); we standardize on `nautobot_floor_plan.placement`. The package `__init__.py` re-exports the singleton `registry`, `PlacementType`, `register`, `register_variant`. For back-compat with the cross-app draft, `register_placeable = register` is aliased so either name works.

3. **One `PlacementType` record** (merged from registry-icons `PlacementType` and forms-api `PlaceableType`). Fields: `key`, `label`, `icon` (glyph-key string or inline `{"viewbox","paths"}` dict), `color` (hex, no `#`), `location_resolver(obj)`, `url_resolver(obj)` (default `obj.get_absolute_url()`), `tooltip_builder(obj)`, `legend_order`, plus lazily-resolved `content_type` and the G3 picker helpers `eligible_queryset(location)` / `picker_source()`. `color` is a fallback swatch only; the renderer keeps preferring `obj.status.color` when the object has a status.

4. **Registration mechanism.** Primary and documented path is the sibling app's `AppConfig.ready()` importing `nautobot_floor_plan.placement` inside `try/except ImportError` (registration is push-based; floor-plan never imports the siblings; either app installs standalone). Registration is keyed by the dotted `"app_label.model"` string, never by importing the model class, so it is safe before ContentTypes exist. The setuptools entry-point mechanism is documented as an optional third-party alternative but is not built in G1 to G4. This resolves registry-icons (offered both) and cross-app (argued ready-only) in favor of ready-only.

5. **Device-by-Role differentiation.** Use the discriminator + variant approach (registry-icons `set_discriminator` / `register_variant`), NOT the "icon may be a callable" approach (cross-app). Variants give per-role `label`, `color`, and `legend_order`, which the G3 picker palette and the G2 legend both need, not just a glyph swap. A single ContentType (`dcim.device`) fans out into role variants; unmapped roles fall through to the `dcim.device` base.

6. **Location resolution.** All location checks go through `PlacementType.location_resolver`, replacing the hardcoded `hasattr(obj, "location")` branch. Default resolver reads `obj.location`; `power_feed` overrides to `obj.power_panel.location`. Add the explicit null-guard from cross-app: a `None` resolved location raises a clean, actionable "no resolvable Location; link it to a Device / set its site first" error instead of a silent `!= None` mismatch. This is required because `Printer`/`Phone` expose `location` as a `@property` returning `device.location` (None until linked), while `PhoneSystem`/`PrinterFleet` have a real nullable FK.

7. **Unique constraint + reverse lookup.** A partial `UniqueConstraint(fields=["placed_content_type","placed_object_id"], condition=Q(placed_object_id__isnull=False))` replaces the free uniqueness the four OneToOneFields gave. Reverse lookup (there is no auto `obj.floor_plan_tile` for a GFK) goes through a manager: `FloorPlanTile.objects.for_object(obj)` returns the queryset, callers use `.first()`. Every legacy `getattr(obj, "floor_plan_tile", None)` call site is routed through it.

8. **Sorting across heterogeneous types.** You cannot `Case/When`-join arbitrary target tables. Adopt forms-api's denormalized `placed_label` CharField (db_index) on the tile, maintained in `save()` from the registry label plus object display. The table/search/legend all read it; the four-FK `Case/When` annotation in `views.py` is dropped. This column lands in G1.

---

## Wave G1: model + migration + registry scaffold + validation refactor

Goal: the GFK pair, the registry core (resolvers only, no rendering), backfill, and generalized validation land so the four DCIM types behave byte-identically while cross-app `PrimaryModel`s become admissible. Independently testable: migrate up/down, place all four legacy types via existing form/API, confirm the generic pair mirrors and the unique constraint holds.

File-by-file, in dependency order:

1. **`nautobot_floor_plan/placement/registry.py`** (new). `PlacementType` dataclass (frozen) with the merged field set from decision 3. `PlacementRegistry` keyed by dotted label, with `_CTEntry` holding a `base` type plus `variants` dict and an optional `discriminator` callable. Methods: `register(model_label, *, label, icon, color, location_resolver=None, url_resolver=None, tooltip_builder=None, legend_order=100, replace=False)`, `register_variant(...)`, `set_discriminator(...)`, `resolve(obj) -> PlacementType|None` (returns None, never raises, for unregistered types; catches discriminator exceptions), `resolve_location(obj)`, `for_content_type(ct)`, `for_object(obj)`, `allowed_content_types() -> QuerySet[ContentType]`, `all()`/`placeable_model_labels()`. Default resolvers: `_default_location = lambda o: getattr(o, "location", None)`, `_default_url = lambda o: o.get_absolute_url()`, `_default_tooltip(label)`. Module singleton `registry`; aliases `register`, `register_variant`.

2. **`nautobot_floor_plan/placement/__init__.py`** (new). Re-export `registry, PlacementType, register, register_variant`; alias `register_placeable = register`.

3. **`nautobot_floor_plan/placement/defaults.py`** (new). `register_builtins()` registering the four DCIM types with location resolvers (`rack`/`device`/`power_panel` -> `o.location`; `power_feed` -> `o.power_panel.location if o.power_panel_id else None`), plus `is_device_like`/icon/color placeholders that G2 consumes. Device-role variants and the discriminator are added in G2, not here.

4. **`nautobot_floor_plan/models.py`**:
   - Imports: `GenericForeignKey`, `ContentType`, `BaseManager`/`RestrictedQuerySet` from `nautobot.apps.models`.
   - Add fields on `FloorPlanTile` next to the four legacy FKs: `placed_content_type = FK("contenttypes.ContentType", on_delete=PROTECT, null=True, blank=True, related_name="floor_plan_tiles")`; `placed_object_id = UUIDField(null=True, blank=True, db_index=True)`; `placed_object = GenericForeignKey(...)`; `placed_label = CharField(max_length=255, blank=True, db_index=True)`.
   - `Meta.constraints`: keep `floorplantile_origin_pairing`, add the partial `floorplantile_unique_placed_object`.
   - Add `FloorPlanTileQuerySet.for_object(obj)` and `placed_object_ids(ct)`; wire `FloorPlanTileManager` and `objects = FloorPlanTileManager()` (subclass whatever restricted queryset the model uses today so `restrict()` keeps working).
   - Add helpers: `_has_placed_object()`, `_sync_placed_object_from_typed()` (mirror a set typed FK into the pair), `sync_legacy_fk_from_generic()` / `clear_legacy_fks()` (used by the G3 form), `_placed_rack()` (returns `self.rack` or `placed_object` when `isinstance Rack`, so rackgroup rules survive the eventual FK drop).
   - `save()`: populate `placed_label` from `registry.resolve(placed_object).label` + object display; call `_sync_placed_object_from_typed()` for programmatic writes.
   - Refactor validation (behavior-preserving for the four types): `allocation_type_assignment` uses `_has_placed_object()`; `_validate_single_object_assignment` additionally reconciles typed FK vs generic pair (raise if they describe different objects); `_validate_object_locations` becomes resolver-driven with the null-guard (decision 6); `_validate_installed_objects` gates on `isinstance(obj, Device)`; overlap/rackgroup methods swap `self.rack` -> `self._placed_rack()`. `clean()` calls `_sync_placed_object_from_typed()` right after `allocation_type_assignment`. Overlap math and `_validate_freeform` are geometry-only, unchanged; overlap still runs only for grid-positioned tiles in grid mode, so freeform placement of new types is unaffected.

5. **`nautobot_floor_plan/signals.py`** (new). `delete_tiles_for_placed_object(sender, instance, ...)`: on object delete, remove tiles whose generic pair points at it. No-op for the four DCIM types while their typed-FK CASCADE still fires (it clears the row first). Becomes load-bearing in G4 for cross-app types. Wire the `pre_delete` connection in G1 but scoped so it only matters for registered non-DCIM types.

6. **`nautobot_floor_plan/apps.py`** `ready()`: `super().ready()`, then `register_builtins()`, then connect the delete signal. (Entry-point discovery is not added.)

7. **`nautobot_floor_plan/migrations/0013_add_generic_placement.py`** (new). Depends on `contenttypes.0002_remove_content_type_name` and `0012_freeform_validation_nullable`. Operations in order: `AddField placed_content_type` (nullable), `AddField placed_object_id` (nullable, indexed), `AddField placed_label` (blank), `RunPython(backfill_generic_from_typed, unbackfill)`, `AddConstraint floorplantile_unique_placed_object`. Backfill reads raw `getattr(tile, f"{field}_id")` columns (never dereferences a cross-app FK through the frozen historical model), caches ContentType rows, `bulk_update` batch 500, also fills `placed_label` from a cheap `str(obj)` where derivable. Reverse `unbackfill` clears the pair and label only; typed FKs are untouched so a rollback to 0012 loses nothing. The GFK itself is virtual and is not a migration operation; only the two concrete columns plus the label are added.

**G1 tests:**
- Migration round-trip: `0012 -> 0013 -> 0012` with a fixture of one tile per legacy type; assert pair populated forward, cleared on reverse, typed FKs preserved both ways.
- `for_object()` returns the placing tile for each legacy type and `.none()` for an unsaved/None object.
- Partial unique constraint: placing the same object on a second tile raises `IntegrityError`; two "empty" (pair-null) rackgroup tiles coexist.
- `_sync_placed_object_from_typed()` mirrors each typed FK; `_validate_single_object_assignment` raises on typed-vs-generic conflict.
- `_validate_object_locations`: same-location passes; wrong-location raises; unregistered type raises "not registered"; registered-but-None-location raises the "no resolvable Location" message (use a `Printer` with `device=None`).
- `_validate_installed_objects` only fires for a Device in a rack; a PhoneSystem-shaped stub is ignored.
- `placed_label` populated on save and re-derived on object change.
- Registry: `resolve()` returns None (no raise) for an unregistered model; `register(..., replace=False)` warns and no-ops on duplicate.

**G1 towncrier fragment** `changes/<pr>.added`: "Added a generic placement target (`placed_content_type` + `placed_object_id`) to floor plan tiles, allowing any object type to be placed. Legacy typed relationships are backfilled and preserved."

---

## Wave G2: SVG icon rendering + legend

Goal: render `placed_object` through the registry with per-type glyphs, a legibility halo, Device-role variants, and a legend. Independently testable: existing freeform tiles now draw icons; unregistered types degrade to a labeled fallback pin; snapshot the SVG.

File-by-file:

1. **`nautobot_floor_plan/placement/icons.py`** (new). `ICON_GLYPHS = {name: {"viewbox": 24, "paths": [...]}}` embedding Lucide (ISC) stroke paths for `monitor, phone, printer, camera, wifi, thermometer, rack/server, power, plug, help`; `FALLBACK_ICON = "help"`. Paths embedded (not `<use href>`) so the "Save SVG" download stays self-contained, same rationale as the base64 blueprint.

2. **`nautobot_floor_plan/placement/defaults.py`** (extend): add `DEVICE_ROLE_ICON_MAP`, `_device_sub_key(device)` (normalize `role.name`, lowercase, collapse `[\s_]+` to `-`, then `.slug` fallback), `set_discriminator("dcim.device", _device_sub_key)`, and `register_variant` calls for `computer/camera/ap/climate/ip-phone`. Unmapped roles fall through to the `dcim.device` base.

3. **`nautobot_floor_plan/svg.py`**:
   - Constants: `ICON_MIN=18`, `ICON_MAX=44`, `ICON_FOOTPRINT_FRAC=0.55`, `CHIP_PAD=4`, `LEGEND_ROW_H=20`, `LEGEND_ICON=14`.
   - Replace `_resolve_tile_object` with `_resolve_placement(tile) -> (obj, ptype, color)`, backfill-aware (prefer `placed_object`, fall back to the four typed FKs), color = `obj.status.color` else `ptype.color` else gray.
   - Add `_draw_icon(...)` (clamp glyph size, opaque halo chip, accent-stroked glyph, optional counter-rotate to keep point markers upright) and `_icon_glyph(ptype)` (inline dict or `ICON_GLYPHS` lookup, fallback to `help`).
   - Revise `_draw_freeform_tile`: route url via `ptype.url_resolver(obj)` (replacing the hardcoded `reverse("dcim:"+url_type)`, which breaks for non-dcim namespaces), keep the backing rect under the icon as the drag target + status fill, call `_draw_icon`, accumulate `self._present_types`, build tooltip via `ptype.tooltip_builder`. Move `_draw_freeform_text` to place the name below the chip.
   - Add `_draw_legend(drawing, viewbox)`: sorted by `legend_order` then label, skip when fewer than 2 rows, include an "Unregistered" row when `None in _present_types`. Init `self._present_types = set()` at top of `render()` and call `_draw_legend` after the tile loop.
   - Leave `_draw_object_tile` (grid path) rectangle-based for back-compat; optionally route it through `_draw_icon` later.

4. **`static/nautobot_floor_plan/css/svg.css`** and **`dark_svg.css`**: append `.marker-icon-chip`, `.marker-icon-glyph`, `.legend-bg`, `.legend-label`, and `paint-order: stroke` label halos, with dark overrides.

**G2 tests:**
- Snapshot/structural SVG test: each of the four legacy types renders its glyph + chip; assert the `<path>` count and the accent stroke.
- Device-role discrimination: a Device with a "camera" role renders the camera variant glyph/color; an unmapped role falls back to the `dcim.device` base.
- Unregistered ContentType (stale/never-registered) renders the `help` glyph, gray, "Unknown" label, and an "Unregistered" legend row without a 500.
- Legend suppressed for a single type; shown and ordered for multiple; icon-size clamp holds at the 18px floor for a point marker and the 44px cap for a large footprint.
- `url_resolver` produces `get_absolute_url()` for a non-dcim object (mock).

**G2 towncrier fragment** `.added`: "Floor plan markers now render per-type icons with a legend; device markers pick an icon from their role."

---

## Wave G3: forms/API object picker + reverse lookup + template_content + filters/tables

Goal: pick any registered type in the UI/API, filter and sort tiles by any placed object, and make "View on Floor Plan" work through the generic reverse lookup. Independently testable: create/edit tiles for each type via form and API; drag-to-place; reverse buttons render.

File-by-file:

1. **`nautobot_floor_plan/registry.py` picker helpers** (extend the G1 registry, not a second module): add `eligible_queryset(location)` and `picker_source()` per `PlacementType`, and `allowed_content_types()` already present from G1. `LEGACY_FIELD_FOR_KEY = {"dcim.rack":"rack", ...}` lives with the form.

2. **`nautobot_floor_plan/forms.py`** `FloorPlanTileForm`: replace the four hardcoded object fields + implicit tab selector with a registry-generated `placed_type = ChoiceField` (Step 1, "What are you placing?") plus one `DynamicModelChoiceField` per registered type (Step 2). Preserve the four legacy field names via `LEGACY_FIELD_FOR_KEY`; generate `object_<ct_pk>` for the rest. Each field's `query_params` carry the generic scoping `nautobot_floor_plan_floor_plan=$floor_plan` and `nautobot_floor_plan_has_floor_plan_tile=False` plus each type's `picker_source()["params"]`. `Meta.exclude` hides `placed_content_type`, `placed_object_id`, `placed_label`, and the geometry fields. `clean()` collapses the selected pair, sets the generic pair, and calls `sync_legacy_fk_from_generic()`. On edit, preselect from the instance's pair. A Stimulus controller (`data-controller="floorplan-placed-type"`) shows only the matching object field and clears the others (same show/hide the current tabs use). Note: a single server-rendered combobox cannot repoint its REST endpoint per selection, so one-field-per-type is the correct transition design; the fully-dynamic single combobox is the SPA/Wave D path.

3. **`nautobot_floor_plan/api/serializers.py`** `FloorPlanTileSerializer`: add writable `placed_content_type = ContentTypeField(queryset set in __init__ to registry.allowed_content_types())`, `placed_object_id = UUIDField`, read-only `placed_object = SerializerMethodField` (minimal rep via `get_serializer_for_model`). `validate()` enforces pairing (XOR), existence, registry `resolve_location` == plan location, and uniqueness (exclude self). Keep `fields="__all__"`. Do NOT add the placement fields to `TILE_GEOMETRY_FIELDS`: any PATCH touching placement then falls through to full model validation automatically, while pure drag/resize PATCHes stay on the fast path unchanged.

4. **`nautobot_floor_plan/api/views.py`**: expose a `placeable-types/` action/endpoint returning the registry as JSON (`key`, `content_type`, `label`, `icon`, `color`, `weight`, `object_source{list_url, params}`) scoped by `?floor_plan=<uuid>`. This drives both the form palette and the future SPA. Adding a type is registry-only, no client change.

5. **`nautobot_floor_plan/filters.py`** `FloorPlanTileFilterSet`: add `placed_content_type` (ContentTypeFilter), `placed_object_id`, `placed_object` (MultiValueUUID on `placed_object_id`); keep the legacy four during transition. `q` SearchFilter searches the denormalized `placed_label`.

6. **`nautobot_floor_plan/filter_extensions.py`**: replace the four hand-written `FilterExtension`s with a registry-driven factory `make_floor_plan_extension(placeable)` producing generic `nautobot_floor_plan_floor_plan` (location-path-aware per type) and `nautobot_floor_plan_has_floor_plan_tile` (membership via `FloorPlanTile.objects.placed_object_ids(ct)`, NOT the reverse OneToOne, which no longer reflects generic placements). Emit one per registered type, cross-app included. `FloorPlanCoordinateFilter` (grid X/Y origin) stays, registered only for grid-capable types.

7. **`nautobot_floor_plan/tables.py`** + **`views.py`**: `render_allocated_object` reads `record.placed_object` with a legacy fallback; the `allocated_object` column uses `accessor="placed_label"` for DB-side sort/search; drop the four-FK `Case/When` annotation in `get_queryset()`.

8. **`nautobot_floor_plan/template_content.py`**: route every `getattr(obj, "floor_plan_tile", None)` (in `BaseFloorPlanButton.should_render`, `DeviceFloorPlanButton.get_link`/`should_render`, including the `device.rack.floor_plan_tile` path) through `FloorPlanTile.objects.for_object(...)`. Generalize the highlight param to `highlight_object=<app_label.model>:<pk>` while keeping the typed `highlight_<type>` params working during transition. This must land with G3 or the buttons silently stop rendering for generically-placed objects.

**G3 tests:**
- Form: `placed_type` choices come from the registry; selecting a type + object saves the generic pair and mirrors the legacy FK for the four DCIM types; editing preselects; submitting a stale hidden object field is rejected/cleared; missing object raises a field error.
- Serializer: create/patch with `placed_content_type` + `placed_object_id` for a DCIM type and a mocked cross-app type; pairing XOR, non-existent object, wrong-location, and duplicate-placement each return field-anchored 400s; a geometry-only PATCH still skips full validation (fast path); an unregistered CT is rejected at field level.
- `placeable-types/` endpoint returns the expected shape scoped by `floor_plan`, including Device role entries with `role` params.
- Filters: `has_floor_plan_tile=false` excludes placed objects via the generic pair for all types; `placed_content_type`/`placed_object_id` filter tiles; `q` matches `placed_label`.
- Table sorts and paginates by `placed_label` across mixed types.
- template_content: reverse button renders for a generically-placed object and hides when unplaced; highlight param round-trips.

**G3 towncrier fragment** `.added`: "The tile form and REST API can place any registered object type; tiles can be filtered and sorted by their placed object regardless of type."

---

## Wave G4: cross-app registration (phones/printers) + Device-role icons in production

Goal: the sibling apps register their standalone models and add reverse buttons, with symmetric graceful-absence. Independently testable in a combined install: place a `PhoneSystem`/`Phone`/`Printer`/`PrinterFleet`, see the icon and legend, use the reverse button.

File-by-file:

1. **`nautobot-app-phones/src/nautobot_phones/apps.py`** `ready()`: `super().ready()`, then `try: from nautobot_floor_plan.placement import register, PlacementType` (guarded). Register `nautobot_phones.phone` (icon `phone`, `location_resolver=lambda o: o.location`, the property returning `device.location`) as the real telephony point object; register `nautobot_phones.phonesystem` (icon `phone`/`server`, direct-FK resolver) as an optional system marker; optionally `analoggateway`. Recommend `Phone` as the primary placeable and `PhoneSystem`/`PrinterFleet` as marker-only, since the latter are informational cluster/grouping roots.

2. **`nautobot-app-printer-models/src/nautobot_printer_models/apps.py`** `ready()`: guarded register of `nautobot_printer_models.printer` (icon `printer`, `location_resolver=lambda o: o.location` via linked Device; resolves None until linked, which the G1 null-guard turns into a clean error) and `nautobot_printer_models.printerfleet` (icon `printer`, direct-FK resolver).

3. **`nautobot_phones/template_content.py`** and **`nautobot_printer_models/template_content.py`** (new/extended): add `TemplateExtension` "View on Floor Plan" buttons guarded by `try/except ImportError`, using `FloorPlanTile.objects.for_object(obj)` and `highlight_object=<app_label.model>:<pk>`. Register in each app's `template_extensions`.

4. **`nautobot_floor_plan/signals.py`** (activate): the `pre_delete` cleanup now matters for these cross-app types (no typed-FK CASCADE backs them), so confirm it deletes tiles when a `PhoneSystem`/`Printer` is deleted.

5. **Device-role icons**: already registered in G2; G4 is where the role->variant map is tuned against the real roles present (computer/camera/ap/climate/ip-phone) and documented.

**G4 tests** (in each sibling app's suite plus a floor-plan integration test):
- Registration is a no-op when floor-plan is absent (import guard), and populates the registry when present.
- Placing a `Phone`/`Printer` with a resolvable location succeeds; an unlinked `Printer` (device=None) is rejected with the "no resolvable Location" message; wrong-location rejected.
- SVG renders the phone/printer glyph and a legend row; unregistered-if-uninstalled degrades to fallback.
- Reverse button renders for a placed sibling object, hidden when unplaced, and the extension is inert when floor-plan is uninstalled.
- Deleting a placed `PhoneSystem`/`Printer` deletes its tile via the signal (no orphan row, since no typed-FK CASCADE).

**G4 towncrier fragments** (one per app repo) `.added`: "Phones (Phone/Phone System) can be placed on Nautobot floor plans, with a View on Floor Plan button." and "Printers and printer fleets can be placed on Nautobot floor plans, with a View on Floor Plan button."

---

## Deferred (G5, noted not specified): ports and regions

The GFK already covers point placement of `dcim.FrontPort`/`RearPort`/`Interface` via device-derived resolvers (`lambda c: c.device.location`), icon `ethernet-port`, with no schema change and no fifth typed FK. Rooms/zones are a separate concern: they need a polygon geometry column rather than the center-anchored point marker, so treat "ports as points" (GFK-ready) apart from "regions as polygons" (needs a geometry field). Do not fold either into G1 to G4.

---

## Risk list

1. **Backfill correctness on large installs.** The 0013 `RunPython` must use raw `*_id` columns and batch (`bulk_update` 500) to avoid dereferencing cross-app FKs through frozen historical models and to bound memory. Mitigate with the explicit migration round-trip test and an iterator-based read.
2. **Lost CASCADE for cross-app types.** A GFK has no DB cascade; without the `pre_delete` signal, deleting a `PhoneSystem`/`Printer` orphans its tile. Mitigate: signal scaffolded in G1, verified in G4. Watch for double-fire on the four DCIM types (guarded because the typed FK cascade clears the row first).
3. **`placed_label` drift.** The denormalized sort/search/legend column is only correct if maintained in `save()` and re-derived when the placed object is renamed. Object renames happen outside the tile's `save()`, so the label can go stale. Mitigate: rebuild in tile `save()`, and consider a light periodic/refresh or a signal on the placed object if staleness matters for search UX.
4. **App-load ordering / ContentType availability.** Registration at `ready()` is string-keyed and safe before ContentTypes exist, but any code that eagerly resolves a ContentType at import time will crash during migrations. Keep ContentType resolution lazy in the registry and never resolve at module import.
5. **Registry naming/import-cycle regressions.** The public surface must be the leaf module `nautobot_floor_plan.placement` (no model/svg imports) so siblings can import it cheaply and guarded. Importing `nautobot_floor_plan.models` from a sibling `ready()` risks a load-order model import; enforce the leaf-module rule in review.
6. **Serializer fast-path leak.** If `placed_content_type`/`placed_object_id` are ever added to `TILE_GEOMETRY_FIELDS`, placement changes would skip full validation. Guard with a test asserting a placement PATCH runs full validation and a geometry PATCH does not.
7. **Filter-extension behavior parity.** The generic `has_floor_plan_tile` must reproduce the exact observable behavior of the four hand-written extensions for the DCIM types (so existing form `query_params` keep resolving) while now sourcing truth from the generic pair. Snapshot the four types' eligible-object queries before and after.
8. **Unregistered/stale ContentType at render.** The GFK can point at anything; `resolve()` returning None (fallback glyph, gray, "Unregistered") is the safety valve and must never 500. Covered by the G2 fallback test.
9. **Device role-map coverage.** Roles outside `DEVICE_ROLE_ICON_MAP` fall back to the base device glyph; confirm no exception on roleless devices and that the normalization (`.name` lower, `[\s_]+`->`-`, `.slug` fallback) matches the deployment's actual role naming.
10. **Highlight-param migration.** Generalizing to `highlight_object=<app_label.model>:<pk>` while keeping typed params must not break the interactive JS keyed on `data-tile-id`/`data-pos-*`; verify both old and new params highlight the correct marker during transition.
11. **PROTECT on `placed_content_type`.** Correct in practice (ContentType rows are not deleted), but if a placeable app is fully uninstalled and its ContentType removed, PROTECT would block the delete. Acceptable, but document that uninstalling a placeable app requires clearing its tiles first.

---

# Adversarial Critiques

## Critic 1: migration + data integrity

I have enough to attack the spec concretely. Here is my adversarial review.

---

# Adversarial review: Generalized Object Placement spec (migration + data-integrity lens)

I read `models.py`, `svg.py`, `template_content.py`, `filter_extensions.py`, and the phones/printers sibling models. The spec is well-structured and most of its self-identified risks are real and correctly mitigated. Below are the places I can actually break it, ordered by severity. Six are blockers or near-blockers.

## BLOCKER 1 — GFK render path is an unbounded N+1; the spec never specifies a prefetch strategy

Location: Wave G2, `svg.py` `_resolve_placement`; Wave G3, `tables.py`/`views.py` `render_allocated_object`.

`render()` today does `self.floor_plan.tiles.all()` and the typed-FK path can (and should) `select_related("rack","device","power_panel","power_feed")` — four joins, one query. A `GenericForeignKey` cannot be `select_related`. Accessing `tile.placed_object` per tile issues at minimum one query for the ContentType (cached after first) plus one query per distinct object, and Django's GFK fetches are **not** batched unless you explicitly use `prefetch_related` with a `GenericPrefetch`/content-type bucketing. A 200-tile plan goes from ~2 queries to 200+.

Failure scenario: a floor plan with a few hundred placed objects renders fine in dev with 5 tiles, then the SVG view times out in production. The table view (`accessor="placed_label"` sorts fine, but `render_allocated_object` reads `record.placed_object`) has the same cliff across paginated rows.

Fix: make prefetch a first-class deliverable in G2/G3. Either `prefetch_related(GenericPrefetch("placed_object", [...registry querysets...]))`, or bucket tiles by `placed_content_type_id`, then one `Model.objects.in_bulk(ids)` per content type and attach. This must land with G2 or the epic ships a regression against the current `select_related` path.

## BLOCKER 2 — no DB-level pairing constraint tying `placed_content_type` to `placed_object_id`

Location: G1, `models.py` `Meta.constraints` (step 4) and migration 0013 (step 7).

The spec adds only the partial `UniqueConstraint(fields=["placed_content_type","placed_object_id"], condition=Q(placed_object_id__isnull=False))`. Pairing is enforced *only* in the serializer `validate()` XOR (G3) — nothing at the DB or model layer stops a half-populated pair. Note the existing code sets the precedent the other direction: `floorplantile_origin_pairing` is a `CheckConstraint` precisely because Python-only pairing checks leak through bulk ops, `bulk_update`, `update()`, and data migrations.

Failure scenario: a `bulk_update` or a future migration sets `placed_content_type` but leaves `placed_object_id` NULL (or vice versa). The partial unique constraint's condition (`placed_object_id__isnull=False`) skips these rows, so no error. Then `_resolve_placement`/`placed_object` dereferences a GFK with content_type-but-no-id → returns None → renders the "Unregistered/help" pin for an object that is actually fine, or worse, an id-but-no-content_type row that the reverse `for_object` lookup can never find. Silent data corruption that no test in the G1 suite would catch (all G1 tests write via save()).

Fix: add a second `CheckConstraint` in the same `Meta.constraints` list and in 0013, mirroring the origin pairing:
```python
models.CheckConstraint(
    name="floorplantile_placed_pairing",
    check=Q(placed_content_type__isnull=False, placed_object_id__isnull=False)
        | Q(placed_content_type__isnull=True, placed_object_id__isnull=True),
)
```

## BLOCKER 3 — `placed_label` backfill via `str(obj)` is wrong (or unsafe) inside the migration

Location: G1, migration 0013 step 7: "also fills `placed_label` from a cheap `str(obj)` where derivable."

This contradicts itself. The same sentence says to read raw `*_id` columns and "never dereference a cross-app FK through the frozen historical model," but `str(obj)` requires loading the object. Two ways to implement it, both broken:
- Via `apps.get_model("dcim","Rack")` (frozen historical model): frozen models have **no custom `__str__`** and no `get_absolute_url`, so `str(obj)` yields `"Rack object (uuid)"`, not the rack name. The denormalized sort/search column is then garbage for every backfilled row.
- Via importing the real model in the migration: violates the spec's own rule and is unsafe across app-load states.

Failure scenario: after 0013, the table's `allocated_object` column (accessor=`placed_label`) sorts/searches on `"Device object (…)"` strings until each tile happens to be re-saved. The G3 test "table sorts by placed_label across mixed types" passes on freshly-saved fixtures and misses this entirely.

Fix: in the migration, build the label from concrete columns the frozen model *does* expose — `obj.name` (rack/device/powerpanel/powerfeed all have `.name`) plus a static per-content-type label string, e.g. `f"{LABELS[ct_key]} {name}"`. Do not call `str()`/`get_absolute_url()`/`registry.resolve()` on frozen instances. Or: leave `placed_label` blank in the migration and populate lazily on next `save()`, and make the table tolerate blank labels (they sort together, acceptable).

## BLOCKER 4 — `pre_delete` signal sender scoping is hand-waved; the only correct-sounding option is a cluster-wide performance tax

Location: G1 step 5 (`signals.py`) + step 6 (`apps.ready`); risk #2.

The spec says "wire the `pre_delete` connection in G1 but scoped so it only matters for registered non-DCIM types" without saying how. There are two implementations and the spec picks neither:
- `@receiver(pre_delete)` with **no sender**: fires on *every* delete of *every* model in the entire Nautobot instance (IPAddress, Interface, Cable, Job results, cache invalidations…), each doing `FloorPlanTile.objects.for_object(instance)` = an extra query per deleted object app-wide. That is a global write-path tax for a feature most objects never touch.
- `pre_delete` connected **per registered model** in `ready()`: correct and cheap, but requires the connection to happen for cross-app types, whose registration runs in the *sibling* app's `ready()`. If floor-plan's `ready()` connects signals for `registry.all()` but a sibling registers *after* floor-plan loads, its model never gets a receiver and its tiles orphan on delete — exactly the G4 failure the signal exists to prevent.

Failure scenario (per-model variant): `nautobot_phones` loads after `nautobot_floor_plan` (very likely — floor-plan is the dependency being imported). Its `PhoneSystem` never gets a `pre_delete` receiver. Deleting a placed `PhoneSystem` leaves an orphan tile pointing at a dead UUID with no CASCADE. The G4 "deleting a placed PhoneSystem deletes its tile" test passes *only if* app load order happens to be favorable in CI.

Fix: don't bind receivers to senders at all. Register **one** `pre_delete` receiver with no sender, but gate the body on `registry.for_content_type(ContentType.objects.get_for_model(sender))` returning a registered non-DCIM type *and short-circuit before any DB query* using an in-memory set of registered `(app_label, model)` tuples the registry already holds. The guard is a dict lookup, not a query, so the cluster-wide tax collapses to a hash check. Or have each sibling app connect its own receiver in its own `ready()` (registration and connection co-located), which sidesteps load order entirely.

## BLOCKER 5 — split-brain precedence: `save()` mirrors typed→generic, but read paths "prefer placed_object"

Location: Decision 1 ("every new read path prefers `placed_object` and falls back to typed FKs") vs G1 step 4 `save()` ("call `_sync_placed_object_from_typed()`") and `_validate_single_object_assignment` ("raise if they describe different objects").

The write precedence (typed FK wins, mirrored into the pair on every save) is the opposite of the read precedence (pair wins). This is fine only while they always agree, but the edit flow makes them disagree.

Failure scenario: an existing tile has `rack=X`, backfilled `placed_object=rack:X`. A user edits it in the new G3 form and picks a **device** via the generic pair. The form sets the generic pair to `device:Y`. If `clear_legacy_fks()` doesn't fire (or fires after validation, or the user came in via a partially-updated API client that sets `placed_object_id` but leaves `rack_id`), then at `save()`:
- `_sync_placed_object_from_typed()` sees `rack=X` still set and mirrors it back, clobbering the user's `device:Y` choice — the edit silently reverts to the rack; or
- `_validate_single_object_assignment` sees `rack=X` (typed) and `placed_object=device:Y` (generic) describing different objects and raises, blocking a legitimate type change.

Either way the "change what's placed" edit is broken or lossy, and which one you hit depends on validation/save ordering that the spec leaves implicit.

Fix: define one authoritative direction for the transition and make save() enforce it. Cleanest: on any write where the generic pair is explicitly provided, the generic pair wins and save() *derives* the typed FK from it (clearing the other three), rather than mirroring typed→generic unconditionally. Reserve typed→generic mirroring strictly for the legacy-only write path (generic pair untouched/absent). Make the reconciliation in `_validate_single_object_assignment` a pure equality assert *after* that normalization, not a competing source of truth.

## NEAR-BLOCKER 6 — `has_floor_plan_tile` sourcing switch drifts from the four hand-written extensions

Location: G3 step 6, `filter_extensions.py` — replace `RelatedMembershipBooleanFilter(field_name="floor_plan_tile")` with membership via `FloorPlanTile.objects.placed_object_ids(ct)`.

Today the DCIM filters use the reverse OneToOne (`floor_plan_tile`), whose truth is the typed FK column. The spec re-sources membership from the generic pair. During the KEEP-both transition these two only agree if *every* write populates the generic pair. Any write that bypasses `save()` — `bulk_create`, `objects.update()`, admin bulk actions, a third-party integration writing `rack_id` directly — populates the typed FK but not the pair.

Failure scenario: an integration bulk-inserts tiles with `rack_id` set. `RackFilterExtension`'s `has_floor_plan_tile=True` (now generic-sourced) reports those racks as *unplaced*, so they show up as eligible in the tile-add form's `query_params` scoping and a user can "place" an already-placed rack, tripping the OneToOne IntegrityError at save. Risk #7 acknowledges the parity concern but the chosen implementation (generic-only source) *causes* it rather than mitigating it.

Fix: during the transition, source `has_floor_plan_tile` from the **union** of the typed reverse relation and the generic pair (`Q(floor_plan_tile__isnull=False) | Q(pk__in=placed_object_ids(ct))`), for the four DCIM types. Drop the typed half only in the post-G4 FK-removal migration. Also make `placed_object_ids(ct)` return a `.values_list("placed_object_id", flat=True)` **subquery**, not a materialized Python list, or the `id__in` clause loads every placed id into the app on each filter eval.

## MEDIUM 7 — API `placed_object_id` (raw UUIDField) drops the object-level permission gate the form had

Location: G3 step 3, serializer `placed_object_id = UUIDField`.

The form's `DynamicModelChoiceField` naturally restricts selectable objects to what the requesting user can view (queryset `restrict()`). The serializer replaces this with a bare UUID plus existence/location/uniqueness checks — none of which is an object-level permission check on the *target*. A user with tile-add permission but no view permission on `dcim.device` (or on a sibling app's `PhoneSystem`) can place, and thereby enumerate existence + confirm the location of, objects they cannot otherwise see.

Failure scenario: cross-tenant Nautobot with object-level perms; a low-privilege user probes `placed_content_type=nautobot_phones.phonesystem` + guessed/scraped UUIDs and reads back success/location from the 400-vs-201 response, an enumeration oracle across content types.

Fix: in `validate()`, resolve the target through a `restrict(request.user, "view")` queryset (the registry's `eligible_queryset(location)` should apply `restrict`), and 400 if the user can't view it — matching the form's implicit gate.

## MEDIUM 8 — UUID assumption on `placed_object_id`

Location: G1 step 4, `placed_object_id = UUIDField`.

Fine for DCIM and the current siblings (all UUID PKs), but the registry is explicitly an open extension surface. A third-party app registering a model with a non-UUID PK (or ContentType-style int PK) will fail on write with an opaque cast error, and the failure surfaces only at their integration time.

Fix: either document "registered models must have UUID primary keys" as a hard contract in the registry API docstring and validate it in `register()` (reject at registration, not at write), or use a `CharField` object_id like Django's own contenttypes does. Given Nautobot's UUID standardization, documenting + validating at `register()` is the lighter correct choice.

## Things that are sound (brief)

- KEEP the four typed FKs through G4, backfill-then-add-constraint ordering, and a separate post-G4 drop — correct and low-risk. Backfill cannot produce duplicate `(content_type, object_id)` pairs because each source column is already OneToOne, so `AddConstraint` after `RunPython` is safe.
- Partial unique with `condition=object_id__isnull=False` correctly lets many pair-null (status/rackgroup-only) tiles coexist, and matches the existing `unique_together` NULL semantics for freeform tiles.
- Migration reversibility (`RemoveConstraint → unbackfill → RemoveField`, typed FKs untouched) genuinely loses nothing on rollback to 0012.
- String-keyed registration before ContentTypes exist, lazy CT resolution, and the leaf-module import rule (`nautobot_floor_plan.placement` importing neither models nor svg) correctly avoid the migration-time and import-cycle traps. `models → svg → placement` and `models → placement` stay acyclic *as long as* `placement/*` never imports `models`; enforce that in review as stated.
- Decision-6 null-guard is correct and necessary: `Printer.location`/`Phone.location` are `@property` returning `device.location` (verified — None until the device link exists), while `PhoneSystem`/`PrinterFleet` carry a real nullable FK, so a resolver-driven check with an explicit None→actionable-error is the right unification.
- Not adding placement fields to `TILE_GEOMETRY_FIELDS` (so placement PATCHes fall to full validation while drag/resize stays fast) is the right call; risk #6's guard test is the correct backstop.

## Minor / watch

- `pre_delete` for the four DCIM types double-fires with their CASCADE (signal deletes the tile, then the collector deletes an already-gone row). Idempotent in practice but confirm the signal handles `DoesNotExist`/empty queryset cleanly rather than assuming a row.
- `for_object(...).first()` silently collapses multiple matches; harmless once the pairing + unique constraints (Blockers 2) are both in place, fragile before then.
- power_feed resolver change alters the *message* from "must belong to Location X" to "no resolvable Location" when `power_panel.location` is None — a behavior/message change any existing test asserting the old string will flag. (PowerPanel.location is non-null in DCIM, so it's only a test-text risk, not a functional one.)

Net: the architecture is sound and the KEEP-both/backfill/constraint sequencing is right. Blockers 1–5 are the ones that will actually bite — the GFK N+1, the missing pairing CheckConstraint, the `str(obj)` backfill, the signal sender scoping, and the save-vs-read precedence split — and each has a concrete, localized fix above.

## Critic 2: cross-app coupling

I read the actual code (models.py, `__init__.py` app config, filter_extensions.py, and both sibling apps). The spec is well-structured and most of its core decisions are sound, but I found several concrete breaks, ranked by severity. Grounding facts I confirmed: all placeable siblings (`PhoneSystem`, `Phone`, `Printer`, `PrinterFleet`) are `PrimaryModel` (UUID PK, so the `UUIDField` GFK column is safe); `Phone.location`/`Printer.location` are `@property` returning `self.device.location` with `device` on `SET_NULL`; the existing `filter_extensions` is a module-level list of `FilterExtension` classes each bound to a foreign `model=`.

---

## BLOCKER 1 — FilterExtension registration can't see cross-app types (G3, step 6)

This is the sharpest hit on the ordering lens. `filter_extensions.py` builds `filter_extensions = [...]` at **module import time**, and Nautobot enumerates that list per app during extras loading. Sibling registration happens in each sibling's `AppConfig.ready()`, which runs **after** floor-plan's `filter_extensions` module is already imported and collected. So `make_floor_plan_extension()` iterating `registry.all()` at import will see an **empty/DCIM-only registry** and never emit extensions for `nautobot_phones.phone` / `nautobot_printer_models.printer`.

Failure scenario: install floor-plan + phones. Phone registers in `phones.ready()`. The picker form's `query_params` for the phone field reference `nautobot_floor_plan_floor_plan` / `nautobot_floor_plan_has_floor_plan_tile` on the Phone filterset, but no FilterExtension was ever added to `PhoneFilterSet` → the DynamicModelChoiceField silently returns unscoped/empty results, and `has_floor_plan_tile=false` scoping in the picker is a no-op. Cross-app placement UI is broken with no error.

Second-order break: emitting a FilterExtension with `model="nautobot_phones.phone"` **unconditionally** from floor-plan breaks graceful degradation when phones is *not* installed (Nautobot resolves the target filterset at load; a missing model errors or is dropped).

Concrete fix: FilterExtensions that extend a sibling model's filterset must live in **that sibling's** `filter_extensions.py`, guarded by `try/except ImportError` on floor-plan (same pattern as the reverse buttons in G4). Floor-plan keeps only the generic filters that live on `FloorPlanTileFilterSet` itself (`placed_content_type`/`placed_object_id`/`placed_object`), plus the DCIM-four which it legitimately owns during transition. Do not try to generate foreign FilterExtensions from a registry populated at `ready()`. Also note `RelatedMembershipBooleanFilter(field_name="floor_plan_tile")` cannot be reused for the generic pair — there is no reverse relation on the target model unless you declare a `GenericRelation` there; the generic `has_floor_plan_tile` must be a custom `BooleanFilter` doing `pk__in=FloorPlanTile.objects.placed_object_ids(ct)`.

---

## BLOCKER 2 — Backfill `placed_label` via `str(obj)` is wrong in a migration (G1, step 7)

The 0013 `RunPython` is (correctly) told to read raw `*_id` columns and never dereference cross-app FKs through frozen models, but it *also* says "fills `placed_label` from a cheap `str(obj)` where derivable." In a migration, `obj` comes from `apps.get_model(...)` — a **historical** model with no custom `__str__`. `str(historical_instance)` yields `"Rack object (uuid)"`, not the rack name. Every backfilled label is garbage, and since search/sort/legend all read `placed_label` (decision 8), the table sorts by `"Device object (…)"` strings until each tile is re-saved.

Concrete fix: in the migration, either (a) leave `placed_label` blank and let the next `save()` populate it (accepting stale-until-touched), or (b) build the label from the known name column per type via a `.values_list("pk","name")` map (rack/device/powerpanel have `name`; powerfeed too), joined in Python. Do not call `str()` on historical instances. Also ensure ContentType lookups in the backfill use `ContentType.objects.get(app_label=…, model=…)` against the historical ContentType, not `get_for_model(RealClass)`.

---

## HIGH 3 — GenericForeignKey render path is an N+1 cliff (G2)

The four typed FKs allowed `select_related("rack","device",…)` when rendering. `placed_object` (a GFK) **cannot be `select_related`**. `_resolve_placement()` dereferences `tile.placed_object` per tile (1 query), then `obj.status` (another), and for phones/printers `obj.location` is a property that dereferences `obj.device.location` (another). A 100-tile freeform plan renders in 300+ queries. `placed_label` denormalization only fixes sort/search, not render.

Concrete fix: in `render()`, group tiles by `placed_content_type`, `prefetch_related` per content type (or bulk-fetch each CT's objects with one `in_bulk(ids)` per type and attach), and denormalize the render inputs you actually need — at minimum cache `status_id`/color, or store an `placed_status_color` alongside `placed_label`. For phones/printers, `.location` hitting `.device` per object should be `select_related("device__location")` in the per-type bulk fetch.

---

## HIGH 4 — Global `pre_delete` signal: ordering + system-wide overhead (G1, step 5)

You cannot connect `pre_delete` per-registered-sender at floor-plan `ready()`, because siblings register in *their* `ready()`, which may run later — you'd miss them. So it must be a **single global** `pre_delete` (sender=None) that fires on **every model delete in the entire Nautobot instance** and checks the registry. That's real overhead on all deletions, and a correctness trap if the handler isn't cheap.

Concrete fix: keep the global connection but short-circuit in the first line with a cheap in-memory check — `ct = ContentType.objects.get_for_model(instance)` is cached, then `if ct_id not in registry._registered_ct_ids: return`. Better ownership alternative: have each sibling connect its own `post_delete` in its guarded `ready()` (it already imports `nautobot_floor_plan.placement`), so the signal is scoped to exactly the sibling's models and there's no global fan-out. Either way, spell out in the spec that the handler must be O(1) before the DB filter, and that for the 4 DCIM types the `on_delete=CASCADE` already deletes the whole tile row (losing geometry) — that is pre-existing behavior, but the signal must be idempotent against a row already being cascaded.

---

## HIGH 5 — No object-level permission check on the placement target (G3, steps 2–3; security)

`placed_content_type` is constrained to `registry.allowed_content_types()` (good), but `placed_object_id` is a bare UUID and the serializer `validate()` only checks "existence" and location. A user who can edit tiles but cannot *view* a given Device/Phone can place it by supplying its UUID (IDOR). The 4 legacy DynamicModelChoiceFields got object-level perm enforcement for free via each model's REST endpoint; the generic path loses it. Additionally, `get_svg(user=…)` renders `placed_object` name/status with no per-object `restrict()` on the target — generalizing placement broadens a pre-existing SVG perm-bypass to sensitive cross-app objects (phones).

Concrete fix: in serializer/form `validate()/clean()`, resolve the target object through a `restrict(request.user, "view")` queryset for that content type before accepting it. Document/verify whether `get_svg` should restrict target objects per user, or explicitly accept that anything placed on a plan is visible to plan viewers.

---

## MEDIUM 6 — Import-cycle risk in the registry's own method list (decision 2 / G1 step 1)

Risk 5 correctly mandates `nautobot_floor_plan.placement` be a leaf with no model/svg imports, but the G1 method list puts `for_object(obj)` and `for_content_type(ct)` **on the registry**, and those must touch `FloorPlanTile`. `models.py` already imports `svg` at top, and G2 makes `svg` import `placement` at top, so the chain is `models → svg → placement`. If `placement/registry.py` imports `models` at module scope for `for_object`, that's a hard cycle at import.

Concrete fix: `for_object`/`for_content_type` must do the `FloorPlanTile` import lazily *inside* the method, or — cleaner — drop them from the registry entirely and keep object↔tile reverse lookup solely on the manager (`FloorPlanTile.objects.for_object`, which decision 7 already introduces). The registry should only know content types and resolvers, never the tile model. Add this as an explicit review gate, not just prose.

---

## MEDIUM 7 — Partial `UniqueConstraint(condition=…)` is Postgres-only (G1, step 4/7)

`floorplantile_unique_placed_object` uses `condition=Q(placed_object_id__isnull=False)`. Conditional unique constraints are not supported on MySQL/MariaDB, which Nautobot 2.x still supports. On MySQL the migration will fail or the constraint won't enforce. The existing `floorplantile_origin_pairing` is a `CheckConstraint` (portable), so there's no precedent proving partial-unique works here.

Concrete fix: confirm the deployment's DB backend policy. If MySQL must be supported, enforce single-placement in `validate_unique()`/`clean()` plus a plain (non-partial) unique guarded by application logic, or use a nullable-friendly approach. At minimum, add a note and a MySQL migration test.

---

## MEDIUM 8 — Stale ContentType + `PROTECT` fails `remove_stale_contenttypes`, not just uninstall (risk 11 understated)

Risk 11 frames this as "uninstalling an app requires clearing tiles first," but the concrete trigger is Django's `remove_stale_contenttypes` management command (run during/after migrations when a model disappears). With `on_delete=PROTECT` and a tile still pointing at that CT, the command raises `ProtectedError` and aborts — a confusing failure far from the actual uninstall action. Meanwhile `tile.placed_object` already degrades to None safely (model_class() is None), which G2's fallback handles fine.

Concrete fix: keep `PROTECT` (it's the safe choice) but document the exact command, and provide a small management command / pre-uninstall step that nulls or deletes tiles for a given app_label so operators aren't stuck debugging `ProtectedError` from a core Django command.

---

## MEDIUM 9 — Form field names keyed on `ContentType.pk` are non-portable (G3, step 2)

`object_<ct_pk>` uses the ContentType primary key, which is an auto-increment assigned in arbitrary order at first migrate and **differs across databases/installs**. Field names, the `placeable-types/` payload keys, and any test/JS referencing them become environment-specific.

Concrete fix: key generated fields and payload entries by the stable dotted label slug, e.g. `object_dcim_device` / `object_nautobot_phones_phone`, never by `ct.pk`.

---

## LOW 10 — Unlinked Printer/Phone becomes unplaceable; resolver property hits the DB (decision 6)

`Printer.location`/`Phone.location` return None until a Device is linked, and the null-guard turns that into a hard "no resolvable Location" error. That's internally consistent (G4 test expects the rejection), but it means the *primary* cross-app use case — dropping a printer marker on a plan — fails for any printer not yet linked to a Device, even though the plan itself already has a Location. Confirm this is the intended product behavior; if operators want informational markers for unlinked assets, the resolver needs a fallback (e.g., allow placement when `floor_plan.location` is set and the object's resolved location is None, treating None as "unconstrained" rather than "mismatch").

---

## LOW 11 — Typed-FK vs generic-pair divergence has no DB guard (G1, step 4)

`_validate_single_object_assignment` reconciles typed FK vs generic pair only in `clean()`, which bulk/ORM `save()` paths skip. Nothing at the DB level stops `rack_id=X` coexisting with `placed_object_id=Y`. During the G1–G4 transition with two sources of truth, a non-`clean()` writer can desync them. Acceptable if all writers go through forms/serializers, but state it as an assumption and consider asserting it in `save()` (not just `clean()`), since `save()` already runs `_sync_placed_object_from_typed()`.

---

## What's sound (briefly)

Keeping the four typed FKs writable through G4 with `placed_object`-preferred reads is the right conservative call. String-keyed, push-based registration via a **module-level singleton** genuinely does make sibling registration independent of app-`ready()` ordering (the singleton exists at import, so a sibling registering before or after floor-plan's own `register_builtins()` both work) — this part of the ordering story holds up. Lazy ContentType resolution, `resolve()` returning None instead of raising, and the G2 fallback glyph are the correct safety valves for stale/uninstalled types. The UUID-PK assumption behind `placed_object_id` is valid for every named sibling. The `placed_label` denormalization is the right answer for cross-type sort/search (just fix its migration backfill and add render-input denormalization per BLOCKER 2 / HIGH 3). Discriminator-catches-exceptions and unmapped-role fallthrough for device variants are handled correctly.

## Critic 3: rendering + forms + UX

I have enough grounding. Here is my adversarial review.

---

# Adversarial review: Generalized Object Placement spec

Lens: rendering, forms/UX, validation, and the drag-to-place/PATCH contract. I read the actual `models.py`, `svg.py`, `forms.py`, `filter_extensions.py`, `template_content.py`, `tables.py`, `views.py`, `api/serializers.py`, the two sibling apps' `location` properties, and the highlight JS. Findings are ranked by severity. Several are blocking.

## Blocking

### 1. Filter extensions are a module-level list built before sibling apps register (G3 step 6)
`nautobot_floor_plan/filter_extensions.py` exposes `filter_extensions = [...]` as a static module attribute that Nautobot collects during app loading. The spec's `make_floor_plan_extension(placeable)` factory iterates the registry to "Emit one per registered type, cross-app included." But cross-app types are only registered in the sibling apps' `AppConfig.ready()`, and `ready()` ordering across apps follows `INSTALLED_APPS`. If `nautobot_floor_plan` is loaded before `nautobot_phones`, the generated list contains only the four builtins and the phone/printer filter extensions never exist.

Failure scenario: install floor-plan + phones, open the tile form, pick "Phone" in step 1. The step-2 `DynamicModelChoiceField` sends `query_params={"nautobot_floor_plan_floor_plan": "$floor_plan", "nautobot_floor_plan_has_floor_plan_tile": False}` to the Phone list endpoint, but `PhoneFilterSet` has no such filter (extension was never emitted). Depending on `settings.STRICT_FILTER_MODE` the request either 400s or silently ignores the params and returns every Phone in the database, including already-placed ones and phones at other locations. The "scoped to location" picker is broken exactly for the cross-app types this epic exists to support.

Fix: do not derive `FilterExtension` classes from registry state at import time. Register one generic extension per model whose *class body* is static, and resolve content-type-specific behavior at filter call time; or have each sibling app own its own `FilterExtension` (register it in the sibling's `filter_extensions.py`, guarded by `try/except ImportError`) rather than floor-plan generating them. The latter matches the push-based registration philosophy the spec already adopts for the registry. Risk #4 only covers ContentType laziness; it does not cover this list-built-at-import ordering hazard. Add it.

### 2. The registry stores a Python resolver lambda but the picker/filters need an ORM path string (decision 3 + G3 steps 1, 6)
`PlacementType.location_resolver` is a callable like `lambda o: o.device.location`. The spec reuses it for three different jobs: runtime validation (fine, it is a callable), `eligible_queryset(location)`, and the "location-path-aware" filter extension. The last two need a Django ORM lookup path, and you cannot derive `device__location__floor_plan` from a lambda.

This is worse for the sibling models than the spec implies. I confirmed `Phone.location` and `Printer.location` are `@property` methods returning `self.device.location if self.device_id else None` (nautobot-app-phones `endpoints.py:174`, nautobot-app-printer-models `printer.py:127`). There is no `location` column on Phone or Printer at all, so `FloorPlanTile.objects.filter(... location__floor_plan=...)` and any filter path through `location` raises `FieldError`. The correct ORM path is `device__location__floor_plan`, which is information the resolver callable does not carry.

Failure scenario: `eligible_queryset(location)` for Phone is implemented as `Phone.objects.filter(location=location)` (mirroring the resolver) and throws `FieldError: Cannot resolve keyword 'location'`, 500-ing the `placeable-types/` endpoint and the picker.

Fix: add an explicit `location_field` ORM-path string to `PlacementType` (e.g., `location_field="device__location"` for Phone/Printer, `"location"` for Device/Rack/PowerPanel, `"power_panel__location"` for PowerFeed). Use `location_field` for filter/queryset construction and keep `location_resolver` only for the per-object validation read. Decision 3's field list is missing this; it is not optional.

### 3. Serializer placement validation has no object-level permission check across content types (G3 step 3; risk list is silent)
`validate()` is specified to enforce pairing, existence, location, and uniqueness. It does not check that the requesting user may view the object being placed. The existing four typed FKs already skip object-level perms in the API, but generalizing to "any registered content type" widens a narrow gap into a broad one: a user with `change_floorplantile` can place any object of any registered type by POSTing `placed_content_type` + `placed_object_id`, then the tile detail view and the server-rendered SVG tooltip leak that object's data to anyone who can view the floor plan.

Failure scenario: object-level permissions restrict user B from viewing a specific Device (or a Phone with a sensitive extension/MAC). User A places it on a tile. The SVG `_get_tooltip_data`/`tooltip_builder` renders Name, Status, serial, asset_tag, phone MAC, etc. into `data-tooltip` for every viewer of the plan. `get_svg` restricts the *FloorPlan* to `view` (views.py:30) but never restricts the *placed objects*.

Fix: in the serializer `validate()`, resolve the object through `model.objects.restrict(request.user, "view")` and reject with a field-anchored 400 if not found. For rendering, either accept that placement implies visibility (document it) or have `_resolve_placement` skip/redact tooltip data for objects the requesting user cannot view. At minimum this must be a stated decision, not an omission.

### 4. Icon glyphs inlined per marker is a payload cliff, and the "self-contained" justification is wrong (G2 step 1 + svg.py `_draw_icon`)
The spec embeds `ICON_GLYPHS` path sets inline into each marker "so the Save SVG download stays self-contained, same rationale as the base64 blueprint." That rationale is false. The blueprint is base64-embedded because it is an *external* file reference; an internal `<defs>` symbol referenced by `<use href="#icon-phone">` is equally self-contained (no external fetch) and deduplicated.

Failure scenario: a facility floor plan with 800 placed endpoints inlines the full path set 800 times inside 800 rotated groups, each with a halo chip and counter-rotate transform. That is tens to hundreds of KB of duplicated vector data, regenerated server-side on every view and on every geometry PATCH re-render. This is directly on top of the already-1.33x base64 blueprint.

Fix: define each glyph once in `<defs>` as a `<symbol>`/`<g id="icon-...">` and reference with `<use>`. Self-contained and O(distinct glyphs) instead of O(markers). Browsers and Inkscape flatten internal `<use>` on export fine.

## High

### 5. Migration backfill of `placed_label` produces garbage or blanks (G1 step 7)
The backfill is specified to fill `placed_label` "from a cheap `str(obj)` where derivable" while explicitly not dereferencing FKs through frozen historical models. These two constraints collide. Historical models (`apps.get_model`) have fields but not the app's custom `__str__`/`.name`-based display, so `str(historical_device)` yields `"Device object (uuid)"`, not the device name. And resolving the label via the live `registry` inside `RunPython` couples a frozen migration to mutable runtime state (non-deterministic replays).

Failure scenario: after 0013, the tile list `allocated_object` column (which the spec repoints to `placed_label`) shows `"Device object (…)"` for every pre-existing tile, and search/sort by `placed_label` matches on that garbage. If instead you leave it blank to be safe, the column is empty for all legacy tiles until each is re-saved, a visible regression the moment the migration lands.

Fix: in the migration, read the real display fields directly from the current model via `apps.get_model(...).objects.filter(pk__in=...).values("name")` (batched) rather than `str()` or the registry, and compose the label from plain fields. Do not import the runtime registry into the migration. If the label logic is too registry-dependent to replicate, populate it in a separate idempotent data step (post-migrate or management command) and accept blank-until-recompute, but say so explicitly.

### 6. Drag-to-place vs the geometry-only fast path: the client contract is unstated and easy to violate (G3 step 3 + Wave D)
The fast path in `FloorPlanTileSerializer.update` only skips full validation when `set(validated_data).issubset(TILE_GEOMETRY_FIELDS)` (serializers.py:68). The spec correctly keeps placement fields out of `TILE_GEOMETRY_FIELDS`. But it never pins the Wave D client contract, and the natural front-end implementation breaks it.

Failure scenario: the drag handler, to be defensive, re-sends the tile's current `placed_content_type` + `placed_object_id` alongside the new `pos_x/pos_y`. The set is no longer a geometry subset, so the PATCH falls into full `clean()`, which re-runs `_validate_object_locations` against the object's *current* location. If the placed Device was moved to another Location after placement, a pure drag now fails with a wrong-location error, which is precisely the failure the fast path exists to prevent. This will manifest as "I can't move markers for objects that changed location."

Fix: state and test the contract that a reposition PATCH carries geometry fields only. Optionally harden the server: if placement fields are present but byte-identical to the stored values, strip them before deciding the fast path. Add the explicit test the spec's risk #6 gestures at, but for the *unchanged-placement-plus-geometry* case, not just the pure-geometry case.

### 7. `highlight_object=<app_label.model>:<pk>` cannot find the marker without a shared id scheme (G3 step 8; risk #10 understates)
The highlight JS resolves markers by `svg.getElementById(`${type}-${pk}`)` with hardcoded prefixes `rack`, `device`, `powerpanel`, `powerfeed` (floorplan.js:711-717). The SVG sets the marker `<a id>` to `f"{url_type}-{obj.pk}"` using those same short dcim tokens (svg.py:520, 888). Generalizing the URL param to `app_label.model:pk` means the JS must map `dcim.rack` back to element id `rack-<pk>`, and cross-app markers need a deterministic prefix that both svg.py and the JS agree on.

Failure scenario: a Phone reverse button links `?highlight_object=nautobot_phones.phone:<pk>`. The JS has no mapping for that label; even if it strips to `phone`, the SVG marker id must be exactly `phone-<pk>`. Any divergence (e.g., svg.py uses the ContentType model name `phone` but JS passes `nautobot_phones.phone`) means `getElementById` returns null and the highlight silently no-ops. Risk #10 only worries about `data-tile-id`/`data-pos-*`; the actual break is the id lookup.

Fix: define one canonical marker-id scheme keyed by `app_label.model` (e.g., id=`place-<app_label>-<model>-<pk>`), emit it from both the grid and freeform draw paths, and have the JS build the same string from the `highlight_object` param. Keep the legacy `highlight_<type>` params mapping onto the same canonical ids during transition.

### 8. `allocation_type_assignment` reads placement state before the sync runs (G1 step 4)
The spec orders `clean()` as: `allocation_type_assignment` (now using `_has_placed_object()`), then `_sync_placed_object_from_typed()` "right after." A legacy or API write that sets only a typed FK (`rack=R`) has not yet mirrored into the generic pair when `allocation_type_assignment` runs. If `_has_placed_object()` inspects only the generic pair, the tile is misclassified `RACKGROUP` instead of `OBJECT`.

Failure scenario: an existing API client creates a tile with `{"rack": "<uuid>", "status": ...}` (the documented legacy path the spec promises to keep working through G4). `allocation_type` comes out `RACKGROUP`, which changes overlap behavior and the rendered underlay, silently diverging from pre-epic behavior. Note the current code (models.py:509) sets `RACKGROUP` whenever status is set and only upgrades to `OBJECT` when a typed FK is truthy, so getting the ordering wrong regresses real behavior.

Fix: sync typed to generic *before* `allocation_type_assignment`, or make `_has_placed_object()` consider both the typed FKs and the generic pair. The specified order is backwards.

### 9. Tile list view N+1 on heterogeneous `placed_object` (G3 step 7)
`render_allocated_object` currently reads `record.device or record.rack or ...` (tables.py:122), which the ORM populates via the existing `select_related`-friendly FKs and the `object_name` Case/When annotation (views.py:242). The spec drops the annotation and reads `record.placed_object` with a legacy fallback. `hyperlinked_object` needs the real object for the link, so every row triggers a GenericForeignKey fetch.

Failure scenario: a 100-row tile list page issues up to 100 extra per-row queries (one per distinct object, ungrouped) because GFKs are not covered by `select_related`. `placed_label` handles the sortable text, but the link column still materializes each object.

Fix: `prefetch_related("placed_object")` on the tile queryset (Django groups GFK prefetch by content type) and build the hyperlink from `placed_label` + a stored/resolved URL where possible. Confirm the prefetch composes with `.restrict()`.

## Medium

### 10. Legend/marker disagreement for Device role variants, and no legend in grid mode (G2 steps 3, plus `_draw_object_tile` left as-is)
Two concrete gaps. First, `resolve(obj)` returns a role *variant* PlacementType (camera, ap, ...), and the marker draws the variant glyph, but the spec text for accumulating `self._present_types` is ambiguous about whether it collects the variant or the base `dcim.device`. If it collects the base, the legend shows one generic "Device" row with the base glyph while the canvas shows camera/phone/ap glyphs. Second, `_draw_object_tile` (the grid path) is explicitly left rectangle-based and does not accumulate `_present_types`, so a plan in grid mode with placed objects renders no legend at all, and a mixed grid+freeform plan undercounts.

Fix: accumulate the resolved variant into `_present_types` in both the freeform and grid draw paths, and decide deliberately whether grid-mode plans get a legend (they should, for consistency).

### 11. No accessible name for marker type; type conveyed by color+glyph only (G2 rendering; not mentioned)
Markers are `<a>` with a JSON `data-tooltip` consumed by JS. There is no `<title>`/`aria-label` carrying the object type into the accessibility tree, and the legend distinguishes types partly by color swatch. A screen-reader user gets the object name (link text) but not "Camera" vs "IP Phone"; a low-vision user relying on the legend gets color plus glyph (glyphs differ, so WCAG 1.4.1 is arguably met, but only if every glyph is visually distinct at the 18px floor).

Fix: add a `<title>` child (and `role="img"`/`aria-label`) to each marker with "Name, Type" so the type is a text alternative, independent of color. This is cheap and squarely in the UX/accessibility lane the epic touches.

### 12. Global `pre_delete` is a site-wide performance tax unless connected per sender (G1 step 5; risk #2 understates)
The signal must delete tiles when a placed object is deleted. If connected with `sender=None` and the handler filters by registry membership, every delete of every model anywhere in Nautobot runs the handler and (naively) a `FloorPlanTile.objects.for_object(instance)` query. The spec says "scoped so it only matters for registered non-DCIM types" without saying how.

Fix: connect `pre_delete` per resolved sender class in `ready()` after the registry is populated (model classes are importable at `ready()` time, unlike at migration time), and re-connect on any late registration. This bounds the cost to actual placeable models. Also verify cascade-delete paths (deleting a Location that cascades to Devices) still fire per-instance `pre_delete` so tiles are not orphaned.

### 13. `eligible_queryset` and the widget filterset are two sources of truth for the same scoping (G3 steps 1, 2, 4)
The `placeable-types/` endpoint advertises each type's `object_source{list_url, params}` (driving the widget's server-side filtering) while `eligible_queryset(location)` computes eligibility separately for the JSON/SPA path. These can diverge, so the picker widget may offer an object that `eligible_queryset` would exclude (or vice versa), producing a create that then fails model `clean()`.

Fix: derive both from the same `location_field` path (see finding 2), so the widget's `query_params` and `eligible_queryset` cannot disagree.

## Sound parts (brief)

Keeping the four typed FKs writable through G4 with a sync-both-ways reconciliation is the right call and avoids a big-bang cutover. The partial `UniqueConstraint` with `condition=placed_object_id__isnull=False` correctly reproduces the OneToOne uniqueness while allowing many pair-null rackgroup tiles. Not adding placement fields to `TILE_GEOMETRY_FIELDS` is correct (the fast-path leak in risk #6 is real and worth its guard test). The leaf-module rule for `nautobot_floor_plan.placement` (no model/svg imports) is the correct way to keep sibling `ready()` imports cheap and cycle-free; I found no import cycle *provided* that discipline holds and `models.py` is the only thing importing `placement` (not the reverse). Push-based registration via guarded `ready()` imports, keyed by dotted string, is the right pattern and correctly sidesteps ContentType-at-import crashes. Treating "ports as points" (GFK-ready) separately from "regions as polygons" (needs a geometry column) in G5 is the correct decomposition.
