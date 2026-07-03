"""API views for nautobot_floor_plan."""

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.clickjacking import xframe_options_sameorigin
from drf_spectacular.utils import extend_schema
from nautobot.apps.api import NautobotModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED

from nautobot_floor_plan import filters, models
from nautobot_floor_plan.api import serializers
from nautobot_floor_plan.choices import PlacementModeChoices
from nautobot_floor_plan.placement import registry


def _object_source_for(placement, location):
    """Describe where the picker fetches eligible objects of a type, scoped to a location."""
    return {
        "content_type": placement.key,
        "params": {placement.location_field: str(location.pk)},
    }


class FloorPlanViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """FloorPlan viewset."""

    queryset = models.FloorPlan.objects.all()
    serializer_class = serializers.FloorPlanSerializer
    filterset_class = filters.FloorPlanFilterSet

    @extend_schema(exclude=True)
    @action(detail=True)
    @xframe_options_sameorigin
    def svg(self, request, *, pk):
        """SVG representation of a FloorPlan."""
        # Restrict to objects the caller may view (enforces object-level permissions, not just model).
        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "view"), pk=pk)
        drawing = floor_plan.get_svg(user=request.user, base_url=request.build_absolute_uri("/"), request=request)
        return HttpResponse(drawing.tostring(), content_type="image/svg+xml; charset=utf-8")

    @extend_schema(request=None, responses={200: serializers.ConvertToFreeformResultSerializer})
    @action(detail=True, methods=["post"], url_path="convert-to-freeform")
    def convert_to_freeform(self, request, *, pk):
        """Seed freeform coordinates for this plan's grid tiles and (optionally) switch it to freeform.

        Idempotent: pass ``force=true`` to re-seed already-positioned tiles. ``set_mode=false`` seeds
        without changing the placement mode.
        """
        # Object-level "change" restriction; a caller constrained to other objects gets a 404 here.
        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "change"), pk=pk)
        force = str(request.data.get("force", "")).lower() in ("true", "1", "yes", "on") or request.data.get(
            "force"
        ) is True
        set_mode = request.data.get("set_mode", True)
        grid_tiles = floor_plan.tiles.filter(x_origin__isnull=False).count()
        with transaction.atomic():
            modified = floor_plan.convert_to_freeform(force=force, save=True)
            if set_mode and floor_plan.placement_mode != PlacementModeChoices.FREEFORM:
                floor_plan.placement_mode = PlacementModeChoices.FREEFORM
                floor_plan.validated_save()
        return Response(
            {
                "placement_mode": floor_plan.placement_mode,
                "tiles_seeded": len(modified),
                "tiles_skipped": grid_tiles - len(modified),
                "tiles_total": floor_plan.tiles.count(),
            }
        )

    @extend_schema(responses={200: serializers.PlaceableTypeSerializer(many=True)})
    @action(detail=True, url_path="placeable-types")
    def placeable_types(self, request, *, pk):
        """List the object types that can be placed on this plan, scoped to its location."""
        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "view"), pk=pk)
        rows = [
            {
                "key": placement.key,
                "content_type": placement.key,
                "label": placement.label,
                "icon": placement.icon,
                "color": placement.color,
                "legend_order": placement.legend_order,
                "object_source": _object_source_for(placement, floor_plan.location),
            }
            for placement in registry.base_types()
        ]
        rows.sort(key=lambda row: (row["legend_order"], row["label"]))
        return Response(
            {"floor_plan": floor_plan.pk, "location": floor_plan.location.pk, "placeable_types": rows}
        )


class FloorPlanTileViewSet(NautobotModelViewSet):
    """FloorPlanTile viewset."""

    queryset = models.FloorPlanTile.objects.all()
    serializer_class = serializers.FloorPlanTileSerializer
    filterset_class = filters.FloorPlanTileFilterSet

    @extend_schema(
        request=serializers.FloorPlanTilePlacementSerializer,
        responses={201: serializers.FloorPlanTileSerializer},
    )
    @action(detail=False, methods=["post"])
    def place(self, request):
        """Place any registered object type on a floor plan at a normalized position."""
        serializer = serializers.FloorPlanTilePlacementSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        tile = serializer.save()
        return Response(
            serializers.FloorPlanTileSerializer(tile, context={"request": request}).data,
            status=HTTP_201_CREATED,
        )
