# Final Domain Operations Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all three Important and one Minor findings in the final branch review while preserving secret safety, lease fencing, and existing script bytes.

**Architecture:** Extend the LDAP result boundary with non-sensitive phase metadata and a guarded create-user resume mode. Persist LDAP outcomes and local mirror mutations in the same lease-fenced transaction, but treat stale or missing mirror rows as sync-needed metadata rather than LDAP failure. Keep download eligibility server-enforced and move modal behavior into a dependency-free JavaScript controller with Node tests.

**Tech Stack:** Python 3.12, Django, ldap3, SQLite test databases, Django templates, vanilla JavaScript, Node built-in test runner.

**Spec:** `C:\Users\a8715\Desktop\code\py\net\.worktrees\asset-dashboard-domain-ops\.superpowers\final-branch-review.md`

## Global Constraints

- Work only in `C:\Users\a8715\Desktop\code\py\net\.worktrees\asset-dashboard-domain-ops\py\net` and do not contact a real LDAP server or mutate a real database.
- Use `C:\Users\a8715\Desktop\code\py\net\.venv\Scripts\python.exe` for Python commands.
- Never persist or render passwords, bind credentials, LDAP raw errors, or exception details.
- Preserve existing user changes and the untracked `../../.superpowers/final-branch-review.md` and `../__pycache__/__init__.cpython-312.pyc` files.
- Execute inline without subagents, as explicitly required by the user.

---

### Task 1: Recoverable create-user phases

**Files:**
- Modify: `net/domain/client.py`
- Modify: `net/tasks/domain.py`
- Modify: `net/tasks/executors/domain.py`
- Modify: `index/views/domain_operations.py`
- Modify: `index/views/tasks.py`
- Modify: `index/templates/tasks/detail.html`
- Test: `index/test_domain_actions.py`
- Test: `index/test_domain_worker.py`
- Test: `index/test_domain_permissions_ui.py`

**Interfaces:**
- Produces: `DomainActionResult.stage` plus allowlisted non-sensitive `DomainActionResult.details` such as `distinguished_name`.
- Produces: `DomainClient.execute(..., recovery_stage=None)`; the trusted password-pending recovery mode performs a BASE identity read before password replacement.
- Consumes: a copied trusted `user_created_password_pending` result stage when retrying one failed create-user target.

- [ ] **Step 1: Write client RED tests**

Add tests that make `connection.add()` succeed and `connection.modify()` fail, expecting the fixed password-pending stage and DN; make `add()` raise `LDAPException`, expecting `manual_intervention_required`; and seed an unrelated existing entry in resume mode, expecting no add and no modify.

- [ ] **Step 2: Run client RED tests**

Run: `python manage.py test index.test_domain_actions.DomainClientTests --verbosity 2`

Expected: FAIL because `DomainActionResult` has no stage/manual metadata and `execute()` has no resume mode.

- [ ] **Step 3: Implement minimal client phase state**

Add fixed stage constants, catch uncertainty only at the create-user add boundary, preserve `user_created_password_pending` after a confirmed add, and in resume mode BASE-search `sAMAccountName` and `displayName` before replacing `unicodePwd`. A normal LDAP result 68 remains an ordinary fixed “already exists” failure and never enters resume.

- [ ] **Step 4: Run client GREEN tests**

Run: `python manage.py test index.test_domain_actions.DomainClientTests --verbosity 2`

Expected: PASS.

- [ ] **Step 5: Write queue/worker/UI RED tests**

Add an end-to-end fake-LDAP test for add success/password failure → failed snapshot → retry with a new password → BASE identity verification → password-only success. Add tests that unknown add outcomes persist manual intervention and cannot retry, ordinary existing users without a trusted stage cannot be taken over, retry snapshots copy only the trusted stage, all audit statuses stay failed/success consistently, fixed errors contain no submitted password, and the task page hides retry while showing the manual-check instruction.

