# Chapter 78 – Observability

## 78.1 Purpose

This chapter specifies cross-cutting observability properties (Chapter 10.16) that every component shall satisfy, complementing the Logging (36), Monitoring (60), Diagnostics (61), and Health (62) modules.

## 78.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-78001 | Every component shall emit the three telemetry signals — structured logs (36.2), metrics (60.2), and traces (FR-60006) — correlated by correlation and trace identifiers. | M | I |
| NFR-78002 | Every component shall expose liveness, readiness, health detail, and metrics endpoints (CON-163). | M | T |
| NFR-78003 | Every domain event shall carry event identifier, type, schema version, timestamp, producer, correlation identifier, and causation identifier (the event or command that caused it). | M | I |
| NFR-78004 | Every external call shall be instrumented with duration, outcome, and target, and exported as metrics and trace spans. | M | I |
| NFR-78005 | Telemetry emission overhead on the critical path shall not exceed 5% of the latency budget (Chapter 65). | M | T |
| NFR-78006 | An operator shall be able to answer, from dashboards alone within 2 minutes: which deployments are running, whether data is fresh, whether venues are connected, whether orders are flowing, and whether risk limits are near breach (QAS-08). | M | D |
| NFR-78007 | Service level indicators (SLIs) shall be defined for each capability in Chapter 66 and computed from telemetry. | S | I |
| NFR-78008 | Telemetry shall never contain secrets (CON-080) and shall mask personal data per policy. | M | T |
| NFR-78009 | Telemetry pipelines shall degrade gracefully: telemetry backend unavailability shall not impair trading (buffer and drop by priority). | M | T |

*Part D – Non-Functional Requirements is complete.*

---

*End of Chapter 78 – Observability*
