# Phase 3 Multi-Channel Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inherited/default alert policies, Feishu, DingTalk, and SMTP delivery, per-run abnormal notifications, recovery notifications, delivery history, detail pages, filters, and export.

**Architecture:** Executors emit normalized findings; an alert service groups findings per run/target, compares persisted prior state, creates abnormal or recovery events, then fans delivery out to selected channels. Delivery failure is isolated from inspection/analysis outcome and retried a finite number of times by the Worker.

**Tech Stack:** Django ORM, Requests, Python `smtplib`, HMAC signing, existing database task Worker.

**Spec:** `docs/superpowers/specs/2026-08-30-automation-alerting-and-records-design.md`

## Global Constraints

- Channels are multi-select; project policies either inherit the default policy or override it.
- Every abnormal run sends; there is no cooldown.
- Recovery sends only on an abnormal → normal transition.
- Same run and target merge findings into one event before channel fan-out.
- Channel failures never change inspection or computer-analysis status.
- Secrets never appear in table definitions, exports, templates, demo values, or raw error messages.
- Every behavior change follows RED → GREEN → focused tests → full tests → commit.

---

### Task 1: Alert models and secret-safe forms

**Files:**
- Create: `net/models/alerts.py`
- Modify: `net/models/__init__.py`
- Create: `net/migrations/0003_alerting.py`
- Create: `index/forms/alerts.py`
- Create: `index/test_phase3_alert_models.py`

**Interfaces:**
- Produces `AlertChannel`, `AlertPolicy`, `AlertState`, `AlertEvent`, `AlertDelivery`.
- Channel types: `feishu`, `dingtalk`, `email`.
- Event types: `abnormal`, `recovery`.

- [ ] **Step 1: Write failing model/form tests**

Test one default policy, multi-channel policy membership, inheritance/override validation, unique `(profile_type, profile_id, target_type, target_id, finding_key)` state, masked secret form initialization, and secret omission from string representations.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase3_alert_models -v 2`

- [ ] **Step 3: Implement models and forms**

Store channel-specific settings in typed fields or validated JSON, but expose one form class per channel. Empty submitted secret means “keep existing”; explicit replacement is write-only.

- [ ] **Step 4: Generate migration, run tests, commit**

```bash
git add py/net/net/models py/net/net/migrations py/net/index/forms/alerts.py py/net/index/test_phase3_alert_models.py
git commit -m "feat: add alert channels policies and events"
```

### Task 2: Feishu, DingTalk, and SMTP senders

**Files:**
- Create: `net/alerts/__init__.py`
- Create: `net/alerts/base.py`
- Create: `net/alerts/feishu.py`
- Create: `net/alerts/dingtalk.py`
- Create: `net/alerts/email.py`
- Create: `net/alerts/messages.py`
- Create: `index/test_phase3_senders.py`

**Interfaces:**
- `send_alert(channel, message) -> DeliveryResult(success, response_summary, retryable)`.
- `build_alert_message(event) -> AlertMessage(title, text, facts, detail_url)`.

- [ ] **Step 1: Write failing adapter tests**

Mock HTTP/SMTP and verify Feishu/DingTalk signatures, request timeout, response parsing, TLS/SSL exclusivity, multiple recipients, success, permanent credential failure, retryable timeout, and secret redaction.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase3_senders -v 2`

- [ ] **Step 3: Implement adapters**

Use explicit connect/read timeout, maximum response-summary length, and `redact_secret()` before persistence. Do not log request headers or full Webhook URLs.

- [ ] **Step 4: Implement normalized messages**

Include event type, project, target, run time, merged findings, and an internal detail path. Recovery copy names the previously abnormal finding.

- [ ] **Step 5: Run tests and commit**

```bash
git add py/net/net/alerts py/net/index/test_phase3_senders.py
git commit -m "feat: send alerts through feishu dingtalk and email"
```

