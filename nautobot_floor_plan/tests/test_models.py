"""Test FloorPlan."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from nautobot.core.testing import TestCase
from nautobot.dcim.models import Device, Location, PowerFeed, PowerPanel, Rack, RackGroup
from nautobot.extras.models import Status
from nautobot.tenancy.models import Tenant

from nautobot_floor_plan import models
from nautobot_floor_plan.choices import ObjectOrientationChoices, PlacementModeChoices
from nautobot_floor_plan.placement import registry
from nautobot_floor_plan.tests import fixtures


class TestFloorPlan(TestCase):
    """Test FloorPlan model."""

    def setUp(self):
        """Create LocationType, Status, Location, and FloorPlan records."""
        prerequisites = fixtures.create_prerequisites()

        # Keep the most frequently used attributes as instance variables
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]
        self.floor_plans = fixtures.create_floor_plans(self.floors)
        self.rack_group = RackGroup.objects.create(name="RackGroup 1", location=self.floors[2])
        self.rack = Rack.objects.create(
            name="Rack 1", status=self.status, rack_group=self.rack_group, location=self.floors[2]
        )

        # Store less frequently used attributes in a dictionary
        self._test_data = {
            "location": prerequisites["location"],
            "device_type": prerequisites["device_type"],
            "device_role": prerequisites["device_role"],
            "valid_rack_group": RackGroup.objects.create(name="RackGroup 2", location=self.floors[3]),
        }

    def test_create_floor_plan_valid(self):
        """Successfully create various FloorPlan records."""
        # Create new locations for these tests to avoid conflicts
        new_floors = []
        for i in range(3):
            new_floors.append(
                Location.objects.create(
                    name=f"Test Floor {i}",
                    location_type=self.floors[0].location_type,
                    status=self.status,
                    parent=self.floors[0].parent,
                )
            )

        floor_plan_minimal = models.FloorPlan(location=new_floors[0], x_size=1, y_size=1)
        floor_plan_minimal.validated_save()
        floor_plan_huge = models.FloorPlan(location=new_floors[1], x_size=100, y_size=100)
        floor_plan_huge.validated_save()
        floor_plan_pos_neg_step = models.FloorPlan(
            location=new_floors[2], x_size=20, y_size=20, x_axis_step=-1, y_axis_step=2
        )
        floor_plan_pos_neg_step.validated_save()

    def test_create_floor_plan_invalid_no_location(self):
        """Can't create a FloorPlan with no Location."""
        with self.assertRaises(ValidationError):
            models.FloorPlan(x_size=1, y_size=1).validated_save()

    def test_create_floor_plan_invalid_x_size(self):
        """A FloorPlan must have an x_size greater than zero."""
        with self.assertRaises(ValidationError):
            models.FloorPlan(location=self.floors[0], x_size=0, y_size=1).validated_save()

    def test_create_floor_plan_invalid_y_size(self):
        """A FloorPlan must have a y_size greater than zero."""
        with self.assertRaises(ValidationError):
            models.FloorPlan(location=self.floors[0], x_size=1, y_size=0).validated_save()

    def test_create_floor_plan_invalid_duplicate_location(self):
        """Only one FloorPlan per Location can be created."""
        # First floor plan is already created in setUp
        with self.assertRaises(ValidationError) as context:
            models.FloorPlan(location=self.floors[0], x_size=1, y_size=1).validated_save()

        self.assertIn("location", context.exception.message_dict)
        self.assertIn("Floor plan with this Location already exists.", context.exception.message_dict["location"])

    def test_origin_seed_x_increase(self):
        """Test that existing tile origins are updated during origin_seed updates"""
        # Create a new location for this test
        new_floor = Location.objects.create(
            name="Test Floor X Increase",
            location_type=self.floors[0].location_type,
            status=self.status,
            parent=self.floors[0].parent,
        )
        floor_plan = models.FloorPlan.objects.create(
            location=new_floor, x_size=3, y_size=3, x_origin_seed=1, y_origin_seed=1
        )

        tile_1_1_1 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=1, y_origin=1, status=self.status)
        tile_2_3_1 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=3, y_origin=1, status=self.status)
        tile_1_1_1.validated_save()
        tile_2_3_1.validated_save()
        tile_1_id = tile_1_1_1.id
        tile_2_id = tile_2_3_1.id

        floor_plan.x_origin_seed = 3
        floor_plan.validated_save()
        self.assertEqual(floor_plan.tiles.get(id=tile_1_id).x_origin, 3)
        self.assertEqual(floor_plan.tiles.get(id=tile_2_id).x_origin, 5)

    def test_origin_seed_y_decrease(self):
        """Test that existing tile origins are updated during origin_seed updates"""
        # Create a new location for this test
        new_floor = Location.objects.create(
            name="Test Floor Y Decrease",
            location_type=self.floors[0].location_type,
            status=self.status,
            parent=self.floors[0].parent,
        )
        floor_plan = models.FloorPlan.objects.create(
            location=new_floor, x_size=3, y_size=3, x_origin_seed=3, y_origin_seed=3
        )
        tile_1_5_5 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=5, y_origin=5, status=self.status)
        tile_1_5_5.validated_save()

        floor_plan.y_origin_seed = 1
        floor_plan.validated_save()
        self.assertEqual(floor_plan.tiles.first().y_origin, 3)

    def test_origin_seed_x_increase_y_decrease(self):
        """Test that existing tile origins are updated during origin_seed updates"""
        # Create a new location for this test
        new_floor = Location.objects.create(
            name="Test Floor XY Change",
            location_type=self.floors[0].location_type,
            status=self.status,
            parent=self.floors[0].parent,
        )
        floor_plan = models.FloorPlan.objects.create(
            location=new_floor, x_size=5, y_size=5, x_origin_seed=3, y_origin_seed=3
        )

        tile_1_3_3 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=3, y_origin=4, status=self.status)
        tile_2_5_3 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=5, y_origin=4, status=self.status)
        tile_3_4_3 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=4, y_origin=4, status=self.status)
        tile_4_4_5 = models.FloorPlanTile(floor_plan=floor_plan, x_origin=4, y_origin=5, status=self.status)
        ids = []
        for tile in (tile_1_3_3, tile_2_5_3, tile_3_4_3, tile_4_4_5):
            tile.validated_save()
            ids.append(tile.id)

        floor_plan.x_origin_seed = 4
        floor_plan.y_origin_seed = 2
        floor_plan.validated_save()
        self.assertEqual(floor_plan.tiles.get(id=ids[0]).x_origin, 4)
        self.assertEqual(floor_plan.tiles.get(id=ids[0]).y_origin, 3)
        self.assertEqual(floor_plan.tiles.get(id=ids[1]).x_origin, 6)
        self.assertEqual(floor_plan.tiles.get(id=ids[1]).y_origin, 3)
        self.assertEqual(floor_plan.tiles.get(id=ids[2]).x_origin, 5)
        self.assertEqual(floor_plan.tiles.get(id=ids[2]).y_origin, 3)
        self.assertEqual(floor_plan.tiles.get(id=ids[3]).x_origin, 5)
        self.assertEqual(floor_plan.tiles.get(id=ids[3]).y_origin, 4)

    def test_create_floor_plan_invalid_step(self):
        """A FloorPlan must not use a step value of zero."""
        with self.assertRaises(ValidationError):
            models.FloorPlan(
                location=self.floors[1], x_size=100, y_size=100, x_axis_step=0, y_axis_step=2
            ).validated_save()

    def test_resize_x_floor_plan_with_tiles(self):
        """Test that a FloorPlan cannot be resized after tiles are placed."""
        # Create a new location for this test
        new_floor = Location.objects.create(
            name="Test Floor X Resize",
            location_type=self.floors[0].location_type,
            status=self.status,
            parent=self.floors[0].parent,
        )
        floor_plan = models.FloorPlan.objects.create(
            location=new_floor, x_size=3, y_size=3, x_origin_seed=1, y_origin_seed=1
        )
        tile = models.FloorPlanTile(floor_plan=floor_plan, x_origin=1, y_origin=1, status=self.status)
        tile.validated_save()

        # Attempt to resize the FloorPlan
        floor_plan.x_size = 5
        with self.assertRaises(ValidationError):
            floor_plan.validated_save()

    def test_resize_y_floor_plan_with_tiles(self):
        """Test that a FloorPlan cannot be resized after tiles are placed."""
        # Create a new location for this test
        new_floor = Location.objects.create(
            name="Test Floor Y Resize",
            location_type=self.floors[0].location_type,
            status=self.status,
            parent=self.floors[0].parent,
        )
        floor_plan = models.FloorPlan.objects.create(
            location=new_floor, x_size=3, y_size=3, x_origin_seed=1, y_origin_seed=1
        )
        tile = models.FloorPlanTile(floor_plan=floor_plan, x_origin=1, y_origin=1, status=self.status)
        tile.validated_save()

        # Attempt to resize the FloorPlan
        floor_plan.y_size = 4
        with self.assertRaises(ValidationError):
            floor_plan.validated_save()


