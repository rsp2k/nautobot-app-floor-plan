"""Models for Nautobot Floor Plan."""

import logging
import math
from dataclasses import dataclass

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from nautobot.apps.models import PrimaryModel, StatusField, extras_features
from nautobot.core.models.querysets import RestrictedQuerySet

from nautobot_floor_plan.choices import (
    ORIENTATION_TO_DEGREES,
    AllocationTypeChoices,
    AxisLabelsChoices,
    CustomAxisLabelsChoices,
    ObjectOrientationChoices,
    PlacementModeChoices,
)
from nautobot_floor_plan.placement import registry
from nautobot_floor_plan.svg import FloorPlanSVG
from nautobot_floor_plan.templatetags.seed_helpers import (
    render_axis_origin,
)
from nautobot_floor_plan.utils.custom_validators import ValidateNotZero
from nautobot_floor_plan.utils.label_generator import FloorPlanLabelGenerator

logger = logging.getLogger(__name__)


@dataclass
class TileOverlapData:
    """Data container for tile overlap validation."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int
    allocation_type: str
    rack_group: object
    tile: "FloorPlanTile"

    @classmethod
    def from_tile(cls, tile: "FloorPlanTile"):
        """Create TileOverlapData from a FloorPlanTile instance."""
        x_min, y_min, x_max, y_max, allocation_type, rack_group = tile.bounds
        return cls(x_min, y_min, x_max, y_max, allocation_type, rack_group, tile)

    def overlaps_with(self, other: "TileOverlapData") -> bool:
        """Check if this tile overlaps with another tile."""
        return (
            self.x_min <= other.x_max
            and other.x_min <= self.x_max
            and self.y_min <= other.y_max
            and other.y_min <= self.y_max
        )


@extras_features(
    "custom_fields",
    # "custom_links",  Not really needed since this doesn't have distinct views as compared to a Location.
    "custom_validators",
    "export_templates",
    "graphql",
    "relationships",
    "webhooks",
)
class FloorPlan(PrimaryModel):
    """
    Model representing the floor plan of a given Location.

    Within a FloorPlan, individual areas are defined as FloorPlanTile records.
    """

    location = models.OneToOneField(to="dcim.Location", on_delete=models.CASCADE, related_name="floor_plan")

    x_size = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text='Absolute width of the floor plan, in "tiles"',
    )
    y_size = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text='Absolute depth of the floor plan, in "tiles"',
    )
    tile_width = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        default=100,
        help_text='Relative width of each "tile" in the floor plan (cm, inches, etc.)',
    )
    tile_depth = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        default=100,
        help_text='Relative depth of each "tile" in the floor plan (cm, inches, etc.)',
    )
    x_axis_labels = models.CharField(
        max_length=12,
        choices=AxisLabelsChoices,
        default=AxisLabelsChoices.NUMBERS,
        help_text="Grid labels of X axis (horizontal).",
    )
    y_axis_labels = models.CharField(
        max_length=12,
        choices=AxisLabelsChoices,
        default=AxisLabelsChoices.NUMBERS,
        help_text="Grid labels of Y axis (vertical).",
    )
    x_origin_seed = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0)], default=1, help_text="User defined starting value for grid labeling"
    )
    y_origin_seed = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0)], default=1, help_text="User defined starting value for grid labeling"
    )
    x_axis_step = models.IntegerField(
        validators=[ValidateNotZero(0)],
        default=1,
        help_text="Positive or negative integer that will be used to step labeling.",
    )
    y_axis_step = models.IntegerField(
        validators=[ValidateNotZero(0)],
        default=1,
        help_text="Positive or negative integer that will be used to step labeling.",
    )
    is_tile_movable = models.BooleanField(default=True, help_text="Determines if Tiles can be moved once placed")

    placement_mode = models.CharField(
        max_length=10,
        choices=PlacementModeChoices,
        default=PlacementModeChoices.GRID,
        help_text="Grid snaps tiles to cells. Freeform places objects at any position over a background image.",
    )
    show_grid = models.BooleanField(
        default=True,
        help_text="Show the tile grid overlay. Turn off to show only the blueprint and placed objects.",
    )
    background_image = models.ImageField(
        upload_to="floor_plan_backgrounds/",
        blank=True,
        null=True,
        width_field="background_image_width",
        height_field="background_image_height",
        help_text="Optional blueprint image rendered behind the floor plan.",
    )
    background_image_width = models.PositiveIntegerField(blank=True, null=True, editable=False)
    background_image_height = models.PositiveIntegerField(blank=True, null=True, editable=False)
    background_opacity = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Opacity of the background blueprint image, from 0 (transparent) to 100 (opaque).",
    )
    # Calibration: placement rectangle of the blueprint in SVG user units. Null means auto-fit to the grid extent.
    bg_x = models.FloatField(blank=True, null=True, help_text="Calibration: blueprint left offset in SVG units.")
    bg_y = models.FloatField(blank=True, null=True, help_text="Calibration: blueprint top offset in SVG units.")
    bg_width = models.FloatField(blank=True, null=True, help_text="Calibration: blueprint width in SVG units.")
    bg_height = models.FloatField(blank=True, null=True, help_text="Calibration: blueprint height in SVG units.")
    bg_rotation = models.FloatField(default=0, help_text="Calibration: blueprint rotation in degrees.")
    # Source document (e.g. an architectural PDF) the blueprint is imported from. Kept so a user can
    # re-pick a page / re-crop later without re-uploading. The RenderBlueprintPages job rasterizes it
    # into BlueprintPage rows; the picked crop lands in background_image.
    source_document = models.FileField(
        upload_to="floor_plan_sources/",
        blank=True,
        null=True,
        help_text="Optional source document (e.g. a PDF) that blueprint pages are rendered from.",
    )

    class Meta:
        """Metaclass attributes."""

        ordering = ["location___name"]

    def __str__(self):
        """Stringify instance."""
        return f'Floor Plan for Location "{self.location.name}"'

    def get_svg(self, *, user, base_url, request=None):
        """Get SVG representation of this FloorPlan."""
        return FloorPlanSVG(floor_plan=self, user=user, base_url=base_url, request=request).render()

    def clean(self):
        """Validate the floor plan dimensions and other constraints."""
        super().clean()
        self.validate_no_resizing_with_tiles()

    def save(self, *args, **kwargs):
        """Override save in order to update any existing tiles."""
        if self.present_in_database:
            # Get origin_seed pre/post values
            initial_instance = self.__class__.objects.get(pk=self.pk)
            x_initial = initial_instance.x_origin_seed
            y_initial = initial_instance.y_origin_seed
            changed = x_initial != self.x_origin_seed or y_initial != self.y_origin_seed
        else:
            changed = False

        with transaction.atomic():
            super().save(**kwargs)

            if changed:
                tiles = self.update_tile_origins(x_initial, self.x_origin_seed, y_initial, self.y_origin_seed)
                for tile in tiles:
                    tile.validated_save()

    def update_tile_origins(self, x_initial, x_updated, y_initial, y_updated):
        """Update any existing tiles if axis_origin_seed was modified."""
        # Pure-freeform tiles have no grid origins to shift.
        tiles = self.tiles.filter(x_origin__isnull=False, y_origin__isnull=False)
        x_delta = x_updated - x_initial
        y_delta = y_updated - y_initial

        if x_delta > 0:
            tiles = tiles.order_by("-x_origin")
        if y_delta > 0:
            tiles = tiles.order_by("-y_origin")

        for tile in tiles:
            tile.x_origin += x_delta
            tile.y_origin += y_delta

        return tiles

    def validate_no_resizing_with_tiles(self):
        """Prevent resizing the floor plan dimensions if tiles have been placed."""
        if self.tiles.exists():
            # Check for original instance
            original = self.__class__.objects.filter(pk=self.pk).first()
            if original:
                # Don't allow resize if tile is placed
                if self.x_size != original.x_size or self.y_size != original.y_size:
                    raise ValidationError(
                        "Cannot resize a FloorPlan after tiles have been placed. "
                        f"FloorPlan must maintain original size: ({original.x_size}, {original.y_size}), "
                    )

    def generate_labels(self, axis, count):
        """
        Generate labels for the specified axis.

        This method creates an instance of FloorPlanLabelGenerator and uses it to generate labels
        based on the specified axis and count. It will first check for any custom labels defined
        for the axis and use them if available; otherwise, it will generate default labels.
        """
        generator = FloorPlanLabelGenerator(self)
        return generator.generate_labels(axis, count)

    def reset_seed_for_custom_labels(self):
        """Reset seed and step values when custom labels are added."""
        # Only proceed if there are custom labels
        if not self.custom_labels.exists():
            return

        changed = False
        x_has_custom = self.custom_labels.filter(axis="X").exists()
        y_has_custom = self.custom_labels.filter(axis="Y").exists()

        if x_has_custom and (self.x_origin_seed != 1 or self.x_axis_step != 1):
            self.x_origin_seed = 1
            self.x_axis_step = 1
            changed = True

        if y_has_custom and (self.y_origin_seed != 1 or self.y_axis_step != 1):
            self.y_origin_seed = 1
            self.y_axis_step = 1
            changed = True

        if changed:
            # Get the current values before updating
            initial_instance = self.__class__.objects.get(pk=self.pk)
            x_initial = initial_instance.x_origin_seed
            y_initial = initial_instance.y_origin_seed

            # Update tile positions only for axes that have custom labels
            tiles = self.update_tile_origins(
                x_initial=x_initial if x_has_custom else self.x_origin_seed,
                x_updated=1 if x_has_custom else self.x_origin_seed,
                y_initial=y_initial if y_has_custom else self.y_origin_seed,
                y_updated=1 if y_has_custom else self.y_origin_seed,
            )

            # Save without triggering another reset
            super().save()

            # Update tiles
            for tile in tiles:
                tile.validated_save()

    def convert_to_freeform(self, *, force=False, save=True):
        """Seed freeform coordinates for grid tiles from their grid cells.

        Positions are center-anchored and normalized to the content rect, matching the renderer. This
        is idempotent (already-seeded tiles are skipped unless ``force``) and reversible: grid origins
        are never touched, so a plan can be switched back to grid mode without data loss. Returns the
        list of modified tiles.
        """
        x_total, y_total = self.x_size, self.y_size  # >= 1 via MinValueValidator, so no divide-by-zero
        modified = []
        for tile in self.tiles.filter(x_origin__isnull=False, y_origin__isnull=False):
            if not force and tile.pos_x is not None and tile.pos_y is not None:
                continue
            col = tile.x_origin - self.x_origin_seed
            row = tile.y_origin - self.y_origin_seed
            tile.pos_x = (col + 0.5 * tile.x_size) / x_total
            tile.pos_y = (row + 0.5 * tile.y_size) / y_total
            tile.width = tile.x_size / x_total
            tile.height = tile.y_size / y_total
            tile.rotation = ORIENTATION_TO_DEGREES.get(tile.object_orientation, 0)
            modified.append(tile)
        if save and modified:
            with transaction.atomic():
                for tile in modified:
                    # In-range by construction (validate_tile_placement bounds), so save the geometry
                    # fields directly rather than re-running grid overlap validation.
                    tile.save(update_fields=["pos_x", "pos_y", "width", "height", "rotation"])
        return modified


@extras_features(
    "custom_fields",
    "custom_validators",
    "graphql",
    "relationships",
    "webhooks",
)
class FloorPlanCustomAxisLabel(models.Model):
    """Model allowing for the creation of custom grid labels."""

    floor_plan = models.ForeignKey(
        to="FloorPlan",
        on_delete=models.CASCADE,
        related_name="custom_labels",
    )
    axis = models.CharField(
        max_length=1,
        choices=(("X", "X Axis"), ("Y", "Y Axis")),
    )
    label_type = models.CharField(
        max_length=20,
        choices=CustomAxisLabelsChoices,
        default=AxisLabelsChoices.LETTERS,
        help_text="Type of labeling system to use",
    )
    start_label = models.CharField(
        max_length=10,
        help_text="Starting label for this custom label range.",
    )
    end_label = models.CharField(
        max_length=10,
        help_text="Ending label for this custom label range.",
    )
    step = models.IntegerField(
        validators=[ValidateNotZero(0)],
        default=1,
        help_text="Positive or negative step for this label range.",
    )
    increment_letter = models.BooleanField(
        default=True,
        help_text="For letter-based labels, determines increment pattern.",
    )

    order = models.PositiveIntegerField(
        default=0,
        help_text="Order of the custom label range.",
    )

    class Meta:
        """Meta attributes."""

        ordering = ["floor_plan", "axis", "order"]

    def save(self, *args, **kwargs):
        """Override save to reset seed values when custom labels are added."""
        super().save(*args, **kwargs)
        # Reset the corresponding seed value to 1
        self.floor_plan.reset_seed_for_custom_labels()

    def clean(self):
        """Add validation to ensure seed values are reset."""
        super().clean()
        # If this is a new custom label (no pk) or the axis has changed
        if not self.pk or (self.pk and self._state.fields_cache.get("axis") != self.axis):
            if self.axis == "X" and self.floor_plan.x_origin_seed != 1:
                self.floor_plan.x_origin_seed = 1
            elif self.axis == "Y" and self.floor_plan.y_origin_seed != 1:
                self.floor_plan.y_origin_seed = 1


class FloorPlanTileQuerySet(RestrictedQuerySet):
    """QuerySet adding a generic reverse lookup for placed objects."""

    def for_object(self, obj):
        """Return the tile(s) placing a given object (via the generic placement pair)."""
        if obj is None or obj.pk is None:
            return self.none()
        content_type = ContentType.objects.get_for_model(obj)
        return self.filter(placed_content_type=content_type, placed_object_id=obj.pk)


@extras_features(
    "custom_fields",
    # "custom_links",  Not really needed since this doesn't have distinct views.
    "custom_validators",
    # "export_templates",  Not really useful here
    "graphql",
    "relationships",
    "statuses",
    "webhooks",
)
class FloorPlanTile(PrimaryModel):
    """Model representing a single rectangular "tile" within a FloorPlan, its status, and any Rack that it contains."""

    status = StatusField(blank=False, null=False)
    floor_plan = models.ForeignKey(to=FloorPlan, on_delete=models.CASCADE, related_name="tiles")
    # TODO: for efficiency we could consider using something like GeoDjango, rather than inventing geometry from
    # first principles, but since that requires changing settings.DATABASES and installing libraries, avoid it for now.
    # Nullable so a plan can hold pure-freeform tiles (positioned only by pos_x/pos_y). Grid tiles
    # and grid-to-freeform conversions keep their origins. The Meta CheckConstraint enforces that the
    # pair is set together or not at all.
    x_origin = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)], blank=True, null=True)
    y_origin = models.PositiveSmallIntegerField(validators=[MinValueValidator(0)], blank=True, null=True)
    x_size = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        default=1,
        help_text="Number of tile spaces that this spans horizontally",
    )
    y_size = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        default=1,
        help_text="Number of tile spaces that this spans vertically",
    )
    device = models.OneToOneField(
        to="dcim.Device",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="floor_plan_tile",
    )
    power_panel = models.OneToOneField(
        to="dcim.PowerPanel",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="floor_plan_tile",
    )
    power_feed = models.OneToOneField(
        to="dcim.PowerFeed",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="floor_plan_tile",
    )
    rack = models.OneToOneField(
        to="dcim.Rack",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="floor_plan_tile",
    )

    rack_group = models.ForeignKey(
        to="dcim.RackGroup",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="rack_groups",
    )

    # Generic placement target: lets a tile place ANY object type, not just the four typed FKs above.
    # The typed FKs remain the write path during the transition and are mirrored into this pair.
    # on_delete=CASCADE (not PROTECT) so a removed ContentType (e.g. an uninstalled app) doesn't block
    # `remove_stale_contenttypes`; the orphaned tile is cleaned up with it.
    placed_content_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="floor_plan_tiles",
    )
    placed_object_id = models.UUIDField(blank=True, null=True, db_index=True)
    placed_object = GenericForeignKey("placed_content_type", "placed_object_id")
    # Denormalized display for the placed object, kept in sync on save. Enables sorting/searching a
    # table of heterogeneous placed types without joining arbitrary target tables.
    placed_label = models.CharField(max_length=255, blank=True, db_index=True)

    objects = models.Manager.from_queryset(FloorPlanTileQuerySet)()

    object_orientation = models.CharField(
        max_length=10,
        choices=ObjectOrientationChoices,
        blank=True,
        help_text="Direction the object's front is facing on the floor plan",
    )
    allocation_type = models.CharField(
        choices=AllocationTypeChoices,
        max_length=10,
        blank=True,
        help_text="Assigns a type of either Object or RackGroup to a tile",
    )

    on_group_tile = models.BooleanField(
        default=False, blank=True, help_text="Determines if a tile is placed on top of another tile"
    )

    # Freeform placement: normalized position and size relative to the blueprint extent (0..1).
    # Populated when the parent FloorPlan is in freeform mode; null for pure grid tiles.
    pos_x = models.FloatField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Freeform X position (object center), normalized 0..1 across the content rect width.",
    )
    pos_y = models.FloatField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Freeform Y position (object center), normalized 0..1 across the content rect height.",
    )
    width = models.FloatField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="Freeform width, normalized to the content rect width (may exceed 1 near an edge).",
    )
    height = models.FloatField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="Freeform height, normalized to the content rect height (may exceed 1 near an edge).",
    )
    rotation = models.FloatField(default=0, help_text="Freeform rotation in degrees, clockwise.")

    class Meta:
        """Metaclass attributes."""

        ordering = ["floor_plan", "y_origin", "x_origin"]
        unique_together = ["floor_plan", "x_origin", "y_origin", "allocation_type"]
        constraints = [
            models.CheckConstraint(
                name="floorplantile_origin_pairing",
                check=models.Q(x_origin__isnull=False, y_origin__isnull=False)
                | models.Q(x_origin__isnull=True, y_origin__isnull=True),
            ),
            models.CheckConstraint(
                name="floorplantile_placed_object_pairing",
                check=models.Q(placed_content_type__isnull=False, placed_object_id__isnull=False)
                | models.Q(placed_content_type__isnull=True, placed_object_id__isnull=True),
            ),
            # A given object may sit on at most one tile. Plain (not partial) unique constraint so it
            # is portable to MySQL; NULLs are distinct on both backends, so multiple object-less
            # (rackgroup/status) tiles coexist.
            models.UniqueConstraint(
                fields=["placed_content_type", "placed_object_id"],
                name="floorplantile_unique_placed_object",
            ),
        ]

    def allocation_type_assignment(self):
        """Assign the appropriate tile allocation type when saving in clean."""
        # Reset on_group_tile to False by default
        self.on_group_tile = False

        # Assign Allocation type based off of Tile Assignment
        if self.rack_group is not None or self.status is not None:
            self.allocation_type = AllocationTypeChoices.RACKGROUP
        if any([self.rack, self.device, self.power_panel, self.power_feed]):
            self.allocation_type = AllocationTypeChoices.OBJECT
        # A generic placement (no typed FK) is still an object tile.
        if self.placed_object_id is not None and self._typed_object() is None:
            self.allocation_type = AllocationTypeChoices.OBJECT

        # Ensure new tiles with just a status get an allocation type
        if not self.allocation_type and self.status:
            self.allocation_type = AllocationTypeChoices.RACKGROUP

    def _typed_object(self):
        """Return the object held by a legacy typed FK, if any (rack/device/power panel/feed)."""
        for obj in (self.rack, self.device, self.power_panel, self.power_feed):
            if obj is not None:
                return obj
        return None

    def _has_placed_object(self):
        """Whether this tile places an object, via either a typed FK or the generic pair."""
        return self.placed_object_id is not None or self._typed_object() is not None

    def _sync_placed_object_from_typed(self):
        """Mirror a set legacy typed FK into the generic placement pair.

        During the transition the typed FK is the write path; this keeps the generic pair (and its
        uniqueness guarantee) consistent. Generic-only placement is managed directly by later waves.
        """
        typed = self._typed_object()
        if typed is not None:
            self.placed_content_type = ContentType.objects.get_for_model(typed)
            self.placed_object_id = typed.pk

    def _update_placed_label(self):
        """Refresh the denormalized display label for the placed object."""
        obj = self._typed_object() or self.placed_object
        if obj is None:
            self.placed_label = ""
            return
        name = getattr(obj, "name", None) or str(obj)
        self.placed_label = str(name)[:255]

    def save(self, *args, **kwargs):
        """Keep the generic placement pair and display label in sync on every save."""
        self._sync_placed_object_from_typed()
        self._update_placed_label()
        super().save(*args, **kwargs)

    def validate_tile_placement(self):
        """Check that tile fits within the floorplan."""
        if self.x_origin > self.floor_plan.x_size + self.floor_plan.x_origin_seed - 1:
            raise ValidationError({"x_origin": f"Too large for {self.floor_plan}"})
        if self.y_origin > self.floor_plan.y_size + self.floor_plan.y_origin_seed - 1:
            raise ValidationError({"y_origin": f"Too large for {self.floor_plan}"})
        if self.x_origin < self.floor_plan.x_origin_seed:
            raise ValidationError({"x_origin": f"Too small for {self.floor_plan}"})
        if self.y_origin < self.floor_plan.y_origin_seed:
            raise ValidationError({"y_origin": f"Too small for {self.floor_plan}"})
        if self.x_origin + self.x_size - 1 > self.floor_plan.x_size + self.floor_plan.x_origin_seed - 1:
            raise ValidationError({"x_size": f"Extends beyond the edge of {self.floor_plan}"})
        if self.y_origin + self.y_size - 1 > self.floor_plan.y_size + self.floor_plan.y_origin_seed - 1:
            raise ValidationError({"y_size": f"Extends beyond the edge of {self.floor_plan}"})

    @property
    def bounds(self):
        """Get the tuple representing the set of grid spaces occupied by this FloorPlanTile.

        This function also serves to return the allocation_type and rack_group of the underlying tile
        to ensure non-like allocation_types can overlap and non-like rack_group are unable to overlap.
        """
        return (
            self.x_origin,
            self.y_origin,
            self.x_origin + self.x_size - 1,
            self.y_origin + self.y_size - 1,
            self.allocation_type,
            self.rack_group,
        )

    def clean(self):
        """
        Validate parameters above and beyond what the database can provide.

        - Ensure that the bounds of this FloorPlanTile lie within the parent FloorPlan's bounds.
        - Ensure that assigned objects belong to the correct Location.
        - Ensure that this FloorPlanTile doesn't overlap with any other FloorPlanTile in this FloorPlan.
        - Ensure that devices aren't currently installed in racks.
        - Ensure that racks belong to the correct rack group when placed on rack group tiles.
        - Ensure that object tiles don't extend beyond their containing rack group tiles.
        - Ensure that rack group tiles from different groups don't overlap.
        - Ensure proper allocation type assignment and group tile status.
        - Ensure only one object is assigned to the tile.
        """
        super().clean()
        FloorPlanTile.allocation_type_assignment(self)
        self._validate_freeform()

        grid_positioned = self.x_origin is not None and self.y_origin is not None
        freeform_mode = self.floor_plan.placement_mode == PlacementModeChoices.FREEFORM
        if grid_positioned:
            # Bounds are always meaningful when origins are set; skipping them would let stale data drift.
            FloorPlanTile.validate_tile_placement(self)
            # Grid cell-collision rules only apply in grid mode. In freeform mode physical adjacency
            # (and overlap) is intentional, so these are suppressed even for converted tiles.
            if not freeform_mode:
                self._validate_tile_overlaps()
                self._validate_rack_rackgroup()

        self._validate_installed_objects()
        self._validate_object_locations()
        self._validate_single_object_assignment()
        self._validate_generic_placement()

    def _validate_generic_placement(self):
        """Validate a generic-only placed object (no typed FK): registered, resolvable, right location."""
        if self._typed_object() is not None or self.placed_object_id is None:
            return
        obj = self.placed_object
        if obj is None:
            return  # dangling id is caught by the pairing constraint / referential checks
        placement = registry.resolve(obj)
        if placement is None:
            raise ValidationError({"placed_content_type": f"{obj._meta.label} is not a registered placeable type."})
        location = registry.resolve_location(obj)
        if location is None:
            raise ValidationError(
                {"placed_object_id": f"{obj} has no resolvable Location; link it to a Device or set its site first."}
            )
        if location != self.floor_plan.location:
            raise ValidationError(
                {"placed_object_id": f"{obj} must belong to Location {self.floor_plan.location}, not {location}."}
            )

    def _validate_freeform(self):
        """Validate freeform placement coordinates when present."""
        if self.pos_x is None and self.pos_y is None:
            return
        if self.pos_x is None or self.pos_y is None:
            raise ValidationError({"pos_x": "Both pos_x and pos_y are required for freeform placement."})
        for field in ("pos_x", "pos_y"):
            value = getattr(self, field)
            if not math.isfinite(value):
                raise ValidationError({field: "Must be a finite number."})
            if not 0 <= value <= 1:
                raise ValidationError({field: "Must be between 0 and 1."})
        for field in ("width", "height"):
            value = getattr(self, field)
            if value is None:
                continue
            if not math.isfinite(value):
                raise ValidationError({field: "Must be a finite number."})
            if value <= 0:
                raise ValidationError({field: "Must be greater than 0."})
        if self.rotation is not None and math.isfinite(self.rotation):
            self.rotation %= 360

    def _validate_installed_objects(self):
        """Validate that devices aren't installed in racks."""
        if self.device and self.device.rack:
            raise ValidationError(
                {
                    "device": f"Device '{self.device}' is installed in Rack '{self.device.rack}'. "
                    "Please remove it from the rack before placing on the floor plan."
                }
            )

    def _validate_object_locations(self):
        """Validate location for all assigned objects."""
        assigned_objects = {
            "device": self.device,
            "rack": self.rack,
            "power_panel": self.power_panel,
            "power_feed": self.power_feed,
        }

        for obj_type, obj in assigned_objects.items():
            if obj is not None:
                # Power Feeds location is not required so we will check the connected power panel location instead
                if obj_type == "power_feed":
                    if obj.power_panel.location != self.floor_plan.location:
                        raise ValidationError(
                            {
                                obj_type: f"{obj.power_panel} must belong to Location {self.floor_plan.location}, not Location {obj.power_panel.location}"
                            }
                        )
                elif hasattr(obj, "location") and obj.location != self.floor_plan.location:
                    raise ValidationError(
                        {
                            obj_type: f"{obj} must belong to Location {self.floor_plan.location}, not Location {obj.location}"
                        }
                    )

    def _validate_tile_overlaps(self):
        """Validate that this FloorPlanTile doesn't overlap with any other FloorPlanTile in this FloorPlan."""
        current_tile_data = TileOverlapData.from_tile(self)

        for other in FloorPlanTile.objects.filter(floor_plan=self.floor_plan).exclude(pk=self.pk):
            other_tile_data = TileOverlapData.from_tile(other)

            if current_tile_data.overlaps_with(other_tile_data):
                # Validate based on allocation types
                self._validate_object_tile_overlap(current_tile_data, other_tile_data)
                self._validate_rackgroup_tile_overlap(current_tile_data, other_tile_data)

    def _validate_object_tile_overlap(self, current: TileOverlapData, other: TileOverlapData):
        """Validate overlaps for object tiles."""
        if current.allocation_type == AllocationTypeChoices.OBJECT:
            if other.allocation_type == AllocationTypeChoices.OBJECT:
                raise ValidationError("Object tiles cannot overlap")
            if other.allocation_type == AllocationTypeChoices.RACKGROUP:
                # Set on_group_tile for any object type overlapping with a RackGroup
                self.on_group_tile = True

                # Special handling for racks to ensure they belong to the correct rack group
                if self.rack and self.rack.rack_group:
                    if other.rack_group != self.rack.rack_group:
                        raise ValidationError(
                            f"Object tile with Rack {self.rack} cannot overlap with RackGroup tile for different group"
                        )
                    self.rack_group = self.rack.rack_group

                # Validate object tile fits within rack group bounds
                if (
                    current.x_min < other.x_min
                    or current.x_max > other.x_max
                    or current.y_min < other.y_min
                    or current.y_max > other.y_max
                ):
                    raise ValidationError("Object tile must not extend beyond the boundary of the rack group tile")

    def _validate_rackgroup_tile_overlap(self, current: TileOverlapData, other: TileOverlapData):
        """Validate overlaps for rack group tiles."""
        if current.allocation_type == AllocationTypeChoices.RACKGROUP:
            if other.allocation_type == AllocationTypeChoices.RACKGROUP:
                # Prevent any rack group tiles from overlapping
                raise ValidationError("RackGroup tiles cannot overlap")
            if other.allocation_type == AllocationTypeChoices.OBJECT and current.rack_group:
                other_tile = other.tile
                if other_tile.rack and other_tile.rack.rack_group and other_tile.rack.rack_group != current.rack_group:
                    raise ValidationError(
                        f"RackGroup tile cannot overlap with Rack {other_tile.rack} from different group"
                    )

    def _validate_rack_rackgroup(self):
        """Validate that racks belong to the correct rack group when placed on rack group tiles."""
        if not self.rack:
            return

        # If this tile has a rack_group, the rack must belong to it
        if self.rack_group and self.rack.rack_group != self.rack_group:
            raise ValidationError(
                f"Rack {self.rack} must belong to rack group {self.rack_group}, not {self.rack.rack_group}"
            )

        # Check if this rack overlaps with any rack group tiles
        overlapping_tiles = FloorPlanTile.objects.filter(
            floor_plan=self.floor_plan, allocation_type=AllocationTypeChoices.RACKGROUP
        ).exclude(pk=self.pk)

        for tile in overlapping_tiles:
            if (
                self.x_origin <= tile.x_origin + tile.x_size - 1
                and tile.x_origin <= self.x_origin + self.x_size - 1
                and self.y_origin <= tile.y_origin + tile.y_size - 1
                and tile.y_origin <= self.y_origin + self.y_size - 1
            ):
                # Only validate if the overlapping tile has a rack_group
                if tile.rack_group and self.rack.rack_group != tile.rack_group:
                    raise ValidationError(
                        f"Rack {self.rack} cannot be placed on rack group tile for {tile.rack_group} "
                        f"as it belongs to {self.rack.rack_group}"
                    )

    def _validate_single_object_assignment(self):
        """Validate that only one object is assigned to the tile."""
        assigned_objects = []
        object_fields = ["device", "rack", "power_panel", "power_feed"]

        for field in object_fields:
            if getattr(self, field) is not None:
                assigned_objects.append(field)

        if len(assigned_objects) > 1:
            object_names = {
                "device": "Device",
                "rack": "Rack",
                "power_panel": "Power Panel",
                "power_feed": "Power Feed",
            }

            # Add error to each selected field except the first one
            raise ValidationError(
                {
                    field: f"Only one object can be selected. You have already selected a {object_names[assigned_objects[0]]}."
                    for field in assigned_objects[1:]
                }
            )

    def __str__(self):
        """Stringify instance."""
        if self.x_origin is None or self.y_origin is None:
            return f"Tile (freeform {self.pos_x}, {self.pos_y}) in {self.floor_plan}"
        return f"Tile ({render_axis_origin(self, 'X')}, {render_axis_origin(self, 'Y')}), ({self.x_size},{self.y_size}) in {self.floor_plan}"


