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

function enterpriseSnapshot() {
  return {
    nodes: [
      { id: 'networks:access', kind: 'backbone', role: 'access_switch', tier: 3, label: 'ACCESS', child_count: 1 },
      { id: 'networks:firewall', kind: 'backbone', role: 'firewall', tier: 0, label: 'FIREWALL', child_count: 0 },
      { id: 'networks:ac', kind: 'backbone', role: 'wireless_controller', tier: 3, label: 'WLAN-AC', child_count: 1 },
      { id: 'networks:core', kind: 'backbone', role: 'core_switch', tier: 1, label: 'CORE', child_count: 0 },
      { id: 'computers:pc-1', kind: 'endpoint', role: 'computer', label: 'PC-01', parent_id: 'networks:access', attachment_source: 'inferred_subnet' },
      { id: 'networks:ap-1', kind: 'endpoint', role: 'access_point', label: 'AP-01', parent_id: 'networks:ac', attachment_source: 'physical_discovered' },
    ],
    attachment_edges: [
      { id: 'attach-pc', kind: 'endpoint_attachment', source: 'networks:access', target: 'computers:pc-1', attachment_source: 'inferred_subnet' },
      { id: 'attach-ap', kind: 'endpoint_attachment', source: 'networks:ac', target: 'networks:ap-1', attachment_source: 'physical_discovered' },
    ],
    interfaces: [
      { id: 'if-firewall', device_id: 'firewall', name: 'Eth1', speed_bps: 10000000000, vlan_ids: [10] },
      { id: 'if-core', device_id: 'core', name: 'XGE1', speed_bps: 10000000000, vlan_ids: [10] },
    ],
    physical_edges: [
      {
        id: 'forward', kind: 'physical_discovered', local_interface_id: 'if-firewall', local_device_id: 'firewall',
        remote_device_id: 'core', remote_interface_id: 'if-core', protocols: ['snmp_lldp'],
        evidence_direction: 'unilateral', resolution_status: 'resolved', status: 'stale',
        confidence: '0.80', last_seen_at: '2026-09-20T09:00:00Z',
      },
      {
        id: 'reverse', kind: 'physical_discovered', local_interface_id: 'if-core', local_device_id: 'core',
        remote_device_id: 'firewall', remote_interface_id: 'if-firewall', protocols: ['ssh_cdp'],
        evidence_direction: 'unilateral', resolution_status: 'resolved', status: 'current',
        confidence: '0.90', last_seen_at: '2026-09-20T09:01:00Z',
      },
    ],
  };
}

test('shows only backbone by default and expands one device deterministically', async () => {
  const { normalizeTopology, visibleTopology } = await loadModule();
  const graph = normalizeTopology(enterpriseSnapshot());

  assert.deepEqual(visibleTopology(graph, new Set()).nodes.map((node) => node.id), [
    'networks:firewall', 'networks:core', 'networks:access', 'networks:ac',
  ]);
  const accessExpanded = visibleTopology(graph, new Set(['networks:access']));
  assert.ok(accessExpanded.nodes.some((node) => node.id === 'computers:pc-1'));
  assert.ok(!accessExpanded.nodes.some((node) => node.id === 'networks:ap-1'));
  assert.equal(accessExpanded.attachmentEdges.length, 1);
  assert.equal(accessExpanded.attachmentEdges[0].attachmentSource, 'inferred_subnet');
});

test('reconciles expansions after refresh removes a backbone node', async () => {
  const { reconcileExpandedIds } = await loadModule();
  const graph = { nodes: [{ id: 'networks:core', kind: 'backbone' }] };

  assert.deepEqual(
    [...reconcileExpandedIds(new Set(['networks:gone', 'networks:core']), graph)],
    ['networks:core'],
  );
});

test('normalization keeps attachment and physical evidence distinct', async () => {
  const { normalizeTopology } = await loadModule();
  const graph = normalizeTopology(enterpriseSnapshot());

  assert.equal(graph.attachmentEdges.length, 2);
  assert.equal(graph.physicalEdges.length, 1);
  assert.deepEqual(graph.physicalEdges[0].protocols, ['snmp_lldp', 'ssh_cdp']);
  assert.equal(graph.physicalEdges[0].status, 'current');
  assert.equal(graph.physicalEdges[0].evidenceDirection, 'bidirectional');
  assert.equal(graph.nodes.find((node) => node.id === 'computers:pc-1').parentId, 'networks:access');
});

test('lays backbone tiers top-down and expanded children below their parent', async () => {
  const { layoutTopology, normalizeTopology, visibleTopology } = await loadModule();
  const graph = visibleTopology(
    normalizeTopology(enterpriseSnapshot()),
    new Set(['networks:access', 'networks:ac']),
  );
  const first = layoutTopology(graph, { width: 360, height: 620 });
  const second = layoutTopology(graph, { width: 360, height: 620 });
  const byId = new Map(first.nodes.map((node) => [node.id, node]));

  assert.deepEqual(first, second);
  assert.ok(byId.get('networks:firewall').y < byId.get('networks:core').y);
  assert.ok(byId.get('networks:core').y < byId.get('networks:access').y);
  assert.ok(byId.get('computers:pc-1').y > byId.get('networks:access').y);
  assert.ok(byId.get('networks:ap-1').y > byId.get('networks:ac').y);
  assert.deepEqual(first.legend, [
    { kind: 'physical', label: 'LLDP/CDP 物理链路' },
    { kind: 'inferred', label: '网段推断终端' },
    { kind: 'external', label: '未解析邻居' },
  ]);
  for (const node of first.nodes) {
    assert.ok(node.x >= first.bounds.minX && node.x <= first.bounds.maxX, node.id);
    assert.ok(node.y >= first.bounds.minY && node.y <= first.bounds.maxY, node.id);
  }
  assert.ok(first.bounds.maxX <= 360);
});
