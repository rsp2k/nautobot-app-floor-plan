"""Tests for the SVG rendering functionality of the Nautobot Floor Plan app."""

from unittest.mock import ANY, MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from nautobot.dcim.models import Device, PowerFeed, PowerPanel, Rack, RackGroup
from nautobot.extras.models import Role
from nautobot.tenancy.models import Tenant
from nautobot.users.models import Token
from rest_framework import status
from rest_framework.test import APIClient

from nautobot_floor_plan import models, svg
from nautobot_floor_plan.choices import ObjectOrientationChoices, PlacementModeChoices
from nautobot_floor_plan.placement import registry
from nautobot_floor_plan.placement.config import current_config_version
from nautobot_floor_plan.placement.icons import ICON_GLYPHS, ICON_VIEWBOX, resolve_glyph
from nautobot_floor_plan.placement.registry import PlacementType
from nautobot_floor_plan.tests import fixtures
from nautobot_floor_plan.tests.fixtures import create_prerequisites

# pylint: disable=protected-access,too-many-instance-attributes

User = get_user_model()


class FloorPlanSVGTestCase(TestCase):
    """Test cases for FloorPlanSVG class."""

    def setUp(self):
        """Set up test environment."""
        # Create mock floor plan
        self.floor_plan = MagicMock()
        self.floor_plan.x_size = 5
        self.floor_plan.y_size = 5
        self.floor_plan.tile_width = 100
        self.floor_plan.tile_depth = 100
        self.floor_plan.x_origin_seed = 1
        self.floor_plan.y_origin_seed = 1

        # Mock location
        self.location = MagicMock()
        self.location.pk = 1
        self.floor_plan.location = self.location

        # Create mock user
        self.user = MagicMock()

        # Base URL for testing
        self.base_url = "http://testserver"

        # Mock drawing
        self.drawing = MagicMock()

        # Start the patch for reverse
        self.reverse_patcher = patch("nautobot_floor_plan.svg.reverse", return_value="/mock/url/")
        self.mock_reverse = self.reverse_patcher.start()

        # Create the SVG instance once with the patch applied
        self.svg = svg.FloorPlanSVG(floor_plan=self.floor_plan, user=self.user, base_url=self.base_url)

    def tearDown(self):
        """Clean up test environment."""
        # Stop the patch
        self.reverse_patcher.stop()

    def test_setup_drawing(self):
        """Test the _setup_drawing method."""
        # Call method
        with patch("svgwrite.Drawing") as mock_drawing_class:
            mock_drawing = MagicMock()
            mock_drawing_class.return_value = mock_drawing

            result = self.svg._setup_drawing(width=500, depth=500)

            # Assertions
            mock_drawing_class.assert_called_once_with(size=(500, 500), debug=False)
            mock_drawing.viewbox.assert_called_once_with(0, 0, width=500, height=500)
            mock_drawing.rect.assert_called()  # Check that rect was called for the border

            self.assertEqual(result, mock_drawing)

    def test_setup_drawing_adds_stylesheet(self):
        """Test that _setup_drawing adds the CSS stylesheet."""
        # Setup mocks
        with (
            patch("os.path.join") as mock_path_join,
            patch("builtins.open") as mock_open,
            patch("svgwrite.Drawing") as mock_drawing_class,
        ):
            mock_path_join.return_value = "/mock/path/to/svg.css"
            mock_file = MagicMock()
            mock_file.__enter__.return_value.read.return_value = "mock css content"
            mock_open.return_value = mock_file

            mock_drawing = MagicMock()
            mock_drawing_class.return_value = mock_drawing
            mock_style = MagicMock()
            mock_drawing.style.return_value = mock_style

            # Call method
            self.svg._setup_drawing(width=500, depth=500)

            # Assertions
            mock_path_join.assert_called()
            mock_open.assert_called_with("/mock/path/to/svg.css", "r", encoding="utf-8")
            mock_drawing.style.assert_called_with("mock css content")
            mock_drawing.defs.add.assert_called_with(mock_style)

    def test_draw_grid(self):
        """Test the _draw_grid method."""
        # Setup
        mock_drawing = MagicMock()

        # Mock the methods called by _draw_grid
        self.svg._draw_grid_lines = MagicMock()
        self.svg._generate_axis_labels = MagicMock(return_value=(["A", "B", "C"], ["1", "2", "3"]))
        self.svg._draw_axis_labels = MagicMock()
        self.svg._draw_tile_links = MagicMock()

        # Call method
        self.svg._draw_grid(mock_drawing)

        # Assertions
        self.svg._draw_grid_lines.assert_called_once_with(mock_drawing)
        self.svg._generate_axis_labels.assert_called_once()
        self.svg._draw_axis_labels.assert_called_once_with(mock_drawing, ["A", "B", "C"], ["1", "2", "3"])
        self.svg._draw_tile_links.assert_called_once_with(mock_drawing, ["A", "B", "C"], ["1", "2", "3"])

    def test_generate_axis_labels(self):
        """Test the _generate_axis_labels method."""
        # Setup
        self.floor_plan.generate_labels = MagicMock()
        self.floor_plan.generate_labels.side_effect = lambda axis, _: (
            ["A", "B", "C", "D", "E"] if axis == "X" else ["1", "2", "3", "4", "5"]
        )

        # Call method
        x_labels, y_labels = self.svg._generate_axis_labels()

        # Assertions
        self.assertEqual(x_labels, ["A", "B", "C", "D", "E"])
        self.assertEqual(y_labels, ["1", "2", "3", "4", "5"])
        self.floor_plan.generate_labels.assert_any_call("X", 5)
        self.floor_plan.generate_labels.assert_any_call("Y", 5)

    def test_draw_grid_lines(self):
        """Test the _draw_grid_lines method."""
        # Setup
        mock_drawing = MagicMock()

        # Call method
        self.svg._draw_grid_lines(mock_drawing)

        # Assertions - should draw x_size + y_size + 2 lines
        expected_calls = self.floor_plan.x_size + 1 + self.floor_plan.y_size + 1
        self.assertEqual(mock_drawing.line.call_count, expected_calls)
        self.assertEqual(mock_drawing.add.call_count, expected_calls)

    def test_draw_axis_labels(self):
        """Test the _draw_axis_labels method."""
        # Setup
        mock_drawing = MagicMock()
        x_labels = ["A", "B", "C", "D", "E"]
        y_labels = ["1", "2", "3", "4", "5"]

        # Call method
        self.svg._draw_axis_labels(mock_drawing, x_labels, y_labels)

        # Assertions - should add text for each label
        expected_calls = len(x_labels) + len(y_labels)
        self.assertEqual(mock_drawing.text.call_count, expected_calls)
        self.assertEqual(mock_drawing.add.call_count, expected_calls)

    def test_draw_tile_link(self):
        """Test the _draw_tile_link method."""
        # Setup
        mock_drawing = MagicMock()
        axis = {"x": "A", "y": "1", "x_idx": 0, "y_idx": 0}

        # Mock the add method to return a link object
        mock_link = MagicMock()
        mock_drawing.add.return_value = mock_link

        # Call method
        self.svg._draw_tile_link(mock_drawing, axis)

        # Assertions
        mock_drawing.a.assert_called_once()  # Should create an anchor
        mock_drawing.add.assert_called_once()  # Should add the anchor to the drawing
        mock_link.add.assert_called()  # Should add elements to the link

    def test_draw_tile(self):
        """Test the _draw_tile method."""
        # Setup
        mock_drawing = MagicMock()
        mock_tile = MagicMock()
        mock_tile.on_group_tile = False
        mock_tile.rack = None
        mock_tile.device = None
        mock_tile.power_panel = None
        mock_tile.power_feed = None

        # Mock the methods called by _draw_tile
        self.svg._draw_defined_rackgroup_tile = MagicMock()
        self.svg._draw_edit_delete_button = MagicMock()
        self.svg._draw_object_tile = MagicMock()

        # Call method
        self.svg._draw_tile(mock_drawing, mock_tile)

        # Assertions
        self.svg._draw_defined_rackgroup_tile.assert_called_once_with(mock_drawing, mock_tile)
        self.svg._draw_edit_delete_button.assert_called_once_with(mock_drawing, mock_tile, 0, 0)
        self.svg._draw_object_tile.assert_not_called()  # No objects on this tile

    def test_draw_object_tile(self):
        """Test the _draw_object_tile method with different object types."""
        # Setup
        mock_drawing = MagicMock()

        # Test cases for different object types
        test_cases = [
            {"object_type": "rack", "expected_obj_type": "Rack"},
            {"object_type": "device", "expected_obj_type": "Device"},
            {"object_type": "power_panel", "expected_obj_type": "Power Panel"},
            {"object_type": "power_feed", "expected_obj_type": "Power Feed"},
        ]

        for case in test_cases:
            # Create mock tile with the specified object
            mock_tile = MagicMock()
            mock_tile.x_origin = 1
            mock_tile.y_origin = 1
            mock_tile.x_size = 1
            mock_tile.y_size = 1
            mock_tile.on_group_tile = True

            # Set all object attributes to None
            mock_tile.rack = None
            mock_tile.device = None
            mock_tile.power_panel = None
            mock_tile.power_feed = None

            # Set the specific object for this test case
            obj_type = case["object_type"]
            mock_obj = MagicMock()
            mock_obj.name = f"Test {obj_type.replace('_', ' ').title()}"

            # Create a proper status object with a string color value
            mock_status = MagicMock()
            mock_status.color = "ff0000"  # Use a valid hex color string
            mock_obj.status = mock_status

            setattr(mock_tile, obj_type, mock_obj)

            # Set the tile status with a proper color string
            mock_tile_status = MagicMock()
            mock_tile_status.color = "ff0000"  # Use a valid hex color string
            mock_tile.status = mock_tile_status

            # Mock methods called by _draw_object_tile
            self.svg._draw_object_orientation = MagicMock()
            self.svg._draw_object_text = MagicMock()
            self.svg._draw_edit_delete_button = MagicMock()

            # Call method
            self.svg._draw_object_tile(mock_drawing, mock_tile)

            # Assertions
            self.svg._draw_object_orientation.assert_called()  # Should call draw_object_orientation
            self.svg._draw_object_text.assert_called()  # Should call draw_object_text
            self.svg._draw_edit_delete_button.assert_called()  # Should call draw_edit_delete_button

    def test_draw_object_orientation(self):
        """Test the _draw_object_orientation method with different orientations."""
        # Setup
        mock_drawing = MagicMock()
        mock_link = MagicMock()
        mock_tile = MagicMock()
        mock_tile.x_origin = 1
        mock_tile.y_origin = 1
        mock_tile.x_size = 1
        mock_tile.y_size = 1
        mock_tile.status.color = "ff0000"
        origin = (50, 50)  # Example origin coordinates

        # Test each orientation
        orientations = [
            ObjectOrientationChoices.UP,
            ObjectOrientationChoices.DOWN,
            ObjectOrientationChoices.LEFT,
            ObjectOrientationChoices.RIGHT,
        ]

        for orientation in orientations:
            mock_tile.object_orientation = orientation

            # Call method
            self.svg._draw_object_orientation(mock_drawing, mock_tile, mock_link, origin)

            # Assertions
            mock_link.add.assert_called()  # Should add orientation indicator

    def test_render(self):
        """Test the complete render method."""
        # Setup
        mock_drawing = MagicMock()

        # Mock tiles (render fetches them via a select_related().prefetch_related() chain).
        mock_tile1 = MagicMock()
        mock_tile2 = MagicMock()
        self.floor_plan.tiles.select_related.return_value.prefetch_related.return_value = [mock_tile1, mock_tile2]

        # Mock methods called by render
        self.svg._drawing_extents = MagicMock(return_value=(0, 0, 796, 796))
        self.svg._setup_drawing = MagicMock(return_value=mock_drawing)
        self.svg._draw_background_image = MagicMock()
        self.svg._draw_underlay_tiles = MagicMock()
        self.svg._draw_grid = MagicMock()
        self.svg._draw_tile = MagicMock()
        self.svg._draw_legend = MagicMock()

        # Call method
        result = self.svg.render()

        # Assertions
        self.svg._setup_drawing.assert_called_once()
        self.svg._draw_background_image.assert_called_once_with(mock_drawing)
        # Grid mode (mock placement_mode != freeform): every tile drawn via grid path with underlays.
        self.assertEqual(self.svg._draw_underlay_tiles.call_count, 2)
        self.svg._draw_grid.assert_called_once_with(mock_drawing)
        self.assertEqual(self.svg._draw_tile.call_count, 2)
        self.assertEqual(result, mock_drawing)

    def test_get_tooltip_data(self):
        """Test the _get_tooltip_data method with different object types."""
        # Create prerequisites which includes status, manufacturer, etc.
        prerequisites = create_prerequisites(floor_count=1)
        active_status = prerequisites["status"]
        device_type = prerequisites["device_type"]
        device_role = prerequisites["device_role"]
        floor = prerequisites["floors"][0]

        # Test Rack tooltip
        rack_group = RackGroup.objects.create(
            name="Test Rack Group",
            location=floor,
        )

        rack = Rack.objects.create(
            name="Test Rack",
            status=active_status,
            location=floor,
            rack_group=rack_group,
        )

        rack_data = self.svg._get_tooltip_data(rack, "Rack")
        self.assertEqual(rack_data["Name"], "Test Rack")
        self.assertEqual(rack_data["Type"], "Rack")
        self.assertEqual(rack_data["Status"], "Active")
        self.assertEqual(rack_data["Utilization"], "0 / 42 RU")
        self.assertEqual(rack_data["Rack_group"], "Test Rack Group")

        # Test Device tooltip
        device = Device.objects.create(
            name="Test Device",
            status=active_status,
            device_type=device_type,
            role=device_role,
            location=floor,
            serial="ABC123",
            asset_tag="ASSET001",
        )

        device_data = self.svg._get_tooltip_data(device, "Device")
        self.assertEqual(device_data["Name"], "Test Device")
        self.assertEqual(device_data["Type"], "Device")
        self.assertEqual(device_data["Status"], "Active")
        self.assertEqual(device_data["Manufacturer"], "Test Manufacturer")
        self.assertEqual(device_data["Model"], "Test Device Type")
        self.assertEqual(device_data["Serial"], "ABC123")
        self.assertEqual(device_data["Asset_tag"], "ASSET001")

        # Test PowerPanel tooltip
        power_panel = PowerPanel.objects.create(
            name="Test Power Panel",
            location=floor,
            rack_group=rack_group,
        )

        # Create some power feeds for the panel
        PowerFeed.objects.create(
            name="Feed 1",
            power_panel=power_panel,
            status=active_status,
        )
        PowerFeed.objects.create(
            name="Feed 2",
            power_panel=power_panel,
            status=active_status,
        )

        panel_data = self.svg._get_tooltip_data(power_panel, "Power Panel")
        self.assertEqual(panel_data["Name"], "Test Power Panel")
        self.assertEqual(panel_data["Type"], "Power Panel")
        self.assertEqual(panel_data["Feeds"], ["Feed 1", "Feed 2"])
        self.assertEqual(panel_data["Rack_group"], "Test Rack Group")

        # Test PowerFeed tooltip
        power_feed = PowerFeed.objects.create(
            name="Test Power Feed",
            status=active_status,
            power_panel=power_panel,
            voltage=220,
            amperage=30,
            phase="Single",
        )

        feed_data = self.svg._get_tooltip_data(power_feed, "Power Feed")
        self.assertEqual(feed_data["Name"], "Test Power Feed")
        self.assertEqual(feed_data["Type"], "Power Feed")
        self.assertEqual(feed_data["Status"], "Active")
        self.assertEqual(feed_data["Panel"], "Test Power Panel")
        self.assertEqual(feed_data["Voltage"], "220V")
        self.assertEqual(feed_data["Amperage"], "30A")
        self.assertEqual(feed_data["Phase"], "Single-phase")

    def test_add_text_element(self):
        """Test the _add_text_element method."""
        # Setup
        mock_drawing = MagicMock()
        mock_tile = MagicMock()
        mock_tile.x_size = 2
        mock_tile.y_size = 2
        origin = (50, 50)

        text_element = svg.TextElement(text="Test Text", line_offset=1, class_name="test-class", color="ff0000")

        # Call method
        self.svg._add_text_element(mock_drawing, text_element, origin, mock_tile)

        # Assertions
        mock_drawing.text.assert_called_once()
        mock_drawing.add.assert_called_once()

        # Verify text parameters
        text_call_args = mock_drawing.text.call_args[0]
        text_call_kwargs = mock_drawing.text.call_args[1]

        self.assertEqual(text_call_args[0], "Test Text")
        self.assertEqual(text_call_kwargs["class_"], "test-class")
        self.assertIn("style", text_call_kwargs)
        self.assertIn("fill: #ffffff", text_call_kwargs["style"])

    def test_draw_object_text(self):
        """Test the _draw_object_text method."""
        # Setup using fixtures
        prerequisites = create_prerequisites(floor_count=1)
        active_status = prerequisites["status"]
        floor = prerequisites["floors"][0]

        # Create a real rack for testing
        rack_group = RackGroup.objects.create(
            name="Test Rack Group",
            location=floor,
        )

        rack = Rack.objects.create(
            name="Test Rack",
            status=active_status,
            location=floor,
            rack_group=rack_group,
        )

        # Setup drawing mocks
        mock_drawing = MagicMock()
        mock_tile = MagicMock()
        mock_link = MagicMock()
        origin = (50, 50)

        # Configure the tile with our real rack
        mock_tile.rack = rack
        mock_tile.device = None
        mock_tile.power_panel = None
        mock_tile.power_feed = None
        mock_tile.x_origin = 1
        mock_tile.y_origin = 1
        mock_tile.status = active_status

        # Mock the _add_text_element method
        self.svg._add_text_element = MagicMock()

        # Call method
        self.svg._draw_object_text(mock_drawing, mock_tile, mock_link, origin)

        # Assertions
        self.assertEqual(self.svg._add_text_element.call_count, 3)
        mock_link.__setitem__.assert_any_call("class", "object-tooltip")
        mock_link.__setitem__.assert_any_call("data-tooltip", ANY)

    def test_draw_edit_delete_button(self):
        """Test the _draw_edit_delete_button method."""
        # Setup
        mock_drawing = MagicMock()
        mock_tile = MagicMock()
        mock_tile.pk = 1
        mock_tile.x_origin = 1
        mock_tile.y_origin = 1
        mock_tile.x_size = 2
        mock_tile.y_size = 2
        mock_tile.allocation_type = "Object"

        # Call method
        self.svg._draw_edit_delete_button(mock_drawing, mock_tile, 0, 0)

        # Assertions
        # Should create two links (edit and delete)
        self.assertEqual(mock_drawing.a.call_count, 2)
        # Should create two rectangles (button backgrounds)
        self.assertEqual(mock_drawing.rect.call_count, 2)
        # Should create two text elements (button symbols)
        self.assertEqual(mock_drawing.text.call_count, 2)
        # Should add all elements to the drawing
        self.assertEqual(mock_drawing.add.call_count, 2)  # One for each button group


