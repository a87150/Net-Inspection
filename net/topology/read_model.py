"""Explicit field allowlists for current physical topology."""
from net.models import NetworkTopologyInterface, NetworkTopologyLink


def interface_payload(row):
    return {'id':str(row.pk),'device_id':str(row.device_id),'stable_key':row.stable_key,
            'if_index':row.if_index,'name':row.name,'description':row.description,
            'mac_address':row.mac_address,'admin_status':row.admin_status,'oper_status':row.oper_status,
            'speed_bps':row.speed_bps,'vlan_ids':row.vlan_ids,'is_stale':row.is_stale,
            'last_seen_at':row.last_seen_at.isoformat()}


def link_payload(row):
    return {'id':str(row.pk),'kind':'physical_discovered','local_interface_id':str(row.local_interface_id),
            'local_device_id':str(row.local_interface.device_id),'remote_device_id':str(row.remote_device_id) if row.remote_device_id else None,
            'remote_interface_id':str(row.remote_interface_id) if row.remote_interface_id else None,
            'remote_chassis_id':row.remote_chassis_id,'remote_port_id':row.remote_port_id,
            'remote_port_description':row.remote_port_description,'remote_system_name':row.remote_system_name,
            'remote_management_addresses':row.remote_management_addresses,'protocols':row.protocols,
            'evidence_direction':row.evidence_direction,'resolution_status':row.resolution_status,
            'status':row.status,'speed_bps':row.speed_bps,'vlan_ids':row.vlan_ids,
            'confidence':str(row.confidence),'last_seen_at':row.last_seen_at.isoformat()}


def current_topology_payload(*, include_stale=False, limit=1000):
    limit=max(1,min(int(limit),5000))
    interfaces=NetworkTopologyInterface.objects.select_related('device').order_by('device_id','stable_key')[:limit]
    links=NetworkTopologyLink.objects.select_related('local_interface').order_by('-last_seen_at','id')
    if not include_stale:links=links.filter(status='current')
    links=links[:limit]
    return {'interfaces':[interface_payload(row) for row in interfaces],
            'links':[link_payload(row) for row in links]}
