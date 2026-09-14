from django.test import SimpleTestCase
from net.infrastructure.ssh_collectors import _linux_cpu
from net.inspections.device_issues import evaluate_device_issues
from net.inspections.record_summary import key_metrics


class ResourceMetricTests(SimpleTestCase):
    def test_cpu_interval_excludes_guest_double_counting(self):
        data = _linux_cpu('Model name: Fixture\n---CPU_SAMPLE---\ncpu 100 10 20 300 10 2 3 4 80 5\ncpu 120 10 30 360 20 2 3 4 90 5')
        self.assertEqual(data['usage_percent'], 30)
        self.assertEqual(_linux_cpu('load average: 80 80 80').get('usage_percent'), None)

    def test_reset_zero_or_invalid_samples_are_unknown(self):
        for tail in ['cpu 1 2 3 4 5 6 7 8\ncpu 1 2 3 4 5 6 7 8',
                     'cpu 2 2 3 4 5 6 7 8\ncpu 1 2 3 4 5 6 7 8', 'bad']:
            self.assertNotIn('usage_percent', _linux_cpu('---CPU_SAMPLE---\n' + tail))

    def test_windows_fields_drive_thresholds_and_summary(self):
        data = {'cpu': {'usage_percent': 1}, 'memory': {'used_percent': 95},
                'storage_status': [{'device': 'C:', 'used_percent': 98}]}
        issues, normal = evaluate_device_issues('servers', list(data), data,
            reachable=True, status='success', server_type='windows')
        self.assertIn('cpu', normal)
        self.assertEqual({issue['analysis_item'] for issue in issues}, {'memory', 'storage_status'})
        self.assertFalse(any(issue.get('data_state') == 'unknown' for issue in issues))
        self.assertIn('C: 98%', key_metrics(data))