class FloorPlanThemeTests(TestCase):
    """Test cases for SVG rendering based on user theme settings."""

    def setUp(self):
        """Set up test environment using fixtures."""
        # Create prerequisites using the fixture
        data = fixtures.create_prerequisites()
        self.floors = data["floors"]
        self.floor_plan = models.FloorPlan.objects.create(
            location=self.floors[0], x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )
        self.user = User.objects.create(username="testuser", is_superuser=True)
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_svg_rendering_light_theme(self):
        """Test SVG rendering with light theme."""
        url = reverse("plugins-api:nautobot_floor_plan-api:floorplan-svg", kwargs={"pk": self.floor_plan.pk})
        response = self.client.get(url, **{"HTTP_COOKIE": "theme=light"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('<style type="text/css">', response.content.decode())  # Check for CSS inclusion
        self.assertNotIn(
            "filter: invert(1) hue-rotate(180deg);", response.content.decode()
        )  # Check that invert filter is not in use

    def test_svg_rendering_dark_theme(self):
        """Test SVG rendering with dark theme and check for invert filter on .object."""
        url = reverse("plugins-api:nautobot_floor_plan-api:floorplan-svg", kwargs={"pk": self.floor_plan.pk})
        response = self.client.get(url, **{"HTTP_COOKIE": "theme=dark"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('<style type="text/css">', response.content.decode())  # Check for CSS inclusion
        self.assertIn(
            "filter: invert(1) hue-rotate(180deg);", response.content.decode()
        )  # Check that invert filter is being used


class FloorPlanBlueprintSVGTests(TestCase):
    """Wave A: blueprint background embedding and freeform tile rendering (real objects)."""

    @staticmethod
    def _image_file(name="blueprint.png", size=(200, 100)):
        """Build an in-memory PNG upload with known pixel dimensions."""
        from io import BytesIO  # pylint: disable=import-outside-toplevel

        from django.core.files.uploadedfile import SimpleUploadedFile  # pylint: disable=import-outside-toplevel
        from PIL import Image  # pylint: disable=import-outside-toplevel

        buffer = BytesIO()
        Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def setUp(self):
        """Set up prerequisites and a rendering user."""
        data = create_prerequisites(floor_count=1)
        self.status = data["status"]
        self.floor = data["floors"][0]
        self.base_url = "http://testserver"
        self.user = User.objects.create(username="svguser", is_superuser=True)

    def _render(self, floor_plan):
        """Render a floor plan to an SVG string."""
        return floor_plan.get_svg(user=self.user, base_url=self.base_url).tostring()

    def test_no_blueprint_grid_mode_has_no_image(self):
        """Grid plan with no blueprint renders no <image> and still publishes the content rect + grid."""
        floor_plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1
        )
        svg_str = self._render(floor_plan)
        self.assertNotIn("<image", svg_str)
        self.assertIn('data-content-w="750"', svg_str)  # 5 * GRID_SIZE_X(150)
        self.assertIn('class="grid"', svg_str)

    def test_background_image_embedded(self):
        """A blueprint is base64-embedded with an id, opacity, and resolved normalized data-bg-* attrs."""
        floor_plan = models.FloorPlan.objects.create(
            location=self.floor,
            x_size=5,
            y_size=5,
            x_origin_seed=1,
            y_origin_seed=1,
            background_image=self._image_file(),
        )
        svg_str = self._render(floor_plan)
        self.assertIn("<image", svg_str)
        self.assertIn("data:image/png;base64,", svg_str)
        self.assertIn('id="blueprint-image"', svg_str)
        self.assertIn("data-bg-width", svg_str)
        self.assertIn('data-bg-autofit="true"', svg_str)
        self.assertIn("opacity=", svg_str)

    def test_background_opacity_zero_skips_image(self):
        """Zero opacity is treated as no blueprint."""
        floor_plan = models.FloorPlan.objects.create(
            location=self.floor,
            x_size=5,
            y_size=5,
            x_origin_seed=1,
            y_origin_seed=1,
            background_image=self._image_file(),
            background_opacity=0,
        )
        self.assertNotIn("<image", self._render(floor_plan))

    def test_show_grid_false_suppresses_grid(self):
        """Hiding the grid removes the grid lines but keeps the rest of the drawing."""
        floor_plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1, show_grid=False
        )
        self.assertNotIn('class="grid"', self._render(floor_plan))

    def test_freeform_tile_center_anchored(self):
        """A freeform tile renders a center-anchored, transform-positioned group at the content-rect point."""
        floor_plan = models.FloorPlan.objects.create(
            location=self.floor,
            x_size=5,
            y_size=5,
            x_origin_seed=1,
            y_origin_seed=1,
            placement_mode=PlacementModeChoices.FREEFORM,
        )
        rack = Rack.objects.create(name="R1", status=self.status, location=self.floor)
        tile = models.FloorPlanTile(
            floor_plan=floor_plan,
            status=self.status,
            x_origin=1,
            y_origin=1,
            rack=rack,
            pos_x=0.5,
            pos_y=0.5,
            width=0.1,
            height=0.1,
        )
        tile.validated_save()
        svg_str = self._render(floor_plan)
        # content_w = 5 * 150 = 750; center = 26 + 0.5 * 750 = 401.
        self.assertIn("translate(401.0,401.0)", svg_str)
        self.assertIn("data-tile-id", svg_str)


class FloorPlanIconRenderingTests(TestCase):
    """Wave G2: per-type marker icons, Device-role variants, fallback, and legend."""

    def setUp(self):
        """Set up prerequisites and a freeform plan."""
        data = create_prerequisites(floor_count=1)
        self.status = data["status"]
        self.device_type = data["device_type"]
        self.device_role = data["device_role"]
        self.floor = data["floors"][0]
        self.user = User.objects.create(username="iconuser", is_superuser=True)
        self.plan = models.FloorPlan.objects.create(
            location=self.floor,
            x_size=6,
            y_size=6,
            x_origin_seed=1,
            y_origin_seed=1,
            placement_mode=PlacementModeChoices.FREEFORM,
        )
        self._next_cell = 1

    def _place(self, **fields):
        """Create a freeform tile placing an object at the next free grid cell."""
        tile = models.FloorPlanTile(
            floor_plan=self.plan,
            status=self.status,
            x_origin=self._next_cell,
            y_origin=1,
            pos_x=0.5,
            pos_y=0.5,
            width=0.12,
            height=0.12,
            **fields,
        )
        tile.validated_save()
        self._next_cell += 1
        return tile

    def _render(self):
        return self.plan.get_svg(user=self.user, base_url="http://testserver").tostring()

    def test_rack_marker_renders_icon_and_chip(self):
        """A placed rack renders an icon glyph inside a chip."""
        self._place(rack=Rack.objects.create(name="IconRack", status=self.status, location=self.floor))
        svg_str = self._render()
        self.assertIn('class="marker-icon-glyph"', svg_str)
        self.assertIn('class="marker-icon-chip"', svg_str)
        # The rack glyph uses the "server" icon.
        self.assertIn(ICON_GLYPHS["server"][0], svg_str)

    def test_device_role_variant_icon(self):
        """A device whose role reads as a camera renders the camera glyph; unmapped roles fall back."""
        camera_role = Role.objects.create(name="Security Camera")
        camera_role.content_types.add(ContentType.objects.get_for_model(Device))
        camera = Device.objects.create(
            name="Cam1", status=self.status, device_type=self.device_type, role=camera_role, location=self.floor
        )
        self._place(device=camera)
        svg_str = self._render()
        self.assertIn(ICON_GLYPHS["camera"][1], svg_str)  # camera lens path

        # A device with the generic prerequisite role (unmapped) uses the base "cpu" glyph.
        plain = Device.objects.create(
            name="Dev1", status=self.status, device_type=self.device_type, role=self.device_role, location=self.floor
        )
        self._place(device=plain)
        self.assertIn(ICON_GLYPHS["cpu"][0], self._render())

    def test_unregistered_type_renders_fallback_without_error(self):
        """A placed object of an unregistered type renders the help glyph and does not 500."""
        tenant = Tenant.objects.create(name="Acme")
        tile = models.FloorPlanTile(
            floor_plan=self.plan,
            status=self.status,
            x_origin=self._next_cell,
            y_origin=1,
            pos_x=0.5,
            pos_y=0.5,
            width=0.1,
            height=0.1,
            placed_content_type=ContentType.objects.get_for_model(Tenant),
            placed_object_id=tenant.pk,
        )
        # Insert without validation to simulate a type that was registered when placed but has since
        # been removed (a live placement of an unregistered type is rejected by validation in G3).
        tile.save()
        svg_str = self._render()
        self.assertIn(ICON_GLYPHS["help"][0], svg_str)

    def test_legend_shown_for_multiple_types_and_suppressed_for_one(self):
        """The legend appears only when two or more distinct types are present."""
        self._place(rack=Rack.objects.create(name="LegRack", status=self.status, location=self.floor))
        self.assertNotIn('class="legend-bg"', self._render())  # single type

        panel = PowerPanel.objects.create(name="LegPanel", location=self.floor)
        self._place(power_panel=panel)
        self.assertIn('class="legend-bg"', self._render())  # two types


class TestGlyphAndColorResolvers(TestCase):
    """Runtime-configurable types: glyph-as-data + per-object glyph/color resolvers."""

    def setUp(self):
        data = create_prerequisites(floor_count=1)
        self.status = data["status"]
        self.floor = data["floors"][0]
        self.user = User.objects.create(username="glyphuser", is_superuser=True)
        self.plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=5, y_size=5, x_origin_seed=1, y_origin_seed=1,
            placement_mode=PlacementModeChoices.FREEFORM,
        )
        self.svg = svg.FloorPlanSVG(floor_plan=self.plan, user=self.user, base_url="http://testserver")

    def test_resolve_glyph_builtin_and_custom(self):
        self.assertEqual(resolve_glyph("server"), (ICON_GLYPHS["server"], ICON_VIEWBOX))
        self.assertEqual(resolve_glyph("server", custom_paths=["M1 1 h2"], viewbox=32), (["M1 1 h2"], 32))

    def test_effective_glyph_static_data(self):
        placement = PlacementType(key="x.y", label="Y", glyph_paths_data=["M2 2 h4"], glyph_viewbox=48)
        self.assertEqual(self.svg._effective_glyph(placement, obj=object()), (["M2 2 h4"], 48))

    def test_effective_glyph_per_object_resolver(self):
        placement = PlacementType(key="x.y", label="Y", glyph_resolver=lambda o: (["M3 3 h5"], 24))
        self.assertEqual(self.svg._effective_glyph(placement, obj=object())[0], ["M3 3 h5"])

    def test_effective_glyph_resolver_failure_falls_back_to_static(self):
        def boom(_obj):
            raise ValueError("nope")

        placement = PlacementType(key="x.y", label="Y", icon="server", glyph_resolver=boom)
        self.assertEqual(self.svg._effective_glyph(placement, obj=object())[0], ICON_GLYPHS["server"])

    def test_effective_type_color_resolver_and_fallback(self):
        placement = PlacementType(key="x.y", label="Y", color="111111", color_resolver=lambda o: "abcdef")
        self.assertEqual(self.svg._effective_type_color(placement, obj=object()), "abcdef")

        def boom(_obj):
            raise ValueError()

        placement2 = PlacementType(key="x.y", label="Y", color="111111", color_resolver=boom)
        self.assertEqual(self.svg._effective_type_color(placement2, obj=object()), "111111")

    def test_custom_glyph_renders_in_svg_output(self):
        """A type registered with glyph-as-data renders its custom path in the SVG (marker + legend)."""
        marker = "M1 2 h9 v9 h-9 z"  # distinctive, collision-unlikely path
        registry.register(
            "dcim.rack", label="Rack", icon="server", color="6c757d",
            legend_order=10, glyph_paths_data=[marker], replace=True,
        )
        # render() calls refresh_if_stale(); if a prior test bumped the DB-config version this would
        # restore_base() and wipe the manual registration above. Align the applied version so the
        # lazy refresh is a no-op and we render exactly the glyph-as-data we just registered.
        registry.applied_config_version = current_config_version()
        try:
            rack = Rack.objects.create(name="GlyphRack", status=self.status, location=self.floor)
            models.FloorPlanTile(
                floor_plan=self.plan, status=self.status, rack=rack, x_origin=1, y_origin=1, pos_x=0.5, pos_y=0.5,
            ).validated_save()
            svg_str = self.plan.get_svg(user=self.user, base_url="http://testserver").tostring()
            self.assertIn(marker, svg_str)
        finally:
            # Restore the builtin so other tests see the stock registration.
            registry.register(
                "dcim.rack", label="Rack", icon="server", color="6c757d", legend_order=10, replace=True,
            )
