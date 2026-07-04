"""Merge admin-defined ``FloorPlanObjectType`` rows into the in-memory placement registry.

Timing: the initial merge runs on ``post_migrate`` (tables exist, and every app's ``ready()`` has
registered its code/app defaults, so the captured base layer is complete). Runtime edits bump a
cache version; ``refresh_if_stale()`` re-applies lazily in each worker when the version moved, so
multi-process deployments stay consistent without a restart.
"""

import logging
import re

from django.core.cache import cache

from nautobot_floor_plan.placement.registry import registry

logger = logging.getLogger(__name__)

CONFIG_VERSION_CACHE_KEY = "nautobot_floor_plan.placement_config_version"


def bump_config_version():
    """Signal all workers that FloorPlanObjectType config changed (exact value is irrelevant)."""
    try:
        cache.incr(CONFIG_VERSION_CACHE_KEY)
    except ValueError:
        cache.set(CONFIG_VERSION_CACHE_KEY, 1)
    except Exception:  # noqa: BLE001  pylint: disable=broad-except
        logger.debug("Could not bump placement config version.", exc_info=True)


def current_config_version():
    """The current config version from the shared cache (0 if unset/unavailable)."""
    try:
        return cache.get(CONFIG_VERSION_CACHE_KEY, 0)
    except Exception:  # noqa: BLE001  pylint: disable=broad-except
        return 0


def _read_attr(obj, dotted):
    """Read a dotted attribute path off ``obj`` (e.g. 'role.name'), or None if any hop is missing."""
    current = obj
    for part in dotted.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _location_resolver_for(location_field):
    """Build a Location resolver that traverses the configured ORM path (e.g. 'device__location').

    So a DB-configured type whose object reaches its Location through a relation (a MedicalDevice
    via its Device, a PowerFeed via its PowerPanel) resolves correctly, not just objects with a
    literal ``.location``. For the default 'location' this is equivalent to ``obj.location``.
    """
    parts = location_field.split("__")

    def resolver(obj):
        current = obj
        for part in parts:
            current = getattr(current, part, None)
            if current is None:
                return None
        return current

    return resolver


def _make_discriminator(rows):
    """Build an ``obj -> variant_key`` callable from DB variant rows (field + keyword + precedence)."""
    ordered = sorted(rows, key=lambda r: r.match_precedence)

    def discriminator(obj):
        for row in ordered:
            if not row.match_field:
                continue
            value = _read_attr(obj, row.match_field)
            if value is None:
                continue
            name = str(value).lower()
            normalized = re.sub(r"[\s_]+", "-", name).strip("-")
            for keyword in row.match_keywords or []:
                key = str(keyword).lower()
                if key in normalized or key in name:
                    return row.variant_key
        return None

    return discriminator


def apply_db_config():
    """Restore the base layer, then overlay every enabled FloorPlanObjectType row onto the registry."""
    from nautobot_floor_plan.models import FloorPlanObjectType  # noqa: PLC0415  (models imports registry)

    version = current_config_version()
    registry.snapshot_base()  # first call captures code + app registrations
    registry.restore_base()  # drop any prior DB overlay before re-applying

    def _label(row):
        ctype = row.content_type
        return f"{ctype.app_label}.{ctype.model}"

    rows = list(FloorPlanObjectType.objects.filter(enabled=True).select_related("content_type"))
    for row in [r for r in rows if not r.variant_key]:
        registry.register(
            _label(row),
            label=row.label,
            icon=row.glyph_key or None,
            color=row.color or None,
            legend_order=row.legend_order,
            location_field=row.location_field,
            location_resolver=_location_resolver_for(row.location_field),
            glyph_paths_data=row.custom_glyph_paths or None,
            glyph_viewbox=row.glyph_viewbox,
            replace=row.override,
        )

    variants_by_type = {}
    for row in [r for r in rows if r.variant_key]:
        label = _label(row)
        variants_by_type.setdefault(label, []).append(row)
        registry.register_variant(
            label,
            row.variant_key,
            label=row.label,
            icon=row.glyph_key or None,
            color=row.color or None,
            legend_order=row.legend_order,
            glyph_paths_data=row.custom_glyph_paths or None,
            glyph_viewbox=row.glyph_viewbox,
        )
    for label, type_rows in variants_by_type.items():
        registry.set_discriminator(label, _make_discriminator(type_rows))

    registry.applied_config_version = version


def refresh_if_stale():
    """Re-apply DB config if another process bumped the version since this process last merged."""
    if registry.applied_config_version != current_config_version():
        try:
            apply_db_config()
        except Exception:  # noqa: BLE001  pylint: disable=broad-except
            logger.debug("Placement DB-config refresh failed; keeping current registry.", exc_info=True)
