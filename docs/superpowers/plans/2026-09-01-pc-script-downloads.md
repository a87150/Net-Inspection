# PC Script Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate downloadable Windows PowerShell and macOS Shell PC collection/upload scripts from a selected analysis profile.

**Architecture:** Keep immutable script templates under `net/scripts/templates` and render them through a small generator that validates profile paths and public upload URL. Reuse the existing asynchronous upload API and Worker analysis pipeline.

**Tech Stack:** Python 3.12+, Django 5.2+, PowerShell 5.1+, POSIX shell on macOS, curl, system_profiler.

**Spec:** `docs/superpowers/specs/2026-09-01-asset-dashboard-domain-operations-design.md`

## Global Constraints

- Scripts contain no Django, domain-controller, or device credentials.
- Generated scripts use all configured scan directories and the first as the default output directory.
- Missing scan directories reject download with a clear validation error.
- Keep the asynchronous `/api/computer_inspection/` contract compatible.

---

### Task 1: Create profile-driven script generator and templates

**Files:**
- Create: `net/scripts/__init__.py`
- Create: `net/scripts/generator.py`
- Create: `net/scripts/templates/GetInfo_Upload.ps1`
- Create: `net/scripts/templates/getinfo_upload_macos.sh`
- Test: `index/test_pc_script_generator.py`

**Interfaces:**
- Produces: `generate_pc_script(profile, platform, public_base_url) -> GeneratedScript(filename, content, content_type)`.
- Consumes: `ComputerAnalysisProfile.scan_directories`, analysis items, and file-time settings.

- [ ] **Step 1: Write failing generator contract tests**

```python
def test_windows_script_contains_profile_paths_and_upload_url(self):
    result = generate_pc_script(self.profile, 'windows', 'https://monitor.example')
    self.assertEqual(result.filename, 'GetInfo_Upload.ps1')
    self.assertIn('C:\\InspectionLogs', result.content)
    self.assertIn('https://monitor.example/api/computer_inspection/', result.content)
    self.assertNotIn('bind_password', result.content)

def test_macos_script_uses_shell_safe_json_configuration(self):
    result = generate_pc_script(self.mac_profile, 'macos', 'https://monitor.example')
    self.assertEqual(result.filename, 'getinfo_upload_macos.sh')
    self.assertIn('/Library/Logs/Inspection', result.content)
    self.assertIn('system_profiler', result.content)
```

- [ ] **Step 2: Run tests and verify missing module failure**

Run: `.venv\Scripts\python.exe manage.py test index.test_pc_script_generator -v 2`

Expected: FAIL because `net.scripts.generator` does not exist.

- [ ] **Step 3: Implement generator validation and escaping**

Create a frozen `GeneratedScript` dataclass. Accept only `windows` and `macos`, require at least one nonblank scan directory, normalize `public_base_url` to an HTTP(S) origin, append `/api/computer_inspection/`, and serialize the profile settings to JSON before inserting them into a single template placeholder.

- [ ] **Step 4: Implement Windows template**

Adapt existing collection logic to emit the accepted payload fields for host, OS, CPU model/core counts, total memory, disks, IP/MAC, and matching log file metadata. POST UTF-8 JSON with `Invoke-RestMethod`; on upload failure, retain the JSON under the configured first directory and return nonzero.

- [ ] **Step 5: Implement macOS template**

Use `/usr/sbin/system_profiler`, `/usr/sbin/sysctl`, `/usr/sbin/diskutil`, `df`, `ifconfig`, and `find` with the configured time window. Build JSON through the system Python 3 interpreter when present; if Python 3 is unavailable, print a clear dependency error before collection. Upload with `/usr/bin/curl --fail-with-body` and retain failed payloads in the first configured directory.

- [ ] **Step 6: Add syntax/contract tests and run them**

Tests must verify quoted paths, non-ASCII paths, recent-days/date-range settings, no secret fields, stable filenames, and JSON keys accepted by `ComputerInspectionSerializer`.

Run: `.venv\Scripts\python.exe manage.py test index.test_pc_script_generator index.tests.ComputerInspectionApiTests -v 2`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add py/net/net/scripts py/net/index/test_pc_script_generator.py
git commit -m "feat: generate cross-platform PC scripts"
```

---

### Task 2: Add authorized script-download endpoints and UI

**Files:**
- Create: `index/views/scripts.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Modify: `index/templates/assets/list.html`
- Modify: `index/templates/tasks/profile_modal.html`
- Modify: `net/settings.py`
- Test: `index/test_pc_script_downloads.py`

**Interfaces:**
- Produces: route `pc_script_download(profile_id: UUID, platform: str)`.
- Consumes: `generate_pc_script` and optional `NET_PUBLIC_BASE_URL` setting.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_download_uses_explicit_public_base_url(self):
    with override_settings(NET_PUBLIC_BASE_URL='https://monitor.example'):
        response = self.client.get(reverse('pc_script_download', args=[self.profile.pk, 'windows']))
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response['Content-Disposition'], 'attachment; filename="GetInfo_Upload.ps1"')
    self.assertContains(response, 'https://monitor.example/api/computer_inspection/')

def test_profile_without_scan_directory_returns_400(self):
    self.profile.scan_directories = []
    self.profile.save()
    response = self.client.get(reverse('pc_script_download', args=[self.profile.pk, 'macos']))
    self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Run endpoint tests and verify missing route failure**

Run: `.venv\Scripts\python.exe manage.py test index.test_pc_script_downloads -v 2`

Expected: FAIL with `NoReverseMatch`.

- [ ] **Step 3: Add safe public URL selection**

Read `NET_PUBLIC_BASE_URL` from environment in settings. If set, require `http` or `https`; otherwise derive scheme and host from the request. Pass only the origin to the generator.

- [ ] **Step 4: Add download view and routes**

Resolve an enabled or disabled profile by UUID, validate platform, return `HttpResponse` with attachment headers and `X-Content-Type-Options: nosniff`. Return 400 for invalid profile configuration and 404 for invalid platform/profile.

- [ ] **Step 5: Add UI buttons next to analysis configuration**

Expose Windows and macOS download actions for the selected/default analysis profile. Show the local-path compatibility note and link to analysis configuration when no directory exists.

- [ ] **Step 6: Run endpoint and UI regression tests**

Run: `.venv\Scripts\python.exe manage.py test index.test_pc_script_downloads index.test_phase2_task_ui index.test_public_urls -v 2`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add py/net/index/views/scripts.py py/net/index/views/__init__.py py/net/index/urls.py py/net/index/templates/assets/list.html py/net/index/templates/tasks/profile_modal.html py/net/net/settings.py py/net/index/test_pc_script_downloads.py
git commit -m "feat: download PC collection scripts"
```

---

### Task 3: Verify PC script release slice

**Files:**
- Modify: `README.md`
- Create: `docs/deployment.md`
- Test: full suites

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: deployable and documented script workflow.

- [ ] **Step 1: Document public URL and endpoint requirements**

Document `NET_PUBLIC_BASE_URL`, per-platform path configuration, script prerequisites, upload failure behavior, and how the Worker processes uploaded data.

- [ ] **Step 2: Run static secret scans on generated fixtures**

Generate both scripts in tests and assert that domain bind password, API tokens, device passwords, Django secret key, and database credentials are absent.

- [ ] **Step 3: Run full verification**

Run Django tests, JS tests, `manage.py check`, migration drift check, and `pip check`; all must succeed.

- [ ] **Step 4: Commit documentation**

```powershell
git add py/net/README.md py/net/docs/deployment.md
git commit -m "docs: explain PC script deployment"
```
