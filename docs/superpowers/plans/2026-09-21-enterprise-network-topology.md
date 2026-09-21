# Enterprise Network Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat subnet graph with a professional, top-down enterprise network backbone whose PC, server, AP, and security endpoints expand on demand.

**Architecture:** Keep the existing page and JSON endpoint, but move topology presentation rules into a focused Python module that classifies network roles, preserves real LLDP/CDP backbone edges, and deterministically attaches endpoints by evidence then subnet. The Vue page receives backbone and endpoint nodes together, renders only the backbone initially, and derives the visible graph from an in-memory expanded-device set. The isolated demo seed creates deterministic enterprise assets and physical topology rows; production never falls back to simulation.

**Tech Stack:** Django ORM and templates, Python `ipaddress`, Vue 3 ESM already vendored in the repository, SVG, CSS, Node test runner, Django `TestCase`.

**Spec:** `docs/superpowers/specs/2026-09-21-enterprise-network-topology-design.md`

## Global Constraints

- Keep `/operations/overview/` and `/operations/overview/data/` unchanged.
- Do not modify inspection, LLDP/CDP collection, database models, or migrations.
- Do not include people, domain accounts, domain computers, or domain groups.
- Simulated devices and links exist only in the isolated `deploy.demo` database through `seed_demo_data`.
- Production with no real backbone data displays an honest empty state and never generated devices.
- Use existing Bootstrap, CSS, Vue bundle, and native browser APIs; add no package or online asset.
- Real LLDP/CDP links and inferred endpoint attachments must remain visually and semantically distinct.

## Review Focus

- Mixed Chinese/English and blank `device_type` values must classify deterministically and never hide a physically linked network device; covered in Task 1 role-classification tests.
- A PC with multiple IP addresses must attach once to the most specific stable same-subnet candidate; covered in Task 2 attachment tests.
- Stale LLDP/CDP links must remain physical links and never be relabeled as inferred; covered in Task 2 payload tests and Task 3 normalization tests.
- A refresh that removes a previously expanded backbone node must remove that stale expansion state without breaking rendering; covered in Task 3 expansion-state tests.
- A database with network devices but no LLDP/CDP links must show disconnected backbone nodes plus a collection hint, while an empty database shows an empty state; covered in Task 1 and Task 4 tests.

---

## File Structure

- Create `index/dashboard/topology_presentation.py`: role normalization, tier assignment, endpoint construction, deterministic subnet attachment, and final presentation payload.
- Modify `index/dashboard/operations.py`: query allowlisted assets and physical data, delegate presentation to the new module, keep the existing view and endpoint.
- Modify `net/management/commands/seed_demo_data.py`: seed deterministic enterprise network devices, endpoints, interfaces, and LLDP/CDP links in the demo database only.
- Modify `static/app/js/operations-overview/topology.js`: normalize the new backbone/endpoint contract, select a visible graph from expanded IDs, and lay it out top-down.
- Modify `static/app/js/operations-overview/app.js`: own expanded-node state, toggle nodes, preserve valid expansions on refresh, and select both physical and inferred edges.
- Modify `index/templates/dashboard/operations_overview.html`: device-card SVG nodes, expand controls, empty/no-link states, and updated link detail copy.
- Modify `static/app/css/operations-overview.css`: enterprise topology card, link, tier, badge, focus, mobile, and reduced-motion styles.
- Modify existing dashboard, demo-seed, frontend, and UI-contract tests; add no new test framework.

### Task 1: Classify Backbone Devices and Produce a Backbone-First Payload

**Files:**
- Create: `index/dashboard/topology_presentation.py`
- Modify: `index/dashboard/operations.py`
- Test: `tests/dashboard/test_operations_overview.py`

**Interfaces:**
- Consumes: `Network_Device` rows and the allowlisted dictionary returned by `current_topology_payload(include_stale=True, limit=1000)`.
- Produces: `network_role(device) -> str`, `network_tier(role) -> int`, and `build_enterprise_topology(*, network_devices, computers, servers, monitors, physical) -> dict`.
- The returned dictionary contains `mode`, `nodes`, `attachment_edges`, `interfaces`, `physical_edges`, `asset_truncation`, and `has_physical_links`.

