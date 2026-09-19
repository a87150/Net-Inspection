# Network Topology Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inspection-driven LLDP/CDP physical topology discovery, durable current topology/evidence, and authenticated read-only APIs.

**Architecture:** Reuse the existing `lldp_neighbors` network inspection item, TaskRun/TaskTargetRun lifecycle, template inheritance, SNMP and Netmiko collectors. Protocol adapters produce a normalized topology payload outside transactions; a focused service persists batches, interfaces, observations and current links in short idempotent transactions. Read models expose explicit whitelists to topology APIs and the future operations overview adapter.

**Tech Stack:** Django 6.0 models/views/test runner, MariaDB/SQLite-compatible migrations, pysnmp, Netmiko, TextFSM/regex templates, existing Worker and page-cache infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-19-network-topology-backend-design.md`

## Global Constraints

- Only explicit LLDP/CDP evidence may create physical links; ARP, FDB/MAC, VLAN, subnet and name similarity never create links.
- Continue using internal item key `lldp_neighbors`; display it as “拓扑发现（LLDP/CDP）”.
- Use existing inspection profiles, schedules, TaskRun, TaskTargetRun, cancellation, leases and retries; do not add a scheduler.
- Perform device I/O, parsing, sanitization and hashing outside database transactions.
- Preserve unresolved/conflicting endpoints and stale links; never automatically delete current link rows.
- Store only sanitized evidence capped at 64 KiB; ordinary APIs never return it.
- Retain observations for 90 days, pruning in bounded batches.
- Do not connect to real devices in automated tests.
- Do not stage or overwrite the existing uncommitted personnel-sync files: `docs/identity-management.md`, `net/people/directory/sync.py`, `tests/people/test_directory_sync.py`.

## Review Focus

- Partial protocol success must refresh valid links without aging unseen links; Task 6 adds this regression.
- A remote name matching multiple devices must remain conflicted and unresolved; Task 5 adds this regression.
- SNMP complete-empty must differ from timeout or a partial walk; Task 3 adds this regression.
- Retried TaskTargetRun execution must not duplicate observations or increment missing counts twice; Task 6 adds this regression.
- Evidence permission must not bypass sanitization, length limits or no-cache headers; Task 7 adds this regression.

---

### Task 1: Topology Models and Migration

**Files:**
- Create: `net/models/topology.py`
- Modify: `net/models/__init__.py`
- Create: `net/migrations/0058_network_topology.py`
- Create: `tests/topology/__init__.py`
- Create: `tests/topology/test_models.py`

**Interfaces:**
- Produces: `TopologyDiscoveryBatch`, `NetworkTopologyInterface`, `NetworkTopologyLink`, `NetworkTopologyObservation`.
- Produces: `NetworkTopologyObservation.Meta.permissions = [('view_topology_evidence', 'Can view topology evidence')]`.

- [ ] **Step 1: Write failing model-contract tests**

Test UUID primary keys, TaskTargetRun one-to-one batch ownership, `(device, stable_key)` and `stable_link_key` uniqueness, observation `(batch, evidence_sha256)` uniqueness, JSON defaults, status choices, indexes and custom permission.

```python
class TopologyModelTests(TestCase):
    def test_source_target_and_stable_keys_are_unique(self):
        batch = make_batch()
        with self.assertRaises(IntegrityError):
            TopologyDiscoveryBatch.objects.create(
                source_task=batch.source_task,
                source_target=batch.source_target,
                device=batch.device,
                protocol='snmp_lldp',
                status='success',
            )
