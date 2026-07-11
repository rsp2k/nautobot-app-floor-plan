"""Tests for FloorPlanLayer: membership resolution, SVG render integration, and the layers API."""

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from nautobot.core.testing import TestCase
from nautobot.dcim.models import Device, Rack, RackGroup
from nautobot.extras.models import DynamicGroup, Tag
from nautobot.users.models import User

from nautobot_floor_plan import models
from nautobot_floor_plan.choices import PlacementModeChoices
from nautobot_floor_plan.layers import applicable_layers, resolve_layers
from nautobot_floor_plan.tests import fixtures


class FloorPlanLayerResolverTestCase(TestCase):
    """resolve_layers: each source, their union, and global vs plan scope."""

    def setUp(self):
        prereq = fixtures.create_prerequisites()
        self.status = prereq["status"]
        self.floors = prereq["floors"]
        self.floor = self.floors[2]
        self.floor_plan = models.FloorPlan(location=self.floor, x_size=4, y_size=4)
        self.floor_plan.validated_save()

        RackGroup.objects.create(name="RG", location=self.floor)
        self.rack = Rack.objects.create(name="R1", status=self.status, location=self.floor)
        self.rack2 = Rack.objects.create(name="R2", status=self.status, location=self.floor)
        self.device = Device.objects.create(
            name="D1",
            status=self.status,
            location=self.floor,
            device_type=prereq["device_type"],
            role=prereq["device_role"],
        )
        # The list of placed objects the resolver operates on -- what the SVG passes at render time.
        self.placed = [self.rack, self.rack2, self.device]
        self.rack_ct = ContentType.objects.get_for_model(Rack)

    def _resolve(self):
        return resolve_layers(self.floor_plan, self.placed)

    def _rack_key(self, rack):
        return ("dcim.rack", rack.pk)

    def test_no_layers_is_empty_and_free(self):
        self.assertEqual(self._resolve(), {})

    def test_content_type_source(self):
        layer = models.FloorPlanLayer.objects.create(name="Racks")
        layer.source_content_types.set([self.rack_ct])
        membership = self._resolve()
        self.assertEqual(set(membership), {self._rack_key(self.rack), self._rack_key(self.rack2)})
        self.assertEqual(membership[self._rack_key(self.rack)], [str(layer.pk)])
        self.assertNotIn(("dcim.device", self.device.pk), membership)

    def test_tag_source(self):
        tag = Tag.objects.create(name="critical")
        tag.content_types.add(self.rack_ct)
        self.rack.tags.add(tag)
        layer = models.FloorPlanLayer.objects.create(name="Critical")
        layer.source_tags.set([tag])
        self.assertEqual(list(self._resolve()), [self._rack_key(self.rack)])

    def test_static_source(self):
        layer = models.FloorPlanLayer.objects.create(name="Manual")
        models.FloorPlanLayerObject.objects.create(layer=layer, content_type=self.rack_ct, object_id=self.rack2.pk)
        self.assertEqual(list(self._resolve()), [self._rack_key(self.rack2)])

    def test_dynamic_group_source(self):
        group = DynamicGroup.objects.create(
            name="NamedRack", content_type=self.rack_ct, filter={"name": [self.rack.name]}
        )
        layer = models.FloorPlanLayer.objects.create(name="ByGroup")
        layer.source_dynamic_groups.set([group])
        self.assertEqual(list(self._resolve()), [self._rack_key(self.rack)])

    def test_union_of_sources(self):
        tag = Tag.objects.create(name="t")
        tag.content_types.add(self.rack_ct)
        self.rack.tags.add(tag)
        layer = models.FloorPlanLayer.objects.create(name="Union")
        layer.source_tags.set([tag])  # -> rack
        models.FloorPlanLayerObject.objects.create(  # -> rack2
            layer=layer, content_type=self.rack_ct, object_id=self.rack2.pk
        )
        self.assertEqual(set(self._resolve()), {self._rack_key(self.rack), self._rack_key(self.rack2)})

    def test_global_and_plan_scope(self):
        other_plan = models.FloorPlan(location=self.floors[3], x_size=2, y_size=2)
        other_plan.validated_save()
        global_layer = models.FloorPlanLayer.objects.create(name="Global")
        global_layer.source_content_types.set([self.rack_ct])
        other_scoped = models.FloorPlanLayer.objects.create(name="Other", floor_plan=other_plan)
        other_scoped.source_content_types.set([self.rack_ct])

        membership = self._resolve()
        # Only the global layer applies here; the layer scoped to another plan is excluded.
        self.assertEqual(membership[self._rack_key(self.rack)], [str(global_layer.pk)])
        self.assertEqual(
            set(applicable_layers(self.floor_plan).values_list("name", flat=True)),
            {"Global"},
        )

    def test_dynamic_group_resolved_once_not_per_object(self):
        # Two placed racks + one DG-backed layer: the group must be evaluated a bounded number of
        # times, not once per marker. Guards against re-introducing a per-object .members access.
        group = DynamicGroup.objects.create(name="AllByName", content_type=self.rack_ct, filter={})
        layer = models.FloorPlanLayer.objects.create(name="G")
        layer.source_dynamic_groups.set([group])
        # Fixed cost: the applicable-layers fetch, four prefetches, the group's content-type lookup,
        # and one .members resolution. The count is independent of how many objects are placed -- that
        # is the property under test (no per-object .members access sneaking back in).
        with self.assertNumQueries(7):
            resolve_layers(self.floor_plan, self.placed)