- [ ] **Step 1: Replace the subnet-oriented tests with failing backbone contract tests**

Add tests equivalent to:

```python
def test_network_roles_accept_chinese_english_and_keep_linked_unknown_devices(self):
    firewall = Network_Device.objects.create(device_name='EDGE-FW-01', ip='10.0.0.1', device_type='防火墙')
    core = Network_Device.objects.create(device_name='CORE-SW-01', ip='10.0.0.2', device_type='core switch')
    ac = Network_Device.objects.create(device_name='WLAN-AC-01', ip='10.0.0.3', device_type='AC')
    unknown = Network_Device.objects.create(device_name='MYSTERY-01', ip='10.0.0.4', device_type='')
    topology = build_operations_snapshot(now=self.now)['topology']
    roles = {node['id']: node['role'] for node in topology['nodes'] if node['kind'] == 'backbone'}
    self.assertEqual(roles[f'networks:{firewall.pk}'], 'firewall')
    self.assertEqual(roles[f'networks:{core.pk}'], 'core_switch')
    self.assertEqual(roles[f'networks:{ac.pk}'], 'wireless_controller')
    self.assertEqual(roles[f'networks:{unknown.pk}'], 'network_other')

def test_empty_inventory_has_honest_empty_backbone_payload(self):
    topology = build_operations_snapshot(now=self.now)['topology']
    self.assertEqual(topology['nodes'], [])
    self.assertFalse(topology['has_physical_links'])
    self.assertEqual(topology['mode'], 'empty')
```

Also assert that AP is classified as `endpoint`, role tiers are firewall `0`, core `1`, distribution `2`, access/AC `3`, physical endpoints survive the network cap, secrets are absent, and the outer schema contains only topology data.

- [ ] **Step 2: Run the dashboard tests and verify the new contract fails**

Run:

```powershell
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py test tests.dashboard.test_operations_overview
```

Expected: FAIL because nodes still use `asset`/`subnet` and have no `role` or `tier`.

- [ ] **Step 3: Implement deterministic role classification and backbone node serialization**

Create constants and pure helpers in `topology_presentation.py`:

```python
ROLE_PATTERNS = (
    ('firewall', ('firewall', '防火墙', '安全网关', '出口网关', 'gateway')),
    ('router', ('router', '路由器')),
    ('core_switch', ('core switch', '核心交换机', '核心')),
    ('distribution_switch', ('distribution switch', 'aggregation', '汇聚交换机', '汇聚')),
    ('access_switch', ('access switch', '接入交换机', '交换机', 'switch')),
    ('wireless_controller', ('wireless controller', '无线控制器', 'wlc', 'ac')),
    ('access_point', ('access point', '无线接入点', 'ap')),
)
ROLE_TIERS = {
    'firewall': 0, 'router': 0, 'core_switch': 1,
    'distribution_switch': 2, 'access_switch': 3,
    'wireless_controller': 3, 'network_other': 3,
}

def network_role(device):
    haystack = ' '.join(filter(None, (device.device_type, device.device_name, device.model))).casefold()
    for role, patterns in ROLE_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return role
    return 'network_other'

def network_tier(role):
    return ROLE_TIERS.get(role, 3)
```

Order patterns from specific to general so `核心交换机` is not consumed by generic `交换机`, and match `access_point` before the short `ac` token using token-aware short-code matching. Serialize allowlisted display fields only: `id`, `kind`, `role`, `tier`, `label`, `subtitle`, `ip`, `vendor`, `model`, `status`, `url`, and `child_count`.

- [ ] **Step 4: Delegate `operations.py` to the presentation builder**

Replace `_build_asset_nodes` with bounded queryset loading and call:

