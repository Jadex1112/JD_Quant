# Chapter 66 – Availability

## 66.1 Purpose

This chapter specifies availability targets per capability and deployment tier, and maintenance constraints.

## 66.2 Deployment Tiers

| Tier | Description |
|---|---|
| T1 – Single Node | REF-S, no redundancy; suitable for individual traders |
| T2 – High Availability | REF-P with redundant instances, replicated database, automatic failover (Chapter 14.15) |

## 66.3 Availability Targets

Availability is measured monthly, excluding announced maintenance windows, as the fraction of minutes a capability is OPERATIONAL (Chapter 62).

| Capability | T1 Target | T2 Target |
|---|---|---|
| Live trading (order submission and cancellation) | 99.5% | 99.95% |
| Risk pre-trade service | Same as live trading (co-dependent) | 99.95% |
| Market data distribution | 99.5% | 99.95% |
| Cancel-only path (ability to cancel open orders) | 99.5% | 99.99% |
| User interface and APIs | 99.0% | 99.9% |
| Backtesting and research | 98.0% | 99.5% |
| AI assistant features | 95.0% | 99.0% |
| Reporting | 98.0% | 99.5% |

## 66.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-66001 | The system shall meet the availability targets of 66.3 for its deployment tier. | M | A |
| NFR-66002 | T2 deployments shall have no single point of failure on the live trading path. | M | I |
| NFR-66003 | T2 failover of trading engines shall complete within 30 s with no loss of acknowledged state (RPO = 0 for orders, fills, positions). | M | T |
| NFR-66004 | Planned maintenance on T2 shall be performed without trading downtime via rolling updates, except for major database migrations announced ≥ 72 h in advance and scheduled outside configured trading hours. | S | D |
| NFR-66005 | Failure of non-critical capabilities (reporting, analytics, AI, research) shall not reduce live trading availability (CON-100). | M | T |
| NFR-66006 | The platform shall measure and report availability per capability monthly (FR-62010). | M | T |

---

*End of Chapter 66 – Availability*