@extras_features(
    "custom_fields",
    "custom_validators",
    "export_templates",
    "graphql",
    "relationships",
    "webhooks",
)
class FloorPlanObjectType(PrimaryModel):
    """Admin-defined placeable-type config, merged into the placement registry at runtime.

    Lets operators add or override how an object type (or a variant of it) is placed and drawn on a
    floor plan — label, glyph, color, legend order — with no code change. External apps can still
    push their own registrations; a row here with ``override=True`` wins over those.
    """

    content_type = models.ForeignKey(
        to=ContentType,
        on_delete=models.CASCADE,
        related_name="floor_plan_object_types",
        help_text="The placeable model this config applies to (e.g. dcim.device).",
    )
    variant_key = models.CharField(
        max_length=100,
        blank=True,
        help_text="If set, defines a variant of the base type, selected by the match rule below.",
    )
    label = models.CharField(max_length=100)
    color = models.CharField(max_length=6, blank=True, help_text="Hex color, no leading '#'.")
    glyph_key = models.CharField(max_length=50, blank=True, help_text="Name of a built-in floor-plan glyph.")
    custom_glyph_paths = models.JSONField(
        null=True,
        blank=True,
        help_text="List of SVG path 'd' strings; overrides glyph_key when set.",
    )
    glyph_viewbox = models.PositiveSmallIntegerField(default=24)
    legend_order = models.IntegerField(default=100)
    location_field = models.CharField(
        max_length=100,
        default="location",
        help_text="ORM path from the object to its Location (e.g. power_panel__location).",
    )
    match_field = models.CharField(
        max_length=100,
        blank=True,
        help_text="For a variant: dotted attribute read from the object (e.g. role.name).",
    )
    match_keywords = models.JSONField(
        null=True,
        blank=True,
        help_text="For a variant: list of substrings that select this variant.",
    )
    match_precedence = models.IntegerField(default=100, help_text="Lower runs first when variants compete.")
    override = models.BooleanField(default=True, help_text="Replace an existing code/app registration for this type.")
    enabled = models.BooleanField(default=True)

    class Meta:
        """Meta attributes."""

        ordering = ["content_type", "legend_order", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "variant_key"],
                name="floorplanobjecttype_unique_type_variant",
            ),
        ]

    def __str__(self):
        """Stringify instance."""
        suffix = f" [{self.variant_key}]" if self.variant_key else ""
        return f"{self.label} ({self.content_type.app_label}.{self.content_type.model}){suffix}"

    def clean(self):
        """Validate the glyph source and the variant match-rule pairing."""
        super().clean()
        from nautobot_floor_plan.placement.icons import ICON_GLYPHS  # noqa: PLC0415  local: keep import light

        if self.glyph_key and self.custom_glyph_paths:
            raise ValidationError("Set either a built-in glyph_key OR custom_glyph_paths, not both.")
        if self.glyph_key and self.glyph_key not in ICON_GLYPHS:
            raise ValidationError({"glyph_key": f"Unknown built-in glyph '{self.glyph_key}'."})
        if bool(self.match_field) != bool(self.match_keywords):
            raise ValidationError("match_field and match_keywords must be set together.")
        if (self.match_field or self.match_keywords) and not self.variant_key:
            raise ValidationError("A match rule (match_field/match_keywords) requires a variant_key.")


