# Chapter 62 – Health Engine

## 62.1 Purpose

The Health Monitoring Engine (HME) continuously determines the health of every component and dependency, aggregates health into a platform health model, and drives automated responses (restart, failover, safe state) and trading-eligibility decisions.

## 62.2 Health Model

| Level | States |
|---|---|
| Component | HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN |
| Dependency (Chapter 17) | AVAILABLE, DEGRADED, UNAVAILABLE |
| Capability (e.g. "Live trading on venue X") | OPERATIONAL, IMPAIRED, DOWN |
| Platform | OPERATIONAL, DEGRADED, MAJOR_OUTAGE, MAINTENANCE |

Each component exposes:

| Probe | Purpose |
|---|---|
| Liveness | Process is running and not deadlocked |
| Readiness | Component can serve requests (dependencies available, warm-up complete) |
| Health detail | Sub-checks with status and message |

## 62.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-62001 | The system shall probe liveness and readiness of every component at a configurable interval (default 5 s) and record health detail. | M | T |
| FR-62002 | The system shall derive dependency health from probe results, circuit breaker states (FR-22026), and error rates. | M | T |
| FR-62003 | The system shall compute capability health from a declared dependency map (e.g. live trading on venue X requires OMS, RMS, EMS, adapter X, market data X, CME, database) and publish changes as events. | M | T |
| FR-62004 | The system shall trigger automated responses per health policy: restart unhealthy components (via supervisor), remove unready instances from load balancing, fail over to standby (HA), and enter SAFE state (FR-51004) for Critical dependency loss. | M | T |
| FR-62005 | The system shall prevent deployments from starting when any capability they require is DOWN, and pause running deployments per configuration when a required capability becomes DOWN. | M | T |
| FR-62006 | The system shall debounce health transitions (default: 3 consecutive failed probes to become UNHEALTHY, 2 consecutive successes to recover) to avoid flapping. | M | T |
| FR-62007 | The system shall display a health map visualizing components, dependencies, and capabilities with current states and history. | M | D |
| FR-62008 | The system shall publish platform status to the status page (FR-60010) and to external monitoring. | M | T |
| FR-62009 | The system shall itself be highly available in HA deployments and fail safe: if the Health Engine is unavailable, trading engines continue with local health checks and block new deployments from starting. | M | T |
| FR-62010 | The system shall compute component availability over time for SLO reporting (Part D). | M | T |

## 62.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-62001 | Given the RMS becomes unhealthy, then the "live trading" capability for all venues becomes DOWN and running live deployments pause per policy. | FR-62003, FR-62005 |
| AC-62002 | Given a probe alternating success/failure every 5 s, then component state does not flap. | FR-62006 |

---

*End of Chapter 62 – Health Engine*