```

- [ ] **Step 2: Run the model test and verify the import fails**

Run: `python manage.py test tests.topology.test_models --verbosity 2`

Expected: FAIL because `net.models.topology` and its exports do not exist.

- [ ] **Step 3: Implement models and migration**

Define:
- `TopologyDiscoveryBatch(source_task PROTECT, source_target OneToOne PROTECT, device PROTECT, protocol, status, schema_version=1, started_at, collected_at, finished_at, message<=1000, interface_count, observation_count, resolved_count, unresolved_count, conflict_count)`.
- `NetworkTopologyInterface(device PROTECT, stable_key<=255, if_index nullable, name, description, mac_address, admin_status, oper_status, speed_bps nullable, vlan_ids JSON list, first_seen_at, last_seen_at, last_batch SET_NULL, is_stale)`.
- `NetworkTopologyLink(stable_link_key unique, local_interface PROTECT, remote_device SET_NULL, remote_interface SET_NULL, remote_chassis_id, remote_port_id, remote_port_description, remote_system_name, remote_management_addresses JSON list, protocols JSON list, evidence_direction, resolution_status, status, speed_bps nullable, vlan_ids JSON list, confidence Decimal(3,2), first_seen_at, last_seen_at, last_batch SET_NULL, missing_complete_batches)`.
- `NetworkTopologyObservation(batch CASCADE, source_target PROTECT, local_device PROTECT, local_interface SET_NULL, link SET_NULL, protocol, neighbor JSON object, evidence TextField, evidence_sha256[64], collected_at)`.

Use validators for list/object JSON values and model constraints for allowed states, bounded confidence, nonnegative counters and chronological timestamps.

- [ ] **Step 4: Run model and migration checks**

Run:
- `python manage.py test tests.topology.test_models --verbosity 2`
- `python manage.py makemigrations --check --dry-run`

Expected: PASS and “No changes detected”.

- [ ] **Step 5: Commit only Task 1 files**

```powershell
git add net/models/topology.py net/models/__init__.py net/migrations/0058_network_topology.py tests/topology
git commit -m "feat: add network topology data model"
```

### Task 2: Expose Topology Discovery as a Network Inspection Item

**Files:**
- Modify: `net/inspections/selection.py`
- Modify: `net/devices/collection_profiles.py`
- Modify: `net/devices/network/collector.py`
- Modify: `net/devices/network/default_profiles.py`
- Modify: `net/devices/network/templates.py`
- Modify: `tests/devices/test_collection_profiles.py`
- Modify: `tests/devices/network/test_default_profiles.py`
- Modify: `tests/devices/network/test_hybrid_collector.py`

**Interfaces:**
- Produces: `collection_method_choices('networks', 'lldp_neighbors') == [('snmp', ...), ('ssh', ...), ('auto', ...)]`.
- Produces: collector planning that may request `lldp_neighbors` from both protocols when its effective method is `auto`.

- [ ] **Step 1: Add failing configuration and planning tests**

Assert the display label, method choices, inherited `auto` default, explicit SNMP-only and SSH-only selection, and automatic dual collection without duplicating the merged item.

```python
def test_lldp_auto_collects_snmp_and_ssh_evidence():
    snmp_items, ssh_items, _ = _network_item_plan(
        'hybrid', ['lldp_neighbors'],
        item_methods={'lldp_neighbors': 'auto'},
    )
    assert snmp_items == ['lldp_neighbors']
    assert ssh_items == ['lldp_neighbors']
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python manage.py test tests.devices.test_collection_profiles tests.devices.network.test_default_profiles tests.devices.network.test_hybrid_collector --verbosity 1`

Expected: FAIL because network function items currently allow only SSH and SNMP does not advertise LLDP support.

- [ ] **Step 3: Implement method allocation**

Special-case `lldp_neighbors` before the generic network-function branch in `collection_method_choices`. Extend `_network_item_plan(mode, selected_items, item_methods=None)` so `auto` adds LLDP to both available protocols, while explicit `snmp` or `ssh` adds only that protocol. Keep other function items SSH-only. Add `lldp_neighbors` to `SNMP_ITEMS` without changing the existing key.

Update default vendor base settings to set `item_methods['lldp_neighbors'] = 'auto'`. Keep existing commands and parser definitions.

- [ ] **Step 4: Run focused tests**

Run: `python manage.py test tests.devices.test_collection_profiles tests.devices.network.test_default_profiles tests.devices.network.test_hybrid_collector --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 2 files**

```powershell
git add net/inspections/selection.py net/devices/collection_profiles.py net/devices/network/collector.py net/devices/network/default_profiles.py net/devices/network/templates.py tests/devices/test_collection_profiles.py tests/devices/network/test_default_profiles.py tests/devices/network/test_hybrid_collector.py
git commit -m "feat: configure topology discovery inspection"
```

### Task 3: Collect and Parse Standard LLDP MIB Data

**Files:**
- Create: `net/devices/network/topology_protocols.py`
- Modify: `net/devices/network/snmp.py`
- Create: `tests/devices/network/test_lldp_snmp.py`

**Interfaces:**
- Produces: `parse_lldp_snapshot(snapshot: dict, table_states: dict) -> dict`.
- Output shape: `{'status', 'complete', 'protocols', 'interfaces', 'neighbors', 'evidence'}`.
- Neighbor fields: `local_key_hint`, `local_port_id`, `local_port_description`, `remote_chassis_id`, `remote_port_id`, `remote_port_description`, `remote_system_name`, `remote_management_addresses`, `protocol`.

- [ ] **Step 1: Add failing LLDP-MIB parser tests**

