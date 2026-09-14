from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from net.devices.network.templates import (
    compile_mib_text, parse_template_output, resolve_symbolic_oid,
    validate_collection_settings,
)


class NetworkTemplateTests(SimpleTestCase):
    def test_validates_data_only_template_and_resolves_symbol(self):
        settings = validate_collection_settings({
            'version': 1, 'vendor': 'huawei', 'subtype': 'switch',
            'commands': {'cpu': ['display cpu-usage']},
            'parsers': {'cpu': {'engine': 'regex', 'template': r'CPU: (?P<usage>\d+)%'}},
            'snmp_oids': {'cpu': 'VENDOR-MIB::cpuUsage'},
            'mib_modules': [{'name': 'VENDOR-MIB', 'symbols': {'cpuUsage': '1.3.6.1.4.1.9.1'}}],
        })
        self.assertEqual(resolve_symbolic_oid(settings['snmp_oids']['cpu'], settings['mib_modules']), '1.3.6.1.4.1.9.1')
        self.assertEqual(parse_template_output(settings['parsers']['cpu'], 'CPU: 42%'), [{'usage': '42'}])

    def test_rejects_unbounded_or_unsupported_configuration(self):
        with self.assertRaises(ValidationError):
            validate_collection_settings({'commands': {'config_info': ['show run']}})
        with self.assertRaises(ValidationError):
            validate_collection_settings({'parsers': {'cpu': {'engine': 'regex', 'template': r'(a+)+$'}}})
        with self.assertRaises(ValidationError):
            validate_collection_settings({'snmp_oids': {'cpu': '__import__("os")'}})

    def test_compiles_static_mib_without_execution(self):
        compiled = compile_mib_text('''
TEST-MIB DEFINITIONS ::= BEGIN
root OBJECT IDENTIFIER ::= { enterprises 999 }
cpuUsage OBJECT-TYPE
    SYNTAX INTEGER
    ::= { root 1 }
END
''')
        self.assertEqual(compiled['name'], 'TEST-MIB')
        self.assertEqual(compiled['symbols']['cpuUsage'], '1.3.6.1.4.1.999.1')

    def test_builtin_groups_device_info_and_keeps_native_marker(self):
        from net.devices.network.templates import builtin_collection_settings
        template = builtin_collection_settings('huawei', 'switch')
        self.assertTrue(template['builtin'])
        self.assertEqual(template['commands']['device_info'], ['display version', 'display device'])
        self.assertIn('cpu', template['commands'])

    def test_large_parse_result_drains_before_process_exit(self):
        value = 'x' * 100000
        rows = parse_template_output({'engine':'regex','template':r'(?P<value>x+)'}, value)
        self.assertEqual(rows,[{'value':value}])

    def test_textfsm_memory_values_normalize_to_numeric_metrics(self):
        from net.infrastructure.ssh_collectors import _template_network_data
        parser = {'engine':'textfsm', 'template':'Value TOTAL (\\d+)\nValue USED (\\d+)\n\nStart\n  ^Memory: ${TOTAL} ${USED} -> Record\n'}
        rows = parse_template_output(parser,'Memory: 2000 500')
        self.assertEqual(_template_network_data({'memory': rows})['memory'],
            {'total_bytes':2000,'used_bytes':500,'usage_percent':25})
