# 网络设备 SNMP 与 SSH 混合巡检 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为网络设备增加 SNMPv2c/SNMPv3 主动轮询，并与现有 SSH 巡检、日志和配置采集按项目组合运行。

**Architecture:** 新增独立的 PySNMP 查询边界和 SNMP 规范化采集器，再由网络协议编排器实现 `ssh/snmp/hybrid/auto` 四种模式。任务队列继续冻结非秘密配置，Worker 执行时读取最新秘密，最后仍通过现有 `CollectionResult`、巡检记录、异常和告警链路持久化。

**Tech Stack:** Python 3.12、Django 6.1、PySNMP 7.1.29 asyncio v3arch、现有线程 Worker、Django TestCase/TransactionTestCase。

**Spec:** `docs/superpowers/specs/2026-09-04-network-snmp-hybrid-design.md`

## Global Constraints

- 保留当前 SSH 采集和 Cisco/H3C 配置导出行为；`net.devices.network.ssh` 的兼容导出不变。
- SNMP 只做 GET/BULK WALK，禁止 SNMP SET；本阶段不实现 Trap、自动发现或远程配置。
- `logs` 和 `config_info` 始终由 SSH 负责；纯 SNMP 请求这两项时必须明确失败。
- 新增秘密字段只写不读，不进入列表、导出、搜索、任务快照、结果或错误信息。
- 已有空白/未知 `connection_type` 按 SSH 处理；不迁移或删除现有资产和记录。
- 使用数字 OID，不依赖运行时 MIB 下载；单个 OID 不支持不能导致整个设备采集崩溃。
- 混合模式保留任一协议成功的数据；最终状态必须由所有请求项目的完成集合统一计算。
- 不修改当前未提交的 `index/inspections/forms.py`、`index/templates/inspections/profile_modal.html`、`static/app/css/style.css`、`static/app/js/inspections/task_ui.js` 及其 UI 测试。
- 所有验证使用内存 SNMP 会话或 mock SSH，不连接真实网络设备。

---

## File Structure Map

- `net/models/devices.py`：网络设备协议枚举、SNMP 非秘密配置和只写凭据字段及模型校验。
- `net/migrations/0022_network_device_snmp.py`：只增加字段/默认值，不改历史记录。
- `net/devices/network/snmp.py`：数字 OID 注册表、PySNMP 鉴权与查询、SNMP 数据规范化。
- `net/devices/network/collector.py`：按模式拆分项目、调用 SNMP/SSH、自动回退并合并结果。
- `net/inspections/executor.py`：网络目标改为调用协议编排器，执行时补充最新 SNMP 秘密。
- `net/inspections/queue.py`：冻结非秘密 SNMP 连接配置。
- `net/data_exchange/inventory_csv.py`：CSV/XLSX 模板导入 SNMP 配置，秘密字段禁止导出。
- `net/admin/assets.py`：SNMP 秘密使用保留旧值的密码框。
- `index/common/table_registry.py`：显示连接方式、SNMP 版本和端口，不显示秘密。
- `tests/devices/network/test_snmp.py`：SNMP 查询、解析和失败边界。
- `tests/devices/network/test_hybrid_collector.py`：四种协议模式和结果合并。

---

### Task 1: 网络设备 SNMP 配置模型与依赖

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements.lock.txt`
- Modify: `tests/system/test_dependencies.py`
- Modify: `net/models/devices.py`
- Create: `net/migrations/0022_network_device_snmp.py`
- Create: `tests/devices/network/test_snmp_model.py`

**Interfaces:**
- Produces: `Network_Device.effective_connection_type -> str`, `Network_Device.uses_snmp -> bool`, `Network_Device.clean()` 的 SNMPv2c/v3 配置约束。
- Produces model fields: `snmp_version`, `snmp_port`, `snmp_community`, `snmp_security_level`, `snmp_username`, `snmp_auth_protocol`, `snmp_auth_password`, `snmp_priv_protocol`, `snmp_priv_password`, `snmp_context_name`, `snmp_retries`。

- [ ] **Step 1: Write failing model and dependency tests**

  Add tests asserting exact dependency pin and model behavior:

  ```python
  class NetworkSnmpModelTests(TestCase):
      def test_blank_or_unknown_transport_retains_ssh_compatibility(self):
          self.assertEqual(Network_Device(connection_type='').effective_connection_type, 'ssh')
          self.assertEqual(Network_Device(connection_type='legacy').effective_connection_type, 'ssh')

      def test_v2c_requires_community_when_snmp_is_enabled(self):
          device = Network_Device(ip='192.0.2.10', connection_type='snmp', snmp_version='v2c')
          with self.assertRaises(ValidationError):
              device.full_clean()

      def test_v3_auth_priv_requires_username_and_both_passwords(self):
          device = Network_Device(
              ip='192.0.2.11', connection_type='hybrid', snmp_version='v3',
              snmp_security_level='authPriv', snmp_username='inspector',
              snmp_auth_protocol='sha256', snmp_priv_protocol='aes128',
          )
          with self.assertRaises(ValidationError):
              device.full_clean()
  ```

  Add `"pysnmp": "7.1.29"` to `LATEST_STABLE_DIRECT_DEPENDENCIES` and assert migration fields exist.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network.test_snmp_model tests.system.test_dependencies.DependencyContractTests.test_direct_dependencies_are_exactly_pinned_and_installed --verbosity 2
  ```

  Expected: FAIL because PySNMP and SNMP model fields do not exist.

