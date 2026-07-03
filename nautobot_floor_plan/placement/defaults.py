"""Built-in placement registrations for the four native DCIM types.

These make the floor plan's existing objects (rack, device, power panel, power feed) first-class in
the registry so the generic placement path treats them identically to types registered by other apps.
Icons and colors are placeholders until the rendering wave; location resolvers here must match the
historical validation behavior exactly.
"""


def _power_feed_location(power_feed):
    """A power feed has no direct Location; use its power panel's."""
    return power_feed.power_panel.location if power_feed.power_panel_id else None


def register_builtins():
    """Register the four native DCIM placeable types. Idempotent."""
    from nautobot_floor_plan.placement.registry import registry  # pylint: disable=import-outside-toplevel

    registry.register("dcim.rack", label="Rack", icon="rack", color="6c757d", legend_order=10)
    registry.register("dcim.device", label="Device", icon="device", color="6c757d", legend_order=20)
    registry.register("dcim.powerpanel", label="Power Panel", icon="power", color="ffc107", legend_order=30)
    registry.register(
        "dcim.powerfeed",
        label="Power Feed",
        icon="power",
        color="ffc107",
        location_resolver=_power_feed_location,
        legend_order=40,
    )