```python
return build_enterprise_topology(
    network_devices=selected_networks,
    computers=selected_computers,
    servers=selected_servers,
    monitors=selected_monitors,
    physical=current_topology_payload(include_stale=True, limit=1000),
    asset_truncation=truncation,
)
```

Keep `_safe_region`, `build_operations_snapshot`, `operations_overview`, and `operations_overview_data`. Bump `schema_version` from `2` to `3` because the JSON shape changes, while preserving both route names and cache middleware behavior.

- [ ] **Step 5: Run the focused tests and commit**

Run the Task 1 command and expect PASS, then:

```powershell
git add index/dashboard/topology_presentation.py index/dashboard/operations.py tests/dashboard/test_operations_overview.py
git commit -m "feat: build backbone-first topology payload"
```

### Task 2: Attach Endpoints Without Inventing Backbone Links

**Files:**
- Modify: `index/dashboard/topology_presentation.py`
- Modify: `index/dashboard/operations.py`
- Test: `tests/dashboard/test_operations_overview.py`

**Interfaces:**
- Consumes: Task 1 `network_role`, `network_tier`, backbone node IDs, the physical link allowlist, and asset IP strings.
- Produces: `address_networks(value) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]`, `attach_endpoints(backbones, endpoints, physical_edges) -> list[dict]`, endpoint nodes with `parent_id`, `attachment_source`, and attachment edges with `kind='endpoint_attachment'`.

- [ ] **Step 1: Write failing attachment priority and stability tests**

Add tests equivalent to:

```python
def test_endpoint_attachment_prefers_access_then_ac_then_higher_tiers_and_is_stable(self):
    core = Network_Device.objects.create(device_name='CORE', ip='10.30.0.2', device_type='core switch')
    access = Network_Device.objects.create(device_name='ACCESS', ip='10.30.0.3', device_type='access switch')
    pc = Computer.objects.create(computer_name='PC-01', ip_addresses='10.30.0.90, 10.31.0.90')
    first = build_operations_snapshot(now=self.now)['topology']
    second = build_operations_snapshot(now=self.now)['topology']
    pc_node = next(node for node in first['nodes'] if node['id'] == f'computers:{pc.pk}')
    self.assertEqual(pc_node['parent_id'], f'networks:{access.pk}')
    self.assertEqual(pc_node['attachment_source'], 'inferred_subnet')
    self.assertEqual(first['attachment_edges'], second['attachment_edges'])

def test_ap_physical_relationship_beats_subnet_inference_and_stale_stays_physical(self):
    switch = Network_Device.objects.create(device_name='ACCESS', ip='10.40.0.2', device_type='access switch')
    ap = Network_Device.objects.create(device_name='AP-01', ip='10.40.0.3', device_type='ap')
    task, target = self.inspection_task(status=TaskRun.Status.SUCCESS, target_id=str(switch.pk))
    batch = TopologyDiscoveryBatch.objects.create(
        source_task=task, source_target=target, device=switch,
        protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
        status=TopologyDiscoveryBatch.Status.SUCCESS,
        started_at=self.now, collected_at=self.now, finished_at=self.now,
    )
    switch_if = NetworkTopologyInterface.objects.create(
        device=switch, stable_key='ifindex:1', name='Gi1/0/1',
        first_seen_at=self.now, last_seen_at=self.now, last_batch=batch,
    )
    ap_if = NetworkTopologyInterface.objects.create(
        device=ap, stable_key='ifindex:1', name='Eth0',
        first_seen_at=self.now, last_seen_at=self.now,
    )
    NetworkTopologyLink.objects.create(
        stable_link_key='a' * 64, local_interface=switch_if,
        remote_device=ap, remote_interface=ap_if,
        protocols=['snmp_lldp'], resolution_status='resolved', status='stale',
        confidence='0.90', first_seen_at=self.now, last_seen_at=self.now,
        last_batch=batch,
    )
    topology = build_operations_snapshot(now=self.now)['topology']
    ap_node = next(node for node in topology['nodes'] if node['id'] == f'networks:{ap.pk}')
    self.assertEqual(ap_node['parent_id'], f'networks:{switch.pk}')
    self.assertEqual(ap_node['attachment_source'], 'physical_discovered')
    self.assertEqual(topology['physical_edges'][0]['status'], 'stale')
```

