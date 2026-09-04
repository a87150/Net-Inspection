# PC Remote Log Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct PC-to-API uploads with Worker-owned SMB or FTP/FTPS retrieval, daily evidence import, analysis, remote archiving, and upgraded Windows/macOS collection scripts.

**Architecture:** A singleton PC log source owns non-secret connection settings while a separate encrypted credential row owns the password. Protocol adapters download remote JSON into a local staging directory; the existing lease-aware Worker persists evidence, creates child analysis tasks, and archives remote files only after database commit. Analysis remains database-backed and protocol-independent.

**Tech Stack:** Python 3.12, Django 6.1, `smbprotocol==1.17.0`, Python `ftplib`, `cryptography.Fernet`, Bootstrap templates, Django TestCase/TransactionTestCase, Node frontend tests.

**Spec:** `docs/superpowers/specs/2026-09-04-pc-remote-log-ingestion-design.md`

## Global Constraints

- Exactly one PC log source may exist; its active protocol is SMB or FTP/FTPS.
- Windows and Linux Workers must use the same connector interface.
- A PC and local calendar date may own at most one formally imported daily log.
- Remote files move only after durable import; transient failures leave the source file available for retry.
- Valid logs with abnormal analysis results go to `processed`; malformed evidence goes to `failed`.
- Windows and macOS collection scripts contain neither API URLs nor Worker credentials.
- Task snapshots, errors, exports, templates, and logs must never expose the SMB/FTP password.
- No old business-data migration or compatibility endpoint is required.
- Preserve the current unstaged manual-task-cancellation changes and commit them separately before Task 1.

---

### Task 1: Persist the singleton source, encrypted credentials, daily identity, and transfer state

**Files:**
- Create: `net/models/pc_sources.py`
- Create: `net/devices/pc/credentials.py`
- Modify: `net/models/records.py`
- Modify: `net/models/tasks.py`
- Modify: `net/models/__init__.py`
- Modify: `net/admin/tasks.py`
- Modify: `net/admin/records.py`
- Modify: `net/settings.py`
- Modify: `.env.example`
- Modify: `requirements.txt`
- Modify: `requirements.lock.txt`
- Create: `net/migrations/0024_pc_remote_log_source.py`
- Test: `tests/devices/pc/test_source_models.py`
- Test: `tests/architecture/test_admin_registry.py`

**Interfaces:**
- Produces: `PCLogSourceConfig.load() -> PCLogSourceConfig | None`
- Produces: `store_pc_source_secret(source: PCLogSourceConfig, password: str) -> None`
- Produces: `load_pc_source_secret(source: PCLogSourceConfig) -> str`
- Produces: `ComputerLogTransfer` durable state rows consumed by Tasks 4 and 5.
- Produces: `ComputerLogFile.collected_date`, `computer`, `platform`, `source_protocol`, and `remote_source_path`.

- [ ] **Step 1: Write model and credential contract tests**

```python
class PCLogSourceModelTests(TestCase):
    def test_singleton_rejects_a_second_source(self):
        PCLogSourceConfig.objects.create(pk=1, source_type='smb', host='files.test',
                                         share_name='logs', remote_incoming_directory='incoming',
                                         local_staging_directory='C:/pc-stage')
        with self.assertRaises(ValidationError):
            PCLogSourceConfig(pk=2, source_type='ftp', host='ftp.test').full_clean()

    def test_encrypted_password_round_trips_without_appearing_on_source(self):
        source = valid_smb_source()
        store_pc_source_secret(source, 'not-plaintext')
        self.assertEqual(load_pc_source_secret(source), 'not-plaintext')
        self.assertNotIn('not-plaintext', str(source.public_data()))
        self.assertNotIn('not-plaintext', bytes(source.credential.encrypted_payload).decode('latin1'))

    def test_one_computer_has_only_one_imported_log_per_day(self):
        ComputerLogFile.objects.create(computer=self.pc, collected_date=date(2026, 9, 4),
                                       content_hash='a' * 64, import_status='imported', **log_fields())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ComputerLogFile.objects.create(computer=self.pc, collected_date=date(2026, 9, 4),
                                               content_hash='b' * 64, import_status='imported', **log_fields())
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_source_models --verbosity 2`

Expected: FAIL because `PCLogSourceConfig`, credential helpers, transfer model, and daily fields do not exist.

- [ ] **Step 3: Add the dependency and focused models**

Add `smbprotocol==1.17.0` to both requirement files with its resolved transitive dependencies. Define:

