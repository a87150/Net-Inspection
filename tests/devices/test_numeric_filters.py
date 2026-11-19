"""Numeric column filters: one operator vocabulary, three comparisons."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from net.models import Server
from tests.payloads import asset_table


class NumericFilterTests(TestCase):
    def setUp(self):
        # 未登录拿到的是公开精简列表，没有筛选面板；筛选本身要用登录会话验证。
        self.client.force_login(
            get_user_model().objects.create_user("filter-reader", password="pw-for-test"),
        )
        for name, memory in (("小内存", 8), ("中内存", 32), ("大内存", 128)):
            Server.objects.create(
                name=name, ip=f"192.0.2.{memory}", memory_total_gb=Decimal(memory),
                disk_total_gb=Decimal(memory * 10),
            )

    def _names(self, params):
        response = self.client.get(reverse("asset_list", args=["servers"]), params)
        self.assertEqual(response.status_code, 200)
        # 行由 Vue 渲染，名字在 bootstrap JSON 里。
        rows = asset_table(response)["rows"]
        return {row["cells"]["name"]["text"] for row in rows}

    def test_greater_than_keeps_only_larger_values(self):
        names = self._names({"filter_memory_total_gb_op": "gt", "filter_memory_total_gb": "16"})
        self.assertEqual(names, {"中内存", "大内存"})

    def test_less_than_keeps_only_smaller_values(self):
        names = self._names({"filter_memory_total_gb_op": "lt", "filter_memory_total_gb": "16"})
        self.assertEqual(names, {"小内存"})

    def test_equals_matches_exactly(self):
        names = self._names({"filter_memory_total_gb_op": "eq", "filter_memory_total_gb": "32"})
        self.assertEqual(names, {"中内存"})

    def test_operator_defaults_to_greater_than_when_missing(self):
        names = self._names({"filter_memory_total_gb": "16"})
        self.assertEqual(names, {"中内存", "大内存"})

    def test_unknown_operator_falls_back_instead_of_failing(self):
        names = self._names({"filter_memory_total_gb_op": "sideways", "filter_memory_total_gb": "16"})
        self.assertEqual(names, {"中内存", "大内存"})

    def test_non_numeric_value_is_ignored(self):
        names = self._names({"filter_memory_total_gb_op": "gt", "filter_memory_total_gb": "abc"})
        self.assertEqual(names, {"小内存", "中内存", "大内存"})

    def test_disk_uses_the_same_query_path(self):
        names = self._names({"filter_disk_total_gb_op": "gt", "filter_disk_total_gb": "500"})
        self.assertEqual(names, {"大内存"})

    def test_filter_control_renders_operator_dropdown_and_number_box(self):
        response = self.client.get(reverse("asset_list", args=["servers"]))
        self.assertContains(response, "table-number-filter__op")
        self.assertContains(response, ">大于<")
        self.assertContains(response, ">小于<")
        self.assertContains(response, ">等于<")