Use numeric-OID fixtures for local port, remote neighbor and management-address tables. Test binary MAC normalization, IPv4/IPv6 address indexes, multiple remote indexes, complete empty tables, truncated table, timeout and malformed index.

```python
def test_timeout_is_not_a_complete_empty_topology():
    result = parse_lldp_snapshot(
        {'tables': {}},
        {'local_ports': 'success', 'neighbors': 'timeout', 'management': 'not_started'},
    )
    assert result['status'] == 'failed'
    assert result['complete'] is False
    assert result['neighbors'] == []
```

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.devices.network.test_lldp_snmp --verbosity 2`

Expected: FAIL because `topology_protocols.parse_lldp_snapshot` does not exist.

- [ ] **Step 3: Implement bounded LLDP collection and parsing**

Add numeric constants for LLDP local port ID/description, remote chassis/port/system fields and management address. Extend the existing SNMP session result with per-table completion states; do not infer empty from absent data. Request LLDP tables only when `lldp_neighbors` is selected.

Implement deterministic parsing in `topology_protocols.py`. Cap neighbor rows and management addresses using the existing SNMP row/size limits. Return only normalized values and a bounded evidence structure; never include SNMP credentials.

- [ ] **Step 4: Run LLDP and existing SNMP tests**

Run: `python manage.py test tests.devices.network.test_lldp_snmp tests.devices.network.test_snmp --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 3 files**

```powershell
git add net/devices/network/topology_protocols.py net/devices/network/snmp.py tests/devices/network/test_lldp_snmp.py
git commit -m "feat: collect standard snmp lldp neighbors"
```

### Task 4: Normalize Vendor SSH LLDP/CDP Results

**Files:**
- Modify: `net/devices/network/topology_protocols.py`
- Modify: `net/devices/network/default_profiles.py`
- Modify: `net/devices/network/templates.py`
- Create: `tests/devices/network/fixtures/lldp_huawei.txt`
- Create: `tests/devices/network/fixtures/lldp_h3c.txt`
- Create: `tests/devices/network/fixtures/lldp_ruijie.txt`
- Create: `tests/devices/network/fixtures/lldp_cisco.txt`
- Create: `tests/devices/network/fixtures/cdp_cisco.txt`
- Create: `tests/devices/network/test_lldp_ssh.py`

**Interfaces:**
- Produces: `normalize_ssh_neighbors(value: object, raw: dict, vendor: str) -> dict` with the same payload shape as Task 3.
- Consumes: validated template records from `execute_template_commands`.

- [ ] **Step 1: Add failing fixture-driven tests**

For each vendor, assert local port and remote identity parsing. Assert Cisco CDP is tagged `cdp`, an explicit empty marker returns `complete=True`, and unmatched nonempty output returns `complete=False` rather than an empty topology.

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.devices.network.test_lldp_ssh --verbosity 2`

Expected: FAIL because SSH topology normalization is absent.

- [ ] **Step 3: Implement SSH normalization and usable defaults**

Add vendor-safe regex/TextFSM parser definitions whose named fields map to the normalized contract. Add Cisco CDP as a second read-only command for the same item, preserving per-command raw keys. Normalize interface abbreviations only for equality comparison; retain original display text. Reject records without a local port and at least one remote identity field.

- [ ] **Step 4: Run template and SSH topology tests**

Run: `python manage.py test tests.devices.network.test_lldp_ssh tests.devices.network.test_templates tests.devices.network.test_default_profiles --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 4 files**

```powershell
git add net/devices/network/topology_protocols.py net/devices/network/default_profiles.py net/devices/network/templates.py tests/devices/network/fixtures tests/devices/network/test_lldp_ssh.py
git commit -m "feat: normalize ssh lldp and cdp evidence"
```

### Task 5: Resolve Interfaces and Physical Endpoints

**Files:**
- Create: `net/topology/__init__.py`
- Create: `net/topology/discovery.py`
- Create: `tests/topology/test_discovery.py`

**Interfaces:**
- Produces: immutable dataclasses `InterfaceCandidate`, `NeighborCandidate`, `ResolvedObservation`, `DiscoveryPayload`.
- Produces: `build_discovery_payload(device, protocol_results, known_devices, known_interfaces, collected_at) -> DiscoveryPayload`.
- Produces: `stable_interface_key(candidate) -> str` and `stable_link_key(observation) -> str`.

- [ ] **Step 1: Write failing pure-domain tests**

Cover key priority ifIndex → MAC → normalized name, SNMP/SSH merge, endpoint resolution by chassis MAC then management IP then unique system name, duplicate-name conflict, unresolved endpoint retention, canonical bidirectional key and confidence values.

