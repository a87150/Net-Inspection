const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

async function loadModule() {
  const source = readFileSync(
    resolve(__dirname, '../../static/app/js/operations-overview/topology.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

function topologySnapshot() {
  return {
    nodes: [
      { id: 'networks:b', kind: 'asset', asset_type: 'networks', label: 'Edge B', parent_id: 'subnet:192.0.2.0/24', status: 'normal' },
      { id: 'subnet:192.0.2.0/24', kind: 'subnet', label: '192.0.2.0/24', parent_id: null, status: 'unknown' },
      { id: 'networks:a', kind: 'asset', asset_type: 'networks', label: 'Core A', parent_id: 'subnet:192.0.2.0/24', status: 'abnormal' },
    ],
    logical_edges: [
      { id: 'z', source: 'subnet:192.0.2.0/24', target: 'missing', relationship: 'subnet_membership' },
      { id: 'b', source: 'subnet:192.0.2.0/24', target: 'networks:b', relationship: 'subnet_membership' },
      { id: 'c', source: 'subnet:192.0.2.0/24', target: 'networks:a', relationship: 'subnet_membership' },
    ],
    interfaces: [
      { id: 'if-a', device_id: 'a', name: 'Gi1/0/1', speed_bps: 1000000000, vlan_ids: [10], is_stale: false },
      { id: 'if-b', device_id: 'b', name: 'Gi1/0/2', speed_bps: 1000000000, vlan_ids: [10], is_stale: false },
    ],
    physical_edges: [
      {
        id: 'reverse', kind: 'physical_discovered', local_interface_id: 'if-b', local_device_id: 'b',
        remote_device_id: 'a', remote_interface_id: 'if-a', protocols: ['ssh_cdp'],
        evidence_direction: 'bidirectional', resolution_status: 'resolved', status: 'current',
        confidence: '0.90', last_seen_at: '2026-09-20T09:01:00Z',
      },
      {
        id: 'forward', kind: 'physical_discovered', local_interface_id: 'if-a', local_device_id: 'a',
        remote_device_id: 'b', remote_interface_id: 'if-b', protocols: ['snmp_lldp'],
        evidence_direction: 'outbound', resolution_status: 'resolved', status: 'current',
        confidence: '0.75', last_seen_at: '2026-09-20T09:00:00Z',
      },
      {
        id: 'external', kind: 'physical_discovered', local_interface_id: 'if-a', local_device_id: 'a',
        remote_device_id: null, remote_interface_id: null, remote_chassis_id: '00:11:22:33:44:55',
        remote_system_name: 'External Edge', remote_port_id: 'Eth9', protocols: ['ssh_lldp'],
        evidence_direction: 'outbound', resolution_status: 'unresolved', status: 'stale',
        speed_bps: 100000000, vlan_ids: [20, 30], confidence: '0.60',
        last_seen_at: '2026-09-19T09:00:00Z',
      },
      {
        id: 'self', kind: 'physical_discovered', local_interface_id: 'if-a', local_device_id: 'a',
        remote_device_id: 'a', remote_interface_id: 'if-a', protocols: ['snmp_lldp'],
      },
      { id: 'invented', kind: 'inferred', local_device_id: 'a', remote_device_id: 'b' },
    ],
  };
}

test('normalizes deterministic logical and physical topology without inventing links', async () => {
  const { normalizeTopology } = await loadModule();
  const graph = normalizeTopology(topologySnapshot());

  assert.deepEqual(graph.nodes.map((node) => node.id), [
    'subnet:192.0.2.0/24', 'networks:a', 'networks:b',
    'external:00-11-22-33-44-55:eth9',
  ]);
  assert.deepEqual(graph.logicalEdges.map((edge) => edge.id), ['c', 'b']);
  assert.equal(graph.physicalEdges.length, 2);

  const resolved = graph.physicalEdges.find((edge) => edge.resolutionStatus === 'resolved');
  assert.deepEqual(resolved.protocols, ['snmp_lldp', 'ssh_cdp']);
  assert.equal(resolved.localInterfaceLabel, 'Gi1/0/1');
  assert.equal(resolved.remoteInterfaceLabel, 'Gi1/0/2');
  assert.equal(resolved.evidenceDirection, 'bidirectional');
  assert.equal(resolved.status, 'current');

  const external = graph.physicalEdges.find((edge) => edge.resolutionStatus === 'unresolved');
  assert.equal(external.target, 'external:00-11-22-33-44-55:eth9');
  assert.equal(external.protocolLabel, 'LLDP');
  assert.equal(external.statusLabel, '已过期');
  assert.equal(external.speedBps, 100000000);
  assert.deepEqual(external.vlanIds, [20, 30]);
  assert.equal(external.confidence, 0.6);
  assert.equal(external.lastSeenAt, '2026-09-19T09:00:00Z');
});

test('normalization is stable when backend arrays arrive in another order', async () => {
  const { normalizeTopology } = await loadModule();
  const first = topologySnapshot();
  const second = topologySnapshot();
  second.nodes.reverse();
  second.logical_edges.reverse();
  second.physical_edges.reverse();

  assert.deepEqual(normalizeTopology(first), normalizeTopology(second));
});

test('lays out every layer deterministically inside a narrow viewport', async () => {
  const { layoutTopology, normalizeTopology } = await loadModule();
  const graph = normalizeTopology(topologySnapshot());
  const viewport = { width: 360, height: 500 };
  const first = layoutTopology(graph, viewport);
  const second = layoutTopology(graph, viewport);

  assert.deepEqual(first, second);
  assert.deepEqual(first.legend, [
    { kind: 'subnet', label: 'IP 网段归属' },
    { kind: 'physical', label: 'LLDP/CDP 物理发现' },
    { kind: 'external', label: '未解析邻居' },
  ]);
  assert.equal(first.logicalEdges.length, 2);
  assert.equal(first.physicalEdges.length, 2);
  for (const node of first.nodes) {
    assert.ok(node.x >= first.bounds.minX && node.x <= first.bounds.maxX, node.id);
    assert.ok(node.y >= first.bounds.minY && node.y <= first.bounds.maxY, node.id);
  }
  assert.ok(first.bounds.maxX <= viewport.width);
  assert.ok(first.bounds.maxY <= viewport.height);
  assert.ok(first.nodes.find((node) => node.kind === 'external').x >= 280);
});
