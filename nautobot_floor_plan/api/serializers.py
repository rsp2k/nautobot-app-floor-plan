"""API serializers for nautobot_floor_plan."""

import math

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from nautobot.apps.api import NautobotModelSerializer, TaggedModelSerializerMixin
from nautobot.extras.models import Status
from rest_framework import serializers

from nautobot_floor_plan import models
from nautobot_floor_plan.placement import registry

# Geometry-only fields that the drag/calibrate client may PATCH in isolation.
TILE_GEOMETRY_FIELDS = {"pos_x", "pos_y", "width", "height", "rotation"}
# Calibration/opacity fields the client PATCHes on the plan while dragging the blueprint.
CALIBRATION_FIELDS = {"bg_x", "bg_y", "bg_width", "bg_height", "bg_rotation", "background_opacity"}


def _default_tile_status():
    """First Status applicable to FloorPlanTile, or a field-anchored error."""
    status = Status.objects.get_for_model(models.FloorPlanTile).first()
    if status is None:
        raise serializers.ValidationError({"status": "No Status is available for floor plan tiles."})
    return status


def validate_finite(value):
    """Reject NaN/Infinity, which slip past DRF min_value/max_value comparisons."""
    if value is not None and not math.isfinite(value):
        raise serializers.ValidationError("Must be a finite number.")


class FloorPlanSerializer(NautobotModelSerializer, TaggedModelSerializerMixin):  # pylint: disable=too-many-ancestors
    """FloorPlan Serializer."""

    bg_x = serializers.FloatField(required=False, allow_null=True, validators=[validate_finite])
    bg_y = serializers.FloatField(required=False, allow_null=True, validators=[validate_finite])
    bg_width = serializers.FloatField(required=False, allow_null=True, validators=[validate_finite])
    bg_height = serializers.FloatField(required=False, allow_null=True, validators=[validate_finite])
    bg_rotation = serializers.FloatField(required=False, validators=[validate_finite])

    class Meta:
        """Meta attributes."""

        model = models.FloorPlan
        fields = "__all__"

    def update(self, instance, validated_data):
        """Persist a calibration/opacity-only change without re-running full plan validation.

        Dragging the blueprint PATCHes only ``bg_*``/opacity; routing that through ``full_clean`` could
        reject a pure reposition on unrelated state (e.g. placement_mode), so those writes are direct.
        """
        if validated_data and set(validated_data).issubset(CALIBRATION_FIELDS):
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save(update_fields=list(validated_data))
            return instance
        return super().update(instance, validated_data)


class FloorPlanCustomAxisLabelSerializer(NautobotModelSerializer, TaggedModelSerializerMixin):
    """FloorPlanCustomAxisLabel Serializer."""

    class Meta:
        """Meta attributes."""

        model = models.FloorPlanCustomAxisLabel
        fields = "__all__"


class FloorPlanTileSerializer(NautobotModelSerializer, TaggedModelSerializerMixin):
    """FloorPlanTile Serializer."""

    pos_x = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=1, validators=[validate_finite])
    pos_y = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=1, validators=[validate_finite])
    width = serializers.FloatField(required=False, allow_null=True, min_value=0, validators=[validate_finite])
    height = serializers.FloatField(required=False, allow_null=True, min_value=0, validators=[validate_finite])
    rotation = serializers.FloatField(required=False, validators=[validate_finite])

    class Meta:
        """Meta attributes."""

        model = models.FloorPlanTile
        fields = "__all__"
        # Generic placement is mirrored from the typed FKs during the transition and exposed read-only;
        # permissioned direct writes land with the drag/place flow in a later wave.
        read_only_fields = ["placed_content_type", "placed_object_id", "placed_label"]

    def update(self, instance, validated_data):
        """Persist a geometry-only change without re-running full object validation.

        A drag/calibrate PATCH touches only position fields. Routing it through ``full_clean`` would
        re-validate unrelated object assignments against their current (possibly since-changed) state
        and reject a pure reposition, so geometry-only writes save the affected fields directly.
        """
        if validated_data and set(validated_data).issubset(TILE_GEOMETRY_FIELDS):
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save(update_fields=list(validated_data))
            return instance
        return super().update(instance, validated_data)


class ConvertToFreeformResultSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Result payload for the convert_to_freeform action."""

    placement_mode = serializers.CharField()
    tiles_seeded = serializers.IntegerField()
    tiles_skipped = serializers.IntegerField()
    tiles_total = serializers.IntegerField()


class FloorPlanTilePlacementSerializer(serializers.Serializer):
    """Input-only serializer that places any registered object type at a normalized position.

    Deliberately a plain Serializer (not a ModelSerializer) so the generic API test harness never
    round-trips it and so the placement fields stay off the main tile serializer's read-only surface.
    """

    floor_plan = serializers.PrimaryKeyRelatedField(queryset=models.FloorPlan.objects.all())
    placed_content_type = serializers.PrimaryKeyRelatedField(queryset=ContentType.objects.all())
    placed_object_id = serializers.UUIDField()
    pos_x = serializers.FloatField(min_value=0, max_value=1, validators=[validate_finite])
    pos_y = serializers.FloatField(min_value=0, max_value=1, validators=[validate_finite])
    width = serializers.FloatField(required=False, allow_null=True, min_value=0, validators=[validate_finite])
    height = serializers.FloatField(required=False, allow_null=True, min_value=0, validators=[validate_finite])
    rotation = serializers.FloatField(required=False, default=0, validators=[validate_finite])
    status = serializers.PrimaryKeyRelatedField(queryset=Status.objects.all(), required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        """Scope the content type choices to registered placeable types."""
        super().__init__(*args, **kwargs)
        self.fields["placed_content_type"].queryset = registry.allowed_content_types()

    def validate(self, attrs):
        """Enforce permissions, registration, resolvable/matching location, and single placement."""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        plan = attrs["floor_plan"]
        if user is not None and not models.FloorPlan.objects.restrict(user, "change").filter(pk=plan.pk).exists():
            raise serializers.ValidationError({"floor_plan": "You do not have permission to change this floor plan."})

        model_cls = attrs["placed_content_type"].model_class()
        if model_cls is None:
            raise serializers.ValidationError({"placed_content_type": "Unknown content type."})
        queryset = model_cls.objects.all()
        if user is not None and hasattr(queryset, "restrict"):
            queryset = queryset.restrict(user, "view")
        obj = queryset.filter(pk=attrs["placed_object_id"]).first()
        if obj is None:
            raise serializers.ValidationError(
                {"placed_object_id": "Object does not exist or you do not have permission to view it."}
            )
        if registry.resolve(obj) is None:
            raise serializers.ValidationError(
                {"placed_content_type": "This object type is not registered as placeable."}
            )
        location = registry.resolve_location(obj)
        if location is None:
            raise serializers.ValidationError({"placed_object_id": f"{obj} has no resolvable Location."})
        if location != plan.location:
            raise serializers.ValidationError({"placed_object_id": f"{obj} must belong to Location {plan.location}."})
        if models.FloorPlanTile.objects.for_object(obj).exists():
            raise serializers.ValidationError({"placed_object_id": f"{obj} is already placed on a floor plan."})
        attrs["_object"] = obj
        return attrs

    def create(self, validated_data):
        """Create a pure-freeform tile placing the object (null grid origins)."""
        tile = models.FloorPlanTile(
            floor_plan=validated_data["floor_plan"],
            placed_content_type=validated_data["placed_content_type"],
            placed_object_id=validated_data["placed_object_id"],
            pos_x=validated_data["pos_x"],
            pos_y=validated_data["pos_y"],
            width=validated_data.get("width"),
            height=validated_data.get("height"),
            rotation=validated_data.get("rotation") or 0,
            status=validated_data.get("status") or _default_tile_status(),
        )
        try:
            tile.validated_save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", None) or exc.messages)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"placed_object_id": "This object was just placed by another request."}
            ) from exc
        return tile


class PlaceableTypeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Read-only schema for a registered placeable type (drives the object picker)."""

    key = serializers.CharField()
    content_type = serializers.CharField()
    label = serializers.CharField()
    icon = serializers.CharField(allow_null=True)
    color = serializers.CharField(allow_null=True)
    legend_order = serializers.IntegerField()
    object_source = serializers.DictField()