- [ ] **Step 3: Add the exact dependency**

  Add `pysnmp==7.1.29` to `requirements.txt`, install it into `.venv`, and regenerate `requirements.lock.txt` using installed distributions without changing unrelated pins. The direct dependency map must become:

  ```python
  LATEST_STABLE_DIRECT_DEPENDENCIES = {
      # existing exact pins...
      'pysnmp': '7.1.29',
  }
  ```

- [ ] **Step 4: Implement model fields and validation**

  Define choices on `Network_Device` and use `MinValueValidator`/`MaxValueValidator` for port and retries. Implement these exact semantics:

  ```python
  @property
  def effective_connection_type(self):
      return self.connection_type if self.connection_type in {'ssh', 'snmp', 'hybrid', 'auto'} else 'ssh'

  @property
  def uses_snmp(self):
      return self.effective_connection_type in {'snmp', 'hybrid', 'auto'}

  def clean(self):
      super().clean()
      if not self.uses_snmp:
          return
      if self.snmp_version == 'v2c' and not self.snmp_community:
          raise ValidationError({'snmp_community': 'SNMPv2c 必须配置 Community。'})
      if self.snmp_version == 'v3':
          # username always; auth password for authNoPriv/authPriv;
          # privacy password for authPriv, with supported protocol choices.
  ```

  Migration `0022_network_device_snmp.py` adds the SNMP fields and alters only the `connection_type` default/choices. Keep `connection_type` nullable/blank for existing rows and set its model default to `ssh`; do not run a data migration.

- [ ] **Step 5: Run Task 1 tests**

  Run the Step 2 command plus:

  ```powershell
  .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
  ```

  Expected: tests PASS and no migration drift.

- [ ] **Step 6: Commit Task 1 without staging concurrent UI changes**

  ```powershell
  git add requirements.txt requirements.lock.txt tests/system/test_dependencies.py net/models/devices.py net/migrations/0022_network_device_snmp.py tests/devices/network/test_snmp_model.py
  git commit -m "feat: add network SNMP connection settings"
  ```

---

### Task 2: PySNMP 查询边界和标准 MIB 解析

**Files:**
- Create: `net/devices/network/snmp.py`
- Create: `tests/devices/network/test_snmp.py`
- Modify: `net/devices/network/__init__.py`
- Modify: `tests/architecture/test_device_boundaries.py`

**Interfaces:**
- Produces: `SnmpQueryError(category: str)`, `PySnmpSession(device, timeout)`, `parse_snmp_snapshot(snapshot, selected_items, vendor) -> tuple[dict, dict, set[str]]`, `collect_network_snmp(device, timeout=12, selected_items=None, session_factory=PySnmpSession) -> CollectionResult`。
- `snapshot` shape: `{'scalars': {oid: value}, 'tables': {base_oid: [(instance_oid, value), ...]}}`。