class TestFloorPlanTile(TestCase):
    """Test FloorPlanTile model."""

    def setUp(self):
        """Create LocationType, Status, Location, and FloorPlan records."""
        prerequisites = fixtures.create_prerequisites()

        # Keep only the most essential attributes as instance variables
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]
        self.floor_plans = fixtures.create_floor_plans(self.floors)
        self.rack = None  # Will be set after rack_group is created

        # Store all other test data in the dictionary
        self._test_data = {
            "location": prerequisites["location"],
            "device_type": prerequisites["device_type"],
            "device_role": prerequisites["device_role"],
            "valid_rack_group": RackGroup.objects.create(name="RackGroup 2", location=self.floors[3]),
            "rack_group": RackGroup.objects.create(name="RackGroup 1", location=self.floors[2]),
        }

        # Create rack after rack_group is in _test_data
        self.rack = Rack.objects.create(
            name="Rack 1", status=self.status, rack_group=self._test_data["rack_group"], location=self.floors[2]
        )

    def test_create_floor_plan_single_tiles_valid(self):
        """A FloorPlanTile can be created for each legal position in a FloorPlan."""
        tile_1_1_1 = models.FloorPlanTile(floor_plan=self.floor_plans[0], x_origin=1, y_origin=1, status=self.status)
        tile_1_1_1.validated_save()
        tile_2_1_1 = models.FloorPlanTile(floor_plan=self.floor_plans[1], x_origin=1, y_origin=1, status=self.status)
        tile_2_1_1.validated_save()
        tile_2_2_1 = models.FloorPlanTile(floor_plan=self.floor_plans[1], x_origin=2, y_origin=1, status=self.status)
        tile_2_2_1.validated_save()
        tile_2_1_2 = models.FloorPlanTile(floor_plan=self.floor_plans[1], x_origin=1, y_origin=2, status=self.status)
        tile_2_1_2.validated_save()
        tile_2_2_2 = models.FloorPlanTile(floor_plan=self.floor_plans[1], x_origin=2, y_origin=2, status=self.status)
        tile_2_2_2.validated_save()
        tile_3_2_2 = models.FloorPlanTile(
            floor_plan=self.floor_plans[2], x_origin=2, y_origin=2, status=self.status, rack=self.rack
        )
        tile_3_2_2.validated_save()

    def test_create_floor_plan_spanning_tiles_valid(self):
        """
        FloorPlanTiles can span multiple squares so long as they do not overlap.
        Racks can be installed on RackGroup Tiles if the Rack is in the correct RackGroup
        +-+-+-+-+
        |2|2|2|4|
        +-+-+-+-+
        |3|1|1|4|
        +-+-+-+-+
        |3|1|1|4|
        +-+-+-+-+
        |3|5|5|5|
        +-+-+-+-+
        """
        valid_rack = Rack.objects.create(
            name="Rack 3",
            status=self.status,
            rack_group=self._test_data["valid_rack_group"],
            location=self.floors[3],
        )
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            rack_group=self._test_data["valid_rack_group"],
            x_origin=2,
            y_origin=2,
            x_size=2,
            y_size=2,
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            rack_group=self._test_data["valid_rack_group"],
            rack=valid_rack,
            x_origin=2,
            y_origin=2,
            x_size=2,
            y_size=2,
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3], status=self.status, x_origin=1, y_origin=1, x_size=3, y_size=1
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3], status=self.status, x_origin=1, y_origin=2, x_size=1, y_size=3
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3], status=self.status, x_origin=4, y_origin=1, x_size=1, y_size=3
        ).validated_save()
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            rack_group=self._test_data["valid_rack_group"],
            x_origin=2,
            y_origin=4,
            x_size=3,
            y_size=1,
        ).validated_save()

    def test_create_floor_plan_single_tile_invalid_duplicate_position(self):
        """Two FloorPlanTiles cannot occupy the same position in the same FloorPlan."""
        models.FloorPlanTile(
            floor_plan=self.floor_plans[0], x_origin=1, y_origin=1, status=self.status
        ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0], x_origin=1, y_origin=1, status=self.status
            ).validated_save()

    def test_create_floor_plan_tile_invalid_duplicate_rack(self):
        """Each Rack can only associate to at most one FloorPlanTile."""
        models.FloorPlanTile(
            floor_plan=self.floor_plans[2], x_origin=1, y_origin=1, status=self.status, rack=self.rack
        ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[2], x_origin=2, y_origin=2, status=self.status, rack=self.rack
            ).validated_save()

    def test_create_floor_plan_tile_invalid_rack_rackgroup(self):
        """A Rack being placed on a Rackgroup tile must also be in the rack_group."""
        valid_rack = Rack.objects.create(
            name="Rack 3", status=self.status, rack_group=self._test_data["valid_rack_group"], location=self.floors[3]
        )
        models.FloorPlanTile(
            floor_plan=self.floor_plans[2],
            x_origin=1,
            y_origin=1,
            x_size=1,
            y_size=1,
            status=self.status,
            rack_group=self._test_data["rack_group"],
        ).validated_save()
        # How about a rack without the correct rack group?
        non_rack_group_rack = Rack.objects.create(name="Rack 2", status=self.status, location=self.floors[2])
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[2],
                x_origin=1,
                y_origin=1,
                x_size=1,
                y_size=1,
                status=self.status,
                rack=non_rack_group_rack,
            ).validated_save()
        # How about a tile with with a rack and the incorrect rackgroup
        invalid_rack_group = RackGroup.objects.create(name="RackGroup 2", location=self.floors[2])
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[2],
                x_origin=1,
                y_origin=1,
                x_size=1,
                y_size=1,
                status=self.status,
                rack_group=invalid_rack_group,
                rack=valid_rack,
            ).validated_save()
        # How about a rack extending beyond the bounds of the rackgroup tile
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[2],
                x_origin=1,
                y_origin=1,
                x_size=2,
                y_size=1,
                status=self.status,
                rack_group=self._test_data["valid_rack_group"],
                rack=valid_rack,
            ).validated_save()

    def test_create_floor_plan_tile_invalid_illegal_position(self):
        """A FloorPlanTile cannot be created outside the bounds of its FloorPlan."""
        # x_origin too small
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0], status=self.status, x_origin=0, y_origin=1
            ).validated_save()
        # x_origin too large
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0],
                status=self.status,
                x_origin=self.floor_plans[0].x_size + 1,
                y_origin=1,
            ).validated_save()
        # x_origin + x_size too large
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0],
                status=self.status,
                x_origin=self.floor_plans[0].x_size,
                y_origin=1,
                x_size=2,
            ).validated_save()
        # y_origin too small
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0], status=self.status, x_origin=1, y_origin=0
            ).validated_save()
        # y_origin too large
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0],
                status=self.status,
                x_origin=1,
                y_origin=self.floor_plans[0].y_size + 1,
            ).validated_save()
        # y_origin + y_size too large
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0],
                status=self.status,
                x_origin=1,
                y_origin=self.floor_plans[0].y_size,
                y_size=2,
            ).validated_save()

    def test_create_floor_plan_tile_invalid_overlapping_tiles(self):
        """FloorPlanTiles cannot overlap one another."""
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            x_size=2,
            y_size=2,
        ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[3],
                status=self.status,
                x_origin=1,
                y_origin=1,
                x_size=2,
                y_size=2,
            ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[3],
                status=self.status,
                x_origin=1,
                y_origin=3,
                x_size=2,
                y_size=2,
            ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[3],
                status=self.status,
                x_origin=3,
                y_origin=1,
                x_size=2,
                y_size=2,
            ).validated_save()
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[3],
                status=self.status,
                x_origin=3,
                y_origin=3,
                x_size=2,
                y_size=2,
            ).validated_save()

    def test_create_floor_plan_tile_invalid_rack_location_mismatch(self):
        """The Rack, if any, attached to a FloorPlanTile must belong to the same location as the FloorPlan."""
        # self.rack is attached to self.floors[-1], not self.floors[0]
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0], status=self.status, x_origin=1, y_origin=1, rack=self.rack
            ).validated_save()
        # How about a rack with no Location at all?
        non_located_rack = Rack.objects.create(name="Rack 2", status=self.status, location=self._test_data["location"])
        with self.assertRaises(ValidationError):
            models.FloorPlanTile(
                floor_plan=self.floor_plans[0], status=self.status, x_origin=1, y_origin=1, rack=non_located_rack
            ).validated_save()

    def test_allocation_type_assignment_rack_group(self):
        """Test that allocation type is correctly assigned for rack group tiles."""
        tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=self._test_data["valid_rack_group"],
        )
        tile.validated_save()
        self.assertEqual(tile.allocation_type, models.AllocationTypeChoices.RACKGROUP)

    def test_allocation_type_assignment_object(self):
        """Test that allocation type is correctly assigned for object tiles."""
        # Create a rack in the correct location
        rack = Rack.objects.create(
            name="Test Rack for Allocation",
            status=self.status,
            location=self.floor_plans[3].location,
        )

        tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack=rack,
        )
        tile.validated_save()
        self.assertEqual(tile.allocation_type, models.AllocationTypeChoices.OBJECT)

    def test_allocation_type_assignment_status_only(self):
        """Test that allocation type is correctly assigned for tiles with only status."""
        tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
        )
        tile.validated_save()
        self.assertEqual(tile.allocation_type, models.AllocationTypeChoices.RACKGROUP)

    def test_rack_on_rackgroup_tile_valid(self):
        """Test that a rack can be placed on a rack group tile if it belongs to that group."""
        # Create a rack group tile
        rackgroup_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=self._test_data["valid_rack_group"],
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        )
        rackgroup_tile.validated_save()

        # Create a rack in the same group
        rack = Rack.objects.create(
            name="Test Rack",
            status=self.status,
            location=self.floor_plans[3].location,
            rack_group=self._test_data["valid_rack_group"],
        )

        # Place rack on the rack group tile
        rack_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack=rack,
        )
        rack_tile.validated_save()  # Should not raise ValidationError
        self.assertTrue(rack_tile.on_group_tile)
        self.assertEqual(rack_tile.rack_group, self._test_data["valid_rack_group"])

    def test_rack_on_rackgroup_tile_invalid_group(self):
        """Test that a rack cannot be placed on a rack group tile if it belongs to a different group."""
        # Create a different rack group
        other_rack_group = RackGroup.objects.create(
            name="Other Rack Group",
            location=self.floor_plans[3].location,
        )

        # Create a rack group tile
        rackgroup_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=self._test_data["rack_group"],
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        )
        rackgroup_tile.validated_save()

        # Create a rack in a different group
        rack = Rack.objects.create(
            name="Test Rack",
            status=self.status,
            location=self.floor_plans[3].location,
            rack_group=other_rack_group,
        )

        # Try to place rack on the rack group tile
        rack_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack=rack,
        )
        with self.assertRaisesRegex(
            ValidationError, "Object tile with Rack .* cannot overlap with RackGroup tile for different group"
        ):
            rack_tile.clean()

    def test_object_tile_within_rackgroup_bounds(self):
        """Test that an object tile must fit within the bounds of its rack group tile."""
        # Create a rack group tile
        models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            x_size=1,
            y_size=1,
            rack_group=self._test_data["valid_rack_group"],
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        ).validated_save()

        # Create a rack in the correct location for this test
        rack = Rack.objects.create(
            name="Test Rack in Floor 4",
            status=self.status,
            location=self.floor_plans[3].location,
        )

        # Try to create an object tile that extends beyond the rack group tile
        object_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            x_size=2,
            y_size=3,
            rack=rack,
        )

        with self.assertRaisesRegex(
            ValidationError, "Object tile must not extend beyond the boundary of the rack group tile"
        ):
            object_tile.clean()

    def test_rackgroup_tiles_cannot_overlap(self):
        """Test that rack group tiles cannot overlap with each other."""
        # Create first rack group tile
        models.FloorPlanTile.objects.create(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=self._test_data["valid_rack_group"],
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        )

        # Create another rack group
        other_rack_group = RackGroup.objects.create(
            name="Other Rack Group",
            location=self.floor_plans[3].location,
        )

        # Try to create an overlapping rack group tile
        overlapping_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=other_rack_group,
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        )

        with self.assertRaisesRegex(ValidationError, "RackGroup tiles cannot overlap"):
            overlapping_tile.clean()

    def test_object_tiles_cannot_overlap(self):
        """Test that object tiles cannot overlap with each other."""
        # Create first object tile with a rack
        rack = Rack.objects.create(
            name="Test Rack for Overlap",
            status=self.status,
            location=self.floor_plans[3].location,
        )
        models.FloorPlanTile.objects.create(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack=rack,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )

        # Try to create an overlapping object tile with a device
        device = Device.objects.create(
            name="Test Device",
            device_type=self._test_data["device_type"],
            role=self._test_data["device_role"],
            status=self.status,
            location=self.floor_plans[3].location,
        )
        overlapping_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            device=device,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )

        with self.assertRaisesRegex(ValidationError, "Object tiles cannot overlap"):
            overlapping_tile.clean()


