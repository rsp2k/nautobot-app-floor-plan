"""Placeable-object registry for Nautobot Floor Plan.

Public surface:
    from nautobot_floor_plan.placement import registry, register, PlacementType
"""

from nautobot_floor_plan.placement.registry import (
    PlacementType,
    register,
    register_variant,
    registry,
)

# Aliases for apps that register under a more explicit name. An external app can call
# register_placeable_type(...) from its AppConfig.ready() to add its own types, glyphs, and colors
# (including per-object glyph_resolver/color_resolver callables). Presentation can also be defined
# admin-side via FloorPlanObjectType, keyed by content type — no cross-app import either way.
register_placeable = register
register_placeable_type = register

__all__ = (
    "registry",
    "register",
    "register_variant",
    "register_placeable",
    "register_placeable_type",
    "PlacementType",
)
