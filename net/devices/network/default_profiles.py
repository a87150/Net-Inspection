"""Version-scoped, editable defaults. Never overwrite an administrator's template."""
from copy import deepcopy
from pathlib import Path

from net.inspections.selection import NETWORK_FUNCTION_ITEMS

BRANDS = {'huawei': '华为 VRP', 'h3c': '华三 Comware', 'ruijie': '锐捷 RGOS', 'cisco': '思科 IOS / IOS XE'}
TYPES = {'switch':'交换机', 'router':'路由器', 'ac':'无线控制器', 'ap':'AP 基础'}
FUNCTION_SCOPES = {
    'switch': {'arp_table','mac_table','lldp_neighbors'},
    'router': {'routing_table','arp_table','lldp_neighbors'},
    'ac': {'routing_table','arp_table','lldp_neighbors','wireless_aps','wireless_clients'},
    # Fit APs are inspected through their controller; don't run AC commands on an AP.
    'ap': {'arp_table','lldp_neighbors'},
}
IP = r'\d{1,3}(?:\.\d{1,3}){3}'
MAC = r'(?:[0-9a-f]{4}[-.]){2}[0-9a-f]{4}|(?:[0-9a-f]{2}:){5}[0-9a-f]{2}'


def regex(pattern, empty=None):
    result={'engine':'regex','template':pattern,'flags':'im'}
    if empty:result['empty_pattern']=empty
    return result


def ntc(name):
    import ntc_templates
    return {'engine':'textfsm','template':(Path(ntc_templates.__file__).parent/'templates'/(name+'.textfsm')).read_text(encoding='utf-8'),'flags':''}


def function_rules(vendor):
    east=vendor in {'huawei','h3c'}
    commands={
        'routing_table':'display ip routing-table' if east else 'show ip route',
        'arp_table':('display arp all' if vendor=='huawei' else 'display arp') if east else 'show ip arp',
        'mac_table':'display mac-address' if east else 'show mac-address-table',
        'lldp_neighbors':{'huawei':'display lldp neighbor','h3c':'display lldp neighbor-information list','ruijie':'show lldp neighbors','cisco':'show lldp neighbors'}[vendor],
        'wireless_aps':{'huawei':'display ap all','h3c':'display wlan ap all','ruijie':'show ap-config summary','cisco':'show ap summary'}[vendor],
        'wireless_clients':{'huawei':'display station all','h3c':'display wlan client','ruijie':'show ac-config client','cisco':'show wireless client summary'}[vendor],
    }
    parsers={}
    parsers['routing_table']=regex(r'^\s*(?P<destination>'+IP+r'/\d{1,2})\s+(?P<protocol>\S+)\s+(?P<preference>\d+)\s+(?P<cost>\d+)\s+(?:[A-Za-z]+\s+)?(?P<next_hop>'+IP+r')\s+(?P<interface>\S+)\s*$',r'^\s*Destinations\s*:\s*0\s+Routes\s*:\s*0\s*$') if east else ntc('cisco_ios_show_ip_route')
    platform={'huawei':'huawei_vrp','h3c':'hp_comware','cisco':'cisco_ios','ruijie':'cisco_ios'}[vendor]
    files={
        'huawei':('display_arp_all','display_mac-address','display_lldp_neighbor'),
        'h3c':('display_arp','display_mac-address','display_lldp_neighbor-information_list'),
        'cisco':('show_ip_arp','show_mac-address-table','show_lldp_neighbors'),
        'ruijie':('show_ip_arp','show_mac-address-table','show_lldp_neighbors'),
    }
    for item,suffix in zip(('arp_table','mac_table','lldp_neighbors'),files[vendor]):
        parsers[item]=ntc(platform+'_'+suffix)
    # RGOS route / neighbor formats follow the documented IOS-style tables; different
    # model output is treated as missing evidence, never assumed successful.
    if vendor=='huawei':
        parsers['wireless_aps']=regex(r'^\s*(?P<id>\d+)\s+(?P<mac_address>'+MAC+r')\s+(?P<name>\S+)\s+(?P<group>\S+)\s+(?P<ip_address>'+IP+r'|-)\s+(?P<model>\S+)\s+(?P<state>\S+)\s+(?P<clients>\d+)\s+(?P<uptime>\S+)(?:[ \t]+.*)?$',r'^\s*Total:\s*0\s*$')
    elif vendor=='h3c':
        parsers['wireless_aps']=regex(r'^\s*(?P<name>\S+)\s+(?P<id>\d+)\s+(?P<state>\S+)\s+(?P<model>\S+)\s+(?P<serial>\S+)\s*$',r'^\s*Total number of APs:\s*0\s*$')
    elif vendor=='ruijie':
        parsers['wireless_aps']=regex(r'^\s*(?P<name>\S+)\s+(?P<ip_address>'+IP+r')\s+(?P<mac_address>'+MAC+r')\s+(?P<radio_info>.*?)\s+(?P<uptime>\S+)\s+(?P<state>\S+)\s*$',r'^\s*(?:Total APs|Total number of APs)\s*:\s*0\s*$')
    else:
        parsers['wireless_aps']=ntc('cisco_ios_show_ap_summary')
        parsers['wireless_aps']['empty_pattern']=r'^\s*Number of APs\s*:\s*0\s*$'
    if vendor=='h3c':
        parsers['wireless_clients']=regex(r'^\s*(?P<mac_address>'+MAC+r')\s+(?P<username>\S+)\s+(?P<ap_name>\S+)\s+(?P<radio>\d+)\s+(?P<ip_address>'+IP+r'|N/A|-)\s+(?P<details>.*\S)\s*$',r'^\s*Total number of clients:\s*0\s*$')
    else:
        parsers['wireless_clients']=regex(r'^\s*(?P<mac_address>'+MAC+r')\s+(?P<details>\S.*)$',r'^\s*(?:Number of Clients|Total STA|Total stations|Total clients)\s*:\s*0\s*$')
    return {'commands':{key:[value] for key,value in commands.items()},'parsers':parsers,'item_methods':{key:'ssh' for key in commands}}


