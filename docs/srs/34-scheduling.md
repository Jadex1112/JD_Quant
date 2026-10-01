# Chapter 34 – Scheduling

## 34.1 Purpose

The Scheduling module triggers platform actions at defined times or recurring intervals: data imports, report generation, model retraining, deployment start/stop, reconciliation, maintenance, and user-defined automations. It is market-calendar aware and guarantees at-most-once or at-least-once firing semantics per schedule configuration.

## 34.2 Domain Entities

### 34.2.1 Schedule

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| trigger_type | Enum: CRON, INTERVAL, ONE_TIME, MARKET_EVENT | Required |
| cron_expression | String | 5- or 6-field cron; required for CRON |
| interval | Duration | ≥ 10 s; required for INTERVAL |
| run_at | Timestamp | Required for ONE_TIME |
| market_event | (venue_id, event: SESSION_OPEN, SESSION_CLOSE, offset) | Required for MARKET_EVENT |
| timezone | IANA timezone | Default UTC |
| calendar_filter | Enum: ALL_DAYS, TRADING_DAYS(venue), BUSINESS_DAYS(country) | Default ALL_DAYS |
| action | (action_type, parameters) | Task Engine job type or workflow reference |
| misfire_policy | Enum: FIRE_ONCE, SKIP, FIRE_ALL | Default FIRE_ONCE |
| concurrency_policy | Enum: ALLOW, FORBID, REPLACE | Default FORBID |
| delivery | Enum: AT_MOST_ONCE, AT_LEAST_ONCE | Default AT_LEAST_ONCE |
| enabled | Boolean | Default true |
| owner_id | Principal | Execution uses owner's permissions or a designated service account |
| start_at / end_at | Timestamp | Optional validity window |

## 34.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-34001 | The system shall support CRON, INTERVAL, ONE_TIME, and MARKET_EVENT schedules with the attributes of 34.2.1. | M | T |
| FR-34002 | The system shall evaluate schedules in the configured timezone and handle daylight-saving transitions such that skipped local times fire at the next valid time and repeated local times fire once. | M | T |
| FR-34003 | The system shall apply calendar filters using venue trading calendars (Chapter 19) and country holiday calendars. | M | T |
| FR-34004 | The system shall fire schedules within 1 s of their due time under normal load. | M | T |
| FR-34005 | The system shall apply the misfire policy for triggers missed during downtime. | M | T |
| FR-34006 | The system shall apply the concurrency policy when a previous execution of the same schedule is still running. | M | T |
| FR-34007 | The system shall guarantee in a multi-instance deployment that each trigger fires exactly once per due time for AT_MOST_ONCE and at least once for AT_LEAST_ONCE, using distributed coordination. | M | T |
| FR-34008 | The system shall display the next 10 fire times for any schedule prior to saving. | M | D |
| FR-34009 | The system shall record every firing with due time, actual fire time, resulting job identifier, and outcome. | M | T |
| FR-34010 | The system shall allow manual "run now" for any schedule without altering its regular cadence. | M | T |
| FR-34011 | The system shall alert the owner when a schedule's execution fails on N consecutive firings (default 3). | M | T |
| FR-34012 | The system shall provide system schedules (reconciliation, snapshots, retention, reference-data sync) that users cannot delete but administrators can adjust within bounds. | M | T |
| FR-34013 | The system shall reject schedules whose action the owner is not authorized to perform at creation and at each firing. | M | T |

## 34.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-34001 | Given a schedule at 02:30 local on a DST spring-forward day where 02:30 does not exist, then it fires at 03:00 local once. | FR-34002 |
| AC-34002 | Given 3 platform instances, then a CRON trigger with AT_MOST_ONCE produces exactly one job per due time. | FR-34007 |
| AC-34003 | Given a MARKET_EVENT schedule at SESSION_CLOSE − 10 min with TRADING_DAYS filter, then it does not fire on a venue holiday. | FR-34001, FR-34003 |

---

*End of Chapter 34 – Scheduling*
