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

# Back-compat alias for apps that register under a more explicit name.
register_placeable = register

__all__ = ("registry", "register", "register_variant", "register_placeable", "PlacementType")