```python
class PCLogSourceConfig(models.Model):
    class SourceType(models.TextChoices):
        SMB = 'smb', 'SMB'
        FTP = 'ftp', 'FTP/FTPS'

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_type = models.CharField(max_length=8, choices=SourceType.choices)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField()
    username = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, blank=True)
    share_name = models.CharField(max_length=255, blank=True)
    remote_root_directory = models.TextField(blank=True)
    remote_incoming_directory = models.TextField()
    remote_processed_directory = models.TextField(default='processed')
    remote_failed_directory = models.TextField(default='failed')
    local_staging_directory = models.TextField()
    terminal_windows_path = models.TextField()
    terminal_macos_path = models.TextField(blank=True)
    recursive = models.BooleanField(default=False)
    file_time_mode = models.CharField(max_length=20, choices=(('recent_days', '最近 N 天'),
                                                              ('date_range', '指定起止日期')))
    recent_days = models.PositiveSmallIntegerField(null=True, blank=True, default=7)
    range_start_date = models.DateField(null=True, blank=True)
    range_end_date = models.DateField(null=True, blank=True)
    ftp_passive = models.BooleanField(default=True)
    ftp_use_tls = models.BooleanField(default=False)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True)
```

Add `PCLogSourceCredential` with a one-to-one source, encrypted binary payload, purpose fingerprint, and timestamps. Reuse the Fernet pattern from `net/domain/secrets.py`, but use required setting `PC_LOG_SOURCE_ENCRYPTION_KEY` and purpose `pc-log-source:<pk>:<source_type>:<host>`.

Add `ComputerLogTransfer` with source, optional task target/log file, remote paths, local staging path, size, mtime, digest, stage choices, attempt count, error, and timestamps. Add a unique active identity over source plus remote source path and observed mtime.

Move file-time fields (`file_time_mode`, `recent_days`, `range_start_date`, `range_end_date`) to `PCLogSourceConfig`. Remove `scan_directories`, `processed_directory`, `failed_directory`, and `recursive` from `ComputerAnalysisProfile`.

- [ ] **Step 4: Generate and inspect the migration**

Run: `.\.venv\Scripts\python.exe manage.py makemigrations net --name pc_remote_log_source`

Expected: one migration creating the source/credential/transfer tables, adding daily/source fields, removing obsolete profile fields, and adding a conditional unique constraint for imported `(computer, collected_date)` logs.

- [ ] **Step 5: Run model/admin tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_source_models tests.architecture.test_admin_registry --verbosity 2`

Expected: PASS with no secret value rendered by admin or serializers.

- [ ] **Step 6: Commit Task 1**

```powershell
git add requirements.txt requirements.lock.txt .env.example config net/models net/admin net/migrations/0024_pc_remote_log_source.py tests/devices/pc/test_source_models.py tests/architecture/test_admin_registry.py
git commit -m "feat: add PC remote log source models"
```

---

### Task 2: Define the connector contract and implement FTP/FTPS

**Files:**
- Create: `net/devices/pc/connectors/__init__.py`
- Create: `net/devices/pc/connectors/base.py`
- Create: `net/devices/pc/connectors/ftp.py`
- Create: `net/devices/pc/connectors/factory.py`
- Test: `tests/devices/pc/connectors/test_contract.py`
- Test: `tests/devices/pc/connectors/test_ftp.py`
- Create: `tests/devices/pc/connector_fakes.py`

**Interfaces:**
- Consumes: `PCLogSourceConfig` and `load_pc_source_secret()` from Task 1.
- Produces: `RemoteLogEntry(path: str, size: int, modified_at: datetime)`.
- Produces: `PCLogConnector.list_json()`, `stat()`, `download()`, `move()`, `mkdirs()`, `probe()`, and `close()`.
- Produces: `build_connector(source: PCLogSourceConfig) -> PCLogConnector` extended by Task 3.
- Produces test-only `memory_connector(files)`, `fake_ftp(entries)`, `fake_smb(files)`, `entry()`, and `aware()` helpers. The memory connector stores `bytes | Exception` by normalized remote path, records downloads and moves, and implements the complete connector contract.

- [ ] **Step 1: Write protocol-independent and FTP behavior tests**

```python
def test_ftp_lists_only_completed_json_in_time_window():
    connector = FTPPCLogConnector(source, password='secret', client=fake_ftp([
        entry('PC1-20260904.json', 120, aware(2026, 9, 4)),
        entry('PC2.uploading', 80, aware(2026, 9, 4)),
    ]))
    self.assertEqual([row.path for row in connector.list_json()],
                     ['incoming/PC1-20260904.json'])

def test_ftps_download_requires_stable_remote_metadata(tmp_path):
    connector = connector_with_stat_sequence(size_then_change())
    with self.assertRaises(RemoteFileChanged):
        connector.download('incoming/PC1-20260904.json', tmp_path / 'file.part')
