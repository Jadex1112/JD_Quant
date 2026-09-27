# Chapter 97 – Verification

## 97.1 Purpose

This chapter specifies how the platform is verified against this SRS ("are we building the product right?").

## 97.2 Verification Methods

| Method | Code | Application |
|---|---|---|
| Test | T | Automated unit, integration, contract, end-to-end, performance, chaos, security tests |
| Inspection | I | Review of code, configuration, documentation, schemas |
| Analysis | A | Static analysis, modeling, statistical analysis of test results, coverage analysis |
| Demonstration | D | Observed execution of features by reviewers |

## 97.3 Verification Levels

| Level | Scope | Environment |
|---|---|---|
| Unit | Functions, classes, calculation rules (BR-*) | Developer / CI |
| Component | Single engine with simulated dependencies | CI |
| Contract | Internal and external interfaces (API-81008, FR-45002) | CI |
| Integration | Multiple engines, real infrastructure services | Integration environment |
| System | Full platform with simulated venues and recorded data | Testing / QA |
| Venue integration | Adapters against venue testnets | Testing / QA |
| Non-functional | Performance, latency, reliability, security, accessibility | Dedicated performance / staging |
| Operational | Backup/restore, failover, DR exercises | Staging / DR |

## 97.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-97001 | Each requirement shall be verified using the method in its Ver column. | M | I |
| OPS-97002 | A simulated venue shall be provided implementing the adapter interface with configurable behaviours (latency, rejects, partial fills, disconnects, out-of-order reports, duplicate fills) for deterministic system tests. | M | T |
| OPS-97003 | Recorded market data sessions covering normal, volatile, and anomalous conditions shall be maintained as regression datasets. | M | I |
| OPS-97004 | Calculation rules (BR-*) and metrics (Chapter 32) shall be verified against independently computed reference values (FR-32051). | M | T |
| OPS-97005 | Verification results shall be recorded per release with pass/fail per requirement and linked in the traceability register. | M | I |
| OPS-97006 | Defects found shall be linked to the violated requirement identifier. | M | I |

---

*End of Chapter 97 – Verification*
