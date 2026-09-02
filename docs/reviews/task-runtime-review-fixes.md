# Task 6 final-review fixes

Date: 2026-08-31. Base: `e47f985`.
Scope: the six findings in `.superpowers/sdd/2026-08-30-phase-2-worker-and-scheduling/task-6-review.md` (repository root).

## Changes

1. Both profile selectors reload the chosen enabled project's full server-rendered state. The submitting form is disabled during navigation. Inspection/analysis items, schedule, concurrency, timeout, scan directories, archive paths and file range belong to the selected profile. Row selection and current filters survive the reload. Manual checkboxes cannot select items excluded by the profile. Multiple scan directories render as real newlines.
2. Record-page filtered execution applies the actual `inspection_records` filters (including the target-detail scope), deduplicates asset IDs, rechecks asset existence and snapshots that scope. No matching records means no task writes.
3. Migration `0006` adds ordinary nullable-column unique constraints for both schedule profile references. No data-repair operation is included. Profile/schedule writes lock the profile inside one transaction; SQLite requests use the existing process database guard from before their first read. No profile can own two schedules; unscheduled profiles may have none.
4. Scan child enqueue and parent result/link persistence now share one lease-fenced transaction. The lease is checked again after child enqueue; a stale owner rolls back the child. A parent-link write error also rolls back child creation. Existing queue snapshots, duplicate protection and Worker dispatch remain intact.
5. Dashboard and asset-detail manual actions link to the configured asset-page modal. They no longer expose the legacy command-profile bypass; compatibility routes themselves remain unchanged.
6. Row buttons clear other selections, choose their row and set selected mode. Computer asset rows no longer offer misleading per-row log-scan execution; the page-level analysis action remains.

## TDD evidence

Before production changes, `index.test_phase2_task6_review` reproduced the profile mismatch, record-filter rejection, duplicate schedules/concurrent SQLite failure, orphan child, legacy entrypoint and missing row state. Both new JS tests failed because profile-switch and row-selection behavior was absent.

Regression coverage now includes disjoint CPU/memory profiles, distinct computer paths/items/date range/schedule, preserving the unselected profile, status/name/target/no-match record filters and deduplication, database uniqueness for both profile types, simultaneous independent request clients, lease expiry immediately before child enqueue, rollback on parent-link failure, configured links and a one-row task snapshot.

## Verification

- Focused: `manage.py test index.test_phase2_task6_review index.test_phase2_task_ui index.test_phase2_task_models index.test_phase2_queue index.test_phase2_schedules index.test_phase2_worker index.test_phase2_worker_review index.test_phase2_computer_logs -v 0` — **165 passed**.
- Full: `manage.py test -v 0` — **371 passed**.
- Bundled Node: `--test static/js/table_workspace.test.js static/js/task_ui.test.js` — **13 passed**; `--check static/js/task_ui.js` passed.
- `manage.py check` passed; `manage.py makemigrations --check --dry-run` reported no changes.
- Fresh temporary SQLite schema migrated through `net.0006`; `git diff --check` passed (only Git's CRLF conversion notices).

### Actual in-app browser acceptance

Used only a temporary SQLite database, demo assets and additional disjoint test profiles at `127.0.0.1:8001`. No real collectors or provider services were called.

- Clicked the Windows server row action: selected mode and exactly that row. Switched CPU-only to memory-only profile: memory checked, CPU disabled, row preserved. Submitted: queued task with one target and memory-only snapshot, profile concurrency 7.
- Ran the queued task in a separate shell Worker with a mocked Windows collector. Reloaded task detail: **1/1 success**, result link visible.
- Switched configuration from CPU to memory: matching name, timeout 123, concurrency 7, daily 04:25 and memory item loaded. Saved daily 05:40 and a renamed memory profile; CPU name/items/timeout/concurrency stayed unchanged.
- Switched computer activation to resource profile: both directory lines, processed/failed paths, recursive flag, August date range, resource-only item and two-hour schedule loaded correctly.
- Submitted an inverted date range: clear error, configuration modal reopened, original date remained unchanged. Saved recent 10 days and three-hour schedule; the other computer profile remained unchanged.
- Filtered server records to normal: three records became a queued task for two deduplicated assets.
- Inspected the configuration modal at 390 x 844: page width 390, no horizontal overflow; screenshot confirmed responsive layout. Reset viewport afterward. Browser console warning/error log was empty.

## Boundaries

Existing Windows/Linux separate Worker startup instructions in `README.md` remain applicable. No alerting or production integration was added. Native Linux and live MySQL were not available for this run; multi-process MySQL lock behavior still needs deployment-environment validation. The portable constraints and lease rollback contracts were exercised on SQLite. No additional review subagent tool was available; this is a fix/evidence report, not an independent approval of the patch.