```

- [ ] **Step 2: Run connector tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.connectors.test_contract tests.devices.pc.connectors.test_ftp --verbosity 2`

Expected: FAIL because the connector package and types do not exist.

- [ ] **Step 3: Implement the connector contract and FTP adapter**

```python
@dataclass(frozen=True)
class RemoteLogEntry:
    path: str
    size: int
    modified_at: datetime

class PCLogConnector(Protocol):
    def list_json(self) -> list[RemoteLogEntry]: raise NotImplementedError
    def stat(self, path: str) -> RemoteLogEntry: raise NotImplementedError
    def download(self, path: str, destination: Path) -> RemoteLogEntry: raise NotImplementedError
    def move(self, source: str, destination: str) -> None: raise NotImplementedError
    def mkdirs(self, path: str) -> None: raise NotImplementedError
    def probe(self) -> None: raise NotImplementedError
    def close(self) -> None: raise NotImplementedError
```

Use `ftplib.FTP` or `FTP_TLS`; call `prot_p()` for FTPS, apply passive mode, use MLSD/MDTM/SIZE, reject parent traversal, normalize POSIX remote paths, stream downloads in bounded chunks, and compare size/mtime before and after download. Translate errors to `PCLogConnectionError`, `PCLogPermissionError`, and `RemoteFileChanged` with sanitized messages.

- [ ] **Step 4: Run FTP connector tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.connectors --verbosity 2`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add net/devices/pc/connectors tests/devices/pc/connectors tests/devices/pc/connector_fakes.py
git commit -m "feat: add FTP PC log connector"
```

---

### Task 3: Implement the cross-platform SMB connector

**Files:**
- Create: `net/devices/pc/connectors/smb.py`
- Modify: `net/devices/pc/connectors/factory.py`
- Test: `tests/devices/pc/connectors/test_smb.py`
- Test: `tests/devices/pc/connectors/test_factory.py`

**Interfaces:**
- Consumes: Task 2 connector protocol and Task 1 SMB source settings.
- Produces: `SMBPCLogConnector` implementing every `PCLogConnector` method.
- Extends: `build_connector()` dispatches `smb` to SMB and `ftp` to FTP/FTPS.

- [ ] **Step 1: Write SMB and factory tests**

```python
def test_smb_download_and_move_use_share_relative_paths(tmp_path):
    connector = SMBPCLogConnector(source, password='secret', client=fake_smb())
    entry = connector.download('incoming/PC1-20260904.json', tmp_path / 'PC1.part')
    self.assertEqual(entry.size, 128)
    connector.move('incoming/PC1-20260904.json', 'processed/PC1-hash.json')
    self.assertEqual(fake_smb().renames[-1], ('incoming/PC1-20260904.json',
                                              'processed/PC1-hash.json'))

def test_factory_never_places_password_in_connector_repr():
    connector = build_connector(source_with_secret('secret'))
    self.assertNotIn('secret', repr(connector))
```

- [ ] **Step 2: Run SMB tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.connectors.test_smb tests.devices.pc.connectors.test_factory --verbosity 2`

Expected: FAIL because `SMBPCLogConnector` and SMB factory dispatch do not exist.

- [ ] **Step 3: Implement SMB operations with `smbclient` from `smbprotocol`**

Register a session per connector using host, port, domain-qualified username, and decrypted password. Build UNC paths only inside the configured share; reject absolute and parent-relative child paths. Implement directory enumeration, stat, streamed download, mkdir, same-share rename, cleanup, and stable metadata checks. Do not use mapped drives or OS-specific mount commands.

```python
def build_connector(source):
    password = load_pc_source_secret(source)
    if source.source_type == PCLogSourceConfig.SourceType.SMB:
        return SMBPCLogConnector(source, password=password)
    return FTPPCLogConnector(source, password=password)
```

- [ ] **Step 4: Run all connector tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.connectors --verbosity 2`

Expected: PASS on Windows without contacting a real server; the adapter boundary is replaced by a complete fake.

- [ ] **Step 5: Commit Task 3**

```powershell
git add net/devices/pc/connectors tests/devices/pc/connectors
git commit -m "feat: add SMB PC log connector"
```

---

### Task 4: Build durable remote discovery, download, daily import, and archive recovery

**Files:**
- Create: `net/devices/pc/remote_ingestion.py`
- Refactor: `net/devices/pc/logs.py`
- Modify: `net/devices/pc/snapshot.py`
- Test: `tests/devices/pc/test_remote_ingestion.py`
- Test: `tests/devices/pc/test_analysis_regressions.py`

