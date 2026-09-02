# Feature-Oriented Project Layout Design

## Status

Approved on 2026-09-02 and amended after Phase 6: the project is not deployed, so
the final structure uses canonical modules directly and removes nonessential legacy
facades. Implementation must pause after each phase for user inspection.

## Goal

Reorganize the monitoring system by business domain and file type so personnel,
Active Directory, each device category, inspections, alerts, integrations, UI code,
static assets, managed-device agents, tests, deployment files, and documentation have
clear ownership.

The reorganization must improve maintainability without changing the deployed
system's database identity or public interface.

## Runtime Stability Requirements

- Keep the existing Django applications and their labels: `net` and `index`.
- Keep every existing database table name, model identity, relationship, permission,
  and migration history.
- Keep `net/migrations/` intact. The reorganization must not require a data migration.
- Keep existing page paths, API paths, URL names, script download paths, and log upload
  paths.
- Keep the Django startup paths `net.settings`, `net.urls`, `net.wsgi`, and `net.asgi`.
- Keep deployment entry points, `deploy.demo`, worker commands, and existing agent
  calls operational.
- Use one canonical Python import path for each feature. Historical aliases are not
  retained because the project has not been deployed; migration imports point
  directly to canonical model modules.
- Do not move, replace, or delete runtime databases, uploaded logs, generated
  configurations, or demo data during the reorganization.

## Chosen Approach

Use feature-oriented packages inside the existing `net` and `index` Django
applications. This gives each business domain a clear folder without introducing new
Django application labels.

Feature modules contain the implementation and project code imports them directly.
After Phase 6 the user explicitly removed the legacy-import requirement, so redundant
facades are deleted and repository tests/migrations use canonical paths.

A true split into separate Django applications is explicitly out of scope because it
would change model ownership, content types, permissions, and migration behavior.

## Target Source Layout

```text
net/
|-- settings.py                 # Stable Django startup entry
|-- urls.py                     # Stable root URL entry
|-- apps.py
|-- models/                     # Database model definitions only
|   |-- people.py
|   |-- domain.py
|   |-- devices.py
|   |-- inspections.py
|   |-- alerts.py
|   `-- integrations.py
|-- people/                     # Personnel import, synchronization, statistics
|-- domain/                     # AD connection, sync, secrets, bulk operations
|-- devices/
|   |-- pc/                     # PC inventory and log analysis
|   |-- server/                 # Windows and Linux servers
|   |-- network/                # Network devices and configuration export
|   `-- security/               # Security-device API integrations
|-- inspections/                # Runs, records, scheduling, target selection
|-- alerts/                     # Policies, routing, Feishu, DingTalk, email
|-- integrations/               # External directory/API adapters
|-- data_exchange/              # CSV import/export and device config export
|-- dashboard/                  # Aggregate dashboard and statistics services
|-- infrastructure/             # SSH, HTTP, queue, shared sanitization
|-- scripts/                    # Downloadable PC agent generation
|-- admin/                      # Django admin registrations by domain
|-- management/commands/
`-- migrations/                 # Existing migration files, unchanged

index/
|-- common/                     # Shared table, filter, pagination helpers
|-- dashboard/
|-- people/
|-- domain/
|-- devices/
|   |-- pc/
|   |-- server/
|   |-- network/
|   `-- security/
|-- inspections/
|-- alerts/
|-- integrations/
|-- templates/                  # Namespaced with the same feature structure
`-- urls.py                     # Stable application URL table

static/
|-- app/
|   |-- css/
|   `-- js/
|       |-- common/
|       |-- domain/
|       |-- devices/
|       `-- inspections/
`-- vendor/bootstrap/

agents/
|-- pc/
|   |-- windows/
|   `-- macos/
`-- server/windows/

tests/
|-- people/
|-- domain/
|-- devices/
|-- inspections/
|-- alerts/
`-- frontend/

deploy/
|-- windows/
`-- linux/
```

The final exact filenames may differ where Django requires a conventional startup
module. Implementation code uses canonical feature packages directly; the ownership
and dependency rules remain authoritative.

## Dependency Direction

The intended dependency direction is:

```text
index request/view layer
        -> net feature services
        -> net infrastructure adapters
        -> external systems

net feature services
        -> net models
        -> inspection/task/alert orchestration
```

