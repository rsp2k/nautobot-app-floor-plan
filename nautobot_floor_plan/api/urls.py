"""Django API urlpatterns declaration for nautobot_floor_plan app."""

from nautobot.apps.api import OrderedDefaultRouter

from nautobot_floor_plan.api import views

router = OrderedDefaultRouter()
# add the name of your api endpoint, usually hyphenated model name in plural, e.g. "my-model-classes"
router.register("floor-plans", views.FloorPlanViewSet)
router.register("floor-plan-tiles", views.FloorPlanTileViewSet)
router.register("object-types", views.FloorPlanObjectTypeViewSet)
router.register("layers", views.FloorPlanLayerViewSet)
router.register("layer-objects", views.FloorPlanLayerObjectViewSet)

app_name = "nautobot_floor_plan-api"
urlpatterns = router.urls