**Interfaces:**
- Consumes: `build_connector()`, `ComputerLogTransfer`, and source file-time settings.
- Produces: `FetchSummary(discovered, downloaded, imported, duplicate, failed, skipped, move_failures, transfers, log_files, errors)`.
- Produces: `fetch_remote_logs(source, *, task_target, now=None, connector=None) -> FetchSummary`.
- Produces: `recover_remote_archives(source, *, connector=None) -> FetchSummary`.

- [ ] **Step 1: Write end-to-end ingestion tests against an in-memory connector**

```python
def test_fetch_imports_once_per_computer_day_and_archives_both_remote_files():
    first = payload('PC1', '2026-09-04 08:00:00')
    second = payload('PC1', '2026-09-04 12:00:00')
    expected_hash_prefix = hashlib.sha256(first).hexdigest()[:12]
    duplicate_hash_prefix = hashlib.sha256(second).hexdigest()[:12]
    connector = memory_connector({
        'incoming/PC1-20260904.json': first,
        'incoming/PC1-copy-20260904.json': second,
    })
    summary = fetch_remote_logs(self.source, task_target=self.target,
                                connector=connector, now=aware(2026, 9, 4, 18))
    self.assertEqual((summary.imported, summary.duplicate), (1, 1))
    self.assertEqual(ComputerLogFile.objects.filter(computer=self.pc).count(), 1)
    self.assertEqual(set(connector.paths), {
        'processed/PC1-20260904-' + expected_hash_prefix + '.json',
        'processed/PC1-20260904-' + duplicate_hash_prefix + '.json',
    })

def test_malformed_json_moves_to_failed_but_network_failure_stays_in_incoming():
    connector = memory_connector({
        'incoming/BAD-20260904.json': b'{broken',
        'incoming/RETRY-20260904.json': PCLogConnectionError('connection reset'),
    })
    summary = fetch_remote_logs(self.source, task_target=self.target,
                                connector=connector, now=aware(2026, 9, 4, 18))
    self.assertEqual(summary.failed, 1)
    self.assertEqual(summary.move_failures, 0)
    self.assertIn('failed/BAD-20260904-', ' '.join(connector.paths))
    self.assertIn('incoming/RETRY-20260904.json', connector.paths)
    self.assertFalse(ComputerLogFile.objects.filter(remote_source_path__contains='RETRY').exists())
```

- [ ] **Step 2: Run ingestion tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_remote_ingestion --verbosity 2`

Expected: FAIL because `fetch_remote_logs` and durable transfer stages do not exist.

- [ ] **Step 3: Separate byte import from path scanning**

Refactor the reusable core to:

```python
def import_log_bytes(*, raw: bytes, source_path: str, modified_at: datetime,
                     source_protocol: str, remote_source_path: str,
                     transfer: ComputerLogTransfer) -> ImportOutcome:
    """Validate immutable bytes, refresh PC inventory, and enforce daily identity."""
```

Derive `collected_date` from payload `日志时间`, not the remote filename. Keep BOM support, finite JSON validation, sanitization, static PC refresh, content hash, and transaction retry behavior. Return literal outcomes `imported`, `duplicate_content`, `duplicate_day`, or `failed_schema`.

- [ ] **Step 4: Implement remote fetch and archive recovery**

For each candidate, create/lock a transfer row, download to a unique `.part`, verify metadata and digest, import bytes, persist archive intent, and then move remotely. Reconcile `archive_pending` rows before discovering new files. On cancellation or lost lease, stop selecting files and leave remote data in place.

- [ ] **Step 5: Run ingestion and existing analysis regression tests**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_remote_ingestion tests.devices.pc.test_analysis_regressions --verbosity 2`

Expected: PASS; no test accesses a real network share.

- [ ] **Step 6: Commit Task 4**

```powershell
git add net/devices/pc/remote_ingestion.py net/devices/pc/logs.py net/devices/pc/snapshot.py tests/devices/pc/test_remote_ingestion.py tests/devices/pc/test_analysis_regressions.py
git commit -m "feat: ingest and archive remote PC logs"
```

---

### Task 5: Replace scan tasks with Worker-owned PC log fetch tasks

**Files:**
- Modify: `net/models/tasks.py`
- Modify: `net/inspections/queue.py`
- Modify: `net/inspections/schedules.py`
- Modify: `net/inspections/worker.py`
- Refactor: `net/devices/pc/executor.py`
- Modify: `net/inspections/task_summary.py`
- Modify: `net/models/alerts.py`
- Create: `net/migrations/0025_pc_log_fetch_task_names.py`
- Test: `tests/devices/pc/test_log_pipeline.py`
- Test: `tests/inspections/test_queue.py`
- Test: `tests/inspections/test_schedules.py`
- Test: `tests/inspections/test_worker.py`
- Test: `tests/dashboard/test_taskbar.py`

