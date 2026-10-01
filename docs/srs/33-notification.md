# Chapter 33 – Notification

## 33.1 Purpose

The Notification & Communication Engine (NCE) delivers alerts and informational messages to users and external systems through multiple channels. It evaluates notification rules against platform events, applies routing, deduplication, throttling, escalation, and quiet-hours policies, and tracks delivery and acknowledgment.

## 33.2 Domain Entities

### 33.2.1 Notification

| Attribute | Type | Description |
|---|---|---|
| category | Enum: TRADING, RISK, EXECUTION, SYSTEM, SECURITY, AI, DATA, REPORT, WORKFLOW | Required |
| severity | Enum: INFO, WARNING, HIGH, CRITICAL | Required |
| title | String(200) | Required |
| body | Text | Required; plain text and rich variants |
| source_event_id | Reference | Triggering event |
| entity_ref | (type, id) | Related entity for deep link |
| recipients | List of Principal | Resolved recipients |
| dedup_key | String | For suppression of duplicates |
| requires_ack | Boolean | Default true for CRITICAL |
| status | CREATED, DISPATCHED, DELIVERED, FAILED, ACKNOWLEDGED, EXPIRED | Lifecycle |

### 33.2.2 Channel

| Channel | Description | Priority |
|---|---|---|
| IN_APP | In-application notification center and toasts | M |
| EMAIL | SMTP or email service provider | M |
| WEBHOOK | Signed HTTP POST to user-configured endpoint | M |
| CHAT | Chat platforms (e.g. Slack, Microsoft Teams, Telegram, Discord) | S |
| SMS | SMS provider | S |
| PUSH | Mobile / desktop push | C |
| VOICE | Voice call for CRITICAL escalation | C |

### 33.2.3 NotificationRule

| Attribute | Description |
|---|---|
| event_filter | Event type pattern plus attribute conditions |
| severity_override | Optional |
| recipients | Users, roles, groups, on-call schedule |
| channels | Ordered list |
| throttle | Max notifications per window per dedup key |
| escalation | List of (delay, recipients, channels) if not acknowledged |
| quiet_hours_behavior | SUPPRESS, DEFER, IGNORE (always for CRITICAL) |

## 33.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-33001 | The system shall provide default notification rules for: kill switch triggered/released, risk breach, risk warning, order rejected (live), deployment FAILED/HALTED, connection lost/restored, market data stale, reconciliation mismatch, balance mismatch, security events, job failed, and report ready. | M | T |
| FR-33002 | The system shall allow users to create personal rules and administrators to create workspace rules with the attributes of 33.2.3. | M | T |
| FR-33003 | The system shall allow users to configure channel preferences per category and severity, except that CRITICAL notifications to users on the workspace on-call schedule cannot be disabled. | M | T |
| FR-33004 | The system shall deliver IN_APP notifications within 1 s and external channel dispatch within 5 s of the triggering event for HIGH and CRITICAL severity. | M | T |
| FR-33005 | The system shall deduplicate notifications sharing a dedup_key within a configurable window (default 5 min), incrementing an occurrence counter instead of sending duplicates. | M | T |
| FR-33006 | The system shall throttle per rule and per recipient, and summarize throttled notifications in a digest. | M | T |
| FR-33007 | The system shall retry failed deliveries with exponential backoff (up to 5 attempts over 10 min) and fall back to the next channel for HIGH and CRITICAL (DEP-007). | M | T |
| FR-33008 | The system shall escalate unacknowledged notifications per the rule's escalation policy. | M | T |
| FR-33009 | The system shall support acknowledgment from the in-app center and via signed links in email and chat messages. | M | T |
| FR-33010 | The system shall support quiet hours per user with the behaviors in 33.2.3. | S | T |
| FR-33011 | The system shall provide an in-app notification center with filtering, read/unread status, and bulk acknowledgment. | M | D |
| FR-33012 | The system shall sign webhook payloads with HMAC-SHA256 using a per-endpoint secret and include a timestamp to prevent replay. | M | T |
| FR-33013 | The system shall not include secrets, credentials, or full account identifiers in notifications sent to external channels; account identifiers shall be masked. | M | T |
| FR-33014 | The system shall support notification templates with localization (Part D internationalization). | S | T |
| FR-33015 | The system shall support on-call schedules with rotation and overrides for routing operational alerts. | S | T |
| FR-33016 | The system shall provide a test-send function for each configured channel. | M | T |
| FR-33017 | The system shall record delivery history per notification including channel, attempt, result, and latency. | M | T |

## 33.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-33001 | Given 50 identical stale-data events in 5 minutes, then the user receives one notification with an occurrence count of 50. | FR-33005 |
| AC-33002 | Given a CRITICAL notification not acknowledged within the escalation delay, then the next escalation level recipients are notified. | FR-33008 |
| AC-33003 | Given a failed email delivery for a CRITICAL alert, then delivery is attempted on the next configured channel. | FR-33007 |

---

*End of Chapter 33 – Notification*
