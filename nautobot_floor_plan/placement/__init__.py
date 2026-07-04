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

# Aliases for apps that register under a more explicit name. External apps (e.g. a medical-device
# library) call register_placeable_type(...) from their AppConfig.ready() to add their own types,
# glyphs, and colors — including per-object glyph_resolver/color_resolver callables.
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