class TestFloorPlanTilePower(TestCase):
    """Test power-related functionality of FloorPlanTile model."""

    def setUp(self):
        """Create LocationType, Status, Location, and FloorPlan records."""
        prerequisites = fixtures.create_prerequisites()

        # Keep only the most essential attributes as instance variables
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]
        self.floor_plans = fixtures.create_floor_plans(self.floors)

        # Store all other test data in the dictionary
        self._test_data = {
            "location": prerequisites["location"],
            "device_type": prerequisites["device_type"],
            "device_role": prerequisites["device_role"],
            "valid_rack_group": RackGroup.objects.create(name="RackGroup 2", location=self.floors[3]),
        }

    def test_power_objects_on_tiles(self):
        """Test that power panels and power feeds can be placed on tiles and validate overlap rules."""
        # Create a power panel
        power_panel = PowerPanel.objects.create(
            name="Test Power Panel",
            location=self.floor_plans[3].location,
        )

        # Create a power feed connected to the panel
        power_feed = PowerFeed.objects.create(
            name="Test Power Feed",
            status=self.status,
            power_panel=power_panel,
        )

        # Place power panel on a tile
        panel_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            power_panel=power_panel,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        panel_tile.validated_save()

        # Try to place power feed on the same tile - should fail
        feed_tile_overlapping = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            power_feed=power_feed,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        with self.assertRaisesRegex(ValidationError, "Object tiles cannot overlap"):
            feed_tile_overlapping.clean()

        # Place power feed on a different tile - should succeed
        feed_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=3,
            y_origin=3,
            power_feed=power_feed,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        feed_tile.validated_save()

        # Verify allocation types are set correctly
        self.assertEqual(panel_tile.allocation_type, models.AllocationTypeChoices.OBJECT)
        self.assertEqual(feed_tile.allocation_type, models.AllocationTypeChoices.OBJECT)

        # Try to place another object on the power panel tile - should fail
        device = Device.objects.create(
            name="Test Device",
            device_type=self._test_data["device_type"],
            role=self._test_data["device_role"],
            status=self.status,
            location=self.floor_plans[3].location,
        )
        device_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            device=device,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        with self.assertRaisesRegex(ValidationError, "Object tiles cannot overlap"):
            device_tile.clean()

    def test_power_panel_with_rack_group(self):
        """Test that power panels respect rack group assignments and tile validation."""
        # Create a rack group tile
        rackgroup_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            rack_group=self._test_data["valid_rack_group"],
            allocation_type=models.AllocationTypeChoices.RACKGROUP,
        )
        rackgroup_tile.validated_save()

        # Create a power panel in the correct rack group
        power_panel = PowerPanel.objects.create(
            name="Test Power Panel",
            location=self.floor_plans[3].location,
            rack_group=self._test_data["valid_rack_group"],
        )
        power_panel.validated_save()

        # Place power panel on the rack group tile - should succeed
        panel_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            power_panel=power_panel,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        panel_tile.validated_save()

        self.assertEqual(rackgroup_tile.rack_group, self._test_data["valid_rack_group"])
        self.assertEqual(power_panel.rack_group, rackgroup_tile.rack_group)
        self.assertTrue(panel_tile.on_group_tile)

        # Create a power panel in a different rack group
        other_rack_group = RackGroup.objects.create(
            name="Other Rack Group",
            location=self.floor_plans[3].location,
        )
        other_power_panel = PowerPanel.objects.create(
            name="Other Power Panel",
            location=self.floor_plans[3].location,
            rack_group=other_rack_group,
        )

        # Try to place power panel from different rack group on the tile - should fail
        invalid_panel_tile = models.FloorPlanTile(
            floor_plan=self.floor_plans[3],
            status=self.status,
            x_origin=2,
            y_origin=2,
            power_panel=other_power_panel,
            allocation_type=models.AllocationTypeChoices.OBJECT,
        )
        with self.assertRaisesRegex(ValidationError, "Object tiles cannot overlap"):
            invalid_panel_tile.clean()