- Views parse requests, enforce permissions, and prepare responses. They do not
  implement protocol clients or device parsers.
- Feature packages own business rules and result normalization.
- Infrastructure owns generic SSH, HTTP, queue, and sanitization primitives.
- Models contain persistence definitions and small model-specific behavior, not
  protocol workflows.
- Cross-feature task execution goes through the inspection/task orchestration layer.
- Alerts consume normalized task findings instead of calling collectors directly.

For example, a server inspection flows from the `index` action to inspection
orchestration, then to the server feature, then to a generic SSH or HTTP adapter. The
normalized result returns to orchestration for persistence and alert processing.

## Canonical Entry Modules

`net.models` remains Django's model registration surface and `index.views` remains
the URL callable aggregation surface. All other code imports feature modules
directly; duplicate legacy service, task, export, form, and view aliases are removed.

## Runtime and Generated Files

- `demo-runtime/`, the root development database, uploaded logs, processed/failed log
  directories, and generated configuration archives are runtime data and will not be
  moved or overwritten.
- `static/` is source-controlled static content.
- `staticfiles/` is generated by `collectstatic`; it remains a generated deployment
  artifact and is not treated as source.
- `.task6-artifacts/`, worktrees, caches, and virtual environments remain outside the
  source organization and are not reorganized as application code.

## Phased Implementation

Implementation pauses after every phase so the user can inspect the result before the
next phase begins.

### Phase 1: Structure and Application Contracts

Create the package skeleton and focused tests that pin app labels, database table
names, migration state, URL paths/names, and startup imports. Do not move business
implementations yet.

### Phase 2: Models and Admin

Organize model definitions and admin registrations by domain. Keep `net.models` as
the Django model aggregation surface and update migrations to canonical model-module
imports. Confirm that Django detects no schema changes.

### Phase 3: Personnel and Active Directory

Move personnel import/sync/statistics and Active Directory connection, sync, secret,
and bulk-operation code into their feature packages. Preserve permissions and worker
entry points.

### Phase 4: Device Features

Move PC, server, network-device, and security-device inventory, collection, parsing,
and configuration-export behavior into separate device packages.

### Phase 5: Inspections, Analysis, Scheduling, and Alerts

Consolidate task queue orchestration, worker execution, schedules, inspection records,
PC log analysis, alert policies, channels, abnormal notifications, and recovery
notifications.

### Phase 6: UI, Templates, and Static Sources

Organize views, forms, templates, and JavaScript by matching feature names. Keep the
complete external URL contract unchanged. Separate application static sources from
vendor assets and generated `staticfiles` output.

### Phase 7: Tests, Agents, Deployment, and Documentation

Organize tests by business domain, place managed-device scripts under `agents`, tidy
Windows/Linux deployment assets, and update project documentation and developer
navigation guidance.

## Validation Policy

Testing is deliberately proportional to each phase. Do not run broad or repetitive
test suites before they are needed.

After each phase, run only:

- import or contract tests directly affected by the phase;
- `python manage.py check` when Django wiring changed;
- `makemigrations --check --dry-run` when models moved;
- focused JavaScript tests when static JavaScript moved.

At the end of all approved phases, run one full verification pass:

- the complete Django test suite;
- the complete JavaScript test suite;
- migration drift checking;
- `deploy.demo --prepare-only`;
- a smoke start using the existing demo database;
- a focused worker claim/execute smoke check.

## Commit and Rollback Strategy

- Each phase is one reviewable commit unless a phase must be split to keep commits
  buildable.
- The worktree must be clean before beginning the next approved phase.
- A failed phase is corrected or reverted at the code-commit level; databases and
  runtime data are never rolled back or recreated as part of this reorganization.
- Unrelated user changes are preserved.

## Acceptance Criteria

The reorganization is complete when:

- source files have clear feature ownership and common infrastructure is not
  duplicated;
- existing databases open directly and Django reports no unintended migrations;
- existing URLs, URL names, API behavior, script endpoints, and deployment entry
  points remain operational;
- migrations and operators use the documented canonical imports and commands;
- task workers can process existing task records;
- source static assets are separate from generated static output;
- tests and documentation follow the same feature vocabulary as production code;
- the final focused and full verification checks pass.