**Interfaces:**
- Consumes: `fetch_remote_logs()` from Task 4.
- Produces: `enqueue_computer_fetch_task(profile, task_source, overrides=None) -> TaskRun`; it loads the singleton remote source internally.
- Produces: `execute_computer_fetch_target(target_run, *, worker_id, lease_guard=None) -> ExecutionOutcome`.
- Produces: child `COMPUTER_ANALYSIS` tasks for newly imported logs.

- [ ] **Step 1: Rewrite queue and Worker tests around fetch semantics**

```python
def test_manual_fetch_freezes_profile_and_non_secret_source_settings():
    task = enqueue_computer_fetch_task(profile, TaskRun.Source.MANUAL)
    self.assertEqual(task.task_type, 'computer_fetch')
    self.assertEqual(task.target_runs.get().target_type, 'computer_source')
    self.assertNotIn('password', json.dumps(task.profile_snapshot).lower())

def test_cancelled_fetch_does_not_enqueue_analysis_children():
    download_started = threading.Event()
    release_download = threading.Event()
    connector = blocking_memory_connector(download_started, release_download)
    task = enqueue_computer_fetch_task(profile, TaskRun.Source.MANUAL)
    worker = threading.Thread(target=TaskWorker(worker_id='fetch-worker').run_once)
    worker.start()
    self.assertTrue(download_started.wait(2))
    cancel_task(task.pk)
    release_download.set()
    worker.join(2)
    self.assertFalse(worker.is_alive())
    self.assertEqual(TaskRun.objects.filter(task_type='computer_analysis').count(), 0)
    self.assertIn('incoming/PC1-20260904.json', connector.paths)
```

- [ ] **Step 2: Run focused task tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_log_pipeline tests.inspections.test_queue tests.inspections.test_schedules tests.inspections.test_worker --verbosity 1`

Expected: FAIL on missing fetch task constants and executor.

- [ ] **Step 3: Rename task concepts and connect the executor**

Add task type `COMPUTER_FETCH = 'computer_fetch', 'PC 日志获取'` and target type `COMPUTER_SOURCE = 'computer_source', 'PC 日志来源'`. Replace scan enqueue/executor dispatch with fetch equivalents. A fetch has one synthetic source target, stores non-secret source/profile snapshots, and records `FetchSummary` counts in `result_snapshot`.

The executor calls `fetch_remote_logs`, then atomically creates one child analysis task for `summary.log_files` using the frozen analysis profile. Keep the existing lease checks before and after child creation so manual cancellation fences late writes.

- [ ] **Step 4: Update schedules, summaries, alert routing, and migration state**

Scheduled PC analysis enqueues `COMPUTER_FETCH`. Dashboard scan-success labels become fetch-success labels. Update model constraints and choices. The migration may discard obsolete development `computer_scan` task rows rather than preserving business history, as approved by the spec.

- [ ] **Step 5: Run all task pipeline tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_log_pipeline tests.inspections.test_queue tests.inspections.test_schedules tests.inspections.test_worker tests.dashboard.test_taskbar --verbosity 1`

Expected: PASS, including the previously implemented manual-stop behavior.

- [ ] **Step 6: Commit Task 5**

```powershell
git add net/models/tasks.py net/models/alerts.py net/inspections net/devices/pc/executor.py net/migrations/0025_pc_log_fetch_task_names.py tests/devices/pc/test_log_pipeline.py tests/inspections tests/dashboard/test_taskbar.py
git commit -m "feat: run PC log retrieval in background tasks"
```

---

### Task 6: Add source configuration, connection test, and preview UI

**Files:**
- Create: `index/devices/pc/source.py`
- Create: `index/devices/pc/forms.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Modify: `index/inspections/tasks.py`
- Modify: `index/inspections/forms.py`
- Modify: `index/templates/inspections/profile_modal.html`
- Create: `index/templates/devices/pc/source_preview.html`
- Modify: `static/app/js/task_ui.js`
- Modify: `static/app/css/style.css`
- Test: `tests/devices/pc/test_operator_workflows.py`
- Test: `tests/inspections/test_ui.py`
- Test: `tests/frontend/task_ui.test.js`

**Interfaces:**
- Consumes: `build_connector()`, source credential helpers, and `enqueue_computer_fetch_task()`.
- Produces routes: `pc_log_source_save`, `pc_log_source_test`, `pc_log_source_preview`.
- Produces: `PCLogSourceForm.save() -> PCLogSourceConfig` with write-only `password`.

- [ ] **Step 1: Write HTTP and form tests**

```python
def test_source_form_switches_protocol_and_preserves_blank_password():
    source = save_smb_source(password='old-secret')
    response = self.client.post(reverse('pc_log_source_save'), valid_ftp_post(password=''))
    self.assertRedirects(response, reverse('computer_analysis_list'))
    source.refresh_from_db()
    self.assertEqual(source.source_type, 'ftp')
    self.assertEqual(load_pc_source_secret(source), 'old-secret')