Add cases for invalid IPs becoming unattached, an endpoint with multiple addresses producing one attachment, and unknown network endpoints not being lost.

- [ ] **Step 2: Run the focused tests and verify failure**

Run the Task 1 dashboard command. Expected: FAIL because there are no endpoint attachments or attachment-source fields.

- [ ] **Step 3: Implement network parsing and deterministic candidate selection**

Use exact defaults already approved for unmasked addresses:

```python
DEFAULT_PREFIX_LENGTH = {4: 24, 6: 64}
ATTACHMENT_ROLE_PRIORITY = {
    'access_switch': 0, 'wireless_controller': 1,
    'distribution_switch': 2, 'core_switch': 3,
    'router': 4, 'firewall': 5, 'network_other': 6,
}

def address_networks(value):
    networks = set()
    for candidate in re.split(r'[,;|\s]+', str(value or '').strip()):
        if not candidate:
            continue
        try:
            interface = ip_interface(candidate)
            if '/' not in candidate:
                interface = ip_interface(
                    f'{candidate}/{DEFAULT_PREFIX_LENGTH[interface.version]}'
                )
        except ValueError:
            continue
        networks.add(interface.network)
    return tuple(sorted(networks, key=lambda network: (network.version, network.prefixlen, str(network))))

def choose_subnet_parent(endpoint_networks, backbone_nodes):
    candidates = []
    for node in backbone_nodes:
        overlaps = [
            max(endpoint.prefixlen, backbone.prefixlen)
            for endpoint in endpoint_networks
            for backbone in node['_networks']
            if endpoint.version == backbone.version and endpoint.overlaps(backbone)
        ]
        if overlaps:
            candidates.append((max(overlaps), node))
    return min(candidates, key=lambda item: (
        -item[0], ATTACHMENT_ROLE_PRIORITY[item[1]['role']],
        item[1]['tier'], item[1]['id'],
    ), default=(None, None))[1]
```

For Network_Device endpoints such as APs, first inspect resolved physical links for a backbone peer. For PC, server, and security endpoints, use the subnet rule. Emit unattached endpoints with `parent_id=None` and no fabricated edge.

- [ ] **Step 4: Update counts, truncation, and payload safety**

Set each backbone node's `child_count` from attached endpoints. Retain per-kind limits and `asset_truncation`; preserve every physical network endpoint even when the cap is exceeded. Remove internal `_networks` values before serialization and assert no credentials, communities, API URLs with secrets, or passwords enter the payload.

- [ ] **Step 5: Run dashboard tests and commit**

Run the focused dashboard command and expect PASS, then:

```powershell
git add index/dashboard/topology_presentation.py index/dashboard/operations.py tests/dashboard/test_operations_overview.py
git commit -m "feat: attach topology endpoints deterministically"
```

### Task 3: Normalize, Expand, and Lay Out the Visible Graph

**Files:**
- Modify: `static/app/js/operations-overview/topology.js`
- Modify: `static/app/js/operations-overview/app.js`
- Modify: `tests/frontend/operations_overview_topology.test.js`
- Modify: `tests/frontend/operations_overview_state.test.js`

**Interfaces:**
- Consumes: schema v3 nodes with `kind` (`backbone`, `endpoint`), `role`, `tier`, `parent_id`, and `attachment_edges`; existing physical edge/interface allowlists.
- Produces: `normalizeTopology(snapshot)`, `visibleTopology(graph, expandedIds)`, `reconcileExpandedIds(expandedIds, graph)`, and `layoutTopology(graph, viewport)`.

- [ ] **Step 1: Write failing frontend tests for visibility, refresh, and vertical tiers**

