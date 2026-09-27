# Chapter 44 – Data Export

## 44.1 Purpose

The Data Export module allows authorized users and systems to extract platform data in open formats (CON-180) for external analysis, archival, accounting, and integration, under access control, licensing restrictions (CON-066), and audit.

## 44.2 Exportable Data

| Data | Formats |
|---|---|
| Market data (within license entitlements) | CSV, Parquet, JSON Lines |
| Orders, fills, positions, balances | CSV, XLSX, JSON, Parquet |
| Portfolio snapshots and NAV history | CSV, XLSX, JSON |
| Backtest results | CSV, JSON, Parquet |
| Signals and model predictions | CSV, Parquet |
| Reports | PDF, HTML, XLSX (Chapter 37) |
| Audit ranges | JSON Lines with integrity manifest (FR-38009) |
| Strategy source and versions | Archive with manifest |
| Configuration and settings | JSON/YAML without secrets |

## 44.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-44001 | The system shall export the data types in 44.2 with filters for time range, accounts, portfolios, instruments, and deployments. | M | T |
| FR-44002 | The system shall apply the requesting principal's permissions and the data provider's redistribution entitlements to exports; non-exportable data shall be excluded with an explanation. | M | T |
| FR-44003 | The system shall run exports exceeding 100,000 rows asynchronously as Task Engine jobs and deliver a time-limited download link (default 24 h) or write to a configured destination. | M | T |
| FR-44004 | The system shall support scheduled exports to object storage, SFTP, or webhook destinations. | S | T |
| FR-44005 | The system shall include a manifest with each export: generation time, parameters, row counts, schema, and file checksums. | M | T |
| FR-44006 | The system shall never include secrets or credential values in any export (CON-080). | M | T |
| FR-44007 | The system shall audit every export with requester, parameters, and row counts (Chapter 38). | M | T |
| FR-44008 | The system shall allow administrators to restrict export capability by role and to require approval for exports exceeding a configurable size. | S | T |
| FR-44009 | The system shall export timestamps in ISO 8601 UTC by default with optional timezone conversion, and decimals without loss of precision. | M | T |
| FR-44010 | The system shall support a full workspace data export for portability, comprising all exportable data of the workspace. | S | T |

## 44.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-44001 | Given market data licensed for display only, when a user requests an export, then the market data is excluded with an entitlement message. | FR-44002 |
| AC-44002 | Given an export of fills, then decimal values equal stored values exactly. | FR-44009 |

---

*End of Chapter 44 – Data Export*