def base_settings(vendor):
    from net.devices.network.templates import builtin_collection_settings
    from net.inspections.issues import configuration_snapshot
    levels,thresholds=configuration_snapshot('networks')
    result={'commands':deepcopy(builtin_collection_settings(vendor)['commands']), 'parsers':{},
            'thresholds':thresholds,'severity_overrides':levels,
            'item_enabled':{key:False for key in NETWORK_FUNCTION_ITEMS}}
    # Common metrics use standard/vendor MIBs. SSH remains explicitly selectable
    # and its CPU/memory parsers are usable when the operator selects it.
    result['item_methods']={key:'snmp' for key in ('device_info','cpu','memory','temperature','interface_status','vlan_status','traffic')}
    result['item_methods']['logs']='ssh'
    cpu={
        'huawei':r'(?:CPU Usage(?:\(%\))?|CPU utilization|CPU utilization rate)\s*[:=]\s*(?P<usage_percent>\d+(?:\.\d+)?)\s*%',
        'h3c':r'(?P<usage_percent>\d+(?:\.\d+)?)%\s+in\s+last\s+5\s+seconds',
        'ruijie':r'CPU utilization for five seconds:\s*(?P<usage_percent>\d+(?:\.\d+)?)%',
        'cisco':r'CPU utilization for five seconds:\s*(?P<usage_percent>\d+(?:\.\d+)?)%',
    }
    memory={
        'huawei':r'Memory Using Percentage\s*(?:Is\s*)?:?\s*(?P<usage_percent>\d+(?:\.\d+)?)\s*%',
        'h3c':r'Mem:\s+(?P<total_kb>\d+)\s+(?P<used_kb>\d+)\s+\d+',
        'ruijie':r'(?:System|Processor) Pool Total:\s*(?P<total_bytes>\d+)\s+Used:\s*(?P<used_bytes>\d+)',
        'cisco':r'Processor Pool Total:\s*(?P<total_bytes>\d+)\s+Used:\s*(?P<used_bytes>\d+)',
    }
    result['parsers']={'cpu':regex(cpu[vendor]),'memory':regex(memory[vendor])}
    # Functions live in the base as reusable definitions; child types enable only
    # applicable ones. This permits a layer-3 switch to enable routing individually.
    from net.devices.collection_profiles import merge
    return merge(result,function_rules(vendor))


def type_settings(subtype):
    active=FUNCTION_SCOPES[subtype]
    result={'item_enabled':{item:item in active for item in NETWORK_FUNCTION_ITEMS}}
    if subtype in {'router','ap'}:result['item_enabled']['vlan_status']=False
    return result


def create_default_network_templates():
    """Idempotently create four bases and their type children; preserve existing rows."""
    from django.db import transaction
    from net.models import DeviceCollectionTemplate
    candidates={vendor:base_settings(vendor) for vendor in BRANDS}
    created=[]
    with transaction.atomic():
        for vendor,label in BRANDS.items():
            base=DeviceCollectionTemplate.objects.filter(kind='networks',vendor=vendor,subtype='').first()
            if base is None:
                base=DeviceCollectionTemplate(name=label+' 基础模板',kind='networks',vendor=vendor,settings=candidates[vendor])
                base.full_clean();base.save();created.append(base)
            for subtype,title in TYPES.items():
                if DeviceCollectionTemplate.objects.filter(kind='networks',vendor=vendor,subtype=subtype).exists():continue
                row=DeviceCollectionTemplate(name=label+' '+title,kind='networks',vendor=vendor,subtype=subtype,parent=base,settings=type_settings(subtype))
                row.full_clean();row.save();created.append(row)
    return created