Replace subnet fixtures with firewall/core/access/AC/AP/PC fixtures and add:

```javascript
test('shows only backbone by default and expands one device deterministically', async () => {
  const { normalizeTopology, visibleTopology } = await loadModule();
  const graph = normalizeTopology(enterpriseSnapshot());
  assert.deepEqual(visibleTopology(graph, new Set()).nodes.map((node) => node.id), [
    'networks:firewall', 'networks:core', 'networks:access', 'networks:ac',
  ]);
  const expanded = visibleTopology(graph, new Set(['networks:access']));
  assert.ok(expanded.nodes.some((node) => node.id === 'computers:pc-1'));
  assert.ok(!expanded.nodes.some((node) => node.id === 'networks:ap-1'));
});

test('reconciles expansions after refresh removes a backbone node', async () => {
  const { reconcileExpandedIds } = await loadModule();
  const graph = { nodes: [{ id: 'networks:core', kind: 'backbone' }] };
  assert.deepEqual([...reconcileExpandedIds(new Set(['networks:gone', 'networks:core']), graph)], ['networks:core']);
});
```

Assert that physical and attachment edges remain distinct, stale physical status survives, reciprocal physical records merge, tier Y coordinates increase top-down, siblings are deterministic, 360px layouts remain in bounds, and AP expands only under its selected parent.

- [ ] **Step 2: Run frontend tests and verify failure**

Run:

```powershell
node --test tests/frontend/operations_overview_topology.test.js tests/frontend/operations_overview_state.test.js
```

Expected: FAIL because `visibleTopology` and `reconcileExpandedIds` do not exist and layout is horizontal.

- [ ] **Step 3: Implement normalization and visibility as pure functions**

Use:

```javascript
export function reconcileExpandedIds(expandedIds, graph) {
  const valid = new Set(graph.nodes.filter((node) => node.kind === 'backbone').map((node) => node.id));
  return new Set([...expandedIds].filter((id) => valid.has(id)));
}

export function visibleTopology(graph, expandedIds = new Set()) {
  const nodes = graph.nodes.filter((node) => node.kind === 'backbone'
    || (node.kind === 'endpoint' && node.parentId && expandedIds.has(node.parentId)));
  const ids = new Set(nodes.map((node) => node.id));
  return {
    ...graph,
    nodes,
    attachmentEdges: graph.attachmentEdges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
    physicalEdges: graph.physicalEdges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}
```

Normalize `parent_id` to `parentId`, `attachment_source` to `attachmentSource`, and attachment edges separately from physical edges. Never promote an attachment edge into the physical edge array.

- [ ] **Step 4: Implement deterministic top-down layout**

Group backbone nodes by numeric tier; assign tier rows from top to bottom and distribute siblings across width. Place expanded children in a compact grid below their parent while reserving vertical space before the next tier. Return at least a 620px logical height and increase height based on the largest expanded group, rather than clipping children. Use straight or softly orthogonal cubic paths suited to vertical flow.

- [ ] **Step 5: Add Vue expansion state and reconcile it on refresh**

In `app.js`, add `expandedNodeIds: new Set()` and methods:

```javascript
toggleNode(node) {
  if (node.kind !== 'backbone' || !node.childCount) return;
  const next = new Set(this.expandedNodeIds);
  next.has(node.id) ? next.delete(node.id) : next.add(node.id);
  this.expandedNodeIds = next;
  this.liveStatus = `${node.label}${next.has(node.id) ? '已展开' : '已收起'}，${node.childCount} 个终端`;
}
```

Compute a normalized full graph, reconcile expansions in `syncControllerState`, then pass the visible graph to layout. Preserve expansions only for backbone IDs still present. Let Enter and Space call the same toggle method through the template in Task 4.

- [ ] **Step 6: Run frontend tests and commit**

Run the Task 3 command and expect PASS, then:

```powershell
git add static/app/js/operations-overview/topology.js static/app/js/operations-overview/app.js tests/frontend/operations_overview_topology.test.js tests/frontend/operations_overview_state.test.js
git commit -m "feat: add expandable enterprise topology layout"
```

