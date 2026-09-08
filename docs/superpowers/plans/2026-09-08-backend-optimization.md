# Backend optimization implementation plan

## Approved scope

Implement the reviewed backend improvements without changing startup entry points or database selection. Preserve filters, ordering, exports, historical evidence, task cancellation and alert recovery semantics. No live network tests or deletion of current data.

## Parallel ownership

- PC worker: split preparation and persistence; test lease/cancellation before commit.
- Query worker: SQL filtering and pagination, light result projections, no N+1, migration 0036.
- Configuration worker: consistent versioned policy snapshots and one software policy load per task.
- SNMP worker: reuse stable metadata within sampling, prefer 64-bit counters with targeted fallback.
- Database worker: configurable SQLite concurrency, opt-in safe archive/retention maintenance.
- Main: aggregate task statistics, result snapshot references, migration 0037 indexes, integration review.

## Verification steps

- [x] Add focused regressions for pagination, snapshots, progress, and lease-loss rollback.
- [x] Preserve complete dropdown options and exports across page boundaries, including people-mode placeholders.
- [x] Aggregate statistics without hydrating target JSON; paginate historical tasks in SQL.
- [x] Keep alerts functional with lightweight snapshots and historical full snapshots.
- [x] Preserve evidence and dry-run maintenance by default.
- [x] Run targeted unit/integration tests, migration consistency and Django checks.
- [x] Review inter-module interfaces; document restart/migration requirements and optional maintenance.

## Deployment safety

Do not run automatic cleanup or alter current databases while Web/Worker may be active. Schema migrations are additive with historical projection backfill, to be applied together after verification. SQLite WAL configuration must skip memory databases and not change database paths.