class BlueprintPage(PrimaryModel):
    """A single page of a FloorPlan's ``source_document``, rasterized by the RenderBlueprintPages job.

    These are derived artifacts: the job clears and re-creates them from the source document, and the
    picker lists them so a user can choose a page and crop it into ``background_image``. Rendering the
    page (not extracting embedded images) is deliberate -- print-to-PDF sets embed the firm logo, not
    the drawing.
    """

    floor_plan = models.ForeignKey(
        to="nautobot_floor_plan.FloorPlan",
        on_delete=models.CASCADE,
        related_name="blueprint_pages",
    )
    page_number = models.PositiveIntegerField(help_text="1-based page number in the source document.")
    image = models.ImageField(
        upload_to="floor_plan_pages/",
        width_field="image_width",
        height_field="image_height",
        help_text="Full-resolution raster of the rendered page.",
    )
    image_width = models.PositiveIntegerField(blank=True, null=True, editable=False)
    image_height = models.PositiveIntegerField(blank=True, null=True, editable=False)
    thumbnail = models.ImageField(
        upload_to="floor_plan_page_thumbs/",
        blank=True,
        help_text="Downscaled preview for the page picker grid.",
    )

    class Meta:
        """Meta attributes."""

        ordering = ["floor_plan", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["floor_plan", "page_number"],
                name="blueprintpage_unique_plan_page",
            ),
        ]

    def __str__(self):
        """Stringify instance."""
        return f"{self.floor_plan} page {self.page_number}"


