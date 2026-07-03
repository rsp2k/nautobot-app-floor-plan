"""Unit tests for nautobot_floor_plan."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from nautobot.dcim.models import Rack
from nautobot.users.models import Token
from rest_framework import status
from rest_framework.test import APIClient

from nautobot_floor_plan import models
from nautobot_floor_plan.choices import PlacementModeChoices
from nautobot_floor_plan.tests import fixtures

User = get_user_model()


class PlaceholderAPITest(TestCase):
    """Test the FloorPlan API."""

    def setUp(self):
        """Create a superuser and token for API calls."""
        self.user = User.objects.create(username="testuser", is_superuser=True)
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_placeholder(self):
        """Verify that devices can be listed."""
        url = reverse("dcim-api:device-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)


class FreeformAPITest(TestCase):
    """Test freeform placement persistence and the convert_to_freeform action."""

    def setUp(self):
        """Create a superuser client, a grid plan, and one object tile."""
        self.user = User.objects.create(username="ffapi", is_superuser=True)
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

        data = fixtures.create_prerequisites(floor_count=2)
        self.status = data["status"]
        self.floor = data["floors"][0]
        self.plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )
        self.rack = Rack.objects.create(name="RackAPI", status=self.status, location=self.floor)
        self.tile = models.FloorPlanTile(
            floor_plan=self.plan, x_origin=1, y_origin=1, status=self.status, rack=self.rack
        )
        self.tile.validated_save()

    def _tile_url(self):
        return reverse("plugins-api:nautobot_floor_plan-api:floorplantile-detail", kwargs={"pk": self.tile.pk})

    def test_patch_tile_position_persists(self):
        """A geometry-only PATCH updates position and rotation."""
        response = self.client.patch(self._tile_url(), {"pos_x": 0.4, "pos_y": 0.6, "rotation": 30}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.tile.refresh_from_db()
        self.assertAlmostEqual(self.tile.pos_x, 0.4)
        self.assertAlmostEqual(self.tile.pos_y, 0.6)
        self.assertEqual(self.tile.rotation, 30)

    def test_patch_tile_position_out_of_range_returns_400(self):
        """An out-of-range position is a field-keyed 400."""
        response = self.client.patch(self._tile_url(), {"pos_x": 1.5, "pos_y": 0.5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("pos_x", response.json())

    def test_patch_floorplan_calibration_persists(self):
        """Blueprint calibration fields are writable on the floor plan."""
        url = reverse("plugins-api:nautobot_floor_plan-api:floorplan-detail", kwargs={"pk": self.plan.pk})
        response = self.client.patch(
            url, {"bg_x": 0.1, "bg_y": 0.2, "bg_width": 0.8, "bg_height": 0.7, "bg_rotation": 15}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.plan.refresh_from_db()
        self.assertAlmostEqual(self.plan.bg_x, 0.1)
        self.assertAlmostEqual(self.plan.bg_rotation, 15)

    def test_convert_to_freeform_action(self):
        """The action seeds tiles, switches the mode, and is idempotent."""
        url = reverse(
            "plugins-api:nautobot_floor_plan-api:floorplan-convert-to-freeform", kwargs={"pk": self.plan.pk}
        )
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        body = response.json()
        self.assertEqual(body["placement_mode"], PlacementModeChoices.FREEFORM)
        self.assertEqual(body["tiles_seeded"], 1)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.placement_mode, PlacementModeChoices.FREEFORM)
        self.tile.refresh_from_db()
        self.assertIsNotNone(self.tile.pos_x)
        self.assertEqual((self.tile.x_origin, self.tile.y_origin), (1, 1))  # origins preserved

        # A second call seeds nothing.
        second = self.client.post(url, {}, format="json")
        self.assertEqual(second.json()["tiles_seeded"], 0)
