from types import SimpleNamespace
from django.test import SimpleTestCase
from net.devices.network.snmp import collect_network_snmp, VENDOR_OIDS, SYS_NAME, HR_PROCESSOR_LOAD
from tests.devices.network.test_snmp import MemorySession


class CustomOidTests(SimpleTestCase):
    def device(self, settings):
        return SimpleNamespace(vendor='huawei', collection_settings=settings)

    def test_custom_scalars_feed_normalized_metrics_without_changing_vendor_defaults(self):
        original = dict(VENDOR_OIDS['huawei'])
        session = MemorySession(scalars={'1.3.6.1.4.1.999.1.0': 35,
            '1.3.6.1.4.1.999.2.0': 2000, '1.3.6.1.4.1.999.3.0': 500,
            '1.3.6.1.4.1.999.4.0': 46})
        result = collect_network_snmp(self.device({'snmp_oids': {
            'cpu':'1.3.6.1.4.1.999.1.0', 'memory_total':'1.3.6.1.4.1.999.2.0',
            'memory_used':'1.3.6.1.4.1.999.3.0', 'temperature':'1.3.6.1.4.1.999.4.0'}}),
            selected_items=['cpu','memory','temperature'], session_factory=lambda *args: session)
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['cpu']['usage_percent'],35)
        self.assertEqual(result.data['memory']['usage_percent'],25)
        self.assertEqual(result.data['temperature']['values_celsius'],[46])
        self.assertEqual(VENDOR_OIDS['huawei'], original)

    def test_symbolic_system_oid_and_standard_table_override(self):
        session = MemorySession(scalars={'1.3.6.1.4.1.999.5.0':'custom-name'},
            tables={'1.3.6.1.4.1.999.6': [('1', 20), ('2', 40)]})
        result = collect_network_snmp(self.device({'snmp_oids': {
            'sys_name':'TEST::hostname', 'hr_processor_load':'1.3.6.1.4.1.999.6'},
            'mib_modules':[{'name':'TEST','symbols':{'hostname':'1.3.6.1.4.1.999.5.0'}}]}),
            selected_items=['device_info','cpu'], session_factory=lambda *args: session)
        self.assertEqual(result.status,'success')
        self.assertEqual(result.data['device_info']['system_name'],'custom-name')
        self.assertEqual(result.data['cpu']['usage_percent'],30)
        self.assertNotIn(SYS_NAME,session.get_queries)
        self.assertNotIn(HR_PROCESSOR_LOAD,session.walk_queries)

    def test_unknown_metric_or_symbol_fails_before_network_io(self):
        for mapping in ({'unsupported_metric':'1.3.6.1.9'}, {'cpu':'TEST::missing'}):
            def no_connection(*args):
                self.fail('Invalid configuration opened a connection')
            result = collect_network_snmp(self.device({'snmp_oids':mapping}),
                selected_items=['cpu'],session_factory=no_connection)
            self.assertEqual(result.status,'failed')
            self.assertIn('invalid configuration',result.message)


class TemplateProtocolTests(SimpleTestCase):
    def test_explicit_ssh_template_runs_after_successful_snmp_and_updates_metric(self):
        from unittest.mock import Mock
        from net.devices.network.collector import collect_network
        from net.infrastructure.collection import CollectionResult
        device = SimpleNamespace(connection_type='auto', collection_settings={'commands':{'cpu':['display cpu']}})
        snmp = Mock(return_value=CollectionResult(True,'success',data={'cpu':{'usage_percent':20}}))
        ssh = Mock(return_value=CollectionResult(True,'success',data={'cpu':{'usage_percent':35}}))
        result = collect_network(device, selected_items=['cpu'], snmp_collector=snmp,ssh_collector=ssh)
        self.assertEqual(result.data['cpu']['usage_percent'],35)
        self.assertEqual(ssh.call_args.kwargs['selected_items'],['cpu'])
        self.assertEqual(result.status,'success')

    def test_explicit_item_protocol_overrides_device_without_cross_item_fallback(self):
        from unittest.mock import Mock
        from net.devices.network.collector import collect_network
        from net.infrastructure.collection import CollectionResult
        target=SimpleNamespace(connection_type='auto',collection_settings={'item_methods':{'cpu':'snmp','logs':'ssh'},'commands':{'cpu':['display cpu']}})
        snmp=Mock(return_value=CollectionResult(False,'failed','timeout'))
        ssh=Mock(return_value=CollectionResult(True,'success',data={'logs':['ok']}))
        result=collect_network(target,selected_items=['cpu','logs'],snmp_collector=snmp,ssh_collector=ssh)
        self.assertEqual(snmp.call_args.kwargs['selected_items'],['cpu'])
        self.assertEqual(ssh.call_args.kwargs['selected_items'],['logs'])
        self.assertEqual(result.status,'partial')
        self.assertNotIn('cpu',result.data)

    def test_scaled_cpu_value(self):
        session=MemorySession(scalars={'1.3.6.1.4.1.999.1.0':6400})
        target=SimpleNamespace(vendor='huawei',collection_settings={'snmp_oids':{'cpu':'1.3.6.1.4.1.999.1.0'},'snmp_transforms':{'cpu':{'scale':0.01,'offset':0}}})
        result=collect_network_snmp(target,selected_items=['cpu'],session_factory=lambda *args:session)
        self.assertEqual(result.data['cpu']['usage_percent'],64)
        self.assertEqual(result.raw['cpu']['1.3.6.1.4.1.999.1.0'],6400)