- [ ] **Step 6: Run queue/worker/UI RED tests**

Run: `python manage.py test index.test_domain_worker index.test_domain_permissions_ui --verbosity 2`

Expected: FAIL on missing stage copying, resume dispatch, manual retry blocking, and UI state.

- [ ] **Step 7: Implement minimal retry and persistence flow**

Persist the result metadata behind the current task lease/generation. On retry, read the old failed target's immutable identity plus terminal result, accept only the exact trusted password-pending stage with the same DN, require a new password, copy the trusted stage to the queued retry target, and pass resume mode to the client. Reject manual intervention with fixed operator guidance and expose that state on task detail without secrets.

- [ ] **Step 8: Run Task 1 GREEN tests**

Run: `python manage.py test index.test_domain_actions index.test_domain_worker index.test_domain_permissions_ui --verbosity 2`

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Run: `git add net/domain/client.py net/tasks/domain.py net/tasks/executors/domain.py index/views/domain_operations.py index/views/tasks.py index/templates/tasks/detail.html index/test_domain_actions.py index/test_domain_worker.py index/test_domain_permissions_ui.py && git commit -m "fix: make domain user creation recoverable"`

### Task 2: Lease-fenced write-through domain mirror

**Files:**
- Modify: `net/domain/client.py`
- Modify: `net/tasks/executors/domain.py`
- Test: `index/test_domain_actions.py`
- Test: `index/test_domain_worker.py`

**Interfaces:**
- Consumes: `DomainActionResult.details['distinguished_name']` from successful `move_ou`.
- Produces: non-sensitive mirror metadata in `TaskTargetRun.result_snapshot` with `updated` or `sync_required` state.

- [ ] **Step 1: Write mirror RED tests**

Add account and computer tests proving successful `move_ou` updates `distinguished_name` and `ou`, successful enable/disable updates `is_active`, and a newly enqueued action snapshots the new DN. Add missing-row and locally drifted-row tests proving LDAP success remains task success and records `sync_required` without overwriting the drift. Include a create-user success case proving its virtual target ID is never queried as a local account.

- [ ] **Step 2: Run mirror RED tests**

Run: `python manage.py test index.test_domain_worker.DomainOperationWorkerTests --verbosity 2`

Expected: FAIL because LDAP success currently changes only the target result.

- [ ] **Step 3: Return the moved DN and update the mirror transactionally**

Build the new DN from the stable relative RDN and validated destination. In `_persist_domain_outcome`, after the lease/generation fence succeeds, lock the model selected by `target_type`, compare its current DN with the task snapshot (or already-moved DN), and update only the action-specific fields. Missing or conflicting rows add a fixed sync-needed hint and never flip success to failure. Skip all local lookup for `create_user`.

- [ ] **Step 4: Run mirror GREEN tests**

Run: `python manage.py test index.test_domain_actions index.test_domain_worker --verbosity 2`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run: `git add net/domain/client.py net/tasks/executors/domain.py index/test_domain_actions.py index/test_domain_worker.py && git commit -m "fix: mirror successful domain writes locally"`

### Task 3: Reject disabled PC script profiles

**Files:**
- Modify: `index/views/scripts.py`
- Modify: `index/templates/tasks/profile_modal.html`
- Test: `index/test_pc_script_downloads.py`

**Interfaces:**
- Produces: HTTP 400 with a fixed “enable the profile first” message for a saved disabled profile.
- Preserves: enabled Windows BOM, enabled macOS bytes, filenames, and upload URL injection.

- [ ] **Step 1: Change the disabled-profile test to RED**

Replace `test_download_accepts_a_disabled_saved_profile` with a test expecting status 400, a fixed enable-first message, and no script bytes. Add a page test with only disabled profiles proving no executable download link is rendered.

- [ ] **Step 2: Run download RED tests**

Run: `python manage.py test index.test_pc_script_downloads.PcScriptDownloadTests --verbosity 2`

