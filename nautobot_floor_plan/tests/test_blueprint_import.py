"""Tests for the blueprint PDF import pipeline (model, render Job, and API actions)."""

from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.urls import reverse
from nautobot.core.testing import TestCase, TransactionTestCase, run_job_for_testing
from nautobot.extras.models import Job as JobModel
from nautobot.users.models import Token, User
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from nautobot_floor_plan import models
from nautobot_floor_plan.tests import fixtures

JOB_CLASS_PATH = "nautobot_floor_plan.jobs.RenderBlueprintPages"


def _pdf_bytes(pages=2, size=(300, 400)):
    """A minimal multi-page PDF synthesized from blank pages (no real-world data in tests)."""
    images = [Image.new("RGB", size, (255, 255, 255)) for _ in range(pages)]
    buffer = BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def _png_upload(name="page.png", size=(120, 200), color=(200, 200, 200)):
    """An in-memory PNG upload with known pixel dimensions."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class BlueprintPageModelTest(TestCase):
    """The BlueprintPage model and its per-plan page uniqueness."""

    def setUp(self):
        data = fixtures.create_prerequisites(floor_count=1)
        self.floor_plan = models.FloorPlan.objects.create(
            location=data["floors"][0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )

    def test_create_and_str(self):
        page = models.BlueprintPage.objects.create(
            floor_plan=self.floor_plan, page_number=1, image=_png_upload()
        )
        self.assertIn("page 1", str(page))
        self.assertEqual(page.image_width, 120)
        self.assertEqual(page.image_height, 200)

    def test_unique_page_per_plan(self):
        models.BlueprintPage.objects.create(floor_plan=self.floor_plan, page_number=1, image=_png_upload())
        with self.assertRaises(IntegrityError):
            models.BlueprintPage.objects.create(floor_plan=self.floor_plan, page_number=1, image=_png_upload())


class RenderBlueprintPagesJobTest(TransactionTestCase):
    """The render Job rasterizes each source PDF page into a BlueprintPage row.

    Uses TransactionTestCase (Nautobot's base for job runs) so the JobResult / JobLogEntry writes
    across the job-logs database aren't wrapped in an atomic block that breaks teardown.
    """

    def setUp(self):
        super().setUp()
        data = fixtures.create_prerequisites(floor_count=1)
        self.floor_plan = models.FloorPlan.objects.create(
            location=data["floors"][0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )
        self.user = User.objects.create(username="jobrunner", is_superuser=True)
        self.job = JobModel.objects.get_for_class_path(JOB_CLASS_PATH)
        self.job.enabled = True
        self.job.validated_save()

    def test_renders_all_pages(self):
        self.floor_plan.source_document.save("plan.pdf", ContentFile(_pdf_bytes(pages=3)), save=True)
        run_job_for_testing(self.job, username=self.user.username, floor_plan=str(self.floor_plan.pk))
        rows = models.BlueprintPage.objects.filter(floor_plan=self.floor_plan).order_by("page_number")
        self.assertEqual([r.page_number for r in rows], [1, 2, 3])
        self.assertTrue(all(r.image_width and r.image_height and r.thumbnail for r in rows))

    def test_rerender_replaces_previous_pages(self):
        self.floor_plan.source_document.save("a.pdf", ContentFile(_pdf_bytes(pages=3)), save=True)
        run_job_for_testing(self.job, username=self.user.username, floor_plan=str(self.floor_plan.pk))
        self.floor_plan.source_document.save("b.pdf", ContentFile(_pdf_bytes(pages=1)), save=True)
        run_job_for_testing(self.job, username=self.user.username, floor_plan=str(self.floor_plan.pk))
        self.assertEqual(models.BlueprintPage.objects.filter(floor_plan=self.floor_plan).count(), 1)


class BlueprintImportAPITest(TestCase):
    """The import-pdf / pages / extract viewset actions."""

    def setUp(self):
        data = fixtures.create_prerequisites(floor_count=1)
        self.floor_plan = models.FloorPlan.objects.create(
            location=data["floors"][0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )
        self.user = User.objects.create(username="importer", is_superuser=True)
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def _url(self, action):
        return reverse(f"plugins-api:nautobot_floor_plan-api:floorplan-{action}", kwargs={"pk": self.floor_plan.pk})

    def test_import_pdf_saves_source_and_enqueues(self):
        upload = SimpleUploadedFile("plan.pdf", _pdf_bytes(pages=2), content_type="application/pdf")
        response = self.client.post(self._url("import-pdf"), {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn("job_result", response.json())
        self.floor_plan.refresh_from_db()
        self.assertTrue(self.floor_plan.source_document.name.endswith(".pdf"))
        # The render job is enabled on first use so the worker can pick it up.
        self.assertTrue(JobModel.objects.get_for_class_path(JOB_CLASS_PATH).enabled)

    def test_import_pdf_rejects_non_pdf(self):
        upload = SimpleUploadedFile("nope.txt", b"not a pdf", content_type="text/plain")
        response = self.client.post(self._url("import-pdf"), {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pages_lists_rendered_pages(self):
        models.BlueprintPage.objects.create(floor_plan=self.floor_plan, page_number=1, image=_png_upload())
        models.BlueprintPage.objects.create(floor_plan=self.floor_plan, page_number=2, image=_png_upload())
        response = self.client.get(self._url("pages"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pages = response.json()["pages"]
        self.assertEqual([p["page_number"] for p in pages], [1, 2])
        self.assertTrue(pages[0]["image_url"])

    def test_extract_crops_into_background(self):
        # A 120x200 page; crop the top-left quarter -> expect a 60x100 background.
        models.BlueprintPage.objects.create(
            floor_plan=self.floor_plan, page_number=1, image=_png_upload(size=(120, 200))
        )
        payload = {"page_number": 1, "crop_box": [0.0, 0.0, 0.5, 0.5], "rotation": 0}
        response = self.client.post(self._url("extract"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.floor_plan.refresh_from_db()
        self.assertTrue(self.floor_plan.background_image.name)
        self.assertEqual(self.floor_plan.background_image_width, 60)
        self.assertEqual(self.floor_plan.background_image_height, 100)
        # New crop drops stale calibration so the SVG auto-fits.
        self.assertIsNone(self.floor_plan.bg_x)

    def test_extract_rotation_swaps_dimensions(self):
        models.BlueprintPage.objects.create(
            floor_plan=self.floor_plan, page_number=1, image=_png_upload(size=(120, 200))
        )
        # Crop the whole page (120x200), rotate 90 -> result is 200x120.
        payload = {"page_number": 1, "crop_box": [0.0, 0.0, 1.0, 1.0], "rotation": 90}
        response = self.client.post(self._url("extract"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.floor_plan.refresh_from_db()
        self.assertEqual(self.floor_plan.background_image_width, 200)
        self.assertEqual(self.floor_plan.background_image_height, 120)