```python
def test_duplicate_system_name_remains_conflicted():
    payload = build_discovery_payload(
        local, [neighbor(system_name='edge')],
        [device(name='edge'), device(name='edge')],
        [], captured,
    )
    assert payload.observations[0].resolution_status == 'conflict'
    assert payload.observations[0].remote_device_id is None
    assert payload.observations[0].confidence == Decimal('0.25')
```

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.topology.test_discovery --verbosity 2`

Expected: FAIL because the topology domain service does not exist.

- [ ] **Step 3: Implement deterministic discovery**

Use exact normalized equality only. A match tier with multiple candidates stops resolution. Canonicalize resolved endpoint pairs before hashing SHA-256 stable link keys. Keep unresolved remote identifiers in the result. Merge reciprocal observations and set direction/confidence according to the spec.

- [ ] **Step 4: Run pure-domain tests**

Run: `python manage.py test tests.topology.test_discovery --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 5 files**

```powershell
git add net/topology tests/topology/test_discovery.py
git commit -m "feat: resolve physical topology endpoints"
```

### Task 6: Persist Topology Through the Worker Lifecycle

**Files:**
- Create: `net/topology/service.py`
- Modify: `net/inspections/executor.py`
- Create: `tests/topology/test_service.py`
- Create: `tests/topology/test_worker_integration.py`

**Interfaces:**
- Produces: `begin_topology_batch(target_id: UUID, started_at) -> UUID | None`.
- Produces: `complete_topology_batch(batch_id, payload: DiscoveryPayload, *, collection_status, collected_at) -> TopologyDiscoveryBatch`.
- Produces: `fail_topology_batch(batch_id, *, status, message, finished_at) -> None`.
- Consumes: `collection.data['lldp_neighbors']` normalized by Tasks 3–4.

- [ ] **Step 1: Add failing persistence tests**

Test short atomic writes, source-target idempotency, observation digest uniqueness, interface upsert, reciprocal link merge, two complete misses to stale, rediscovery recovery, and no aging on failed/partial/cancelled batches.

Add a retry regression that calls completion twice for the same target and proves observation count and missing counters remain unchanged.

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.topology.test_service tests.topology.test_worker_integration --verbosity 2`

Expected: FAIL because lifecycle functions are absent.

- [ ] **Step 3: Implement lifecycle and executor integration**

Before network collection, create or reuse a running batch only when the target is a network device and `lldp_neighbors` is selected. After collection and outside any transaction, build and sanitize the payload. Recheck cancellation/lease using the existing executor guard, then call the transaction-owning completion service.

On topology persistence failure, convert a successful collection to partial, add a safe `collection_errors['lldp_neighbors']` message, and let ordinary inspection persistence continue. Exception and cancellation paths call `fail_topology_batch` without overwriting an already successful batch.

Only `complete=True` and overall successful topology evidence advances missing counters. Use row locks only for the batch, affected device interfaces and candidate links.

- [ ] **Step 4: Run topology and worker regression tests**

Run: `python manage.py test tests.topology tests.inspections.test_worker tests.inspections.test_worker_review tests.inspections.test_worker_sqlite_busy --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 6 files**

```powershell
git add net/topology/service.py net/inspections/executor.py tests/topology/test_service.py tests/topology/test_worker_integration.py
git commit -m "feat: persist inspection topology discoveries"
```

### Task 7: Add Authenticated Read-Only Topology APIs

**Files:**
- Create: `net/topology/read_model.py`
- Create: `index/operations/__init__.py`
- Create: `index/operations/topology.py`
- Modify: `index/urls.py`
- Create: `tests/topology/test_api.py`
- Modify: `tests/system/test_public_privacy.py`

**Interfaces:**
- Produces: `current_topology_payload(*, include_stale: bool, limit: int) -> dict`.
- Produces routes named `topology_data`, `topology_batches`, `topology_interfaces`, `topology_links`, `topology_evidence`.
- Default link objects include `kind='physical_discovered'` and no evidence field.

- [ ] **Step 1: Add failing API and privacy tests**

Test anonymous redirects/401 behavior matching existing authenticated JSON conventions, GET-only behavior, database pagination/filter/sort, field allowlists, stale filter, and absence of credentials/raw evidence.

