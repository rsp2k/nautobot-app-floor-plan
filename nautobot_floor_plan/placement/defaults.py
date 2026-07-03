"""Built-in placement registrations for the native DCIM types and common Device roles.

These make the floor plan's existing objects first-class in the registry so the generic placement path
treats them identically to types registered by other apps. Location resolvers here must match the
historical validation behavior exactly.
"""

import re

# Device role -> icon variant. Roles are matched by normalized-name keywords, so a site's own role
# naming ("Access Point", "wifi-ap", "WAP") still lands on the right glyph.
_ROLE_KEYWORDS = (
    (("computer", "workstation", "desktop", "laptop", "pc"), "computer"),
    (("camera", "cctv", "surveillance"), "camera"),
    (("access-point", "access point", "ap", "wifi", "wireless", "wap"), "access-point"),
    (("climate", "hvac", "thermostat", "temperature", "cooling", "sensor"), "climate"),
    (("phone", "voip", "voice"), "phone"),
)

# Variant metadata (avoids purple-dominant fills per house style).
_ROLE_VARIANTS = {
    "computer": {"label": "Computer", "icon": "monitor", "color": "0d6efd", "legend_order": 21},
    "camera": {"label": "Camera", "icon": "camera", "color": "198754", "legend_order": 22},
    "access-point": {"label": "Access Point", "icon": "wifi", "color": "20c997", "legend_order": 23},
    "climate": {"label": "Climate Control", "icon": "thermometer", "color": "fd7e14", "legend_order": 24},
    "phone": {"label": "IP Phone", "icon": "phone", "color": "0dcaf0", "legend_order": 25},
}


def _power_feed_location(power_feed):
    """A power feed has no direct Location; use its power panel's."""
    return power_feed.power_panel.location if power_feed.power_panel_id else None


def _device_role_variant(device):
    """Map a Device to an icon variant key based on its role name, or None for the base icon."""
    role = getattr(device, "role", None)
    name = (getattr(role, "name", "") or "").lower()
    normalized = re.sub(r"[\s_]+", "-", name).strip("-")
    for keywords, variant_key in _ROLE_KEYWORDS:
        if any(keyword in normalized or keyword in name for keyword in keywords):
            return variant_key
    return None


def register_builtins():
    """Register the native DCIM placeable types and Device-role variants. Idempotent."""
    from nautobot_floor_plan.placement.registry import registry  # pylint: disable=import-outside-toplevel

    registry.register("dcim.rack", label="Rack", icon="server", color="6c757d", legend_order=10)
    registry.register("dcim.device", label="Device", icon="cpu", color="6c757d", legend_order=20)
    registry.register("dcim.powerpanel", label="Power Panel", icon="power", color="ffc107", legend_order=30)
    registry.register(
        "dcim.powerfeed",
        label="Power Feed",
        icon="plug",
        color="ffc107",
        location_resolver=_power_feed_location,
        legend_order=40,
    )

    for variant_key, meta in _ROLE_VARIANTS.items():
        registry.register_variant(
            "dcim.device",
            variant_key,
            label=meta["label"],
            icon=meta["icon"],
            color=meta["color"],
            legend_order=meta["legend_order"],
        )
    registry.set_discriminator("dcim.device", _device_role_variant)
