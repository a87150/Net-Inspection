# Phase 2 Worker and Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-platform database-backed task queue, multi-thread Worker, manual infrastructure inspections, scheduled inspections, and configurable computer log scanning/analysis.

**Architecture:** Web requests create immutable task/target snapshots; a separate management-command Worker claims tasks with leases and executes targets through a bounded `ThreadPoolExecutor`. Schedules create tasks using either interval or daily rules, and executors isolate infrastructure collection from computer file scanning and analysis.

**Tech Stack:** Django ORM transactions, MySQL row locking, Python `ThreadPoolExecutor`, existing Paramiko/Requests collectors, Windows service/systemd process hosting.

**Spec:** `docs/superpowers/specs/2026-08-30-automation-alerting-and-records-design.md`

## Global Constraints

- Worker code runs unchanged on Windows Server and Linux.
- No Redis, Celery, Cron expressions, weekly schedules, or browser-resident timers.
- Manual HTTP actions enqueue work and return promptly; they never run SSH/HTTP/file analysis inline.
- Same profile and target scope cannot have duplicate active tasks.
- Computer analysis reads server-side PowerShell JSON files only and never triggers remote terminals.
- Every behavior change follows RED → GREEN → focused tests → full tests → commit.

---

### Task 1: Profiles, schedules, runs, and target runs

**Files:**
- Create: `net/models/tasks.py`
- Modify: `net/models/__init__.py`
- Create: `net/migrations/0002_task_profiles_and_schedules.py`
- Create: `index/test_phase2_task_models.py`

**Interfaces:**
- Produces `InspectionProfile`, `ComputerAnalysisProfile`, `Schedule`, `TaskRun`, `TaskTargetRun`.
- `TaskRun.Status`: queued, running, success, partial, failed, cancelled.
- `Schedule.Kind`: interval, daily.

- [ ] **Step 1: Write failing constraints and snapshot tests**

```python
def test_only_supported_schedule_kinds_are_valid(self):
    schedule = Schedule(kind='cron')
    with self.assertRaises(ValidationError):
        schedule.full_clean()

def test_active_duplicate_target_is_rejected(self):
    TaskTargetRun.objects.create(task=task, target_type='server', target_id=server.pk, status='running')
    with self.assertRaises(IntegrityError):
        TaskTargetRun.objects.create(task=task, target_type='server', target_id=server.pk, status='running')
```

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_task_models -v 2`

- [ ] **Step 3: Implement models and validation**

Store task parameters and selected inspection items in JSON snapshots. Add indexes for `(status, available_at)`, `(lease_expires_at)`, and `(schedule, created_at)`.

- [ ] **Step 4: Generate migration and run tests**

Run: `.venv\Scripts\python.exe manage.py makemigrations net`

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_task_models`

- [ ] **Step 5: Commit**

```bash
git add py/net/net/models py/net/net/migrations py/net/index/test_phase2_task_models.py
git commit -m "feat: add inspection task and schedule models"
```

### Task 2: Lease-based queue service

**Files:**
- Create: `net/tasks/__init__.py`
- Create: `net/tasks/queue.py`
- Create: `net/tasks/state.py`
- Create: `index/test_phase2_queue.py`

**Interfaces:**
- `enqueue_task(profile, target_ids, source, overrides=None) -> TaskRun`
- `claim_next_task(worker_id, lease_seconds, now=None) -> TaskRun | None`
- `renew_lease(task_id, worker_id, lease_seconds) -> bool`
- `recover_expired_tasks(now=None) -> int`
- `finish_task(task_id) -> TaskRun`

- [ ] **Step 1: Write failing lease and duplicate tests**

Test two claimers, ownership checks, lease renewal, expired recovery, immutable target snapshots, and duplicate active-scope rejection.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_queue -v 2`

- [ ] **Step 3: Implement transactional claiming**

Use `transaction.atomic()` and `select_for_update()` around the oldest queued/expired candidate. Update status, worker id, lease expiry, and attempt count in the same transaction. SQLite tests serialize access; MySQL is the production concurrency target.

- [ ] **Step 4: Implement task aggregation**

`finish_task` maps target counts to success, partial, or failed and sets progress to 100. Target errors are truncated for summary but retained in the target record.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/tasks py/net/index/test_phase2_queue.py
git commit -m "feat: add lease based database task queue"
```

### Task 3: Interval and daily schedule engine

**Files:**
- Create: `net/tasks/schedules.py`
- Create: `index/test_phase2_schedules.py`

**Interfaces:**
- `next_run_at(schedule, after) -> datetime`
- `enqueue_due_schedules(now=None) -> list[TaskRun]`

- [ ] **Step 1: Write failing timezone-aware schedule tests**

