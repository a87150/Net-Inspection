from types import SimpleNamespace
from django.test import TestCase
from net.models import InspectionProfile, TaskRun, TaskTargetRun, Network_Device, Network_Device_Inspection
from net.alerts.service import findings_for_target


class CompactResultTests(TestCase):
    def test_compact_snapshot_keeps_summary_but_not_duplicate_evidence(self):
        from net.inspections.result_storage import compact_result_snapshot
        record = SimpleNamespace(pk='result-id', status='success', summary='done',
            details={'health_status': 'normal', 'large': 'x' * 10000})
        result = compact_result_snapshot(record, result_type='computer_analysis')
        self.assertEqual(result['result_id'], 'result-id')
        self.assertEqual(result['health_status'], 'normal')
        self.assertEqual(result['snapshot_version'], 2)
        self.assertNotIn('details', result)
        self.assertLess(len(str(result)), 500)

    def test_reference_snapshot_uses_record_for_alerts(self):
        profile = InspectionProfile.objects.create(name='compact', device_type='network_device', selected_items=['cpu'])
        task = TaskRun.objects.create(task_type='inspection', source='manual', inspection_profile=profile)
        target = TaskTargetRun.objects.create(task=task, target_type='network_device', target_id='switch', status='success')
        device = Network_Device.objects.create(device_name='Switch', ip='192.0.2.1')
        record = Network_Device_Inspection.objects.create(device=device, task_target=target, status='success',
            details={'normal_issue_items': ['inspection_collection'], 'issue_findings': [
                {'project': 'networks', 'analysis_item': 'cpu', 'severity': 'warning',
                 '问题类型': 'CPU异常', '详细问题': 'CPU 99%'}]})
        TaskTargetRun.objects.filter(pk=target.pk).update(result_type='network_device_inspection', result_id=str(record.pk),
            result_snapshot={'snapshot_version': 2, 'result_type': 'network_device_inspection',
                             'result_id': str(record.pk), 'status': 'success', 'health_status': 'abnormal'})
        findings = findings_for_target(target)
        self.assertEqual({f.key: f.state for f in findings},
                         {'inspection.cpu': 'abnormal', 'inspection.collection': 'normal'})
