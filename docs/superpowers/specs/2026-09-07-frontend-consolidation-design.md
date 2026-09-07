# Frontend Consolidation Design

## Goal

Consolidate the existing Apple-inspired operations UI without changing business logic, routes, models, integrations, or task processing. The result must be easier to maintain, more consistent across Django Admin and application pages, more accessible, and more usable on dense tables and the dashboard.

## Approved scope

1. Split the accumulated stylesheet into ordered local layers while preserving the current cascade and offline deployment.
2. Reuse common templates and JavaScript behavior for branding, sortable table headers, status badges, empty states, scheduling controls, confirmations, and form feedback.
3. Make Django Admin use the same header language and expose only 返回运维总览、修改密码、注销 in its user controls.
4. Improve field labels, inline errors, table semantics, focus targets, compact text, responsive table actions, active filters, pagination, and dashboard action hierarchy.

## Constraints

- Keep Bootstrap and native JavaScript; add no production framework or online asset.
- Preserve every existing action, filter, sort, page-size selector, import/export flow, inspection flow, alert flow, domain flow, and background task flow.
- Do not call real Feishu, DingTalk, domain-controller, alert-channel, or device endpoints during verification.
- Password and file controls keep their existing security behavior.
- Work in the current branch and preserve unrelated dirty-worktree changes.

## Design

The existing `style.css` cascade will be split at its established section boundaries into tokens, foundation, operations components, and modal workflow styles. `style.css` remains the stable entry point and imports the local layers in the original order, so templates and deployment configuration do not change.

Repeated presentation logic will move into narrow Django includes and small native-JavaScript controllers. Table rows remain page-specific because they contain business-specific output, while sortable headers, captions, state badges, empty states, active-filter controls, confirmation behavior, and field-error accessibility become shared primitives.

Django Admin keeps Django's forms and views but shares the application brand and token vocabulary. Its user-tools block contains only the three approved actions and does not expose a nonfunctional theme toggle.

The dashboard retains all links and data. Cards with actionable errors or abnormal counts are ordered before healthy cards, each card presents one clear primary action, and remaining links stay available in a compact secondary area.

## Verification

Use Django rendering tests for shared template behavior, Node tests for native JavaScript controllers, Django system checks, and the existing frontend/domain/dashboard/table test suites. Browser screenshot validation remains desirable but is not a completion gate while the local CUA browser is blocked by the current Windows ACL failure.