Test evidence access for ordinary, staff without explicit permission, user with `view_topology_evidence`, and superuser. Assert evidence remains sanitized, at most 64 KiB and returns `Cache-Control: no-store`.

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.topology.test_api tests.system.test_public_privacy --verbosity 2`

Expected: FAIL because routes and read model do not exist.

- [ ] **Step 3: Implement queries, serializers and views**

Use QuerySet pagination before serialization. Serialize explicit keys only. Reuse `current_topology_payload` in `topology_data` so the frontend operations adapter can include the same object under its `topology` key. Enforce `net.view_network_device` or the project’s existing read-access predicate on ordinary endpoints. Require `net.view_topology_evidence` for evidence; Django superuser semantics remain valid.

Invalidate the existing permissions-aware page-cache namespace after successful topology completion. Never cache the evidence response.

- [ ] **Step 4: Run API/security tests**

Run: `python manage.py test tests.topology.test_api tests.system.test_public_privacy tests.system.test_page_cache --verbosity 1`

Expected: PASS.

- [ ] **Step 5: Commit Task 7 files**

```powershell
git add net/topology/read_model.py index/operations index/urls.py tests/topology/test_api.py tests/system/test_public_privacy.py
git commit -m "feat: expose read-only physical topology api"
```

### Task 8: Prune Evidence and Document Operations

**Files:**
- Modify: `net/management/commands/archive_task_results.py`
- Modify: `tests/system/test_backend_maintenance.py`
- Modify: `README.md`
- Modify: `docs/device-inspection.md`
- Modify: `docs/backend-maintenance.md`

**Interfaces:**
- Extends: `archive_task_results --topology-evidence-days 90 --batch-size 500`.
- Preserves: interfaces, links and batches while deleting expired observations only.

- [ ] **Step 1: Add failing retention tests**

Create observations at 89 and 91 days, run a batch size of one, and assert only expired observations are deleted while links/interfaces remain. Assert invalid retention/batch values are rejected and dry-run changes nothing.

- [ ] **Step 2: Run and verify failure**

Run: `python manage.py test tests.system.test_backend_maintenance --verbosity 2`

Expected: FAIL because topology evidence options are absent.

- [ ] **Step 3: Implement bounded pruning and documentation**

Add options with defaults 90 days and 500 rows. Delete by ordered primary-key batches with one short transaction per batch. Document enabling the topology item, SNMP/SSH/auto behavior, recommended 30–60 minute profile, migration/restart, evidence permission, stale-after-two-complete-runs semantics and real-device validation.

- [ ] **Step 4: Run maintenance and documentation checks**

Run:
- `python manage.py test tests.system.test_backend_maintenance tests.topology --verbosity 1`
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `git diff --check`

Expected: all pass; no migration drift.

- [ ] **Step 5: Commit Task 8 files**

```powershell
git add net/management/commands/archive_task_results.py tests/system/test_backend_maintenance.py README.md docs/device-inspection.md docs/backend-maintenance.md
git commit -m "docs: operate and retain topology evidence"
```

### Task 9: Full Verification and Review

**Files:**
- Review all files changed by Tasks 1–8.
- Do not edit unrelated personnel-sync files unless separately requested.

**Interfaces:**
- Verifies the complete data path: inspection configuration → task snapshot → SNMP/SSH collection → normalization → Worker lifecycle → database → authenticated API.

- [ ] **Step 1: Run focused topology and network suites**

Run:
```powershell
python manage.py test tests.topology tests.devices.network tests.devices.test_collection_profiles tests.inspections.test_worker tests.inspections.test_worker_review tests.system.test_public_privacy tests.system.test_backend_maintenance --verbosity 1
```

Expected: PASS.

- [ ] **Step 2: Run full isolated suite**

Run with `NET_ENV_FILE=absent`, `DB_ENGINE=sqlite` and an explicit temporary `DJANGO_SQLITE_PATH`:

```powershell
python manage.py test --verbosity 1
```

Expected: PASS. If the existing isolated `PCUploadConfig.load()` failure remains, reproduce it separately and report it as an unrelated pre-existing failure; do not claim the full suite passed.

- [ ] **Step 3: Run final static checks**

Run:
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `git diff --check`
- `git status --short`

Expected: no system-check issue, no migration drift, no whitespace error, and only intentionally uncommitted personnel-sync files outside topology commits.

- [ ] **Step 4: Review security and task semantics**

Inspect the final diff and confirm no secret appears in migrations, fixtures, logs or API payloads; no network call occurs inside `transaction.atomic()`; partial/failed collection cannot age links; evidence requires its separate permission; ordinary topology queries paginate in SQL; and single-device manual inspection only affects its target.

- [ ] **Step 5: Report deployment impact**

Report applied changes, migration `0058`, required Web/Worker restart, simulated verification, and the exact real-device checks not performed. Do not push or deploy unless separately authorized.