class TestFreeformPlacement(TestCase):
    """Test freeform placement, validation, and grid-to-freeform conversion."""

    def setUp(self):
        """Set up prerequisites."""
        prerequisites = fixtures.create_prerequisites(floor_count=3)
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]

    def _grid_plan(self, **kwargs):
        return models.FloorPlan.objects.create(
            location=self.floors[0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1, **kwargs
        )

    def test_convert_to_freeform_center_anchored(self):
        """Conversion seeds center-anchored, content-rect-normalized coordinates from grid cells."""
        plan = self._grid_plan()
        tile = models.FloorPlanTile(floor_plan=plan, x_origin=2, y_origin=3, status=self.status)
        tile.validated_save()

        modified = plan.convert_to_freeform()

        self.assertEqual(len(modified), 1)
        tile.refresh_from_db()
        # col=1, row=2, x_size=y_size=1, totals=5.
        self.assertAlmostEqual(tile.pos_x, (1 + 0.5) / 5)
        self.assertAlmostEqual(tile.pos_y, (2 + 0.5) / 5)
        self.assertAlmostEqual(tile.width, 1 / 5)
        self.assertAlmostEqual(tile.height, 1 / 5)
        # Grid origins are retained (reversible).
        self.assertEqual((tile.x_origin, tile.y_origin), (2, 3))

    def test_convert_seeds_rotation_from_orientation(self):
        """Conversion seeds rotation from the tile's discrete orientation."""
        plan = self._grid_plan()
        rack = Rack.objects.create(name="RR", status=self.status, location=self.floors[0])
        tile = models.FloorPlanTile(
            floor_plan=plan,
            x_origin=1,
            y_origin=1,
            status=self.status,
            rack=rack,
            object_orientation=ObjectOrientationChoices.LEFT,
        )
        tile.validated_save()
        plan.convert_to_freeform()
        tile.refresh_from_db()
        self.assertEqual(tile.rotation, 270)

    def test_convert_is_idempotent_and_forceable(self):
        """A second conversion skips seeded tiles unless forced."""
        plan = self._grid_plan()
        tile = models.FloorPlanTile(floor_plan=plan, x_origin=2, y_origin=2, status=self.status)
        tile.validated_save()
        plan.convert_to_freeform()

        # Manual edit that a plain re-convert must preserve.
        tile.refresh_from_db()
        tile.pos_x = 0.9
        tile.save(update_fields=["pos_x"])

        self.assertEqual(plan.convert_to_freeform(), [])  # already seeded -> skipped
        tile.refresh_from_db()
        self.assertAlmostEqual(tile.pos_x, 0.9)

        self.assertEqual(len(plan.convert_to_freeform(force=True)), 1)  # forced -> re-seeded
        tile.refresh_from_db()
        self.assertAlmostEqual(tile.pos_x, (1 + 0.5) / 5)

    def test_pos_out_of_range_rejected(self):
        """pos_x/pos_y outside 0..1 fail validation."""
        plan = self._grid_plan(placement_mode=PlacementModeChoices.FREEFORM)
        tile = models.FloorPlanTile(floor_plan=plan, x_origin=1, y_origin=1, status=self.status, pos_x=1.5, pos_y=0.5)
        with self.assertRaises(ValidationError):
            tile.validated_save()

    def test_pos_pairing_required(self):
        """Setting only one of pos_x/pos_y is rejected."""
        plan = self._grid_plan(placement_mode=PlacementModeChoices.FREEFORM)
        tile = models.FloorPlanTile(floor_plan=plan, x_origin=1, y_origin=1, status=self.status, pos_x=0.5)
        with self.assertRaises(ValidationError):
            tile.validated_save()

    def test_width_zero_rejected(self):
        """A non-positive footprint is rejected."""
        plan = self._grid_plan(placement_mode=PlacementModeChoices.FREEFORM)
        tile = models.FloorPlanTile(
            floor_plan=plan, x_origin=1, y_origin=1, status=self.status, pos_x=0.5, pos_y=0.5, width=0
        )
        with self.assertRaises(ValidationError):
            tile.validated_save()

    def test_origin_pairing_constraint(self):
        """A half-null grid origin is rejected by the pairing constraint."""
        plan = self._grid_plan(placement_mode=PlacementModeChoices.FREEFORM)
        tile = models.FloorPlanTile(
            floor_plan=plan, x_origin=1, y_origin=None, status=self.status, pos_x=0.5, pos_y=0.5
        )
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                tile.validated_save()

    def test_overlap_allowed_in_freeform_mode(self):
        """Overlapping object footprints are allowed in freeform mode (rejected in grid mode)."""
        rack_a = Rack.objects.create(name="RA", status=self.status, location=self.floors[0])
        rack_b = Rack.objects.create(name="RB", status=self.status, location=self.floors[0])

        # Grid mode: a 2-wide tile then a tile inside its span overlap and are rejected.
        grid_plan = self._grid_plan()
        models.FloorPlanTile(
            floor_plan=grid_plan, x_origin=1, y_origin=1, x_size=2, status=self.status, rack=rack_a
        ).validated_save()
        with self.assertRaisesRegex(ValidationError, "Object tiles cannot overlap"):
            models.FloorPlanTile(
                floor_plan=grid_plan, x_origin=2, y_origin=1, status=self.status, rack=rack_b
            ).validated_save()

        # Freeform mode: the same overlapping geometry saves without error.
        free_plan = models.FloorPlan.objects.create(
            location=self.floors[1],
            x_size=5,
            y_size=5,
            x_origin_seed=1,
            y_origin_seed=1,
            placement_mode=PlacementModeChoices.FREEFORM,
        )
        rack_c = Rack.objects.create(name="RC", status=self.status, location=self.floors[1])
        rack_d = Rack.objects.create(name="RD", status=self.status, location=self.floors[1])
        models.FloorPlanTile(
            floor_plan=free_plan, x_origin=1, y_origin=1, x_size=2, status=self.status, rack=rack_c
        ).validated_save()
        # Should not raise.
        models.FloorPlanTile(
            floor_plan=free_plan, x_origin=2, y_origin=1, status=self.status, rack=rack_d
        ).validated_save()


class TestGenericPlacement(TestCase):
    """Test the generic placement pair mirrored from the legacy typed FKs (Wave G1)."""

    def setUp(self):
        """Set up a plan and prerequisites."""
        prerequisites = fixtures.create_prerequisites(floor_count=2)
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]
        self.plan = models.FloorPlan.objects.create(
            location=self.floors[0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )

    def test_typed_fk_mirrors_to_generic_pair_and_label(self):
        """Saving a tile with a typed FK populates the generic pair and the display label."""
        rack = Rack.objects.create(name="MirrorRack", status=self.status, location=self.floors[0])
        tile = models.FloorPlanTile(floor_plan=self.plan, x_origin=1, y_origin=1, status=self.status, rack=rack)
        tile.validated_save()
        tile.refresh_from_db()
        self.assertEqual(tile.placed_content_type, ContentType.objects.get_for_model(Rack))
        self.assertEqual(tile.placed_object_id, rack.pk)
        self.assertEqual(tile.placed_object, rack)
        self.assertEqual(tile.placed_label, "MirrorRack")

    def test_for_object_reverse_lookup(self):
        """objects.for_object() finds the placing tile, and is empty for None/unsaved objects."""
        rack = Rack.objects.create(name="LookupRack", status=self.status, location=self.floors[0])
        tile = models.FloorPlanTile(floor_plan=self.plan, x_origin=2, y_origin=1, status=self.status, rack=rack)
        tile.validated_save()
        self.assertEqual(models.FloorPlanTile.objects.for_object(rack).first(), tile)
        self.assertFalse(models.FloorPlanTile.objects.for_object(None).exists())
        self.assertFalse(models.FloorPlanTile.objects.for_object(Rack(name="Unsaved", status=self.status)).exists())

    def test_status_only_tile_has_null_generic_pair(self):
        """A tile with no object leaves the generic pair null (pairing constraint satisfied)."""
        tile = models.FloorPlanTile(floor_plan=self.plan, x_origin=3, y_origin=1, status=self.status)
        tile.validated_save()
        tile.refresh_from_db()
        self.assertIsNone(tile.placed_content_type)
        self.assertIsNone(tile.placed_object_id)
        self.assertEqual(tile.placed_label, "")


class TestPlacementRegistry(TestCase):
    """Test the placeable-type registry (Wave G1)."""

    def test_builtins_are_registered(self):
        """The four native DCIM types resolve to a PlacementType."""
        placement = registry.resolve(Rack())
        self.assertIsNotNone(placement)
        self.assertEqual(placement.label, "Rack")

    def test_unregistered_type_resolves_to_none(self):
        """An unregistered model resolves to None rather than raising."""
        self.assertIsNone(registry.resolve(Status()))

    def test_duplicate_registration_is_noop(self):
        """Re-registering an existing type without replace=True does not overwrite it."""
        original = registry.resolve(Rack()).label
        registry.register("dcim.rack", label="SHOULD-NOT-STICK")
        self.assertEqual(registry.resolve(Rack()).label, original)

    def test_resolve_location_uses_registered_resolver(self):
        """The registry resolves an object's Location via its registered resolver."""
        prerequisites = fixtures.create_prerequisites(floor_count=1)
        floor = prerequisites["floors"][0]
        rack = Rack.objects.create(name="RegLocRack", status=prerequisites["status"], location=floor)
        self.assertEqual(registry.resolve_location(rack), floor)


class TestGenericPlacementValidation(TestCase):
    """Test model-level validation of a generic (non-typed-FK) placement (Wave G3)."""

    def setUp(self):
        """Set up two locations and a plan on the first."""
        prerequisites = fixtures.create_prerequisites(floor_count=2)
        self.status = prerequisites["status"]
        self.floors = prerequisites["floors"]
        self.plan = models.FloorPlan.objects.create(
            location=self.floors[0],
            x_size=5,
            y_size=5,
            x_origin_seed=1,
            y_origin_seed=1,
            placement_mode=PlacementModeChoices.FREEFORM,
        )

    def _generic_tile(self, obj):
        return models.FloorPlanTile(
            floor_plan=self.plan,
            status=self.status,
            x_origin=1,
            y_origin=1,
            pos_x=0.5,
            pos_y=0.5,
            placed_content_type=ContentType.objects.get_for_model(obj),
            placed_object_id=obj.pk,
        )

    def test_wrong_location_rejected(self):
        """A generically-placed object in a different Location than the plan is rejected."""
        rack = Rack.objects.create(name="ElsewhereRack", status=self.status, location=self.floors[1])
        with self.assertRaises(ValidationError):
            self._generic_tile(rack).validated_save()

    def test_unregistered_type_rejected(self):
        """A generically-placed object of an unregistered type is rejected."""
        tenant = Tenant.objects.create(name="AcmeCorp")
        with self.assertRaises(ValidationError):
            self._generic_tile(tenant).validated_save()

    def test_registered_same_location_allowed(self):
        """A generically-placed registered object in the plan's Location validates."""
        rack = Rack.objects.create(name="HereRack", status=self.status, location=self.floors[0])
        tile = self._generic_tile(rack)
        tile.validated_save()  # should not raise
        self.assertEqual(tile.placed_object, rack)
