"""API views for nautobot_floor_plan."""

import io

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.clickjacking import xframe_options_sameorigin
from drf_spectacular.utils import extend_schema
from nautobot.apps.api import NautobotModelViewSet
from PIL import Image
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.status import HTTP_201_CREATED

from nautobot_floor_plan import filters, models
from nautobot_floor_plan.api import serializers
from nautobot_floor_plan.choices import PlacementModeChoices
from nautobot_floor_plan.placement import registry

# Upload guardrail for the untrusted-parser surface; page/pixel caps live in the render Job.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


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
        from nautobot_floor_plan.placement.config import refresh_if_stale  # noqa: PLC0415

        refresh_if_stale()
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

    @extend_schema(request=None, responses={202: serializers.BlueprintImportResultSerializer})
    @action(
        detail=True,
        methods=["post"],
        url_path="import-pdf",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_pdf(self, request, *, pk):
        """Store an uploaded PDF as the plan's source document and enqueue the page-render Job.

        The heavy rasterizing runs in the Job (worker), not this request. The client then polls
        ``pages/`` until page thumbnails appear (or watches the returned JobResult for errors).
        """
        # Local import: jobs.py imports models, so keep it off the module import path.
        from nautobot.extras.models import Job as JobModel  # noqa: PLC0415
        from nautobot.extras.models import JobResult

        from nautobot_floor_plan.jobs import RenderBlueprintPages  # noqa: PLC0415

        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "change"), pk=pk)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": "No file was uploaded."}, status=http_status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_UPLOAD_BYTES:
            return Response(
                {"file": f"File is {upload.size} bytes; the limit is {MAX_UPLOAD_BYTES}."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        name = (upload.name or "").lower()
        if not (name.endswith(".pdf") or upload.content_type == "application/pdf"):
            return Response({"file": "Only PDF uploads are supported."}, status=http_status.HTTP_400_BAD_REQUEST)

        # Persist the source and clear any prior pages so the picker starts empty while rendering.
        floor_plan.source_document.save(upload.name, upload, save=False)
        floor_plan.save(update_fields=["source_document"])
        models.BlueprintPage.objects.filter(floor_plan=floor_plan).delete()

        try:
            job_model = RenderBlueprintPages().job_model
        except JobModel.DoesNotExist:
            return Response(
                {"detail": "The blueprint render Job is not registered yet (run post_upgrade)."},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Nautobot registers new jobs disabled by default; this app-owned render job is core to the
        # feature and only ever runs when a user uploads a PDF here, so enable it on first use.
        if not job_model.enabled:
            job_model.enabled = True
            job_model.save()
        job_result = JobResult.enqueue_job(job_model, request.user, floor_plan=str(floor_plan.pk))
        return Response({"job_result": str(job_result.pk)}, status=http_status.HTTP_202_ACCEPTED)

    @extend_schema(responses={200: serializers.BlueprintPageSerializer(many=True)})
    @action(detail=True)
    def pages(self, request, *, pk):
        """List the rendered blueprint pages for this plan (drives the page-picker grid)."""
        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "view"), pk=pk)
        rows = models.BlueprintPage.objects.filter(floor_plan=floor_plan).order_by("page_number")
        data = serializers.BlueprintPageSerializer(rows, many=True, context={"request": request}).data
        return Response({"floor_plan": floor_plan.pk, "pages": data})

    @extend_schema(request=serializers.BlueprintExtractSerializer, responses={200: serializers.FloorPlanSerializer})
    @action(detail=True, methods=["post"])
    def extract(self, request, *, pk):
        """Crop the chosen page to the drawing region, then orient it, as this plan's background_image.

        Crop-then-rotate: ``crop_box`` is normalized to the original (un-rotated) page image, and
        ``rotation`` orients the resulting crop. That keeps the client's crop box in page coordinates
        regardless of the chosen orientation.
        """
        floor_plan = get_object_or_404(self.queryset.restrict(request.user, "change"), pk=pk)
        serializer = serializers.BlueprintExtractSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page_number = serializer.validated_data["page_number"]
        crop_box = serializer.validated_data["crop_box"]
        rotation = serializer.validated_data["rotation"]

        page = get_object_or_404(models.BlueprintPage, floor_plan=floor_plan, page_number=page_number)
        with page.image.open("rb") as handle:
            image = Image.open(handle)
            image.load()
        image = image.convert("RGB")

        width, height = image.size
        x, y, w, h = crop_box
        box = (round(x * width), round(y * height), round((x + w) * width), round((y + h) * height))
        cropped = image.crop(box)
        if rotation:
            # PIL rotates counter-clockwise for positive angles; negate so positive = clockwise.
            cropped = cropped.rotate(-rotation, expand=True)

        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG", optimize=True)
        floor_plan.background_image.save(
            f"fp_{floor_plan.pk}_blueprint.png", ContentFile(buffer.getvalue()), save=False
        )
        # New crop = new aspect ratio, so drop the old calibration and let the SVG auto-fit it.
        floor_plan.bg_x = floor_plan.bg_y = floor_plan.bg_width = floor_plan.bg_height = None
        floor_plan.bg_rotation = 0
        floor_plan.save(
            update_fields=[
                "background_image",
                "background_image_width",
                "background_image_height",
                "bg_x",
                "bg_y",
                "bg_width",
                "bg_height",
                "bg_rotation",
            ]
        )
        return Response(serializers.FloorPlanSerializer(floor_plan, context={"request": request}).data)


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
        from nautobot_floor_plan.placement.config import refresh_if_stale  # noqa: PLC0415

        refresh_if_stale()
        serializer = serializers.FloorPlanTilePlacementSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        tile = serializer.save()
        return Response(
            serializers.FloorPlanTileSerializer(tile, context={"request": request}).data,
            status=HTTP_201_CREATED,
        )


class FloorPlanObjectTypeViewSet(NautobotModelViewSet):
    """FloorPlanObjectType viewset."""

    queryset = models.FloorPlanObjectType.objects.all()
    serializer_class = serializers.FloorPlanObjectTypeSerializer
    filterset_class = filters.FloorPlanObjectTypeFilterSet
