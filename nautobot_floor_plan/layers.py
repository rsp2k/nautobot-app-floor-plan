"""Resolve which ``FloorPlanLayer``(s) each placed object on a plan belongs to.

Membership is the union of a layer's rule sources (content types, tags, dynamic groups) and its
manual static set. This runs once per plan render: every rule source is turned into a set of object
PKs a single time -- never per object -- then intersected with the plan's placed objects. That
ordering matters most for dynamic groups: a filter-based ``DynamicGroup`` evaluates its whole
FilterSet on ``.members`` access, so touching it once and intersecting in Python is the difference
between one query and one-per-marker.

With no layers defined, ``resolve_layers`` short-circuits to an empty dict and adds zero overhead.
"""

import logging
from collections import defaultdict

from django.db.models import Q

logger = logging.getLogger(__name__)


def resolve_layers(floor_plan, placed_objects):
    """Map each placed object to the layer IDs it belongs to.

    Args:
        floor_plan: the ``FloorPlan`` being rendered.
        placed_objects: iterable of the placed model instances on the plan (generic, mixed types).

    Returns:
        ``{(label_lower, pk): [layer_id_str, ...]}`` -- keyed to match the SVG marker lookup. Objects
        in no layer are absent from the dict.
    """
    from nautobot_floor_plan.models import FloorPlanLayer  # noqa: PLC0415  avoid import cycle at module load

    applicable = list(
        FloorPlanLayer.objects.filter(Q(floor_plan__isnull=True) | Q(floor_plan=floor_plan)).prefetch_related(
            "source_content_types",
            "source_tags",
            "source_dynamic_groups",
            "static_objects",
        )
    )
    if not applicable:
        return {}

    # Index the plan's placed objects once: by content-type label (for the content-type rule) and as a
    # flat PK set (to intersect tag/DG/static membership down to what's actually on this plan).
    pk_to_key = {}
    placed_by_ct = defaultdict(set)
    all_pks = set()
    for obj in placed_objects:
        if obj is None:
            continue
        label = obj._meta.label_lower
        pk_to_key[obj.pk] = (label, obj.pk)
        placed_by_ct[label].add(obj.pk)
        all_pks.add(obj.pk)

    if not all_pks:
        return {}

    membership = defaultdict(list)  # object pk -> [layer_id_str, ...]
    for layer in applicable:
        member_pks = set()

        for content_type in layer.source_content_types.all():
            member_pks |= placed_by_ct.get(f"{content_type.app_label}.{content_type.model}", set())

        tag_ids = [tag.pk for tag in layer.source_tags.all()]
        if tag_ids:
            member_pks |= _tagged_pks(tag_ids, all_pks)

        for dynamic_group in layer.source_dynamic_groups.all():
            member_pks |= _dynamic_group_pks(dynamic_group) & all_pks

        member_pks |= {row.object_id for row in layer.static_objects.all()} & all_pks

        layer_id = str(layer.pk)
        for pk in member_pks:
            membership[pk].append(layer_id)

    return {pk_to_key[pk]: layer_ids for pk, layer_ids in membership.items() if pk in pk_to_key}


def _tagged_pks(tag_ids, candidate_pks):
    """PKs among ``candidate_pks`` whose object carries any of ``tag_ids`` (one query)."""
    from nautobot.extras.models import TaggedItem  # noqa: PLC0415

    return set(
        TaggedItem.objects.filter(tag_id__in=tag_ids, object_id__in=candidate_pks).values_list("object_id", flat=True)
    )


def _dynamic_group_pks(dynamic_group):
    """Member PKs of a dynamic group, resolved once. Never raises -- a broken group yields nothing."""
    try:
        return set(dynamic_group.members.values_list("pk", flat=True))
    except Exception:  # noqa: BLE001  pylint: disable=broad-except
        logger.debug("Dynamic group %s member resolution raised; treating as empty.", dynamic_group, exc_info=True)
        return set()


def applicable_layers(floor_plan):
    """The layers that apply to a plan (its own + global), ordered for the panel/API."""
    from nautobot_floor_plan.models import FloorPlanLayer  # noqa: PLC0415

    return FloorPlanLayer.objects.filter(Q(floor_plan__isnull=True) | Q(floor_plan=floor_plan))
