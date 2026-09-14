from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase

from net.devices.network.default_profiles import function_rules, create_default_network_templates, BRANDS
from net.devices.network.templates import parse_template_output, execute_template_commands, validate_collection_settings
from net.infrastructure.ssh_collectors import _template_network_data
from net.inspections.device_issues import evaluate_device_issues

# Synthetic, non-production examples of the documented output layouts.
ROUTE_EAST='Destination/Mask Proto Pre Cost Flags NextHop Interface\n0.0.0.0/0 Static 60 0 RD 192.0.2.1 Vlan-interface10\n'
ROUTE_IOS='Gateway of last resort is 192.0.2.1 to network 0.0.0.0\nS* 0.0.0.0/0 [1/0] via 192.0.2.1, GigabitEthernet0/1\nC 192.0.2.0/24 is directly connected, GigabitEthernet0/1\n'
AP_OUTPUT={
 'huawei':'0 0000-0000-0001 ap1 group1 192.0.2.10 AP6010 nor 2 1H\n1 0000-0000-0002 ap2 group1 - - fault 0 -\n',
 'h3c':'AP name APID State Model Serial ID\nap1 1 R/M WA6526 TEST001\nap2 2 I WA6526 TEST002\n',
 'ruijie':'AP Name IP Address Mac Address Radio 1 Radio 2 Up/Off time State\nap1 192.0.2.10 0000.0000.0001 E 2 6 100 E 0 149 100 1:00:00 Run\nap2 192.0.2.11 0000.0000.0002 D 0 6 100 D 0 149 100 1:00:00 Quit\n',
 'cisco':'Number of APs: 2\nAP Name Slots AP Model Ethernet MAC Radio MAC Location Country IP Address State\nap1 2 C9130 0000.0000.0001 0000.0000.0101 lobby US 192.0.2.10 Registered\nap2 2 C9130 0000.0000.0002 0000.0000.0102 lobby US 192.0.2.11 Disconnected\n',
}
CLIENT_OUTPUT={
 'huawei':'STA MAC AP ID AP name Rf/WLAN Band Type Rx/Tx RSSI VLAN IP address\n0000-0000-0001 1 ap1 1/1 5G ax 0/0 -45 10 192.0.2.20\n',
 'h3c':'MAC address Username AP name RID IP address VLAN\n0000-0000-0001 N/A ap1 1 192.0.2.20 10\n',
 'ruijie':'STA MAC WLAN AP IP\n0000.0000.0001 1 ap1 192.0.2.20\n',
 'cisco':'MAC Address AP Name Type ID State\n0000.0000.0001 ap1 WLAN 1 Run\n',
}

class DefaultFunctionParsingTests(SimpleTestCase):
    def test_all_vendor_route_and_wireless_parsers_return_structured_records(self):
        for vendor in BRANDS:
            rules=function_rules(vendor);validate_collection_settings(rules)
            samples={'routing_table':ROUTE_EAST if vendor in {'h3c','huawei'} else ROUTE_IOS,'wireless_aps':AP_OUTPUT[vendor],'wireless_clients':CLIENT_OUTPUT[vendor]}
            for item,output in samples.items():
                with self.subTest(vendor=vendor,item=item):
                    parsed=parse_template_output(rules['parsers'][item],output)
                    data=_template_network_data({item:parsed})
                    self.assertGreater(data[item]['count'],0)
                    if item=='wireless_aps':
                        findings,_=evaluate_device_issues('networks',[item],data,reachable=True,status='success')
                        self.assertEqual(len(findings),1)
                        self.assertEqual(findings[0]['severity'],'warning')

    def test_arp_mac_lldp_known_tables(self):
        samples={
            'h3c':{'arp_table':'192.0.2.1 0000-0000-0001 10 GE1/0/1 20 D\n','mac_table':'0000-0000-0001 10 Learned GE1/0/1 Y\n','lldp_neighbors':'Local Interface Chassis ID Port ID System Name\nGE1/0/1 0000-0000-0001 GE1/0/2 peer\n'},
            'cisco':{'arp_table':'Internet 192.0.2.1 2 0000.0000.0001 ARPA Gi1/0/1\n','mac_table':'Vlan Mac Address Type Ports\n10 0000.0000.0001 DYNAMIC Gi1/0/1\n','lldp_neighbors':'Device ID Local Intf Hold-time Capability Port ID\npeer                 Gi1/0/1 120 B Gi1/0/2\n'},
            'huawei':{'arp_table':'192.0.2.1 0000-0000-0001 20 D-0 GE1/0/1\n','mac_table':'0000-0000-0001 10/- GE1/0/1 dynamic\n'},
        }
        samples['ruijie']=samples['cisco']
        for vendor,items in samples.items():
            for item,output in items.items():
                with self.subTest(vendor=vendor,item=item):
                    self.assertTrue(parse_template_output(function_rules(vendor)['parsers'][item],output))

    def test_command_error_is_not_a_healthy_table_and_explicit_zero_is_empty(self):
        rules=function_rules('h3c')
        settings={'commands':{'wireless_clients':rules['commands']['wireless_clients']},'parsers':{'wireless_clients':rules['parsers']['wireless_clients']}}
        data,_=execute_template_commands(settings,lambda *a,**k:'% Unrecognized command found at position')
        self.assertEqual(data['wireless_clients']['status'],'failed')
        data,_=execute_template_commands(settings,lambda *a,**k:'Total number of clients: 0')
        normalized=_template_network_data(data)
        self.assertEqual(normalized['wireless_clients'],{'records':[],'count':0})
        issues,normal=evaluate_device_issues('networks',['wireless_clients'],normalized,reachable=True,status='success')
        self.assertEqual(issues,[])
        self.assertIn('wireless_clients',normal)
        data,_=execute_template_commands(settings,lambda *a,**k:'new unsupported output format')
        self.assertEqual(data['wireless_clients']['status'],'missing')

    def test_partial_command_set_does_not_crash_on_multiple_rows_or_claim_success(self):
        settings={'commands':{'routing_table':['display ip routing-table','display ip routing-table verbose']},'parsers':{'routing_table':function_rules('h3c')['parsers']['routing_table']}}
        output=ROUTE_EAST+ROUTE_EAST.splitlines()[1]+'\n'
        data,_=execute_template_commands(settings,Mock(side_effect=[output,RuntimeError('timeout')]))
        self.assertEqual(data['routing_table']['status'],'partial')
        normalized=_template_network_data(data)
        issues,_=evaluate_device_issues('networks',['routing_table'],normalized,reachable=True,status='partial')
        self.assertEqual(issues[0]['severity'],'info')

