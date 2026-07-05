# Generalized Object Placement (Design)

Working design doc for expanding what a floor plan can hold: beyond racks and power
equipment, place computers, IP phones, printers, cameras, access points, climate
control, and cross-app records (printers, phone systems), with building wiring ports
as a future step. Status: proposal, pending review.

## Why

Operators want the blueprint to be the physical map of a whole facility (offices,
conference rooms, not just data-center rows), with every kind of endpoint shown where
it sits. This turns the floor plan into the one "where is this thing" view across the
estate, which is also what the MCP servers and SSOT apps want to point at.

## Finding: two families of placeable things

1. **`dcim.Device` differentiated by Role** — computers, cameras, access points,
   climate controllers, and most IP phones are Devices with a role. One model, many
   roles.
2. **Standalone models in sibling apps** — `nautobot_phones.PhoneSystem`,
   `nautobot_printer_models.Printer` / `PrinterFleet` are independent `PrimaryModel`
   classes, NOT `dcim.Device` subclasses. Future building wiring ports are likely the
   same shape.

The current `FloorPlanTile` has four hardcoded `OneToOneField`s (`rack`, `device`,
`power_panel`, `power_feed`). It structurally cannot reference the cross-app models,
and adding one FK per new type does not scale and couples this app to every other app.

## Decision: a GenericForeignKey placement target

Add to `FloorPlanTile`:

- `placed_content_type` (FK to `ContentType`, nullable)
- `placed_object_id` (UUID, nullable)
- `placed_object = GenericForeignKey("placed_content_type", "placed_object_id")`

A tile can then reference any Nautobot model. Keep the four legacy typed FKs during a
transition; a data migration backfills the generic pair from whichever typed FK is set.
New placements (including all freeform work) use the generic pair. Uniqueness ("an
object sits on one tile") becomes a unique constraint on
`(placed_content_type, placed_object_id)` where non-null.

Reverse lookup nuance: the "View on Floor Plan" buttons currently use
`obj.floor_plan_tile` (the reverse of a OneToOne). With a generic FK there is no
automatic reverse accessor, so add a helper
`FloorPlanTile.objects.for_object(obj)` and update `template_content.py`.

## Type registry (icon + style + resolvers)

A registry keyed by content type (and, for Devices, by Role) supplies per-type:

- **icon** — an SVG glyph (Lucide-style path set embedded in the renderer; no external
  fetch, keeps the SVG self-contained), e.g. monitor, phone, printer, camera, wifi,
  thermometer.
- **label / color** — display name and accent.
- **location resolver** — how to derive the object's Location so we can validate it
  belongs to the plan's location. Devices use `.location`; cross-app models may resolve
  via a related field (e.g. a printer's site/location). Each app registers its own.
- **detail URL / tooltip fields** — for the link and hover card.

Apps register their models with the floor-plan registry (an entry point or an
app-config hook), so phones/printers plug in without this app importing them directly.

## Rendering

Freeform markers already position and rotate arbitrary content. Extend the freeform
draw path to render the registry icon + label instead of a bare rectangle, colored by
type/status. Grid path keeps the current rectangle for back-compat. Icons scale with
the marker footprint. Legend lists the types present.

## Forms / API

Replace the four object dropdowns with a two-step picker: a content-type (or a friendly
"what are you placing?" list) then a `DynamicModelChoiceField` scoped to that type and
the plan's location. The API tile serializer exposes `placed_content_type` +
`placed_object_id` (writable), with validation that the object exists and resolves to
the plan's location.

## Migration

1. Additive nullable columns `placed_content_type`, `placed_object_id` (migration 0013).
2. `RunPython` backfill: for each tile, set the generic pair from whichever typed FK is
   populated.
3. Unique constraint on the generic pair (partial, where non-null).
4. Keep typed FKs for now; a later migration can drop them once nothing reads them.

## Phases

- **G1** model generic FK + registry scaffold + migration + backfill (additive,
  non-destructive; typed FKs still work).
- **G2** SVG icon rendering per type + legend.
- **G3** forms/API two-step object picker + validation + `for_object` reverse lookup
  and `template_content.py` update.
- **G4** cross-app registration for `nautobot_phones` and `nautobot_printer_models`,
  plus Device-role icon mapping (computer/camera/ap/climate/ip-phone).
- **G5 (future)** first-class Room/Zone regions (labeled polygons: Conference Room A,
  Office 203) and building wiring ports.

## Open questions

- Telephony: treat IP phones as `dcim.Device` (role = phone) or as `PhoneSystem`
  records, or both? Likely both, resolved by the registry.
- Location resolution for cross-app models whose location lives on a related record.
- Icon set source and licensing (Lucide is ISC-licensed; embed the specific glyph paths
  used).
- Whether to eventually drop the four typed FKs (cleaner) or keep them as convenience
  accessors.
