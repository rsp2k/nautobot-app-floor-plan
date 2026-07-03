"""API serializers for nautobot_floor_plan."""

import math

from nautobot.apps.api import NautobotModelSerializer, TaggedModelSerializerMixin
from rest_framework import serializers

from nautobot_floor_plan import models

# Geometry-only fields that the drag/calibrate client may PATCH in isolation.
TILE_GEOMETRY_FIELDS = {"pos_x", "pos_y", "width", "height", "rotation"}


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
        # Generic placement is mirrored from the typed FKs during the transition; direct writes to
        # the pair land with the permission-checked object picker in a later wave.
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
