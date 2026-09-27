# Chapter 99 – Testing Criteria

## 99.1 Purpose

This chapter specifies the testing criteria: required test types, coverage, entry and exit criteria, and defect severity definitions.

## 99.2 Defect Severity

| Severity | Definition | Examples |
|---|---|---|
| S1 – Critical | Financial loss possible, safety control bypassed, data integrity or security compromise | Duplicate live order; RMS bypass; lost fill; secret exposure |
| S2 – Major | Major capability unusable, no workaround | Deployments cannot start; backtests fail |
| S3 – Moderate | Capability impaired, workaround exists | Report formatting incorrect; slow query |
| S4 – Minor | Cosmetic or low impact | Label typo |

## 99.3 Required Test Suites

| Suite | Minimum Content |
|---|---|
| Unit | All calculation rules, state machines (FR-89005), validation rules |
| Property-based | OMS and Position Engine invariants (FR-95003) |
| Contract | OpenAPI (API-80002), internal interfaces (API-81008), adapter conformance (FR-45002) |
| End-to-end | SEQ-01 to SEQ-08 (FR-90002) |
| Fault injection | All failure modes of 91.2 (FR-91004) |
| Performance | Load model of 64.3; latency budgets of 65.2 |
| Security | SAST, dependency scanning, DAST, tenant isolation, secret scanning, penetration test |
| Accessibility | Automated WCAG checks + manual audit (NFR-73010) |
| Determinism | Backtest reproducibility (FR-25009), simulator determinism (FR-22086) |
| Recovery | Backup/restore (OPS-75006), failover (NFR-66003), DR (OPS-77006) |

## 99.4 Entry and Exit Criteria

| Phase | Entry | Exit |
|---|---|---|
| System test | Feature complete for the release scope; all unit/contract suites passing | All M requirements verified; no open S1/S2; S3 ≤ agreed threshold |
| Performance test | System test exit | All NFR in Chapters 64–65 met or waived with approval |
| Release candidate | Performance exit, security scan clean of critical/high | Traceability complete (OPS-96003); release approval |

## 99.5 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-99001 | All suites of 99.3 shall exist and run in CI or scheduled pipelines at the frequency appropriate to their cost (unit/contract per change; E2E daily; performance/chaos per release). | M | I |
| OPS-99002 | No release shall ship with an open S1 defect; S2 defects require documented approval by product and engineering leads. | M | I |
| OPS-99003 | Flaky tests shall be quarantined only with an owner and a fix deadline, and never for tests covering S1-class behaviour. | M | I |
| OPS-99004 | Test data shall not contain production personal data or credentials (CON-084). | M | I |

---

*End of Chapter 99 – Testing Criteria*