class FloorPlanLayerRenderTestCase(TestCase):
    """The freeform SVG marker carries data-content-type and data-layers from the resolver."""

    def setUp(self):
        prereq = fixtures.create_prerequisites(floor_count=1)
        self.status = prereq["status"]
        self.floor = prereq["floors"][0]
        self.user = User.objects.create(username="layeruser", is_superuser=True)
        self.plan = models.FloorPlan.objects.create(
            location=self.floor, x_size=5, y_size=5, placement_mode=PlacementModeChoices.FREEFORM
        )
        self.rack = Rack.objects.create(name="RenderRack", status=self.status, location=self.floor)
        models.FloorPlanTile(
            floor_plan=self.plan, status=self.status, rack=self.rack, x_origin=1, y_origin=1, pos_x=0.5, pos_y=0.5
        ).validated_save()

    def _svg(self):
        return self.plan.get_svg(user=self.user, base_url="http://testserver").tostring()

    def test_marker_has_content_type_and_no_layers_without_a_layer(self):
        svg_str = self._svg()
        self.assertIn('data-content-type="dcim.rack"', svg_str)
        self.assertNotIn("data-layers=", svg_str)

    def test_marker_gets_data_layers_when_a_layer_matches(self):
        layer = models.FloorPlanLayer.objects.create(name="Racks")
        layer.source_content_types.set([ContentType.objects.get_for_model(Rack)])
        svg_str = self._svg()
        self.assertIn(f'data-layers="{layer.pk}"', svg_str)


class FloorPlanLayerAPITestCase(TestCase):
    """The plan's layers action and layer CRUD over REST."""

    def setUp(self):
        prereq = fixtures.create_prerequisites(floor_count=1)
        self.floor = prereq["floors"][0]
        self.plan = models.FloorPlan.objects.create(location=self.floor, x_size=3, y_size=3)
        self.user = User.objects.create(username="apiuser", is_superuser=True)
        self.client.force_login(self.user)

    def test_layers_action_lists_applicable(self):
        models.FloorPlanLayer.objects.create(name="Global", display_order=5)
        scoped = models.FloorPlanLayer.objects.create(name="Scoped", floor_plan=self.plan, display_order=1)
        url = reverse("plugins-api:nautobot_floor_plan-api:floorplan-layers", kwargs={"pk": self.plan.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()["layers"]]
        # Scoped sorts first by display_order; both this plan's and the global layer appear.
        self.assertEqual(names, ["Scoped", "Global"])
        self.assertEqual(str(scoped.pk), response.json()["layers"][0]["id"])

    def test_layer_create_round_trip(self):
        url = reverse("plugins-api:nautobot_floor_plan-api:floorplanlayer-list")
        response = self.client.post(url, data={"name": "Created", "opacity": 80}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(models.FloorPlanLayer.objects.filter(name="Created", opacity=80).exists())
