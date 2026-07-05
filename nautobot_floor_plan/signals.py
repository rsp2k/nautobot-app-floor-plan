"""Signals for the Floor Plan App."""

import logging

from django.apps import apps as global_apps
from django.conf import settings

logger = logging.getLogger(__name__)

PLUGIN_SETTINGS = settings.PLUGINS_CONFIG["nautobot_floor_plan"]


def post_migrate_create__add_statuses(sender, *, apps=global_apps, **kwargs):
    """Callback function for post_migrate() -- create default Statuses."""
    # pylint: disable=invalid-name
    if not apps:
        return

    Status = apps.get_model("extras", "Status")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for model_name, default_statuses in PLUGIN_SETTINGS.get("default_statuses", {}).items():
        model = sender.get_model(model_name)
        for status in default_statuses:
            ct_status, _ = Status.objects.get_or_create(name=status["name"], defaults={"color": status["color"]})
            ct_model = ContentType.objects.get_for_model(model)
            if ct_model not in ct_status.content_types.all():
                ct_status.content_types.add(ct_model)
                ct_status.save()


def post_migrate_apply_placement_config(sender, **kwargs):  # pylint: disable=unused-argument
    """Callback for post_migrate() -- merge FloorPlanObjectType rows into the placement registry."""
    from nautobot_floor_plan.placement.config import apply_db_config  # pylint: disable=import-outside-toplevel

    try:
        apply_db_config()
    except Exception:  # noqa: BLE001  pylint: disable=broad-except
        logger.debug("Could not apply placement DB config on post_migrate.", exc_info=True)


def handle_placement_config_change(sender, **kwargs):  # pylint: disable=unused-argument
    """post_save/post_delete on FloorPlanObjectType -- bump the shared version so workers re-merge."""
    from nautobot_floor_plan.placement.config import bump_config_version  # pylint: disable=import-outside-toplevel

    bump_config_version()