- [ ] **Step 1: Write failing parser tests with an in-memory session**

  Tests must demonstrate normalized public behavior rather than PySNMP mock call counts:

  ```python
  def test_standard_mibs_produce_device_and_interface_details(self):
      session = MemorySession(
          scalars={SYS_NAME: 'core-sw-1', SYS_DESCR: 'Example Switch', SYS_UPTIME: 12345},
          tables={IF_NAME: [('1', 'Gi0/1')], IF_OPER_STATUS: [('1', 1)], IF_HC_IN: [('1', 1024)]},
      )
      result = collect_network_snmp(self.device, selected_items=['device_info', 'interface_status'],
                                    session_factory=lambda *_: session)
      self.assertEqual(result.status, 'success')
      self.assertEqual(result.data['device_info']['system_name'], 'core-sw-1')
      self.assertEqual(result.data['interface_status']['interfaces'][0]['oper_status'], 'up')

  def test_missing_oid_marks_only_that_item_missing(self):
      result = collect_network_snmp(
          self.device, selected_items=['device_info', 'temperature'],
          session_factory=lambda *_: MemorySession(scalars={SYS_NAME: 'sw'}, tables={}),
      )
      self.assertEqual(result.status, 'partial')
      self.assertIn('device_info', result.data)
      self.assertNotIn('temperature', result.data)
  ```

  Add cases for CPU averaging, HOST-RESOURCES memory calculation, ENTITY-SENSOR temperature scaling, Q-BRIDGE VLAN names, interface counters, authentication, timeout and response errors. Assert secret strings never appear in `message`, `data` or `raw`.

- [ ] **Step 2: Run parser tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network.test_snmp --verbosity 2
  ```

  Expected: FAIL because `net.devices.network.snmp` is missing.

- [ ] **Step 3: Implement OID registry and pure parsers**

  Define numeric constants for system, IF-MIB/IF-X-MIB, HOST-RESOURCES-MIB, ENTITY-SENSOR-MIB and Q-BRIDGE-MIB. Parsing functions must:

  ```python
  SNMP_ITEMS = frozenset({
      'device_info', 'cpu', 'memory', 'temperature',
      'interface_status', 'vlan_status',
  })

  def parse_snmp_snapshot(snapshot, selected_items, vendor):
      data, raw, completed = {}, {}, set()
      # Add an item only after required values normalize successfully.
      # Keep raw evidence grouped by selected item and containing OID/value only.
      return data, raw, completed
  ```

  Interfaces join table values by OID suffix and emit stable keys: `index`, `name`, `description`, `mac`, `admin_status`, `oper_status`, `speed_mbps`, `in_octets`, `out_octets`, `in_errors`, `out_errors`, `in_discards`, `out_discards`.

  Add a `VENDOR_OIDS` registry with keys `cisco`, `huawei`, `h3c`, and `ruijie`; each item maps `cpu`, `memory_total`, `memory_used`, and `temperature` to ordered numeric OID candidates. Vendor values are optional: try candidates in order, then use the standard HOST-RESOURCES/ENTITY-SENSOR fallback. Tests assert all four registry keys exist and that a valid first candidate wins without querying later candidates.

- [ ] **Step 4: Implement PySNMP 7.1 asyncio adapter**

  `PySnmpSession` must import from `pysnmp.hlapi.v3arch.asyncio`, build `CommunityData(..., mpModel=1)` or `UsmUserData`, use `UdpTransportTarget.create((ip, port), timeout=..., retries=...)`, numeric `ObjectIdentity`, and always call `SnmpEngine.close_dispatcher()` in `finally`.

  A synchronous `collect_network_snmp` calls one private coroutine via `asyncio.run()`, converts provider exceptions to fixed `SnmpQueryError` categories, parses the completed snapshot, and returns `CollectionResult` with status derived from requested/completed item sets.

- [ ] **Step 5: Export and architecture-check the collector**

  Export `SNMP_ITEMS` and `collect_network_snmp` from `net.devices.network.__init__`. Add `("net.devices.network.snmp", "collect_network_snmp")` to the device boundary architecture test.

- [ ] **Step 6: Run Task 2 tests**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network.test_snmp tests.architecture.test_device_boundaries --verbosity 2
  ```

  Expected: PASS without socket activity.

- [ ] **Step 7: Commit Task 2**

  ```powershell
  git add net/devices/network/snmp.py net/devices/network/__init__.py tests/devices/network/test_snmp.py tests/architecture/test_device_boundaries.py
  git commit -m "feat: collect network metrics over SNMP"
  ```

---

### Task 3: SSH/SNMP 协议编排和自动回退

**Files:**
- Create: `net/devices/network/collector.py`
- Create: `tests/devices/network/test_hybrid_collector.py`
- Modify: `net/inspections/executor.py`
- Modify: `net/inspections/queue.py`
- Modify: `net/infrastructure/sanitization.py`
- Modify: `tests/inspections/test_worker.py`
- Modify: `tests/inspections/test_worker_review.py`

**Interfaces:**
- Consumes: `SNMP_ITEMS`, `collect_network_snmp`, existing `collect_network_ssh`。
- Produces: `collect_network(device, timeout=12, selected_items=None) -> CollectionResult`, `_network_item_plan(mode, selected_items) -> tuple[list[str], list[str], bool]`, `_merge_network_results(requested, results) -> CollectionResult`。

