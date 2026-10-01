# Chapter 88 – External Services

## 88.1 Purpose

This chapter specifies interface requirements for other external services listed in Chapter 2.8 and Chapter 17: data providers, news and economic calendars, identity providers, notification services, and monitoring/logging platforms.

## 88.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-88001 | Third-party market data providers shall be integrated through the DATA_SOURCE adapter interface with the same normalization and validation as venue data (Chapter 20). | M | T |
| API-88002 | News providers shall be integrated through a news adapter producing normalized news items (id, timestamp, headline, body, source, instruments, categories, sentiment if provided). | S | T |
| API-88003 | Economic calendar providers shall be integrated producing normalized events (event, country, scheduled time, importance, actual, forecast, previous). | S | T |
| API-88004 | FX rate providers shall supply rates for valuation (DEP-202) with at least hourly updates and end-of-day fixing. | M | T |
| API-88005 | Identity providers shall be integrated via OIDC and SAML 2.0 (FR-40024). | S | T |
| API-88006 | Email delivery shall support SMTP and API-based providers; chat channels via incoming webhooks or bot APIs; SMS via provider APIs (Chapter 33). | M (email, webhook), S (chat, SMS) | T |
| API-88007 | Monitoring and logging exports shall use open standards (e.g. OpenTelemetry for traces/metrics/logs, Prometheus exposition format) (FR-60007, FR-36010). | M | T |
| API-88008 | Every external service integration shall implement timeout, retry, circuit breaker, and health indicator (17.7). | M | I |
| API-88009 | Every external service integration shall be documented in the dependency register with owner, license, data processed, and data policy (CON-144). | M | I |

*Part E – Interface Requirements is complete.*

---

*End of Chapter 88 – External Services*
