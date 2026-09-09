# Personnel phone and role access implementation plan

**Goal:** Add personnel phone throughout the existing data workflow and enforce the approved administrator / reader / guest boundary.

**Approved design:** Active staff or superusers administer all operations. Active authenticated readers may view records and export tables, but never change settings or run work. Guests see only aggregate dashboards and explicitly allowlisted basic inventories; details, raw logs, exports, scripts and configuration downloads require authentication or administration. Credentials never enter ordinary table exports.

**Architecture:** Central fail-closed route policy in `index.common.access`, with `AccessMiddleware` after authentication. Guest-safe public views use their own restricted queries and contexts. Existing authenticated pages retain their layouts but exclude administrator forms for readers. Public login/logout use Django authentication and validated redirects.

**Stack:** Existing Django ORM, templates, middleware and CSV/XLSX adapters; no additional dependency.

## Shared interfaces

- `index.common.access.is_admin(user)` returns true only for active authenticated staff or superusers.
- Context processor `index.common.access.access_context(request)` exposes `can_administer` and `can_view_private`.
- `index.common.public_views.public_response(request, view_name, kwargs)` renders only approved public routes; other routes are denied by middleware.
- Public routes: index, asset_list, people_statistics and legacy item_list for supported inventory types only. All other application GET routes require a logged-in active account by default.
- All unsafe application methods require admin except login/logout. Admin-only GETs include settings, integration previews/results, configuration export, scripts and import templates.
- New model field `People.phone` is optional text, not numeric, and existing records stay empty.

## Work units

- [x] Phone workflow: first test CSV/XLSX and provider roundtrips, implement model/migration/table fields/admin/provider mappings, verify blank and formatted phone values preserve text.
- [x] Central permissions: first test every mutation and sensitive download for guest/reader/admin; implement route policy, cache protection and ordinary login/logout; verify redirects, CSRF and no side effects on denial.
- [x] Guest presentation: first seed recognizable contact/configuration secrets and ensure none occur in responses, filter suggestions or sorting; implement whitelisted queries and public templates.
- [x] Reader presentation: guard settings/run/import forms at context construction and template level; verify readers can still filter/export and admins retain controls.
- [x] Integration: run new permission and phone suites, update old UI tests to explicit appropriate identities without weakening denial cases, run full regressions, review route coverage and document deployment migration.

## Verification result

2026-09-08: full suite ran 1210 tests in an isolated test database, OK with one opt-in real-log test skipped (process-only MD5 hasher for speed). The frontend contract suite also passed 29 tests with normal settings. `makemigrations --check --dry-run` reported no missing changes; `git diff --check` passed. Read-only review found no guest leak or reader write bypass. Existing Django Admin model permissions and people-operation session ownership remain additional restrictions, not automatically granted by the business administrator role. No real API/device calls or live database changes were made.

## Constraints and validation

Use isolated test databases only; do not contact real LDAP/API/devices. Do not clear or reseed operational data. No password/API key output. Existing databases gain the optional phone field through a normal migration; `deploy.demo` applies it on startup. No automatic git merge or branch deletion is part of this request.

Authorization tests must exercise HTTP endpoints and assert persisted counts/values unchanged on denied operations, not merely check button visibility. Public response tests include malicious sensitive filter parameters and direct sensitive URLs. Export tests check reader access and guest denial, including generated files and downloads.