### Task 4: Render Professional Device Cards and Accessible States

**Files:**
- Modify: `index/templates/dashboard/operations_overview.html`
- Modify: `static/app/css/operations-overview.css`
- Modify: `static/app/js/operations-overview/app.js`
- Modify: `tests/frontend/test_ui_design_contracts.py`

**Interfaces:**
- Consumes: Task 3 positioned nodes, `toggleNode(node)`, `expandedNodeIds`, physical edges, and attachment edges.
- Produces: accessible SVG device cards, expanded badges, distinct edge classes, empty/no-physical-link messages, and mobile-safe controls.

- [ ] **Step 1: Write failing UI-contract tests**

Add assertions equivalent to:

```python
def test_network_topology_uses_expandable_device_cards_and_distinct_edges(self):
    response = self.client.get(reverse('operations_overview'))
    self.assertContains(response, 'data-topology-device-card')
    self.assertContains(response, '@click="toggleNode(node)"')
    self.assertContains(response, 'topology-edge--physical')
    self.assertContains(response, 'topology-edge--inferred')
    self.assertContains(response, '暂无可绘制的主要网络设备')
    self.assertNotContains(response, 'IP 网段归属')
```

Add CSS contract checks for `:focus-visible`, `prefers-reduced-motion`, 44px mobile controls, `.topology-device-card`, `.topology-device-card__badge`, and no page-level `overflow-x`.

- [ ] **Step 2: Run the UI-contract tests and verify failure**

Run:

```powershell
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py test tests.frontend.test_ui_design_contracts
```

Expected: FAIL because the page still uses circles and subnet copy.

- [ ] **Step 3: Replace circle nodes with SVG device cards**

Render backbone nodes as `<g data-topology-device-card>` with a rounded `<rect>`, a small local SVG icon drawn with paths/rectangles (no external images), role label, name, management IP, vendor, status text, and a child-count badge. Use `role="button"`, `tabindex="0"`, `aria-expanded`, Enter, and Space only when `child_count > 0`; retain a separate details link so expanding does not unexpectedly navigate.

Render endpoints as smaller cards with explicit type text (`PC`, `服务器`, `AP`, `摄像头`, `门禁`, or `终端`). Do not encode type or status only by color.

- [ ] **Step 4: Render edge sources and honest states**

Loop `topology.attachmentEdges` separately with class `topology-edge--inferred` or `topology-edge--attachment-physical` based on `attachmentSource`. Keep LLDP/CDP in `topology-edge--physical`. Update detail text so inferred edges show `网段推断` and never show confidence/protocol fields that do not exist.

Add:

```html
<div v-if="snapshot.topology?.mode === 'empty'" class="topology-empty-state" role="status">
  <strong>暂无可绘制的主要网络设备</strong>
  <span>请先维护防火墙、交换机、路由器或无线 AC。</span>
</div>
<p v-else-if="!snapshot.topology?.has_physical_links" class="topology-collection-hint">
  已显示主要网络设备，尚未采集到 LLDP/CDP 物理链路。
</p>
```

- [ ] **Step 5: Apply professional responsive topology styling**

Use restrained dark glass cards, consistent type icons, 1.5-2px physical links, 1px inferred dashes, visible selected/focus states, and tier labels. Keep desktop cards readable without neon overload. At `max-width: 767.98px`, wrap toolbar controls, retain minimum 44px hit targets, increase SVG logical height as needed, and keep horizontal movement inside `.topology-canvas-shell`. Under `prefers-reduced-motion: reduce`, disable card/link transitions and ambient motion.

- [ ] **Step 6: Run UI and frontend tests and commit**

Run the Task 2 and Task 3 frontend commands and expect PASS, then:

```powershell
git add index/templates/dashboard/operations_overview.html static/app/css/operations-overview.css static/app/js/operations-overview/app.js tests/frontend/test_ui_design_contracts.py
git commit -m "style: render professional enterprise topology cards"
```