def test_preview_lists_candidates_without_downloading_or_moving():
    response = self.client.post(reverse('pc_log_source_preview'))
    self.assertContains(response, 'PC1-20260904.json')
    self.assertEqual(self.connector.download_calls, [])
    self.assertEqual(self.connector.move_calls, [])
```

- [ ] **Step 2: Run UI tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_operator_workflows tests.inspections.test_ui --verbosity 2`

Expected: FAIL because source forms, routes, and controls do not exist.

- [ ] **Step 3: Implement forms and POST-only views**

Require administrator login for saving and testing credentials. Validate protocol-specific fields, safe remote relative paths, absolute local staging path, distinct incoming/processed/failed directories, ports 1–65535, and file-time mode. Save connection settings and password in one transaction; blank password retains the encrypted row.

Connection test calls `connector.probe()` and stores `last_tested_at` or a sanitized `last_test_error`. Preview calls `list_json()`, applies the configured time filter, and returns at most 500 rows with duplicate-day hints.

- [ ] **Step 4: Replace the PC collection section in the modal**

Render protocol-specific SMB and FTP fields, common terminal paths, staging/time settings, and separate “测试连接” and “预览日志” POST forms. Preserve the existing scrollable modal and analysis/schedule fields. Update manual action text to “手动执行分析” while it enqueues a fetch task.

- [ ] **Step 5: Run Django and Node UI tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_operator_workflows tests.inspections.test_ui --verbosity 1`

Run: `node --test tests/frontend/task_ui.test.js`

Expected: both commands PASS.

- [ ] **Step 6: Commit Task 6**

```powershell
git add index/devices/pc index/views/__init__.py index/urls.py index/inspections index/templates/inspections/profile_modal.html index/templates/devices/pc/source_preview.html static/app tests/devices/pc/test_operator_workflows.py tests/inspections/test_ui.py tests/frontend/task_ui.test.js
git commit -m "feat: configure and preview PC log sources"
```

---

### Task 7: Replace HTTP upload scripts with daily shared-folder collectors

**Files:**
- Modify: `net/scripts/generator.py`
- Replace: `net/scripts/templates/GetInfo_Upload.ps1`
- Replace: `net/scripts/templates/getinfo_upload_macos.sh`
- Modify: `index/devices/pc/scripts.py`
- Test: `tests/devices/pc/test_script_generator.py`
- Test: `tests/devices/pc/test_script_downloads.py`
- Create: `tests/devices/pc/fixtures/terminal_log_windows.json`
- Create: `tests/devices/pc/fixtures/terminal_log_macos.json`

**Interfaces:**
- Consumes: `PCLogSourceConfig.terminal_windows_path` and `.terminal_macos_path`.
- Produces: `generate_pc_script(profile: ComputerAnalysisProfile, source: PCLogSourceConfig, platform: str) -> GeneratedScript`.
- Produces: daily JSON matching the fixture schemas used by Task 8.

- [ ] **Step 1: Write generator and script behavior tests**

```python
def test_windows_script_writes_daily_json_to_share_without_http_or_password():
    script = generate_pc_script(self.profile, self.source, 'windows').content
    self.assertIn('PC_LOG_DESTINATION', script)
    self.assertIn('yyyyMMdd', script)
    self.assertIn('.uploading', script)
    self.assertNotIn('Invoke-RestMethod', script)
    self.assertNotIn('/api/', script)
    self.assertNotIn('worker-secret', script)

def test_generated_fixture_contains_full_windows_analysis_schema():
    payload = json.loads(WINDOWS_FIXTURE.read_text(encoding='utf-8'))
    self.assertEqual(set(required_windows_sections()) - set(payload), set())
```

- [ ] **Step 2: Run script tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_script_generator tests.devices.pc.test_script_downloads --verbosity 2`

Expected: FAIL because current scripts contain the API URL and HTTP clients.

- [ ] **Step 3: Rebuild the Windows collector from `TerminalLogs.ps1`**

Port the full data collection sections while replacing hard-coded domain, KMS, IP, DNS, path, and DLL values with profile/source settings or safe optional detection. Emit a top-level `日志时间` plus the existing Chinese schema. Publish `<computer>-<yyyyMMdd>.json` by writing a unique `.uploading` path and renaming it after close. Use current domain computer permissions; do not embed credentials.

