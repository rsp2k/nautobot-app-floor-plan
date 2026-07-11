"""Tests for marker icon scaling: model fields, serializer fast-paths, and decoupled SVG sizing."""

import re

from django.core.exceptions import ValidationError
from nautobot.core.testing import TestCase
from nautobot.dcim.models import Rack
from nautobot.users.models import User

from nautobot_floor_plan import models
from nautobot_floor_plan.api.serializers import CALIBRATION_FIELDS, TILE_GEOMETRY_FIELDS
from nautobot_floor_plan.choices import PlacementModeChoices
from nautobot_floor_plan.tests import fixtures


class IconScaleModelTestCase(TestCase):
    """The new icon_scale fields: defaults and validation."""

    def setUp(self):
        prereq = fixtures.create_prerequisites(floor_count=1)
        self.status = prereq["status"]
        self.floor = prereq["floors"][0]
        self.plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=4, y_size=4, placement_mode=PlacementModeChoices.FREEFORM
        )
        self.rack = Rack.objects.create(name="R1", status=self.status, location=self.floor)

    def test_defaults(self):
        self.assertEqual(self.plan.icon_scale, 1.0)
        tile = models.FloorPlanTile(
            floor_plan=self.plan, status=self.status, rack=self.rack, x_origin=1, y_origin=1, pos_x=0.5, pos_y=0.5
        )
        tile.validated_save()
        self.assertEqual(tile.icon_scale, 1.0)

    def test_plan_scale_below_min_rejected(self):
        self.plan.icon_scale = 0.05
        with self.assertRaises(ValidationError):
            self.plan.full_clean()

    def test_tile_scale_below_min_rejected(self):
        tile = models.FloorPlanTile(
            floor_plan=self.plan, status=self.status, rack=self.rack, x_origin=1, y_origin=1,
            pos_x=0.5, pos_y=0.5, icon_scale=0.0,
        )
        with self.assertRaises(ValidationError):
            tile.full_clean()


class IconScaleFastPathTestCase(TestCase):
    """icon_scale rides the drag/calibrate fast-path allow-lists (bypasses full plan/tile validation)."""

    def test_allow_lists_include_icon_scale(self):
        self.assertIn("icon_scale", CALIBRATION_FIELDS)
        self.assertIn("icon_scale", TILE_GEOMETRY_FIELDS)


class IconScaleRenderTestCase(TestCase):
    """Marker size is decoupled from footprint: base * plan.icon_scale * tile.icon_scale, uniform per scale."""

    def setUp(self):
        prereq = fixtures.create_prerequisites(floor_count=1)
        self.status = prereq["status"]
        self.floor = prereq["floors"][0]
        self.user = User.objects.create(username="scaleuser", is_superuser=True)
        self.plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=6, y_size=6, placement_mode=PlacementModeChoices.FREEFORM
        )
        # Two racks placed with deliberately DIFFERENT footprints (width/height) but default icon_scale.
        self.rack_a = Rack.objects.create(name="RA", status=self.status, location=self.floor)
        self.rack_b = Rack.objects.create(name="RB", status=self.status, location=self.floor)
        models.FloorPlanTile(
            floor_plan=self.plan, status=self.status, rack=self.rack_a, x_origin=1, y_origin=1,
            pos_x=0.25, pos_y=0.25, width=0.05, height=0.05,
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.plan, status=self.status, rack=self.rack_b, x_origin=2, y_origin=1,
            pos_x=0.75, pos_y=0.25, width=0.30, height=0.30,
        ).validated_save()

    def _marker_sizes(self):
        svg = self.plan.get_svg(user=self.user, base_url="http://testserver").tostring()
        return [float(v) for v in re.findall(r'data-marker-size="([\d.]+)"', svg)]

    def test_uniform_regardless_of_footprint(self):
        # Different width/height, same scale -> identical marker sizes (the "matching base sizes" goal).
        sizes = self._marker_sizes()
        self.assertEqual(len(sizes), 2)
        self.assertEqual(sizes[0], sizes[1])

    def test_global_scale_multiplies_all(self):
        base = self._marker_sizes()[0]
        self.plan.icon_scale = 2.0
        self.plan.save(update_fields=["icon_scale"])
        scaled = self._marker_sizes()
        self.assertTrue(all(abs(s - base * 2.0) < 0.01 for s in scaled))

    def test_per_tile_scale_enlarges_one(self):
        tile_a = models.FloorPlanTile.objects.get(floor_plan=self.plan, rack=self.rack_a)
        tile_a.icon_scale = 1.5
        tile_a.save(update_fields=["icon_scale"])
        sizes = sorted(self._marker_sizes())
        # One marker (the 1.5x tile) is now larger than the other.
        self.assertLess(sizes[0], sizes[1])
        self.assertAlmostEqual(sizes[1] / sizes[0], 1.5, places=2)
