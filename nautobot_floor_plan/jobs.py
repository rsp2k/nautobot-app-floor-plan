"""Nautobot Jobs for the Floor Plan app.

Jobs are the app's async unit: they run on whatever queue the platform provides (Celery on stock
Nautobot, the Procrastinate fork in this deployment) without the app importing that queue. This one
rasterizes a Floor Plan's uploaded ``source_document`` (a PDF) into ``BlueprintPage`` rows so the
user can pick a page and crop it into the plan's ``background_image``.
"""

import io

import pypdfium2 as pdfium
from django.core.files.base import ContentFile
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from PIL import Image

from nautobot_floor_plan.models import BlueprintPage, FloorPlan

# Caps for the untrusted-parser surface. A malformed or hostile PDF should never render forever or
# exhaust memory, so bound page count, per-page pixels, and resolution before rasterizing.
MAX_PAGES = 50
RENDER_DPI = 200
MAX_PIXELS = 40_000_000  # ~40 MP per page ceiling; scale is reduced to fit if a page exceeds it
THUMBNAIL_MAX_PX = 320  # longest edge of the picker thumbnail

name = "Floor Plan"  # Jobs UI grouping


class RenderBlueprintPages(Job):
    """Render each page of a Floor Plan's source PDF into BlueprintPage images."""

    floor_plan = ObjectVar(
        model=FloorPlan,
        description="Floor plan whose uploaded source document should be rendered to page images.",
    )

    class Meta:
        """Job metadata."""

        name = "Render Blueprint PDF Pages"
        description = "Rasterize a floor plan's uploaded PDF into per-page blueprint images for the page picker."
        has_sensitive_variables = False
        soft_time_limit = 600
        time_limit = 660

    def run(self, floor_plan):  # pylint: disable=arguments-differ
        """Render every page of ``floor_plan.source_document`` into BlueprintPage rows."""
        document = floor_plan.source_document
        if not document:
            raise ValueError("Floor plan has no source_document to render.")

        with document.open("rb") as handle:
            data = handle.read()
        self.logger.info(f"Rendering `{document.name}` ({len(data)} bytes).", extra={"object": floor_plan})

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-except
            raise ValueError(f"Could not open source document as a PDF: {exc}") from exc

        try:
            page_count = len(pdf)
            if page_count > MAX_PAGES:
                raise ValueError(f"Document has {page_count} pages; the limit is {MAX_PAGES}.")

            deleted, _ = BlueprintPage.objects.filter(floor_plan=floor_plan).delete()
            if deleted:
                self.logger.info(f"Cleared {deleted} previously rendered page(s).", extra={"object": floor_plan})

            base_scale = RENDER_DPI / 72.0
            created = 0
            for index in range(page_count):
                page = pdf[index]
                width_pt, height_pt = page.get_size()
                # Reduce scale for this page if it would exceed the pixel ceiling.
                projected = int(width_pt * base_scale) * int(height_pt * base_scale)
                scale = base_scale if projected <= MAX_PIXELS else base_scale * (MAX_PIXELS / projected) ** 0.5

                pil_image = page.render(scale=scale).to_pil().convert("RGB")
                page_number = index + 1

                full_bytes = _png_bytes(pil_image)
                thumb = pil_image.copy()
                thumb.thumbnail((THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX), Image.LANCZOS)
                thumb_bytes = _png_bytes(thumb)

                blueprint_page = BlueprintPage(floor_plan=floor_plan, page_number=page_number)
                stem = f"fp_{floor_plan.pk}_p{page_number}"
                blueprint_page.image.save(f"{stem}.png", ContentFile(full_bytes), save=False)
                blueprint_page.thumbnail.save(f"{stem}_thumb.png", ContentFile(thumb_bytes), save=False)
                blueprint_page.save()
                created += 1
                self.logger.info(
                    f"Rendered page {page_number}/{page_count} ({pil_image.width}x{pil_image.height}).",
                    extra={"object": floor_plan},
                )
        finally:
            pdf.close()

        self.logger.success(f"Rendered {created} page(s) from `{document.name}`.", extra={"object": floor_plan})
        return {"floor_plan": str(floor_plan.pk), "pages_rendered": created}


def _png_bytes(image):
    """Encode a PIL image to optimized PNG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


register_jobs(RenderBlueprintPages)