@extras_features(
    "custom_fields",
    "custom_validators",
    "export_templates",
    "graphql",
    "relationships",
    "webhooks",
)
class FloorPlanLayer(PrimaryModel):
    """A named, show/hideable group of markers on rendered floor plans (e.g. an "AP layer").

    Membership is the union of rule sources (content types, tags, dynamic groups) and a manual static
    set (``FloorPlanLayerObject`` rows): a marker belongs to the layer if it matches any source. A
    layer scoped to a ``floor_plan`` applies only there; a global layer (``floor_plan=None``) applies
    to every plan. Styling is visibility + dim only -- a layer never recolors a marker, so a marker's
    Status color always wins. ``color`` here is a panel swatch, nothing more.
    """

    name = models.CharField(max_length=100)
    floor_plan = models.ForeignKey(
        to="nautobot_floor_plan.FloorPlan",
        on_delete=models.CASCADE,
        related_name="layers",
        blank=True,
        null=True,
        help_text="If set, this layer applies only to this plan; leave blank for a global layer.",
    )
    source_content_types = models.ManyToManyField(
        to="contenttypes.ContentType",
        related_name="floor_plan_layers",
        blank=True,
        help_text="Markers of these content types are members.",
    )
    source_tags = models.ManyToManyField(
        to="extras.Tag",
        related_name="floor_plan_layers",
        blank=True,
        help_text="Markers whose object carries any of these tags are members.",
    )
    source_dynamic_groups = models.ManyToManyField(
        to="extras.DynamicGroup",
        related_name="floor_plan_layers",
        blank=True,
        help_text="Markers whose object is a member of any of these dynamic groups are members.",
    )
    color = models.CharField(
        max_length=6,
        blank=True,
        help_text="Hex color for the panel swatch only, no leading '#'. Never applied to markers.",
    )
    opacity = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Default dim level (0-100) applied to members while the layer is shown.",
    )
    default_visible = models.BooleanField(default=True, help_text="Whether the layer starts shown when a plan loads.")
    display_order = models.IntegerField(default=100, help_text="Lower sorts first in the Layers panel.")

    class Meta:
        """Meta attributes."""

        ordering = ["display_order", "name"]
        constraints = [
            # NULL floor_plan is distinct on both backends, so two global layers may share a name; a
            # per-plan name is unique within that plan. Good enough without a partial index.
            models.UniqueConstraint(fields=["floor_plan", "name"], name="floorplanlayer_unique_plan_name"),
        ]

    def __str__(self):
        """Stringify instance."""
        scope = self.floor_plan if self.floor_plan_id else "global"
        return f"{self.name} ({scope})"