Cover minute/hour interval, daily time, disabled plan, missed daily time, repeated polling idempotency, and `Asia/Shanghai` daylight-independent behavior.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_schedules -v 2`

- [ ] **Step 3: Implement schedule calculations**

Interval plans add `timedelta(minutes=N)` or `timedelta(hours=N)`. Daily plans combine the local date and configured time, advancing one day when the candidate is not after the reference time. Lock the schedule when creating its task and update `last_enqueued_at` plus `next_run_at` atomically.

- [ ] **Step 4: Run tests and commit**

```bash
git add py/net/net/tasks/schedules.py py/net/index/test_phase2_schedules.py
git commit -m "feat: schedule interval and daily tasks"
```

### Task 4: Multi-thread Worker and infrastructure executor

**Files:**
- Create: `net/tasks/worker.py`
- Create: `net/tasks/executors/__init__.py`
- Create: `net/tasks/executors/inspection.py`
- Create: `net/management/commands/run_task_worker.py`
- Refactor: `net/management/commands/run_network_checks.py`
- Create: `index/test_phase2_worker.py`

**Interfaces:**
- `execute_target(target_run) -> ExecutionOutcome`
- `TaskWorker.run_once() -> bool`
- CLI options: `--threads`, `--poll-seconds`, `--lease-seconds`, `--once`.

- [ ] **Step 1: Write failing Worker tests**

Mock collectors and assert bounded concurrency, per-target isolation, lease renewal, partial task status, `--once` exit, and no collector invocation in the Web process.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_worker -v 2`

- [ ] **Step 3: Extract infrastructure collection into executor**

Reuse `collect_network_ssh`, `collect_linux_ssh`, `collect_windows_http`, and `collect_security_api`. Persist only selected inspection items plus required raw status, and link the record to `TaskTargetRun`.

- [ ] **Step 4: Implement Worker loop**

The loop recovers expired tasks, enqueues due schedules, claims one task, executes targets through `ThreadPoolExecutor(max_workers=min(global_threads, profile.concurrent_workers))`, renews leases while futures run, and aggregates final state.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/tasks py/net/net/management/commands py/net/index/test_phase2_worker.py
git commit -m "feat: run inspection tasks in threaded worker"
```

### Task 5: Computer directory scanner and analysis executor

**Files:**
- Create: `net/tasks/executors/computer_analysis.py`
- Create: `net/services/computer_logs.py`
- Create: `net/services/computer_analysis.py`
- Create: `index/test_phase2_computer_logs.py`

**Interfaces:**
- `scan_log_directory(profile, now=None) -> ScanSummary`
- `import_log_file(profile, path) -> ComputerLogFile`
- `analyze_log(log_file, analysis_items) -> ComputerAnalysis`

- [ ] **Step 1: Write failing filesystem tests using temporary directories**

Cover recent-N-days, start/end dates, recursive off/on, hash deduplication, processed move, failed move, malformed JSON, root escape rejection, and reanalysis without file rescan.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_computer_logs -v 2`

- [ ] **Step 3: Implement safe scanning**

Resolve configured roots and candidate files before reading/moving. Require every candidate and destination to remain under its configured root. Compare timezone-aware file modification times to the profile range.

- [ ] **Step 4: Implement import and analysis**

Compute SHA-256 before database creation. Parse the existing PowerShell JSON schema into static `Computer` fields and store the raw payload on `ComputerLogFile`. Analysis functions accept an explicit set such as activation, software, processes, BitLocker, Defender, patches, domain, resource usage, and event findings.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/tasks/executors/computer_analysis.py py/net/net/services py/net/index/test_phase2_computer_logs.py
git commit -m "feat: scan and analyze computer log files"
```

### Task 6: Manual execution, configuration, progress, and schedules UI

**Files:**
- Create: `index/views/tasks.py`
- Create: `index/forms/tasks.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Create: `index/templates/tasks/run_modal.html`
- Create: `index/templates/tasks/profile_modal.html`
- Create: `index/templates/tasks/list.html`
- Create: `index/templates/tasks/detail.html`
- Modify: asset and analysis templates
- Create: `index/test_phase2_task_ui.py`

**Interfaces:**
- POST `manual_task_create` accepts profile, target mode (`all`, `selected`, `filtered`), selected item keys, target ids/filter query, and optional concurrency.
- Profile forms expose only registry-valid inspection/analysis item keys.

- [ ] **Step 1: Write failing UI tests**

Assert three infrastructure pages show `手动执行巡检`, computer analysis shows `手动执行分析`, configuration buttons are adjacent, filtered targets are snapshotted, duplicate active tasks return a message, and task details show target progress/errors.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase2_task_ui -v 2`

- [ ] **Step 3: Implement forms and views**

Validate target ids against the active filtered queryset and item keys against the profile registry. Use POST/CSRF for creation and normal GET pages for task status.

- [ ] **Step 4: Implement profile and schedule controls**

Use Bootstrap modals for configuration, with conditional interval/daily fields and server-side validation. Display next run, last run, enabled state, timeout, and concurrency.

- [ ] **Step 5: Run full phase verification and browser acceptance**

Run system check, migration check, full Django tests, JS tests, then verify manual enqueue, task progress, interval/daily forms, log directory range controls, desktop/narrow layout, and console errors in a real browser.

- [ ] **Step 6: Commit**

```bash
git add py/net/index py/net/net/tasks py/net/net/services py/net/net/management/commands
git commit -m "feat: configure and monitor scheduled inspection tasks"
```
