"""Registry of object types that can be placed on a floor plan.

Apps register their placeable models here (icon, label, and a resolver that derives the object's
Location) so the floor plan can place and render any object type without importing the owning app.
Registration is push-based and keyed by the dotted ``"app_label.model"`` string, so it is safe to
call before ContentTypes exist and it never imports the target model class.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _default_location(obj):
    """Default Location resolver: the object's own ``location`` attribute, if any."""
    return getattr(obj, "location", None)


def _default_url(obj):
    """Default detail-URL resolver."""
    getter = getattr(obj, "get_absolute_url", None)
    return getter() if callable(getter) else None


@dataclass(frozen=True)
class PlacementType:
    """Describes how one object type is placed and rendered on a floor plan."""

    key: str  # dotted "app_label.model"
    label: str
    icon: Optional[str] = None  # glyph key resolved by the renderer (G2)
    color: Optional[str] = None  # fallback swatch (hex, no leading '#'); status color wins when present
    location_resolver: Callable = field(default=_default_location)
    url_resolver: Callable = field(default=_default_url)
    tooltip_builder: Optional[Callable] = None
    legend_order: int = 100
    # ORM path from the object to its Location, used to build the picker's location filter param.
    # Keep consistent with location_resolver so eligibility and validation agree.
    location_field: str = "location"
    # Glyph-as-data: a stored list of SVG path-"d" strings drawn on a ``glyph_viewbox`` grid,
    # used instead of a built-in ICON_GLYPHS key when ``icon`` doesn't cover it.
    glyph_paths_data: Optional[list] = None
    glyph_viewbox: int = 24
    # Per-object dynamic resolvers for unbounded, library-owned types (e.g. a MedicalDeviceType
    # library where each type row owns its glyph/color). Resolved at render time, in svg.py only.
    glyph_resolver: Optional[Callable] = None  # obj -> (paths, viewbox)
    color_resolver: Optional[Callable] = None  # obj -> hex color (no leading '#')


@dataclass
class _Entry:
    """Registry entry for a content type: a base placement type plus optional role variants."""

    base: PlacementType
    variants: dict = field(default_factory=dict)
    discriminator: Optional[Callable] = None  # obj -> variant key


class PlacementRegistry:
    """Process-wide registry of placeable types, keyed by dotted model label."""

    def __init__(self):
        """Initialize an empty registry."""
        self._entries = {}

    @staticmethod
    def _label_for(obj):
        meta = obj._meta  # pylint: disable=protected-access
        return f"{meta.app_label}.{meta.model_name}"

    def register(
        self,
        model_label,
        *,
        label,
        icon=None,
        color=None,
        location_resolver=None,
        url_resolver=None,
        tooltip_builder=None,
        legend_order=100,
        location_field="location",
        glyph_paths_data=None,
        glyph_viewbox=24,
        glyph_resolver=None,
        color_resolver=None,
        replace=False,
    ):
        """Register a placeable type by its dotted ``app_label.model`` label."""
        key = model_label.lower()
        if key in self._entries and not replace:
            logger.warning("Placement type %s already registered; ignoring duplicate.", key)
            return self._entries[key].base
        placement = PlacementType(
            key=key,
            label=label,
            icon=icon,
            color=color,
            location_resolver=location_resolver or _default_location,
            url_resolver=url_resolver or _default_url,
            tooltip_builder=tooltip_builder,
            legend_order=legend_order,
            location_field=location_field,
            glyph_paths_data=glyph_paths_data,
            glyph_viewbox=glyph_viewbox,
            glyph_resolver=glyph_resolver,
            color_resolver=color_resolver,
        )
        self._entries[key] = _Entry(base=placement)
        return placement

    def register_variant(
        self,
        model_label,
        variant_key,
        *,
        label,
        icon=None,
        color=None,
        legend_order=100,
        glyph_paths_data=None,
        glyph_viewbox=None,
        glyph_resolver=None,
        color_resolver=None,
    ):
        """Register a per-discriminator variant (e.g. a Device Role) of an existing type."""
        entry = self._entries.get(model_label.lower())
        if entry is None:
            logger.warning("Cannot add variant %s: base type %s is not registered.", variant_key, model_label)
            return
        base = entry.base
        entry.variants[variant_key] = PlacementType(
            key=f"{base.key}#{variant_key}",
            label=label,
            icon=icon or base.icon,
            color=color or base.color,
            location_resolver=base.location_resolver,
            url_resolver=base.url_resolver,
            tooltip_builder=base.tooltip_builder,
            legend_order=legend_order,
            location_field=base.location_field,
            glyph_paths_data=glyph_paths_data if glyph_paths_data is not None else base.glyph_paths_data,
            glyph_viewbox=glyph_viewbox if glyph_viewbox is not None else base.glyph_viewbox,
            glyph_resolver=glyph_resolver or base.glyph_resolver,
            color_resolver=color_resolver or base.color_resolver,
        )

    def set_discriminator(self, model_label, discriminator):
        """Set the callable that maps an object to a variant key (e.g. a Device to its role)."""
        entry = self._entries.get(model_label.lower())
        if entry is not None:
            entry.discriminator = discriminator

    def resolve(self, obj):
        """Return the PlacementType for an object, or None if its type is not registered.

        Never raises: a failing discriminator falls back to the base type.
        """
        if obj is None:
            return None
        entry = self._entries.get(self._label_for(obj))
        if entry is None:
            return None
        if entry.discriminator is not None:
            try:
                variant = entry.variants.get(entry.discriminator(obj))
                if variant is not None:
                    return variant
            except Exception:  # noqa: BLE001  pylint: disable=broad-except
                logger.debug("Discriminator for %s raised; using base type.", entry.base.key, exc_info=True)
        return entry.base

    def resolve_location(self, obj):
        """Resolve the Location of a placed object via its registered resolver (or None)."""
        placement = self.resolve(obj)
        if placement is None:
            return None
        try:
            return placement.location_resolver(obj)
        except Exception:  # noqa: BLE001  pylint: disable=broad-except
            logger.debug("Location resolver for %s raised.", placement.key, exc_info=True)
            return None

    def is_registered(self, obj):
        """Whether the object's type is registered."""
        return obj is not None and self._label_for(obj) in self._entries

    def model_labels(self):
        """All registered dotted model labels."""
        return list(self._entries)

    def base_types(self):
        """The base PlacementType for every registered content type (excludes role variants)."""
        return [entry.base for entry in self._entries.values()]

    def allowed_content_types(self):
        """A ContentType queryset covering all registered types (empty if none resolve)."""
        from django.contrib.contenttypes.models import ContentType  # pylint: disable=import-outside-toplevel
        from django.db.models import Q  # pylint: disable=import-outside-toplevel

        query = Q(pk__in=[])
        for label in self._entries:
            app_label, model = label.split(".", 1)
            query |= Q(app_label=app_label, model=model)
        return ContentType.objects.filter(query)


registry = PlacementRegistry()
register = registry.register
register_variant = registry.register_variant