### Task 5: Seed an Isolated Enterprise Demo Topology

**Files:**
- Modify: `net/management/commands/seed_demo_data.py`
- Modify: `tests/architecture/test_demo_seed.py`
- Test: `tests/dashboard/test_operations_overview.py`

**Interfaces:**
- Consumes: existing `_demo_uuid`, fixed-ownership guards, `Network_Device`, `Computer`, `Server`, `SecurityDevice`, `NetworkTopologyInterface`, and `NetworkTopologyLink`.
- Produces: `Command._seed_enterprise_topology(anchor, networks) -> None`, deterministic demo interfaces/links, and expanded network/endpoint seed specs.

- [ ] **Step 1: Write failing deterministic demo-topology tests**

Add:

```python
@patch('requests.sessions.Session.request')
def test_demo_seed_creates_enterprise_backbone_without_network_calls(self, request_mock):
    call_command('seed_demo_data', reset=True, stdout=StringIO())
    roles = set(Network_Device.objects.values_list('device_type', flat=True))
    self.assertTrue({'firewall', 'core switch', 'distribution switch', 'access switch', 'ac', 'ap'} <= roles)
    self.assertGreaterEqual(NetworkTopologyLink.objects.filter(status='current').count(), 7)
    self.assertTrue(Computer.objects.filter(ip_addresses__startswith='10.10.10.').exists())
    self.assertTrue(Server.objects.filter(ip__startswith='10.20.20.').exists())
    self.assertTrue(SecurityDevice.objects.filter(ip__startswith='10.30.30.').exists())
    request_mock.assert_not_called()
```

Extend the repeatability snapshot with interface IDs, stable keys, link IDs, and stable link keys. Run `seed_demo_data --reset` twice and assert snapshots are identical. Assert all seeded link endpoints belong to deterministic demo network devices.

- [ ] **Step 2: Run demo-seed tests and verify failure**

Run:

```powershell
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py test tests.architecture.test_demo_seed
```

Expected: FAIL because only the existing three demonstration network devices exist and there are no enterprise topology links.

- [ ] **Step 3: Expand deterministic asset specs**

Update `_seed_networks` to include fixed identities and non-routable/private demonstration addresses for two firewalls, two cores, office/server/security distribution or access switches, one AC, and APs. Update PC/server/security demo IPs so each group shares the intended access-device subnet. Preserve offline-only credentials and disabled schedules; do not call discovery collectors.

Use English canonical `device_type` values (`firewall`, `core switch`, `distribution switch`, `access switch`, `ac`, `ap`) while retaining Chinese display names.

- [ ] **Step 4: Seed interfaces and resolved LLDP/CDP links directly and idempotently**

Call `_seed_enterprise_topology(anchor, networks)` immediately after `_seed_networks`. For each trunk pair, upsert deterministic local/remote interfaces and one link:

```python
stable_key = hashlib.sha256(f'{local.pk}:{remote.pk}:demo-link'.encode()).hexdigest()
NetworkTopologyLink.objects.update_or_create(
    stable_link_key=stable_key,
    defaults={
        'local_interface': local_interface,
        'remote_device': remote,
        'remote_interface': remote_interface,
        'remote_chassis_id': f'demo-{remote.pk}',
        'remote_port_id': remote_interface.name,
        'remote_system_name': remote.device_name or '',
        'remote_management_addresses': [remote.ip],
        'protocols': ['snmp_lldp'],
        'evidence_direction': 'bidirectional',
        'resolution_status': 'resolved',
        'status': 'current',
        'speed_bps': 10_000_000_000,
        'vlan_ids': [10, 20, 30],
        'confidence': '0.95',
        'first_seen_at': anchor,
        'last_seen_at': anchor,
    },
)
```

Keep `last_batch=None`; demo links are offline fixtures, not fabricated inspection executions. Update fixed-owner preflight checks so a UUID collision raises `CommandError` instead of overwriting user data.