Expected: FAIL because the endpoint still generates the script and the UI assumes an enabled default profile.

- [ ] **Step 3: Enforce profile eligibility and UI state**

Return `HttpResponseBadRequest('分析配置已停用，请先启用配置。')` before generation, and render download links only when the enabled default profile has scan directories.

- [ ] **Step 4: Run download GREEN tests**

Run: `python manage.py test index.test_pc_script_downloads.PcScriptDownloadTests --verbosity 2`

Expected: PASS, including unchanged enabled BOM assertions.

- [ ] **Step 5: Commit Task 3**

Run: `git add index/views/scripts.py index/templates/tasks/profile_modal.html index/test_pc_script_downloads.py && git commit -m "fix: reject disabled PC script profiles"`

### Task 4: Live domain modal impact summary

**Files:**
- Create: `static/js/domain_operation_modal.js`
- Create: `static/js/domain_operation_modal.test.js`
- Modify: `index/templates/domain/operation_modal.html`

**Interfaces:**
- Produces: `bindDomainOperationModal(document)` and `refreshDomainOperationSummary(...)` CommonJS exports for dependency-free tests while auto-binding in the browser.
- Consumes: `data-domain-summary-input`, `data-domain-confirm-scope-row`, and `data-domain-confirm-scope` elements.

- [ ] **Step 1: Write modal JavaScript RED tests**

Use Node's built-in test runner with a minimal fake DOM. Assert both `input` and `change` listeners are registered on destination/group fields and dispatching either event immediately updates the visible scope text for `move_ou`, `add_group`, and `remove_group`.

- [ ] **Step 2: Run modal RED tests**

Run: `node --test static/js/domain_operation_modal.test.js`

Expected: FAIL because the controller does not exist.

- [ ] **Step 3: Extract and bind the controller**

Move the inline behavior into the static module, add a generic scope summary row that labels OU versus group, bind `input` and `change` on all scope-affecting controls, keep submit-time hidden target creation, and load the module with `{% static %}`.

- [ ] **Step 4: Run modal GREEN and template tests**

Run: `node --test static/js/domain_operation_modal.test.js` and `python manage.py test index.test_domain_permissions_ui --verbosity 2`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run: `git add static/js/domain_operation_modal.js static/js/domain_operation_modal.test.js index/templates/domain/operation_modal.html index/test_domain_permissions_ui.py && git commit -m "fix: refresh domain impact summary live"`

### Task 5: Final verification, self-review, report, and final commit

**Files:**
- Modify: `../../.superpowers/final-branch-fix-report.md`
- Modify if needed: files from Tasks 1–4 only

**Interfaces:**
- Produces: a final report mapping all findings to code/tests, RED/GREEN evidence, scans, and commit hashes.

- [ ] **Step 1: Run focused suites**

Run the domain action/worker/UI and PC download Django modules plus `node --test static/js/*.test.js` using the bundled Node executable.

- [ ] **Step 2: Run full verification**

Run `python manage.py test --parallel 4`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and `python -m pip check`.

- [ ] **Step 3: Run repository safety scans**

Run `git diff --check`; inspect `git status --short`; scan the changed diff and tracked files for submitted test-password literals, private-key headers, password-bearing snapshots, and common credential assignment patterns. Confirm no command connected to LDAP and no non-test database was opened.

- [ ] **Step 4: Self-review the complete diff**

Read `git diff --stat`, `git diff`, and the original final review. Check every requirement against an observable test and verify unrelated untracked files remain unstaged.

- [ ] **Step 5: Commit the implementation and report**

Stage only intended implementation files and commit with `fix: resolve final domain operations review`. Write `../../.superpowers/final-branch-fix-report.md` with item-by-item mappings, exact RED/GREEN commands and outcomes, all final verification outcomes, and that implementation commit hash; commit the report separately so its recorded implementation hash remains stable.