- [ ] **Step 4: Rebuild the macOS collector for mounted-share publishing**

Retain system/network/resource collection, add a platform marker, publish the same daily filename convention to `terminal_macos_path`, and omit unsupported Windows sections rather than inventing empty normal values.

- [ ] **Step 5: Update generator/download view and verify GREEN**

The download view requires a saved source and profile, but the generator receives only public source paths and non-secret collection settings. Run:

`.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_script_generator tests.devices.pc.test_script_downloads --verbosity 1`

Expected: PASS and no generated byte string contains `http`, `password`, or an API route.

- [ ] **Step 6: Commit Task 7**

```powershell
git add net/scripts index/devices/pc/scripts.py tests/devices/pc/test_script_generator.py tests/devices/pc/test_script_downloads.py tests/devices/pc/fixtures
git commit -m "feat: publish daily PC logs to shared storage"
```

---

### Task 8: Upgrade analysis rules and database-backed personnel/domain enrichment

**Files:**
- Modify: `net/devices/pc/analysis.py`
- Modify: `net/devices/pc/checks.py`
- Modify: `net/devices/pc/snapshot.py`
- Create: `net/devices/pc/enrichment.py`
- Modify: `index/common/table_registry.py`
- Modify: `index/inspections/records.py`
- Modify: `index/templates/inspections/record_detail.html`
- Modify: `index/common/exports.py`
- Test: `tests/devices/pc/test_log_analysis.py`
- Test: `tests/devices/pc/test_inventory.py`
- Create: `tests/devices/pc/test_enrichment.py`
- Test: `tests/inspections/test_record_workspace.py`

**Interfaces:**
- Consumes: full Windows/macOS fixtures from Task 7.
- Produces: analysis item keys `browser_extensions`, `identity_match`, `cpu_health`, `domain_trust`, and `group_policy`.
- Produces: `build_pc_enrichment(computer: Computer, payload: Mapping) -> dict`.

- [ ] **Step 1: Write analysis and enrichment tests**

```python
def test_windows_fixture_reports_domain_trust_and_cpu_temperature_problems():
    analysis = analyze_log(self.windows_log, ['domain_trust', 'cpu_health'],
                           rules={'cpu_temperature_max_celsius': 85})
    self.assertEqual(analysis.status, 'failed')
    self.assertEqual({row.error_type for row in analysis.errors.all()},
                     {'域信任问题', 'CPU温度问题'})

def test_macos_missing_windows_sections_is_not_a_fault_when_not_selected():
    analysis = analyze_log(self.macos_log, ['system', 'resource'])
    self.assertEqual(analysis.status, 'success')

def test_enrichment_matches_employee_and_domain_objects_without_ldap_calls():
    result = build_pc_enrichment(self.pc, self.payload)
    self.assertEqual(result['employee_number'], 'H051281')
    self.assertEqual(result['computer_ou'], 'OU=Computers,OU=Site')
```

- [ ] **Step 2: Run analysis tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_log_analysis tests.devices.pc.test_enrichment --verbosity 2`

Expected: FAIL because the new rule keys and enrichment function do not exist.

- [ ] **Step 3: Port and normalize the remaining checks**

Parse CPU temperature/frequency values with units, normalize old domain states (`正常`, `失败`, `检测失败`) into known/failed/unknown, compare computer name with the login account suffix, preserve browser extension and GPO evidence, and add configurable CPU temperature threshold. Keep missing/malformed selected data abnormal; platform-inapplicable items are not selectable for macOS default analysis.

- [ ] **Step 4: Add database enrichment and presentation fields**

Match `Personnel.employee_number` using the normalized current-login identifier, match `Domain_Account.login_name`, and match `Domain_Computer.computer_name`. Return OU values from synchronized rows and derive site through a configurable IP-prefix mapping stored in settings or a small JSON configuration field, never by opening LDAP connections in the analysis loop.

Expose enrichment in detail pages and filtered exports without changing immutable analysis evidence.

- [ ] **Step 5: Run analysis, inventory, record, and export tests**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc.test_log_analysis tests.devices.pc.test_inventory tests.devices.pc.test_enrichment tests.inspections.test_record_workspace --verbosity 1`

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

```powershell
git add net/devices/pc index/common index/inspections/records.py index/templates/inspections/record_detail.html tests/devices/pc tests/inspections/test_record_workspace.py
git commit -m "feat: expand PC analysis and directory enrichment"
```

---

### Task 9: Remove the obsolete upload API, update demos/docs, and verify the full story

