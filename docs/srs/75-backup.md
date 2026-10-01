# Chapter 75 – Backup

## 75.1 Purpose

This chapter specifies backup requirements for all persistent data. Operations requirements use the format `OPS-75NNN`.

## 75.2 Data Classes and Backup Policy

| Data Class | Examples | Method | Frequency | Retention |
|---|---|---|---|---|
| Transactional (critical) | Orders, fills, positions, balances, deployments, risk config, audit | Continuous log shipping + daily full | Continuous / daily | 35 days of point-in-time recovery; monthly full kept 7 years |
| Configuration | Settings, configuration store, workflows, strategies, models metadata | Daily full + on change | Daily | 1 year |
| Artifacts | Strategy artifacts, model artifacts, reports | Object storage versioning + replication | On write | Per retention policy |
| Time-series market data | Candles, trades, books | Incremental | Daily | Per data retention; raw data may be re-downloadable |
| Logs and metrics | Operational telemetry | Replication | Continuous | Per Chapter 36 / 60 |
| Secrets | Secret store | Secret store native backup, encrypted | Daily | 90 days |

## 75.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-75001 | The system shall back up each data class per 75.2. | M | T |
| OPS-75002 | Backups shall be encrypted (SEC-70001) and stored in a location isolated from the primary (different failure domain; off-site for T2, Chapter 14.16). | M | I |
| OPS-75003 | Backups shall be immutable for their retention period (write-once) to protect against ransomware and accidental deletion. | M | I |
| OPS-75004 | Backup success and failure shall be monitored; a failed or missed backup shall raise a High alert within 1 hour. | M | T |
| OPS-75005 | Backup integrity shall be verified automatically by checksum and by restoring a sample to an isolated environment at least weekly. | M | T |
| OPS-75006 | Full restoration testing of the critical transactional data class shall be performed at least quarterly with results recorded (FR-61006 backup freshness). | M | T |
| OPS-75007 | Backup and restore operations shall be audited (Chapter 38). | M | T |
| OPS-75008 | T1 deployments shall provide a documented, one-command backup and restore procedure suitable for individual users. | M | D |

---

*End of Chapter 75 – Backup*