class DefaultTemplateWorkflowTests(TestCase):
    def test_basic_ssh_cpu_and_memory_parsers_have_numeric_evidence(self):
        from net.devices.network.default_profiles import base_settings
        samples={
            'huawei':('CPU Usage : 37%', 'Memory Using Percentage Is: 50%'),
            'h3c':('37% in last 5 seconds', 'Mem: 2048 1024 1024'),
            'ruijie':('CPU utilization for five seconds: 37%', 'System Pool Total: 2048 Used: 1024 Free: 1024'),
            'cisco':('CPU utilization for five seconds: 37%/0%', 'Processor Pool Total: 2048 Used: 1024 Free: 1024'),
        }
        for vendor,(cpu,memory) in samples.items():
            settings=base_settings(vendor)
            for item,output,expected in [('cpu',cpu,37),('memory',memory,50)]:
                with self.subTest(vendor=vendor,item=item):
                    rows=parse_template_output(settings['parsers'][item],output)
                    result=_template_network_data({item:rows})
                    self.assertEqual(result[item]['usage_percent'],expected)

    def test_creates_twenty_templates_idempotently_and_scopes_function_items(self):
        from net.models import DeviceCollectionTemplate,Network_Device,InspectionProfile,TaskRun
        from net.devices.collection_profiles import resolve_collection_settings
        from net.inspections.queue import enqueue_task
        from net.inspections.executor import _device_task
        self.assertEqual(len(create_default_network_templates()),20)
        root=DeviceCollectionTemplate.objects.get(kind='networks',vendor='h3c',subtype='')
        root.settings['thresholds']['cpu']=66;root.save()
        self.assertEqual(create_default_network_templates(),[])
        root.refresh_from_db();self.assertEqual(root.settings['thresholds']['cpu'],66)
        switch=Network_Device.objects.create(ip='192.0.2.11',vendor='h3c',device_type='switch')
        router=Network_Device.objects.create(ip='192.0.2.12',vendor='h3c',device_type='router')
        ac=Network_Device.objects.create(ip='192.0.2.13',vendor='h3c',device_type='ac')
        self.assertNotIn('wireless_aps',resolve_collection_settings('networks',switch)['selected_items'])
        self.assertIn('routing_table',resolve_collection_settings('networks',router)['selected_items'])
        self.assertIn('wireless_aps',resolve_collection_settings('networks',ac)['selected_items'])
        profile=InspectionProfile.objects.create(name='功能巡检',device_type='network_device',selected_items=['mac_table','wireless_aps'])
        task=enqueue_task(profile,[switch.pk,ac.pk],TaskRun.Source.MANUAL)
        for target in task.target_runs.all():
            local=_device_task(target,task)
            self.assertEqual(set(local.selected_items_snapshot),{'config_info','mac_table' if str(target.target_id)==str(switch.pk) else 'wireless_aps'})

    def test_new_function_ssh_output_is_persisted_and_displayed(self):
        from net.models import Network_Device,InspectionProfile,TaskRun,Network_Device_Inspection
        from net.inspections.queue import enqueue_task,claim_next_task
        from net.inspections.executor import execute_target
        from net.devices.network.collector import collect_network
        from net.infrastructure.ssh_collectors import collect_network_ssh
        from net.inspections.record_summary import key_metrics
        create_default_network_templates()
        asset=Network_Device.objects.create(ip='192.0.2.55',vendor='h3c',device_type='ac',username='test',password='test')
        profile=InspectionProfile.objects.create(name='无线检查',device_type='network_device',selected_items=['wireless_aps'])
        enqueue_task(profile,[asset.pk],TaskRun.Source.MANUAL)
        task=claim_next_task('default-template-test',30)
        client=Mock();client.find_prompt.return_value='<AC>'
        client.send_command.side_effect=lambda command,**kwargs:'' if 'screen-length' in command else AP_OUTPUT['h3c']
        # Native config capture has separate coverage; keep this integration focused
        # on selection -> real SSH parser -> issue evaluator -> database persistence.
        def collect(device,timeout=12,selected_items=None):
            return collect_network(device,timeout,[x for x in selected_items if x!='config_info'],ssh_collector=collect_network_ssh)
        with patch('net.inspections.executor.collect_network',side_effect=collect),patch('net.infrastructure.ssh_collectors._connect_network',return_value=client):
            execute_target(task.target_runs.get(),worker_id='default-template-test')
        record=Network_Device_Inspection.objects.get()
        self.assertEqual(record.details['wireless_aps']['count'],2)
        self.assertTrue(any(f['analysis_item']=='wireless_aps' for f in record.details['issue_findings']))
        self.assertIn('无线 AP 状态 2 条',key_metrics(record.details))
        self.assertIn('display wlan ap all',record.raw_output)