- [ ] **Step 1: Write failing four-mode routing tests**

  Use fake collectors returning real `CollectionResult` objects:

  ```python
  def test_hybrid_routes_metrics_to_snmp_and_log_config_to_ssh(self):
      result = collect_network(
          self.device, selected_items=['cpu', 'interface_status', 'logs', 'config_info'],
          snmp_collector=self.snmp, ssh_collector=self.ssh,
      )
      self.assertEqual(self.snmp.selected_items, ['cpu', 'interface_status'])
      self.assertEqual(self.ssh.selected_items, ['logs', 'config_info'])
      self.assertEqual(result.status, 'success')

  def test_auto_falls_back_to_ssh_only_for_missing_snmp_items(self):
      self.snmp.result = CollectionResult(True, 'partial', data={'cpu': {'usage_percent': 10}})
      collect_network(self.device, selected_items=['cpu', 'memory'],
                      snmp_collector=self.snmp, ssh_collector=self.ssh)
      self.assertEqual(self.ssh.selected_items, ['memory'])
  ```

  Add pure SSH, pure SNMP unsupported-item, mixed partial result and both-failed cases.

- [ ] **Step 2: Run orchestrator tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network.test_hybrid_collector --verbosity 2
  ```

  Expected: FAIL because `collector.py` is missing.

- [ ] **Step 3: Implement project routing and deterministic merge**

  The exact mode behavior is:

  ```python
  SSH_ONLY_ITEMS = frozenset({'logs', 'config_info'})

  # ssh: ssh=requested, snmp=[]
  # snmp: snmp=requested & SNMP_ITEMS; unsupported remains missing
  # hybrid: snmp=requested & SNMP_ITEMS; ssh=requested & SSH_ONLY_ITEMS
  # auto: initial plan same as hybrid, then SSH receives SNMP items absent from result.data
  ```

  Merge `data` item-by-item, merge `raw` without overwriting keys (prefix collisions with `snmp:`/`ssh:`), sum durations, set reachable if either protocol is reachable, and calculate final status only from `requested - data.keys()`.

- [ ] **Step 4: Freeze public SNMP settings and resolve live secrets**

  Add non-secret fields to the network `_ASSET_SPECS` snapshot in `net/inspections/queue.py`. Update `_asset_context` in `net/inspections/executor.py` to load:

  ```python
  ('username', 'password', 'snmp_community', 'snmp_auth_password', 'snmp_priv_password')
  ```

  Update `configuration_secrets()` usage so all SNMP secrets participate in sanitization. Replace direct `collect_network_ssh` dispatch with `collect_network` and keep the existing missing-configuration result for modes whose required protocol has no credentials.

- [ ] **Step 5: Add Worker integration assertions**

  Enqueue a hybrid network task with CPU and logs, mock SNMP/SSH at the collector boundary, run `TaskWorker.run_once()`, and assert one `Network_Device_Inspection` contains both results. Assert task/target snapshots do not contain known SNMP or SSH secrets and an SNMP failure plus SSH success becomes `partial` rather than failed.

- [ ] **Step 6: Run Task 3 tests**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network.test_hybrid_collector tests.inspections.test_worker tests.inspections.test_worker_review tests.devices.test_collection_evidence tests.devices.network.test_configuration_export --verbosity 2
  ```

  Expected: PASS; all protocol calls mocked.

- [ ] **Step 7: Commit Task 3**

  ```powershell
  git add net/devices/network/collector.py net/inspections/executor.py net/inspections/queue.py net/infrastructure/sanitization.py tests/devices/network/test_hybrid_collector.py tests/inspections/test_worker.py tests/inspections/test_worker_review.py
  git commit -m "feat: orchestrate hybrid network inspections"
  ```

---

### Task 4: 导入模板、列表、管理页面和演示数据

**Files:**
- Modify: `net/data_exchange/inventory_csv.py`
- Modify: `index/common/table_registry.py`
- Modify: `net/admin/assets.py`
- Modify: `net/management/commands/seed_demo_data.py`
- Modify: `tests/devices/pc/test_inventory.py`
- Modify: `tests/common/test_table_exports.py`
- Modify: `tests/architecture/test_admin_registry.py`
- Modify: `tests/architecture/test_demo_seed.py`

**Interfaces:**
- Consumes: Task 1 model fields and `SecretPreservingModelForm`。
- Produces: CSV/XLSX columns for all SNMP settings; public list fields `connection_type`, `snmp_version`, `snmp_port`; admin write-only handling for three SNMP secrets。

