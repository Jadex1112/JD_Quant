# Chapter 69 – Maintainability

## 69.1 Purpose

This chapter specifies requirements ensuring the platform can be understood, modified, tested, and evolved efficiently and safely (Chapter 10.20).

## 69.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-69001 | Automated test coverage shall be ≥ 90% line coverage for OMS, RMS, Position Engine, and metric library, and ≥ 75% for all other core modules. | M | A |
| NFR-69002 | Every engine shall have unit, integration, and contract tests executed in continuous integration on every change. | M | I |
| NFR-69003 | Architectural fitness functions shall enforce layering and dependency constraints (CON-001, CON-007) in continuous integration. | M | T |
| NFR-69004 | Static analysis (linting, type checking, security scanning) shall run on every change with zero high-severity findings permitted to merge. | M | I |
| NFR-69005 | Cyclomatic complexity of any function shall not exceed 15 without documented justification. | S | A |
| NFR-69006 | Every public interface, event schema, and configuration key shall be documented and generated from source-of-truth definitions (OpenAPI, schema registry). | M | I |
| NFR-69007 | A developer familiar with the stack shall be able to set up a complete local development environment in ≤ 30 minutes using documented automation. | M | D |
| NFR-69008 | The full CI pipeline (build, unit, integration) shall complete in ≤ 20 minutes. | S | T |
| NFR-69009 | Dependencies shall be reviewed monthly; known critical vulnerabilities shall be patched within 7 days and high within 30 days. | M | I |
| NFR-69010 | Database schema changes shall be applied through versioned, reversible migrations. | M | I |
| NFR-69011 | Each module shall have a designated owner and an architecture decision record log. | M | I |
| NFR-69012 | Deprecated features shall be flagged in code and documentation for at least one minor release before removal. | M | I |

---

*End of Chapter 69 – Maintainability*