### Task 3: State transitions, grouping, fan-out, and retry

**Files:**
- Create: `net/alerts/service.py`
- Create: `net/tasks/executors/alert_delivery.py`
- Modify: `net/tasks/worker.py`
- Modify: infrastructure/computer executors to emit normalized findings
- Create: `index/test_phase3_alert_service.py`

**Interfaces:**
- `process_target_findings(target_run, findings) -> list[AlertEvent]`.
- `deliver_event(event) -> list[AlertDelivery]`.
- A `Finding` has stable key, severity, title, and detail.

- [ ] **Step 1: Write failing transition tests**

Cover normal→abnormal, abnormal→abnormal on every run, abnormal→normal recovery, normal→normal no event, multiple findings merged into one run-target event, default policy inheritance, override policy, one channel failure with another success, and finite retry count.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase3_alert_service -v 2`

- [ ] **Step 3: Implement transactional state/event creation**

Lock relevant `AlertState` rows, update state, create at most one event per `(target_run, event_type)`, and store the merged finding snapshot before delivery.

- [ ] **Step 4: Implement channel fan-out and retry**

Create one delivery row per selected channel. Retry only `retryable=True` failures up to the configured fixed maximum; preserve every attempt count and latest redacted response.

- [ ] **Step 5: Integrate after record persistence**

Inspection/analysis records commit before alert processing. If processing fails before a real event and its delivery rows commit, persist a separate target alert-processing error and keep it eligible for retry without changing the target result or occupying the real event's unique slot. Once event/delivery rows exist, delivery failures remain recorded on those rows. Task 4 displays pre-event processing errors and their retry state on task details.

- [ ] **Step 6: Run tests and commit**

```bash
git add py/net/net/alerts py/net/net/tasks py/net/index/test_phase3_alert_service.py
git commit -m "feat: emit abnormal and recovery alerts"
```

### Task 4: Alert configuration, records, test-send, and export UI

**Files:**
- Create: `index/views/alerts.py`
- Modify: `index/views/__init__.py`
- Modify: `index/urls.py`
- Create: `index/templates/alerts/channel_modal.html`
- Create: `index/templates/alerts/policy_modal.html`
- Create: `index/templates/alerts/list.html`
- Create: `index/templates/alerts/detail.html`
- Modify: task/profile page headers
- Modify: `index/table_registry.py`
- Create: `index/test_phase3_alert_ui.py`

**Interfaces:**
- POST test-send route accepts a saved channel id only; server loads secrets.
- Alert table key `alert_events` supports event type, project, target, severity/status, channel outcome, and time filters.

- [ ] **Step 1: Write failing UI/security tests**

Assert global and project config buttons, multiple channel selection, inheritance, test-send result, detail delivery rows, filtered export, CSRF, masked secrets, and absence of secret values in response content.

- [ ] **Step 2: Run and verify RED**

Run: `.venv\Scripts\python.exe manage.py test index.test_phase3_alert_ui -v 2`

- [ ] **Step 3: Implement configuration and test-send views**

Put `告警配置` beside manual execution/configuration buttons. Use messages plus modal auto-open on validation failure.

- [ ] **Step 4: Implement alert list/detail/export**

Reuse phase-one compact workspace and filtered export service. Display delivery status per channel without raw secret-bearing endpoints.

- [ ] **Step 5: Extend deterministic demo data**

Create disabled fake channels, default and override policies, abnormal/recovery events, and mixed delivery results without making network calls.

- [ ] **Step 6: Full verification and browser acceptance**

Run system/migration/full Django/JS tests. In browser verify channel forms, multi-select inheritance, test-send mocked/dev-safe behavior, list/detail/export, narrow layout, and zero console errors.

- [ ] **Step 7: Commit**

```bash
git add py/net/index py/net/net/alerts py/net/net/models py/net/net/tasks
git commit -m "feat: manage and review multi channel alerts"
```
