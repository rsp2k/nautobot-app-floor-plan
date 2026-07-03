# Hierarchical Location placement (campus → building → floor)

Floor plans model *layout within one place*; Nautobot's **Location** tree models
*which place contains which*. This doc covers (A) how to set up a
Campus → Building → Floor hierarchy in Nautobot, and (B) the drill-down placement
feature that lets a parent's blueprint carry clickable markers for its children.

## A. Modelling the hierarchy (Nautobot core, no app change)

Buildings and floors are **Locations** distinguished by **LocationType**. Define a
LocationType chain and parent your Locations along it:

```
LocationType:   Campus  ─parent→  Building  ─parent→  Floor
Location:       BH Campus
                  └─ Building 1        (location_type=Building, parent=BH Campus)
                       ├─ Floor 1      (location_type=Floor, parent=Building 1)
                       └─ Floor 2
```

- Each Location at **any** level can own exactly one `FloorPlan` (the model is a
  `OneToOneField` to `dcim.Location`). So a Campus can have a site map, each Building
  a footprint, each Floor a detailed plan.
- "Not always multiple floors" is fine: a single-floor building either holds its plan
  directly, or has one Floor child that does. The tree is as deep as reality.
- Enable the `nautobot_floor_plan.floorplan` **content type** on each LocationType you
  want plannable (Building, Floor, and Campus if you want a campus map).

Reference setup (shell_plus), adjust names to taste:

```python
from nautobot.dcim.models import LocationType
from django.contrib.contenttypes.models import ContentType
from nautobot_floor_plan.models import FloorPlan

fp_ct = ContentType.objects.get_for_model(FloorPlan)
campus = LocationType.objects.get_or_create(name="Campus")[0]
building = LocationType.objects.get_or_create(name="Building", defaults={"parent": campus})[0]
floor = LocationType.objects.get_or_create(name="Floor", defaults={"parent": building})[0]
for lt in (campus, building, floor):
    lt.content_types.add(fp_ct)  # allow floor plans at this level
```

Note: as of this writing bingham prod has a **single flat `Site` LocationType** — the
hierarchy above isn't modelled there yet. Defining it is a prerequisite for the
drill-down markers to be meaningful.

## B. Location as a drill-down marker

`dcim.location` is registered as a **placeable type** (`placement/defaults.py`), so you
can drop a child Location onto its parent's plan and click it to descend the tree.

Semantics:

- **A Location's "place" is its parent.** `location_resolver` returns `location.parent`,
  and `location_field="parent"` scopes the object picker to the plan's direct children.
  So on a Campus plan you place its Buildings; on a Building plan, its Floors.
- **A marker links to the placed Location's own floor plan tab** (`url_resolver` →
  `location_floor_plan_tab`). Clicking a building on the campus map opens that building's
  plan; clicking a floor opens the floor's plan. That's the navigable drill-down.
- **Container vs leaf glyphs.** A discriminator infers the icon from the tree, not from
  hardcoded type names: a Location *with* children renders as a **Building** (building
  glyph), a leaf renders as **Floor / Room** (layers glyph). Deployments with their own
  naming still get sensible icons.
- **Top-level Locations aren't placeable.** A Campus (no parent) resolves to no location,
  so it can't be dropped onto anything — which is correct.

Validation is enforced in two places that both go through the registry, so ORM writes
and the REST `place` endpoint agree: `FloorPlanTile._validate_generic_placement` and
`FloorPlanTilePlacementSerializer.validate`. A Floor can only land on its own Building's
plan; anything else is rejected.

No new model fields and **no migration** — this rides entirely on the generic-FK
placement registry (`placed_content_type` + `placed_object_id`) already shipped in
`0013`.

### Customising icons per LocationType

The container/leaf split is a sensible default. To brand specific types (e.g. a distinct
"Wing" or "Suite" glyph), register variants keyed by your own discriminator:

```python
from nautobot_floor_plan.placement.registry import registry
registry.register_variant("dcim.location", "wing", label="Wing", icon="building", legend_order=5)
# and set_discriminator(...) to map a Location -> "wing" by its location_type.name
```

## Tests

`nautobot_floor_plan/tests/test_models.py::TestLocationPlacement` covers registration,
parent resolution, the drill-down URL, a valid child-on-parent placement, and the two
rejection cases (wrong parent, top-level location).