- [ ] **Step 5: Update existing exact demo counts and task selection assumptions**

Change tests and seed code that assume exactly three network devices or use `number % 3` to use the returned deterministic list length or select the first three inspection-capable trunk devices explicitly. Preserve the existing connection-mode coverage (`ssh`, `hybrid`, `auto`) and downloadable-configuration fixtures.

- [ ] **Step 6: Run seed, dashboard, and full topology tests and commit**

Run:

```powershell
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py test tests.architecture.test_demo_seed tests.dashboard.test_operations_overview tests.topology
```

Expect PASS, then:

```powershell
git add net/management/commands/seed_demo_data.py tests/architecture/test_demo_seed.py tests/dashboard/test_operations_overview.py
git commit -m "feat: seed isolated enterprise demo topology"
```

### Task 6: Browser Verification and Full Regression

**Files:**
- Modify only if verification reveals a defect in files already listed above.
- Test: existing frontend, dashboard, architecture, topology, network collection, and UI suites.

**Interfaces:**
- Consumes: complete schema v3 topology page and isolated demo database.
- Produces: a clean working tree with passing automated and browser verification; no persistent browser or demo worker processes.

- [ ] **Step 1: Run static and migration checks**

Run:

```powershell
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

Expected: no Django issues, no migrations, no whitespace errors.

- [ ] **Step 2: Run frontend and relevant Django regression suites**

Run:

```powershell
node --test tests/frontend/*.test.js
$env:NET_ENV_FILE='C:\Users\a8715\Desktop\code\py\net\.absent-test.env'
$env:DB_ENGINE='sqlite'
.\.venv\Scripts\python.exe manage.py test tests.frontend tests.architecture tests.dashboard tests.topology tests.devices.network.test_lldp_ssh tests.devices.network.test_lldp_snmp tests.devices.network.test_hybrid_collector
```

Expected: all tests pass without accessing real Feishu, DingTalk, domain, or device services.

- [ ] **Step 3: Start the isolated demo without a worker and verify desktop interaction**

Run `\.venv\Scripts\python.exe -m deploy.demo --no-worker --port 8765`, log in with a temporary local visual-audit account, and verify at 1440×900:

- only firewall/switch/router/AC backbone cards are visible initially;
- the seeded LLDP/CDP backbone forms a top-down enterprise hierarchy;
- clicking an access switch expands its PCs and APs, clicking again collapses them;
- clicking the security switch expands cameras/door access devices;
- physical and inferred links have visibly different styles and correct detail text;
- no horizontal page overflow and no browser console errors.

- [ ] **Step 4: Verify mobile and accessibility behavior**

At 360×800, verify toolbar wrapping, 44px controls, canvas-contained movement, readable cards, keyboard Enter/Space expansion, visible focus, updated `aria-expanded`, text-equivalent node list, and reduced-motion behavior. Confirm the page body never becomes horizontally scrollable.

- [ ] **Step 5: Stop all verification processes and commit any verification-only fixes**

Stop the exact demo server session and the exact headless browser profile processes. If verification required fixes, rerun Steps 1-4 and commit only those focused changes:

```powershell
git add index/dashboard/topology_presentation.py index/dashboard/operations.py index/templates/dashboard/operations_overview.html static/app/css/operations-overview.css static/app/js/operations-overview/app.js static/app/js/operations-overview/topology.js net/management/commands/seed_demo_data.py tests/dashboard/test_operations_overview.py tests/architecture/test_demo_seed.py tests/frontend/operations_overview_topology.test.js tests/frontend/operations_overview_state.test.js tests/frontend/test_ui_design_contracts.py
git commit -m "fix: polish enterprise topology interaction"
```

- [ ] **Step 6: Confirm final repository state**

Run `git status --short` and `git log -6 --oneline`. Expected: no uncommitted tracked changes, and the topology implementation is represented by focused commits from Tasks 1-5 plus an optional verification-fix commit.