- [ ] **Step 1: Write failing import, export and admin secret tests**

  Assert an imported hybrid v3 row persists normalized settings, templates contain a safe example, and filtered/list exports exclude all secret fields:

  ```python
  forbidden = {'SNMP Community', 'SNMP认证密码', 'SNMP加密密码'}
  self.assertTrue(forbidden.isdisjoint(csv_export_headers))
  self.assertTrue(forbidden.isdisjoint(field.label for field in get_table_definition('networks').fields))
  ```

  Extend admin tests so blank SNMP secret inputs preserve saved values and replacement values overwrite them. Extend credential visibility checks with `snmp_community`, `snmp_auth_password`, and `snmp_priv_password`.

- [ ] **Step 2: Run Task 4 focused tests and verify RED**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_inventory tests.common.test_table_exports tests.architecture.test_admin_registry tests.architecture.test_demo_seed --verbosity 2
  ```

  Expected: FAIL because import/admin/list definitions do not know SNMP fields.

- [ ] **Step 3: Extend inventory exchange safely**

  Add columns in this order after SSH credentials: SNMP 版本、SNMP 端口、SNMP Community、SNMPv3 用户名、安全级别、认证协议、认证密码、加密协议、加密密码、上下文、重试次数。 Add the three secret field names to `secret_fields`. The sample is a hybrid SNMPv3 device using `DEMO-ONLY-NOT-A-SECRET`; template examples may contain that literal, normal asset exports must not contain secrets.

- [ ] **Step 4: Extend list and admin management**

  Add optional table fields for connection mode, SNMP version and port. Set:

  ```python
  class NetworkDeviceAdminForm(SecretPreservingModelForm):
      secret_fields = ('password', 'snmp_community', 'snmp_auth_password', 'snmp_priv_password')
  ```

  Admin list displays mode/version/SNMP port but no credentials. Update demo networks deterministically with a mix of `ssh`, `hybrid`, and `auto`; all demo SNMP modes stay non-routable and no real collection is executed by seeding.

- [ ] **Step 5: Run Task 4 tests**

  Run the Step 2 command. Expected: PASS.

- [ ] **Step 6: Commit Task 4**

  ```powershell
  git add net/data_exchange/inventory_csv.py index/common/table_registry.py net/admin/assets.py net/management/commands/seed_demo_data.py tests/devices/pc/test_inventory.py tests/common/test_table_exports.py tests/architecture/test_admin_registry.py tests/architecture/test_demo_seed.py
  git commit -m "feat: manage SNMP network device settings"
  ```

---

### Task 5: 运维说明与聚焦验收

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/superpowers/plans/2026-09-04-network-snmp-hybrid.md`

**Interfaces:**
- Documents: Worker 依赖、UDP/161、四种模式、SNMPv3 推荐、SNMPv2c 风险、支持项目、SSH 配置/日志边界和无真实设备测试声明。

- [ ] **Step 1: Update operator documentation**

  Document that production firewalls must allow Worker-to-device UDP/161; SNMPv3 `authPriv` is recommended; v2c is retained only for legacy devices; `hybrid` is the normal choice. Include a troubleshooting table for timeout, authentication, unsupported OID, partial result and SSH fallback.

- [ ] **Step 2: Run the focused final verification**

  ```powershell
  .\.venv\Scripts\python.exe manage.py test tests.devices.network tests.devices.test_collection_evidence tests.inspections.test_worker tests.inspections.test_worker_review tests.devices.pc.test_inventory tests.common.test_table_exports tests.architecture.test_device_boundaries tests.architecture.test_admin_registry tests.architecture.test_demo_seed tests.system.test_dependencies --verbosity 1
  .\.venv\Scripts\python.exe manage.py check
  .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
  git diff --check
  ```

  Expected: all selected tests PASS, Django reports no issues, no migration drift and no whitespace errors. Do not run a real SNMP/SSH target.

- [ ] **Step 3: Verify concurrent UI changes remain untouched**

  Compare `git status --short` with the initial dirty set. The pre-existing UI files and `.ui-inspect-runtime/` must remain unstaged/uncommitted unless their owning task has committed them independently; this feature must not delete or rewrite them.

- [ ] **Step 4: Commit documentation and completed checklist**

  ```powershell
  git add README.md docs/deployment.md docs/superpowers/plans/2026-09-04-network-snmp-hybrid.md
  git commit -m "docs: explain hybrid network inspections"
  ```
