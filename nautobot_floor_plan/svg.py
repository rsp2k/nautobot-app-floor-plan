"""Render a FloorPlan as an SVG image."""

import base64
import json
import logging
import math
import mimetypes
import os
from dataclasses import dataclass

import svgwrite
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.http import urlencode
from nautobot.core.templatetags.helpers import fgcolor
from nautobot.dcim.models import Device, PowerFeed, PowerPanel, Rack

from nautobot_floor_plan.choices import AllocationTypeChoices, ObjectOrientationChoices, PlacementModeChoices
from nautobot_floor_plan.placement import registry
from nautobot_floor_plan.placement.icons import ICON_VIEWBOX, glyph_paths
from nautobot_floor_plan.templatetags.seed_helpers import render_axis_origin

logger = logging.getLogger(__name__)


@dataclass
class TextElement:
    """Container for text element parameters."""

    text: str
    line_offset: int
    class_name: str
    color: str


class FloorPlanSVG:  # pylint: disable=too-many-instance-attributes
    """Use this class to render a FloorPlan as an SVG image."""

    BORDER_WIDTH = 10
    CORNER_RADIUS = 6
    TILE_INSET = 2
    TEXT_LINE_HEIGHT = 16
    GRID_OFFSET = 26
    OBJECT_INSETS = (3 * TILE_INSET, 3 * TILE_INSET + TEXT_LINE_HEIGHT)
    OBJECT_PADDING = 4
    OBJECT_TILE_INSET = 3
    OBJECT_FRONT_DEPTH = 15
    OBJECT_BUTTON_OFFSET = 5
    OBJECT_ORIENTATION_OFFSET = 14
    RACKGROUP_TEXT_OFFSET = 12
    Y_LABEL_TEXT_OFFSET = 34
    # Fallback normalized footprint for a freeform object whose width/height are unset.
    DEFAULT_MARKER_FRAC = 0.04
    # Per-type marker icon sizing and legend layout.
    ICON_MIN = 18
    ICON_MAX = 44
    ICON_FOOTPRINT_FRAC = 0.55
    CHIP_PAD = 4
    LEGEND_ROW_H = 22
    LEGEND_ICON = 14
    LEGEND_WIDTH = 168

    def __init__(self, *, floor_plan, user, base_url, request=None):
        """
        Initialize a FloorPlanSVG.

        Args:
            floor_plan (FloorPlan): FloorPlan to render
            user (User): User making this request
            base_url (str): Server URL, needed to prepend to URLs included in the rendered SVG.
            request (HttpRequest): The current request object
        """
        self.floor_plan = floor_plan
        self.user = user
        self.base_url = base_url.rstrip("/")
        self.request = request
        self._present_types = {}
        self.add_url = self.base_url + reverse("plugins:nautobot_floor_plan:floorplantile_add")
        self.return_url = (
            reverse("plugins:nautobot_floor_plan:location_floor_plan_tab", kwargs={"pk": self.floor_plan.location.pk})
            + "?tab=nautobot_floor_plan:1"
        )

    @cached_property
    def GRID_SIZE_X(self):  # pylint: disable=invalid-name
        """Grid spacing in the X (width) dimension."""
        return max(150, (150 * self.floor_plan.tile_width) // self.floor_plan.tile_depth)

    @cached_property
    def GRID_SIZE_Y(self):  # pylint: disable=invalid-name
        """Grid spacing in the Y (depth) dimension."""
        return max(150, (150 * self.floor_plan.tile_depth) // self.floor_plan.tile_width)

    @property
    def is_freeform(self):
        """Whether this floor plan positions objects by freeform coordinates."""
        return self.floor_plan.placement_mode == PlacementModeChoices.FREEFORM

    def _is_freeform_tile(self, tile):
        """A tile that should be rendered via the freeform path (positioned, in a freeform plan)."""
        return self.is_freeform and tile.pos_x is not None and tile.pos_y is not None

    @cached_property
    def content_rect(self):
        """The grid drawing area in SVG user units: (x, y, w, h).

        This single rectangle is the normalization basis for both freeform object positions and the
        blueprint calibration. It is always defined, independent of whether a blueprint is present.
        """
        return (
            self.GRID_OFFSET,
            self.GRID_OFFSET,
            self.floor_plan.x_size * self.GRID_SIZE_X,
            self.floor_plan.y_size * self.GRID_SIZE_Y,
        )

    @cached_property
    def _background_data_uri(self):
        """Base64 data URI for the blueprint image, or None.

        Embedded (not URL-referenced) so the rendered SVG is self-contained for the "Save SVG"
        download and cross-context fetch. This inflates the payload ~1.33x; upload size is bounded
        by the form.
        """
        image = self.floor_plan.background_image
        if not image:
            return None
        try:
            with image.open("rb") as handle:
                raw = handle.read()
        except (OSError, ValueError) as error:
            logger.warning("Could not read background image for %s: %s", self.floor_plan, error)
            return None
        mime = mimetypes.guess_type(image.name)[0] or "image/png"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _background_rect(self):
        """Resolve the blueprint placement rectangle.

        Returns (user_rect, norm_rect, autofit) where user_rect is (x, y, w, h) in SVG user units,
        norm_rect is the same rectangle normalized to the content rect (always concrete numbers, even
        when auto-fitting, so the calibrate overlay has a rectangle to attach to), and autofit is True
        when the placement was derived rather than user-calibrated.
        """
        cx, cy, cw, ch = self.content_rect
        fp = self.floor_plan
        calibrated = None not in (fp.bg_x, fp.bg_y, fp.bg_width, fp.bg_height)
        if calibrated:
            norm = (fp.bg_x, fp.bg_y, fp.bg_width, fp.bg_height)
        else:
            norm = self._autofit_norm_rect(cw, ch)
        nx, ny, nw, nh = norm
        user_rect = (cx + nx * cw, cy + ny * ch, nw * cw, nh * ch)
        return user_rect, norm, not calibrated

    def _autofit_norm_rect(self, content_w, content_h):
        """Aspect-correct letterbox of the image inside the content rect, normalized to it."""
        img_w = self.floor_plan.background_image_width
        img_h = self.floor_plan.background_image_height
        if not img_w or not img_h or content_w <= 0 or content_h <= 0:
            # Pixel dimensions unknown: fill the content rect (rendering falls back to a
            # non-distorting preserveAspectRatio in that case).
            return (0.0, 0.0, 1.0, 1.0)
        content_aspect = content_w / content_h
        img_aspect = img_w / img_h
        if img_aspect > content_aspect:
            # Image is relatively wider: full width, letterbox vertically.
            nw, nh = 1.0, content_aspect / img_aspect
            return (0.0, (1.0 - nh) / 2, nw, nh)
        # Image is relatively taller: full height, pillarbox horizontally.
        nw, nh = img_aspect / content_aspect, 1.0
        return ((1.0 - nw) / 2, 0.0, nw, nh)

    @staticmethod
    def _rotated_bounds(x, y, w, h, degrees):
        """Axis-aligned bounds (min_x, min_y, max_x, max_y) of a rectangle rotated about its center."""
        if not degrees:
            return (x, y, x + w, y + h)
        cx, cy = x + w / 2, y + h / 2
        rad = math.radians(degrees)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        xs, ys = [], []
        for corner_x, corner_y in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
            dx, dy = corner_x - cx, corner_y - cy
            xs.append(cx + dx * cos_a - dy * sin_a)
            ys.append(cy + dx * sin_a + dy * cos_a)
        return (min(xs), min(ys), max(xs), max(ys))

    def _drawing_extents(self, default_width, default_depth):
        """Compute the viewBox (x, y, w, h) enclosing the grid frame, blueprint, and freeform markers.

        With no blueprint and no freeform markers this returns exactly (0, 0, default_width,
        default_depth), keeping grid-mode output identical to prior behavior.
        """
        min_x, min_y, max_x, max_y = 0.0, 0.0, float(default_width), float(default_depth)
        expanded = False

        if self._background_data_uri and self.floor_plan.background_opacity > 0:
            (bx, by, bw, bh), _norm, _autofit = self._background_rect()
            rb = self._rotated_bounds(bx, by, bw, bh, self.floor_plan.bg_rotation)
            min_x, min_y = min(min_x, rb[0]), min(min_y, rb[1])
            max_x, max_y = max(max_x, rb[2]), max(max_y, rb[3])
            expanded = True

        cx, cy, cw, ch = self.content_rect
        for tile in self.floor_plan.tiles.all():
            if not self._is_freeform_tile(tile):
                continue
            center_x = cx + tile.pos_x * cw
            center_y = cy + tile.pos_y * ch
            pw = (tile.width if tile.width is not None else self.DEFAULT_MARKER_FRAC) * cw
            ph = (tile.height if tile.height is not None else self.DEFAULT_MARKER_FRAC) * ch
            rb = self._rotated_bounds(center_x - pw / 2, center_y - ph / 2, pw, ph, tile.rotation or 0)
            min_x, min_y = min(min_x, rb[0]), min(min_y, rb[1])
            max_x, max_y = max(max_x, rb[2]), max(max_y, rb[3])
            expanded = True

        if expanded:
            min_x, min_y = min_x - self.BORDER_WIDTH, min_y - self.BORDER_WIDTH
            max_x, max_y = max_x + self.BORDER_WIDTH, max_y + self.BORDER_WIDTH
        return (min_x, min_y, max_x - min_x, max_y - min_y)

    def _setup_drawing(self, width, depth, viewbox=None):
        """Initialize an appropriate svgwrite.Drawing instance."""
        vx, vy, vw, vh = viewbox if viewbox is not None else (0, 0, width, depth)
        # Intrinsic size matches the viewBox so the inline SVG is not letterbox-scaled before JS mounts.
        drawing = svgwrite.Drawing(size=(vw, vh), debug=False)
        drawing.viewbox(vx, vy, width=vw, height=vh)
        # Publish the content rect so the interactive layer shares the server's normalization basis.
        content_x, content_y, content_w, content_h = self.content_rect
        drawing["data-content-x"] = content_x
        drawing["data-content-y"] = content_y
        drawing["data-content-w"] = content_w
        drawing["data-content-h"] = content_h
        # A11y root semantics. role starts "group" so a reading screen-reader user keeps the virtual
        # cursor over the marker labels; the JS mode machine swaps to role="application" in place/
        # calibrate where Arrow keys must reach our handlers. No tabindex on the root: the single tab
        # stop is the active roving marker (its <g> carries tabindex="0").
        loc_name = getattr(getattr(self.floor_plan, "location", None), "name", "") or ""
        drawing["role"] = "group"
        drawing["aria-roledescription"] = "Floor plan"
        drawing["aria-label"] = ("Floor plan for %s" % loc_name).strip()

        # Get theme from request cookies if available
        theme = self.request.COOKIES.get("theme", "light") if self.request else "light"
        css_filename = "dark_svg.css" if theme == "dark" else "svg.css"
        logger.debug("Using CSS file: %s for theme: %s", css_filename, theme)

        # Add our custom stylesheet
        with open(
            os.path.join(os.path.dirname(__file__), "static", "nautobot_floor_plan", "css", css_filename),
            "r",
            encoding="utf-8",
        ) as css_file:
            drawing.defs.add(drawing.style(css_file.read()))

        border_offset = self.BORDER_WIDTH / 2
        drawing.add(
            drawing.rect(
                insert=(border_offset, border_offset),
                size=(
                    self.floor_plan.x_size * self.GRID_SIZE_X + self.GRID_OFFSET + self.BORDER_WIDTH,
                    self.floor_plan.y_size * self.GRID_SIZE_Y + self.GRID_OFFSET + self.BORDER_WIDTH,
                ),
                class_="frame",
            )
        )

        return drawing

    def _draw_tile_link(self, drawing, axis):
        """Draw a '+' link for adding a new tile at the specified grid position."""
        query_params = urlencode(
            {
                "floor_plan": self.floor_plan.pk,
                "x_origin": axis["x"],
                "y_origin": axis["y"],
                "return_url": self.return_url,
            }
        )
        add_url = f"{self.add_url}?{query_params}"
        add_link = drawing.add(drawing.a(href=add_url, target="_top"))

        # Use grid indices for positioning
        x_pos = axis["x_idx"]
        y_pos = axis["y_idx"]

        add_link.add(
            drawing.rect(
                (
                    (x_pos + 0.5) * self.GRID_SIZE_X + self.GRID_OFFSET - (self.TEXT_LINE_HEIGHT / 2),
                    (y_pos + 0.5) * self.GRID_SIZE_Y + self.GRID_OFFSET - (self.TEXT_LINE_HEIGHT / 2),
                ),
                (self.TEXT_LINE_HEIGHT, self.TEXT_LINE_HEIGHT),
                class_="add-tile-button",
                rx=self.CORNER_RADIUS,
            )
        )
        add_link.add(
            drawing.text(
                "+",
                insert=(
                    (x_pos + 0.5) * self.GRID_SIZE_X + self.GRID_OFFSET,
                    (y_pos + 0.5) * self.GRID_SIZE_Y + self.GRID_OFFSET,
                ),
                class_="button-text",
            )
        )

    def _draw_grid(self, drawing):
        """Render the grid underlying all tiles."""
        self._draw_grid_lines(drawing)
        x_labels, y_labels = self._generate_axis_labels()
        self._draw_axis_labels(drawing, x_labels, y_labels)
        self._draw_tile_links(drawing, x_labels, y_labels)

    def _draw_grid_lines(self, drawing):
        """Draw the vertical and horizontal grid lines."""
        for x in range(0, self.floor_plan.x_size + 1):
            drawing.add(
                drawing.line(
                    start=(x * self.GRID_SIZE_X + self.GRID_OFFSET, self.GRID_OFFSET),
                    end=(
                        x * self.GRID_SIZE_X + self.GRID_OFFSET,
                        self.floor_plan.y_size * self.GRID_SIZE_Y + self.GRID_OFFSET,
                    ),
                    class_="grid",
                )
            )
        for y in range(0, self.floor_plan.y_size + 1):
            drawing.add(
                drawing.line(
                    start=(self.GRID_OFFSET, y * self.GRID_SIZE_Y + self.GRID_OFFSET),
                    end=(
                        self.floor_plan.x_size * self.GRID_SIZE_X + self.GRID_OFFSET,
                        y * self.GRID_SIZE_Y + self.GRID_OFFSET,
                    ),
                    class_="grid",
                )
            )

    def _generate_axis_labels(self):
        """Generate labels for the X and Y axes."""
        x_labels = self.floor_plan.generate_labels("X", self.floor_plan.x_size)
        y_labels = self.floor_plan.generate_labels("Y", self.floor_plan.y_size)
        return x_labels, y_labels

    def _draw_axis_labels(self, drawing, x_labels, y_labels):
        """Draw labels on the X and Y axes with clickable links to rack elevations."""
        # Create X-axis labels (column labels)
        for idx, label in enumerate(x_labels):
            # Create filter URL for racks in this row
            filter_params = urlencode(
                {
                    "nautobot_floor_plan_floor_plan": self.floor_plan.pk,
                    "nautobot_floor_plan_tile_x_origin": label,  # Pass the label instead of the numeric position
                }
            )
            rack_url = f"{self.base_url}/dcim/rack-elevations/?{filter_params}"

            # Create clickable link
            link = drawing.add(drawing.a(href=rack_url, target="_top"))
            link.add(
                drawing.text(
                    label,
                    insert=(
                        (idx + 0.5) * self.GRID_SIZE_X + self.GRID_OFFSET,
                        self.BORDER_WIDTH + self.TEXT_LINE_HEIGHT / 2,
                    ),
                    class_="grid-label clickable-label",
                )
            )

        # Create Y-axis labels (row labels)
        max_y_length = max(len(str(label)) for label in y_labels)
        y_label_text_offset = self._calculate_y_label_offset(max_y_length)

        for idx, label in enumerate(y_labels):
            # Create filter URL for racks in this row
            filter_params = urlencode(
                {
                    "nautobot_floor_plan_floor_plan": self.floor_plan.pk,
                    "nautobot_floor_plan_tile_y_origin": label,  # Pass the label instead of the numeric position
                }
            )
            rack_url = f"{self.base_url}/dcim/rack-elevations/?{filter_params}"

            # Create clickable link
            link = drawing.add(drawing.a(href=rack_url, target="_top"))
            link.add(
                drawing.text(
                    label,
                    insert=(
                        self.BORDER_WIDTH + self.TEXT_LINE_HEIGHT / 2 - y_label_text_offset,
                        (idx + 0.5) * self.GRID_SIZE_Y + self.GRID_OFFSET,
                    ),
                    class_="grid-label clickable-label",
                )
            )

    def _calculate_y_label_offset(self, max_y_length):
        """Calculate the offset for Y-axis labels."""
        # Add prefix length for binary (0b) and hex (0x) labels when calculating max length
        adjusted_length = max_y_length
        if str(self.floor_plan.y_origin_seed).startswith(("0b", "0x")):
            adjusted_length = max_y_length + 2
        # Base offset calculation
        base_offset = (
            self.Y_LABEL_TEXT_OFFSET - (6 - len(str(self.floor_plan.y_origin_seed))) if adjusted_length > 1 else 0
        )
        # Calculate additional offset
        # Add 1 to additional offset for 02WW scenario
        if adjusted_length == 4:
            adjusted_length = adjusted_length + 1
        if adjusted_length > 4:
            # Add 10 for each increment of 2 beyond 4 and handle odd cases
            additional_offset = ((adjusted_length - 4 + 1) // 2) * 10
        else:
            additional_offset = 0
        return base_offset + additional_offset

    def _draw_tile_links(self, drawing, x_labels, y_labels):
        """Draw links for each tile in the grid."""
        for y_idx, y_label in enumerate(y_labels):
            for x_idx, x_label in enumerate(x_labels):
                try:
                    axis = {"x": x_label, "y": y_label, "x_idx": x_idx, "y_idx": y_idx}
                    self._draw_tile_link(drawing, axis)
                except (ValueError, TypeError) as e:
                    logger.warning("Error processing grid position (%s, %s): %s", x_idx, y_idx, e)
                    continue

    def _draw_tile(self, drawing, tile):
        """Render an individual FloorPlanTile to the drawing."""
        # Draw defined rackgroup tile and status tiles
        self._draw_defined_rackgroup_tile(drawing, tile)
        # Add buttons for editing and deleting the group tile definition
        if tile.on_group_tile is False:
            self._draw_edit_delete_button(drawing, tile, 0, 0)
        # Draw tiles that contain objects
        if any([tile.rack, tile.device, tile.power_panel, tile.power_feed]):
            self._draw_object_tile(drawing, tile)

    # Draw a outline of status and Rackgroup
    def _draw_underlay_tiles(self, drawing, tile):
        """Render a tile based on its Status."""
        # If a tile is a rackgroup or status tile with no installed racks
        # or if a tile is a single Rackgroup tile with a rack installed
        if (tile.allocation_type == AllocationTypeChoices.RACKGROUP) or tile.on_group_tile is False:
            origin = (
                (tile.x_origin - self.floor_plan.x_origin_seed) * self.GRID_SIZE_X + self.GRID_OFFSET + self.TILE_INSET,
                (tile.y_origin - self.floor_plan.y_origin_seed) * self.GRID_SIZE_Y + self.GRID_OFFSET + self.TILE_INSET,
            )
            # Draw the tile outline and fill it with its status color
            drawing.add(
                drawing.rect(
                    origin,
                    (
                        self.GRID_SIZE_X * tile.x_size - self.TILE_INSET * self.TILE_INSET,
                        self.GRID_SIZE_Y * tile.y_size - self.TILE_INSET * self.TILE_INSET,
                    ),
                    rx=self.CORNER_RADIUS,
                    style=f"fill: #{tile.status.color}",
                    class_="tile-status",
                )
            )

    def _draw_defined_rackgroup_tile(self, drawing, tile):
        """Add Status and RackGroup text to a rendered tile."""
        origin = (
            (tile.x_origin - self.floor_plan.x_origin_seed) * self.GRID_SIZE_X + self.GRID_OFFSET + self.TILE_INSET,
            (tile.y_origin - self.floor_plan.y_origin_seed) * self.GRID_SIZE_Y + self.GRID_OFFSET + self.TILE_INSET,
        )
        if tile.on_group_tile is False:
            # Add text at the top of the tile labeling the status
            detail_url = self.base_url + reverse("plugins:nautobot_floor_plan:floorplantile", kwargs={"pk": tile.pk})
            detail_link = drawing.add(drawing.a(href=detail_url + "?tab=main", target="_top"))
            detail_link.add(
                drawing.text(
                    tile.status.name,
                    insert=(
                        origin[0] + (tile.x_size * self.GRID_SIZE_X) / 2,
                        origin[1] + self.TILE_INSET + self.TEXT_LINE_HEIGHT / 2,
                    ),
                    class_="label-text",
                    style=f"fill: {fgcolor(tile.status.color)}",
                )
            )
        # Add text at the top of the tile labeling the rackgroup if defined
        if tile.allocation_type == AllocationTypeChoices.RACKGROUP and tile.rack_group is not None:
            detail_link.add(
                drawing.text(
                    tile.rack_group.name,
                    insert=(
                        origin[0] + (tile.x_size * self.GRID_SIZE_X) / 2,
                        origin[1] + self.TILE_INSET + self.TEXT_LINE_HEIGHT / 2 + self.RACKGROUP_TEXT_OFFSET,
                    ),
                    class_="label-text",
                    style=f"fill: {fgcolor(tile.status.color)}",
                )
            )

    def _draw_object_tile(self, drawing, tile):
        """Draw a generic object tile with appropriate styling and information."""
        origin = (
            (tile.x_origin - self.floor_plan.x_origin_seed) * self.GRID_SIZE_X + self.GRID_OFFSET,
            (tile.y_origin - self.floor_plan.y_origin_seed) * self.GRID_SIZE_Y + self.GRID_OFFSET,
        )

        # Determine the object
        if tile.rack is not None:
            obj = tile.rack
            obj_type = "rack"
            obj_id = obj.pk
        elif tile.device is not None:
            obj = tile.device
            obj_type = "device"
            obj_id = obj.pk
        elif tile.power_panel is not None:
            obj = tile.power_panel
            obj_type = "powerpanel"
            obj_id = obj.pk
        elif tile.power_feed is not None:
            obj = tile.power_feed
            obj_type = "powerfeed"
            obj_id = obj.pk
        else:
            return  # No object to draw

        obj_url = reverse("dcim:" + obj_type, kwargs={"pk": obj_id})
        obj_url = f"{self.base_url}{obj_url}"

        # Create the link with enhanced attributes for highlighting
        link = drawing.add(
            drawing.a(
                href=obj_url,
                target="_top",
                id=f"{obj_type}-{obj_id}",
            )
        )

        # Draw the main object rectangle
        link.add(
            drawing.rect(
                (origin[0] + self.OBJECT_INSETS[0], origin[1] + self.OBJECT_INSETS[1] + self.OBJECT_PADDING),
                (
                    tile.x_size * self.GRID_SIZE_X - self.TILE_INSET * self.OBJECT_INSETS[0],
                    tile.y_size * self.GRID_SIZE_Y - self.OBJECT_INSETS[1] - self.BORDER_WIDTH * self.TILE_INSET,
                ),
                rx=self.CORNER_RADIUS,
                class_="object",
                style=f"fill: #{obj.status.color if hasattr(obj, 'status') else tile.status.color}; ",
            )
        )

        # Draw orientation indicator for any object type if orientation is set
        if tile.object_orientation:
            self._draw_object_orientation(drawing, tile, link, origin)

        # Add the object text
        self._draw_object_text(drawing, tile, link, origin)

        # Add buttons for editing and deleting the tile definition
        if tile.on_group_tile:
            self._draw_edit_delete_button(drawing, tile, self.OBJECT_BUTTON_OFFSET, self.GRID_OFFSET)

    def _draw_object_orientation(self, drawing, tile, link, origin):
        """Draw the object orientation indicator."""
        if tile.object_orientation == ObjectOrientationChoices.UP:
            link.add(
                drawing.rect(
                    (origin[0] + self.OBJECT_INSETS[0], origin[1] + self.OBJECT_INSETS[1]),
                    (
                        tile.x_size * self.GRID_SIZE_X - 2 * self.OBJECT_INSETS[0],
                        self.OBJECT_FRONT_DEPTH,
                    ),
                    rx=self.CORNER_RADIUS,
                    class_="object-orientation",
                )
            )
        elif tile.object_orientation == ObjectOrientationChoices.DOWN:
            link.add(
                drawing.rect(
                    (
                        origin[0] + self.OBJECT_INSETS[0],
                        origin[1]
                        + tile.y_size * self.GRID_SIZE_Y
                        - self.OBJECT_TILE_INSET * self.TILE_INSET
                        - self.OBJECT_FRONT_DEPTH,
                    ),
                    (
                        tile.x_size * self.GRID_SIZE_X - 2 * self.OBJECT_INSETS[0],
                        self.OBJECT_FRONT_DEPTH,
                    ),
                    rx=self.CORNER_RADIUS,
                    class_="object-orientation",
                )
            )
        elif tile.object_orientation == ObjectOrientationChoices.LEFT:
            link.add(
                drawing.rect(
                    (origin[0] + self.OBJECT_INSETS[0], origin[1] + self.OBJECT_INSETS[1] + self.OBJECT_TILE_INSET),
                    (
                        self.OBJECT_FRONT_DEPTH,
                        tile.y_size * self.GRID_SIZE_Y
                        - self.OBJECT_INSETS[1]
                        - 2 * self.TILE_INSET
                        - self.OBJECT_ORIENTATION_OFFSET,
                    ),
                    rx=self.CORNER_RADIUS,
                    class_="object-orientation",
                )
            )
        elif tile.object_orientation == ObjectOrientationChoices.RIGHT:
            link.add(
                drawing.rect(
                    (
                        origin[0] + tile.x_size * self.GRID_SIZE_X - self.OBJECT_INSETS[0] - self.OBJECT_FRONT_DEPTH,
                        origin[1] + self.OBJECT_INSETS[1] + self.OBJECT_TILE_INSET,
                    ),
                    (
                        self.OBJECT_FRONT_DEPTH,
                        tile.y_size * self.GRID_SIZE_Y
                        - self.OBJECT_INSETS[1]
                        - 2 * self.TILE_INSET
                        - self.OBJECT_ORIENTATION_OFFSET,
                    ),
                    rx=self.CORNER_RADIUS,
                    class_="object-orientation",
                )
            )

    def _draw_object_text(self, drawing, tile, link, origin):
        """Draw basic object information and add tooltip data."""
        obj = None
        obj_type = None

        if tile.rack is not None:
            obj = tile.rack
            obj_type = "Rack"
        elif tile.device is not None:
            obj = tile.device
            obj_type = "Device"
        elif tile.power_panel is not None:
            obj = tile.power_panel
            obj_type = "Power Panel"
        elif tile.power_feed is not None:
            obj = tile.power_feed
            obj_type = "Power Feed"

        if obj is None:
            return

        # Add basic text (name and type)
        self._add_text_element(
            drawing,
            TextElement(
                text=obj.name,
                line_offset=-1,
                class_name="label-text-primary",
                color=obj.status.color if hasattr(obj, "status") else tile.status.color,
            ),
            origin,
            tile,
        )

        self._add_text_element(
            drawing,
            TextElement(
                text=obj_type,
                line_offset=1,
                class_name="label-text",
                color=obj.status.color if hasattr(obj, "status") else tile.status.color,
            ),
            origin,
            tile,
        )
        # When Zooming in to a highlighted object on large floor plans the labels are not visible.
        # Adding the labels to the Tiles will make it easier to see where they are located.
        # Use render_axis_origin to retrieve the converted labels for x and y origins
        x_label = render_axis_origin(tile, "X")
        y_label = render_axis_origin(tile, "Y")

        # Display grid coordinates using the converted labels
        grid_coordinates = f"({x_label}, {y_label})"

        self._add_text_element(
            drawing,
            TextElement(
                text=grid_coordinates,
                line_offset=2,  # This will position it below the type text
                class_name="label-text-grid",
                color=obj.status.color if hasattr(obj, "status") else tile.status.color,
            ),
            origin,
            tile,
        )
        # Add tooltip data
        tooltip_data = self._get_tooltip_data(obj, obj_type)
        # Add tooltip data to the link element using proper SVG attribute setting
        link["data-tooltip"] = json.dumps(tooltip_data)
        link["class"] = "object-tooltip"

    def _get_tooltip_data(self, obj, obj_type):
        """Generate tooltip data based on object type."""
        data = {
            "Name": obj.name,
            "Type": obj_type,
        }

        # Add status if available
        if hasattr(obj, "status"):
            data["Status"] = obj.status.name

        # Add type-specific information
        if isinstance(obj, Rack):
            ru_used, ru_total = obj.get_utilization()
            data.update(
                {
                    "Utilization": f"{ru_used} / {ru_total} RU",
                    "Tenant": obj.tenant.name if obj.tenant else None,
                    "Tenant_group": obj.tenant.tenant_group.name if obj.tenant and obj.tenant.tenant_group else None,
                    "Rack_group": obj.rack_group.name if obj.rack_group else None,
                }
            )

        elif isinstance(obj, Device):
            data.update(
                {
                    "Manufacturer": obj.device_type.manufacturer.name,
                    "Model": obj.device_type.model,
                    "Serial": obj.serial if obj.serial else None,
                    "Asset_tag": obj.asset_tag if obj.asset_tag else None,
                }
            )

        elif isinstance(obj, PowerPanel):
            power_feeds = obj.power_feeds.all()
            data.update(
                {
                    "Feeds": [pf.name for pf in power_feeds],
                    "Rack_group": obj.rack_group.name if obj.rack_group else None,
                }
            )

        elif isinstance(obj, PowerFeed):
            data.update(
                {
                    "Panel": obj.power_panel.name,
                    "Voltage": f"{obj.voltage}V" if obj.voltage else None,
                    "Amperage": f"{obj.amperage}A" if obj.amperage else None,
                    "Phase": f"{obj.phase}-phase" if obj.phase else None,
                }
            )

        # Remove None values
        return {k: v for k, v in data.items() if v is not None}

    def _add_text_element(self, drawing, text_element: TextElement, origin, tile):
        """Helper method to add a text element with consistent positioning."""
        drawing.add(
            drawing.text(
                text_element.text,
                insert=(
                    origin[0] + (tile.x_size * self.GRID_SIZE_X) / 2,
                    origin[1]
                    + (tile.y_size * self.GRID_SIZE_Y) / 2
                    + (self.TEXT_LINE_HEIGHT * text_element.line_offset),
                ),
                class_=text_element.class_name,
                style=f"fill: {fgcolor(text_element.color)}",
            )
        )

    def _draw_edit_delete_button(self, drawing, tile, button_offset, grid_offset):
        """Draw edit and delete buttons for a tile."""
        if tile.allocation_type == AllocationTypeChoices.OBJECT:
            tile_inset = 0
        else:
            tile_inset = self.TILE_INSET

        origin = (
            (tile.x_origin - self.floor_plan.x_origin_seed) * self.GRID_SIZE_X + self.GRID_OFFSET + tile_inset,
            (tile.y_origin - self.floor_plan.y_origin_seed) * self.GRID_SIZE_Y + self.GRID_OFFSET + tile_inset,
        )

        # Add a button for editing the tile definition
        edit_url = reverse("plugins:nautobot_floor_plan:floorplantile_edit", kwargs={"pk": tile.pk})
        query_params = urlencode({"return_url": self.return_url})
        edit_url = f"{self.base_url}{edit_url}?{query_params}"
        link = drawing.add(drawing.a(href=edit_url, target="_top"))
        link.add(
            drawing.rect(
                (origin[0] + self.TILE_INSET + button_offset, origin[1] + self.TILE_INSET + grid_offset),
                (self.TEXT_LINE_HEIGHT, self.TEXT_LINE_HEIGHT),
                class_="edit-tile-button",
                rx=self.CORNER_RADIUS,
            )
        )
        link.add(
            drawing.text(
                "✎",
                insert=(
                    origin[0] + self.TILE_INSET + self.TEXT_LINE_HEIGHT / 2 + button_offset,
                    origin[1] + self.TILE_INSET + self.TEXT_LINE_HEIGHT / 2 + grid_offset,
                ),
                class_="button-text",
            )
        )

        # Add a button for deleting the tile definition
        delete_url = reverse("plugins:nautobot_floor_plan:floorplantile_delete", kwargs={"pk": tile.pk})
        query_params = urlencode({"return_url": self.return_url})
        delete_url = f"{self.base_url}{delete_url}?{query_params}"
        link = drawing.add(drawing.a(href=delete_url, target="_top"))
        link.add(
            drawing.rect(
                (
                    origin[0]
                    + tile.x_size * self.GRID_SIZE_X
                    - self.OBJECT_TILE_INSET * self.TILE_INSET
                    - self.TEXT_LINE_HEIGHT,
                    origin[1] + self.TILE_INSET + grid_offset,
                ),
                (self.TEXT_LINE_HEIGHT, self.TEXT_LINE_HEIGHT),
                class_="delete-tile-button",
                rx=self.CORNER_RADIUS,
            )
        )
        link.add(
            drawing.text(
                "X",
                insert=(
                    origin[0]
                    + tile.x_size * self.GRID_SIZE_X
                    - self.OBJECT_TILE_INSET * self.TILE_INSET
                    - self.TEXT_LINE_HEIGHT / 2,
                    origin[1] + self.TILE_INSET + self.TEXT_LINE_HEIGHT / 2 + grid_offset,
                ),
                class_="button-text",
            )
        )

    def _draw_background_image(self, drawing):
        """Embed the blueprint image behind the grid, honoring opacity and calibration."""
        data_uri = self._background_data_uri
        if data_uri is None or self.floor_plan.background_opacity <= 0:
            return
        (bx, by, bw, bh), norm, autofit = self._background_rect()
        known_dims = bool(self.floor_plan.background_image_width and self.floor_plan.background_image_height)
        image = drawing.image(
            href=data_uri,
            insert=(bx, by),
            size=(bw, bh),
            class_="background-image",
            id="blueprint-image",
        )
        # A user-calibrated or aspect-correct auto-fit rect can safely stretch; a dimensionless image
        # would distort, so preserve its aspect ratio instead.
        if autofit and not known_dims:
            image.fit(horiz="center", vert="middle", scale="meet")
        else:
            image.stretch()
        image["opacity"] = self.floor_plan.background_opacity / 100.0
        rotation = self.floor_plan.bg_rotation
        if rotation:
            image.rotate(rotation, center=(bx + bw / 2, by + bh / 2))
        # Resolved normalized rectangle (always concrete) so the calibrate overlay has a target.
        nx, ny, nw, nh = norm
        image["data-bg-x"] = nx
        image["data-bg-y"] = ny
        image["data-bg-width"] = nw
        image["data-bg-height"] = nh
        image["data-bg-rotation"] = rotation
        image["data-bg-autofit"] = "true" if autofit else "false"
        drawing.add(image)

    def _resolve_placement(self, tile):
        """Return (obj, placement_type, color) for a tile's placed object, or None.

        Prefers the generic ``placed_object``, falling back to the legacy typed FKs during transition.
        ``placement_type`` is None when the object's type is not registered.
        """
        obj = tile.placed_object
        if obj is None:
            for candidate in (tile.rack, tile.device, tile.power_panel, tile.power_feed):
                if candidate is not None:
                    obj = candidate
                    break
        if obj is None:
            return None
        placement = registry.resolve(obj)
        obj_status = getattr(obj, "status", None)
        if obj_status is not None:
            color = obj_status.color
        elif placement is not None and placement.color:
            color = placement.color
        elif tile.status is not None:
            color = tile.status.color
        else:
            color = "6c757d"
        return obj, placement, color

    def _placement_url(self, obj, placement):
        """Build the absolute detail URL for a placed object via its registered resolver."""
        try:
            url = placement.url_resolver(obj) if placement is not None else obj.get_absolute_url()
        except Exception:  # noqa: BLE001  pylint: disable=broad-except
            url = None
        if not url:
            return "#"
        return f"{self.base_url}{url}" if url.startswith("/") else url

    def _draw_icon(self, drawing, parent, footprint, placement, color, center=(0, 0)):
        """Draw a type glyph inside a legibility chip, centered at ``center`` within a marker group."""
        size = max(self.ICON_MIN, min(self.ICON_MAX, footprint * self.ICON_FOOTPRINT_FRAC))
        half = size / 2
        pad = self.CHIP_PAD
        cx, cy = center
        parent.add(
            drawing.rect(
                insert=(cx - half - pad, cy - half - pad),
                size=(size + 2 * pad, size + 2 * pad),
                rx=self.CORNER_RADIUS,
                class_="marker-icon-chip",
            )
        )
        glyph = drawing.g(class_="marker-icon-glyph")
        glyph["transform"] = f"translate({cx - half},{cy - half}) scale({size / ICON_VIEWBOX})"
        glyph["style"] = f"stroke: #{color}"
        for path_def in glyph_paths(placement.icon if placement is not None else None):
            glyph.add(drawing.path(d=path_def, fill="none"))
        parent.add(glyph)

    def _draw_freeform_tile(self, drawing, tile):
        """Render a placed object at its freeform (normalized, center-anchored) position with rotation."""
        resolved = self._resolve_placement(tile)
        if resolved is None or tile.pos_x is None or tile.pos_y is None:
            return
        obj, placement, color = resolved
        # Track present types (None = unregistered) so the legend can be built.
        self._present_types[placement.key if placement is not None else None] = placement

        cx, cy, cw, ch = self.content_rect
        center_x = cx + tile.pos_x * cw
        center_y = cy + tile.pos_y * ch
        pw = (tile.width if tile.width is not None else self.DEFAULT_MARKER_FRAC) * cw
        ph = (tile.height if tile.height is not None else self.DEFAULT_MARKER_FRAC) * ch
        rotation = tile.rotation or 0
        label = placement.label if placement is not None else obj._meta.verbose_name.title()

        link = drawing.add(
            drawing.a(
                href=self._placement_url(obj, placement),
                target="_top",
                id=f"{obj._meta.model_name}-{obj.pk}",
            )
        )
        # The anchor is never a tab stop: roving focus lives on the inner <g role="button">. The href
        # is retained for programmatic/view-mode navigation only (JS activates it explicitly on Enter).
        link["tabindex"] = "-1"
        # Children are drawn relative to the object center so a single transform positions and rotates.
        group = drawing.g(class_="object")
        group["transform"] = f"translate({center_x},{center_y}) rotate({rotation})"
        group["data-tile-id"] = str(tile.pk)
        group["data-pos-x"] = tile.pos_x
        group["data-pos-y"] = tile.pos_y
        group["data-rotation"] = rotation
        # Roving-tabindex focusable + accessible name. tabindex="-1" by default; JS promotes the first
        # marker in reading order to "0" so Tab lands on it, and moves it as the user arrows around.
        group["role"] = "button"
        group["tabindex"] = "-1"
        group["aria-label"] = self._marker_aria_label(obj, label, tile)
        group["data-can-move"] = "true"
        # Backing rect: status fill and drag target.
        group.add(
            drawing.rect(
                insert=(-pw / 2, -ph / 2),
                size=(pw, ph),
                rx=self.CORNER_RADIUS,
                class_="object",
                style=f"fill: #{color}",
            )
        )
        icon_size = max(self.ICON_MIN, min(self.ICON_MAX, min(pw, ph) * self.ICON_FOOTPRINT_FRAC))
        self._draw_icon(drawing, group, min(pw, ph), placement, color, center=(0, -icon_size * 0.15))
        self._draw_freeform_text(drawing, group, obj, color, icon_size / 2 + self.TEXT_LINE_HEIGHT)
        # Hidden keyboard focus ring, revealed by JS toggling `.is-focused`. non-scaling-stroke keeps
        # the outline a constant screen width at every zoom; drawn last so it renders on top.
        ring_pad = min(pw, ph) * 0.12
        focus_ring = drawing.rect(
            insert=(-pw / 2 - ring_pad, -ph / 2 - ring_pad),
            size=(pw + 2 * ring_pad, ph + 2 * ring_pad),
            rx=self.CORNER_RADIUS,
            class_="focus-ring",
        )
        focus_ring["vector-effect"] = "non-scaling-stroke"
        focus_ring["aria-hidden"] = "true"
        focus_ring["pointer-events"] = "none"
        group.add(focus_ring)
        link.add(group)
        link["data-tooltip"] = json.dumps(self._get_tooltip_data(obj, label))
        link["class"] = "object-tooltip"

    def _draw_freeform_text(self, drawing, group, obj, color, y):
        """Draw the placed object's name below the icon."""
        name = getattr(obj, "name", None) or str(obj)
        group.add(
            drawing.text(
                name,
                insert=(0, y),
                class_="label-text-primary",
                style=f"fill: {fgcolor(color)}",
            )
        )

    def _marker_aria_label(self, obj, label, tile):
        """Screen-reader name for a freeform marker, e.g. 'rack-01, Rack, Active status, at 62% 40%'."""
        data = self._get_tooltip_data(obj, label)
        parts = [data.get("Name") or str(obj), label]
        status = data.get("Status")
        if status:
            parts.append(f"{status} status")
        parts.append(f"at {round((tile.pos_x or 0) * 100)}% {round((tile.pos_y or 0) * 100)}%")
        return ", ".join(str(p) for p in parts if p)

    def _draw_legend(self, drawing, viewbox):
        """Draw a legend of the placed types present, ordered by legend_order then label."""
        entries = {}
        for placement in self._present_types.values():
            if placement is None:
                entries["Unregistered"] = (10**9, "Unregistered", "help", "6c757d")
            else:
                entries[placement.label] = (
                    placement.legend_order,
                    placement.label,
                    placement.icon,
                    placement.color or "6c757d",
                )
        rows = sorted(entries.values())
        if len(rows) < 2:
            return
        vx, vy, vw, vh = viewbox
        height = len(rows) * self.LEGEND_ROW_H + 2 * self.CHIP_PAD
        x0 = vx + 10
        y0 = vy + vh - height - 10
        drawing.add(
            drawing.rect(insert=(x0, y0), size=(self.LEGEND_WIDTH, height), rx=self.CORNER_RADIUS, class_="legend-bg")
        )
        for index, (_order, label, icon, color) in enumerate(rows):
            row_cy = y0 + self.CHIP_PAD + index * self.LEGEND_ROW_H + self.LEGEND_ROW_H / 2
            glyph = drawing.g(class_="marker-icon-glyph")
            glyph["transform"] = f"translate({x0 + 10},{row_cy - self.LEGEND_ICON / 2}) scale({self.LEGEND_ICON / ICON_VIEWBOX})"
            glyph["style"] = f"stroke: #{color}"
            for path_def in glyph_paths(icon):
                glyph.add(drawing.path(d=path_def, fill="none"))
            drawing.add(glyph)
            drawing.add(
                drawing.text(label, insert=(x0 + 10 + self.LEGEND_ICON + 8, row_cy), class_="legend-label")
            )

    def render(self):
        """Generate an SVG document representing a FloorPlan."""
        logger.debug("Setting up drawing...")
        self._present_types = {}
        default_width = self.floor_plan.x_size * self.GRID_SIZE_X + self.GRID_OFFSET + self.BORDER_WIDTH * 2
        default_depth = self.floor_plan.y_size * self.GRID_SIZE_Y + self.GRID_OFFSET + self.BORDER_WIDTH * 2
        extents = self._drawing_extents(default_width, default_depth)
        drawing = self._setup_drawing(width=default_width, depth=default_depth, viewbox=extents)

        # Fetch tiles once with related objects prefetched, so resolving each placed object (a generic
        # foreign key) and its status doesn't trigger a per-tile query.
        tiles = list(
            self.floor_plan.tiles.select_related(
                "status",
                "rack_group",
                "rack",
                "rack__status",
                "device",
                "device__status",
                "device__role",
                "power_panel",
                "power_feed",
                "power_feed__status",
                "placed_content_type",
            ).prefetch_related("placed_object")
        )

        # 1. Blueprint first, so it sits behind grid and tiles.
        self._draw_background_image(drawing)

        # 2. Grid-geometry status/rackgroup underlays. Skip tiles rendered via the freeform path,
        #    otherwise a repositioned object leaves a ghost status box at its original grid cell.
        logger.debug("Rendering underlying rack_group and status tiles...")
        for tile in tiles:
            if self._is_freeform_tile(tile):
                continue
            self._draw_underlay_tiles(drawing, tile)

        # 3. Overlay the grid, unless the plan hides it in favor of the blueprint.
        if self.floor_plan.show_grid:
            logger.debug("Rendering underlying grid...")
            self._draw_grid(drawing)

        # 4. Object tiles: freeform-positioned where available, grid otherwise.
        logger.debug("Rendering tiles...")
        for tile in tiles:
            if self._is_freeform_tile(tile):
                self._draw_freeform_tile(drawing, tile)
            else:
                self._draw_tile(drawing, tile)

        # 5. Legend of the placed types present (drawn last so it sits on top).
        self._draw_legend(drawing, extents)

        logger.debug("Drawing rendered!")
        return drawing
