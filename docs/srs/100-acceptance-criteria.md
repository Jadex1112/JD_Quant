# Chapter 100 – Acceptance Criteria

## 100.1 Purpose

This final chapter defines the criteria for formal acceptance of JD Quant AI Release 1.0 and consolidates the platform-level acceptance scenarios. Module-level acceptance criteria (AC-CCNNN) are defined at the end of each Part C chapter.

## 100.2 Release 1.0 Scope for Acceptance

Release 1.0 comprises all requirements with priority **M** in Parts B–F. Requirements with priority **S** are targeted for Release 1.x and **C** for future releases.

## 100.3 Platform Acceptance Scenarios

| ID | Scenario | Expected Outcome |
|---|---|---|
| PAC-01 | Research-to-live lifecycle: a researcher creates a strategy from a template, backtests it, optimizes with out-of-sample validation, paper trades for the configured minimum period, obtains risk approval, and deploys live with an automation policy. | Each promotion gate enforces its evidence; live deployment trades within limits; full decision chain available (FR-38007). |
| PAC-02 | Safety under stress: during live trading at the load model, a venue disconnects for 60 s while fills occur. | No duplicate orders; all fills reconciled; new orders blocked during outage; strategies resume per policy. |
| PAC-03 | Kill switch drill: Risk Manager triggers a workspace FLATTEN kill switch with 20 running deployments. | New orders blocked within 50 ms; all open orders canceled; positions flattened; notifications delivered; state persists across restart. |
| PAC-04 | Risk limit enforcement: concurrent strategies on one account attempt to exceed position and daily loss limits. | Serialized evaluation prevents joint breach (FR-93001); REDUCE_ONLY after loss limit; breaches acknowledged and audited. |
| PAC-05 | Recovery: OMS, RMS, and EMS processes are killed during trading. | Recovery sequence completes within RTO; invariants INV-01–INV-10 hold. |
| PAC-06 | Backtest reproducibility: 10 historical runs re-executed. | Bit-identical results (FR-25009). |
| PAC-07 | AI governance: a model is trained, evaluated, shadowed, promoted, drifts, triggers an alert, and is rolled back. | Each stage gated per 49.3; rollback within 30 s; inference records linked to orders. |
| PAC-08 | Copilot safety: a user asks the copilot to perform trading lifecycle actions and exposes it to injected instructions in data. | Actions require confirmation; injected instructions are not followed; all actions audited. |
| PAC-09 | Security: penetration test and tenant-isolation tests. | No critical/high findings open; zero cross-workspace data access; zero secret exposure. |
| PAC-10 | Operability: operator answers the five QAS-08 questions from dashboards within 2 minutes; backup restore test succeeds. | NFR-78006 and OPS-75006 met. |
| PAC-11 | Accessibility and usability: WCAG 2.2 AA audit and usability test. | NFR-73001 met; NFR-72001 success ≥ 80%. |
| PAC-12 | Performance: load model sustained for 8 hours. | Chapters 64–65 targets met; no memory growth beyond NFR-64011. |

## 100.4 Acceptance Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-100001 | All platform acceptance scenarios PAC-01 to PAC-12 shall pass in the staging environment. | M | D |
| OPS-100002 | All module acceptance criteria (AC-*) tracing to M requirements shall pass. | M | T |
| OPS-100003 | Verification (Chapter 97), validation (Chapter 98), and testing exit criteria (Chapter 99) shall be satisfied. | M | I |
| OPS-100004 | The traceability register shall show 100% of M requirements verified (OPS-96003). | M | I |
| OPS-100005 | Formal acceptance shall be recorded with the signatures (or approval records) of the Product Owner, Engineering Lead, Risk Manager, Security Administrator, and Operations Lead. | M | I |

## 100.5 Conclusion of the Specification

With Chapter 100, Volume 2 – Master Software Requirements Specification is complete. It defines the purpose, scope, context, constraints, functional requirements, non-functional requirements, interfaces, system behaviour, and acceptance criteria of JD Quant AI. Subsequent volumes (3–10) shall elaborate the architecture, file responsibilities, classes and functions, database, internal APIs and events, UI/UX, AI/ML architecture, and operations in conformance with this specification.

---

*End of Chapter 100 – Acceptance Criteria*

*End of Volume 2 – Master Software Requirements Specification*