class FloorPlanLayerObject(PrimaryModel):
    """A single object explicitly pinned into a ``FloorPlanLayer``'s static membership set.

    The generic ``(content_type, object_id)`` pair mirrors ``FloorPlanTile``'s placement pair so any
    placeable object can join a layer regardless of type. These rows are unioned with the layer's rule
    sources during membership resolution. Managed through the layer form / REST API, not its own view.
    """

    layer = models.ForeignKey(
        to="nautobot_floor_plan.FloorPlanLayer",
        on_delete=models.CASCADE,
        related_name="static_objects",
    )
    content_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        related_name="floor_plan_layer_objects",
    )
    object_id = models.UUIDField(db_index=True)
    obj = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Meta attributes."""

        ordering = ["layer"]
        constraints = [
            models.UniqueConstraint(
                fields=["layer", "content_type", "object_id"],
                name="floorplanlayerobject_unique_member",
            ),
        ]

    def __str__(self):
        """Stringify instance."""
        return f"{self.obj} in {self.layer}"

    def clean(self):
        """A plan-scoped layer's static object must live in the plan's Location (mirror FloorPlanTile)."""
        super().clean()
        if not (self.layer_id and self.layer.floor_plan_id and self.object_id):
            return
        obj = self.obj
        if obj is None or not registry.is_registered(obj):
            return
        location = registry.resolve_location(obj)
        if location is not None and location != self.layer.floor_plan.location:
            raise ValidationError(
                {"object_id": f"{obj} must belong to Location {self.layer.floor_plan.location}, not {location}."}
            )