**Files:**
- Delete: `net/api/views.py`
- Delete if empty: `net/api/__init__.py`
- Modify: `net/urls.py`
- Modify: `net/management/commands/seed_demo_data.py`
- Modify: `README.md`
- Modify: `deploy/demo.py` and deployment documentation only where they reference the old upload API or scan directories
- Modify: `tests/system/test_application.py`
- Modify: `tests/architecture/test_routes.py`
- Modify: `tests/architecture/test_demo_seed.py`
- Modify: `tests/architecture/test_demo_features.py`
- Modify: `tests/architecture/test_models.py`
- Modify: `tests/dashboard/test_summary.py`
- Modify: `tests/alerts/test_service.py`
- Modify: `tests/alerts/test_review.py`
- Modify: `tests/inspections/test_models.py`
- Modify: `tests/inspections/test_runtime_review.py`
- Modify: `tests/inspections/test_record_summary.py`
- Modify: `tests/system/test_application.py`

**Interfaces:**
- Removes: route name `computer_inspection` and `/api/computer_inspection/`.
- Preserves: PC log list/detail/reanalysis, analysis record pages, schedules, task details, cancellation, alerts, filtering, pagination, and export.

- [ ] **Step 1: Write route removal and integrated workflow tests**

```python
def test_obsolete_computer_upload_api_is_not_routable(self):
    self.assertEqual(self.client.post('/api/computer_inspection/', {},
                                     content_type='application/json').status_code, 404)

def test_remote_file_reaches_analysis_alert_and_processed_archive(self):
    connector = memory_connector({
        'incoming/PC1-20260904.json': WINDOWS_FIXTURE.read_bytes(),
    })
    enqueue_computer_fetch_task(self.profile, TaskRun.Source.MANUAL)
    with patch('net.devices.pc.remote_ingestion.build_connector', return_value=connector):
        worker = TaskWorker(worker_id='story-worker', threads=2)
        self.assertTrue(worker.run_once())
        self.assertTrue(worker.run_once())
    self.assertEqual(ComputerLogFile.objects.filter(import_status='imported').count(), 1)
    self.assertEqual(ComputerAnalysis.objects.count(), 1)
    self.assertEqual(Error_Computer.objects.filter(error_type='域信任问题').count(), 1)
    self.assertEqual(AlertEvent.objects.count(), 1)
    self.assertTrue(any(path.startswith('processed/PC1-20260904-') for path in connector.paths))
```

- [ ] **Step 2: Run route/system tests and verify RED**

Run: `.\.venv\Scripts\python.exe manage.py test tests.architecture.test_routes tests.system.test_application --verbosity 1`

Expected: FAIL while the old endpoint remains and old upload assertions still exist.

- [ ] **Step 3: Delete the API and update every consumer**

Remove the API import and route. Remove upload-specific model/admin references, tests, settings, README instructions, generated script text, and demo data. Seed one non-secret disabled demo source or a source with no credentials, fixture-backed PC logs, analyses, transfers, and fetch tasks suitable for UI display without network calls.

- [ ] **Step 4: Eliminate obsolete names and fields**

Run:

`rg -n "computer_inspection|upload_task|upload_url|scan_directories|processed_directory|failed_directory|computer_scan" net index tests README.md deploy config -S`

Expected: no old ingestion references; legacy view aliases explicitly retained for unrelated page compatibility must be removed because compatibility is not required.

- [ ] **Step 5: Run focused PC and task suites**

Run: `.\.venv\Scripts\python.exe manage.py test tests.devices.pc tests.inspections tests.dashboard tests.alerts tests.architecture.test_routes tests.architecture.test_admin_registry tests.architecture.test_demo_seed tests.system.test_application --verbosity 1`

Expected: PASS.

- [ ] **Step 6: Run project checks**

Run: `.\.venv\Scripts\python.exe manage.py check`

Run: `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

Run: `git diff --check`

Expected: all commands exit 0; migration check prints `No changes detected`.

- [ ] **Step 7: Perform one real-environment smoke test without importing data**

On the deployment host, save the user-provided SMB or FTP settings, click “测试连接”, then “预览日志”. Verify the preview lists files and leaves source/processed/failed counts unchanged. Do not run the import until the operator confirms the displayed source path and date range.

- [ ] **Step 8: Commit Task 9**

```powershell
git add -A net/api net/urls.py net/management README.md deploy config tests
git commit -m "refactor: retire direct PC log uploads"
```

---

## Execution Review Gates

After each task, review the task-scoped diff and its named tests before starting the next task. After Tasks 3, 6, and 8, also inspect the public interface consumed by the following task. Do not connect to a real SMB/FTP server until Task 9 Step 7, and do not mutate remote files during that smoke test.
