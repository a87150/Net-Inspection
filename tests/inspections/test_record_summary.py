from django.test import SimpleTestCase

from net.inspections.record_summary import key_metrics


class DynamicInventoryMetricTests(SimpleTestCase):
    def test_dynamic_ports_and_disk_layout_are_summarized_in_record_lists(self):
        infrastructure = key_metrics({
            'interface_status': {'up': 22, 'down': 2},
            'storage_status': [{'mount': '/', 'usage_percent': 58}],
        })
        computer = key_metrics({
            'computer_info': {'磁盘摘要': 'C: 476 GB NVMe; D: 931 GB SSD'},
        })

        self.assertIn('接口 Up 22 / Down 2', infrastructure)
        self.assertIn('磁盘 / 58%', infrastructure)
        self.assertIn('磁盘 C: 476 GB NVMe; D: 931 GB SSD', computer)
