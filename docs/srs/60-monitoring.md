# Chapter 60 – Monitoring

## 60.1 Purpose

The Monitoring module collects, stores, visualizes, and alerts on metrics describing the health and behavior of JD Quant AI across infrastructure, platform services, trading activity, data, AI, and integrations (Chapter 2.5.11, 11.11, 14.18).

## 60.2 Metric Catalog (Minimum)

| Domain | Metrics |
|---|---|
| Infrastructure | CPU, memory, disk, network I/O, GPU utilization and memory, container restarts |
| Services | Request rate, error rate, latency percentiles per endpoint, queue depths, consumer lag, thread/connection pools |
| Trading | Orders/s by status, rejects by reason, fills/s, order-to-ack latency, open orders, deployments by state, kill switch states |
| Risk | Pre-trade decision latency, rejects by limit, limit utilization, breaches |
| Market Data | Messages/s per source, exchange-to-receive latency, staleness, gaps, anomalies |
| Connectivity | Connection states, reconnects, venue error rates, rate-limit usage |
| AI | Inference latency, request rate, errors, drift scores, LLM token usage and cost |
| Jobs | Queue depth, wait time, run time, failures by type |
| Business | NAV, daily P&L, exposure (per portfolio, for authorized viewers) |

## 60.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-60001 | The system shall collect the metrics of 60.2 from all components via their metrics endpoints (CON-163) at a default resolution of 10 s (1 s for trading-critical metrics). | M | T |
| FR-60002 | The system shall retain metrics at full resolution for 15 days and downsampled (1 min) for 13 months. | M | T |
| FR-60003 | The system shall provide built-in operational dashboards: Platform Overview, Trading Operations, Market Data, Connectivity, Risk, AI, Jobs, and Infrastructure. | M | D |
| FR-60004 | The system shall support metric-based alert rules with thresholds, durations, rate-of-change, absence detection, and severity, routed through NCE. | M | T |
| FR-60005 | The system shall ship default alert rules for: service down, error rate > 1% for 5 min, order-to-ack p99 > threshold, consumer lag growth, disk > 85%, certificate expiry < 30 days, clock offset > CON-165 thresholds, job failure rate spike. | M | T |
| FR-60006 | The system shall support distributed tracing across services for sampled requests and all requests on the order path, viewable by trace identifier. | S | T |
| FR-60007 | The system shall expose metrics in a standard format for integration with external monitoring systems. | M | T |
| FR-60008 | The system shall define and track SLOs (Part D) with error budgets and burn-rate alerts. | S | T |
| FR-60009 | The system shall support maintenance windows that silence non-critical alerts for specified components. | M | T |
| FR-60010 | The system shall provide a status page summarizing component health for all users. | M | D |

## 60.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-60001 | Given a service stopping, then a "service down" alert fires within 30 s. | FR-60004, FR-60005 |
| AC-60002 | Given a maintenance window on the reporting service, then its non-critical alerts are silenced and critical alerts still fire. | FR-60009 |

---

*End of Chapter 60 – Monitoring*
