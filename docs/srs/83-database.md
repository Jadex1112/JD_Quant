# Chapter 83 – Database

## 83.1 Purpose

This chapter specifies persistence requirements (identifier format `DB-83NNN`). Physical schema design is specified in Volume 6.

## 83.2 Data Stores

| Store | Data | Characteristics |
|---|---|---|
| Relational (transactional) | Users, roles, workspaces, accounts, orders, fills, positions, deployments, risk config, workflows, configuration, audit | ACID, strong consistency, point-in-time recovery |
| Time-series | Market data, metrics, NAV history, feature values | High ingest, compression, time-range queries |
| Object storage | Artifacts (strategies, models), datasets (Parquet), reports, backtest results, backups | Immutable versions, checksums |
| Online key-value / cache | Latest quotes, online features, sessions, rate-limit counters | Low latency, TTL |
| Event store | Trading-state events | Append-only, replayable |
| Search index | Logs, audit search, instrument search | Full-text and faceted search |

## 83.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| DB-83001 | Transactional data shall be stored in a relational database with ACID transactions and serializable or snapshot isolation for financial state changes. | M | I |
| DB-83002 | Monetary and quantity columns shall use exact decimal types with sufficient precision (CON-022; C.7). | M | I |
| DB-83003 | All timestamp columns shall be stored in UTC with microsecond precision (CON-023). | M | I |
| DB-83004 | Every mutable entity shall carry the common attributes of C.6 including a version column for optimistic concurrency. | M | I |
| DB-83005 | Workspace isolation shall be enforced at the data-access layer for every query, with automated tests (SEC-70026); row-level security or equivalent shall be used where supported. | M | T |
| DB-83006 | Audit tables shall be append-only, enforced by database permissions preventing UPDATE and DELETE for application roles (CON-085). | M | T |
| DB-83007 | Schema migrations shall be versioned, forward-only in production with tested rollback plans, and applied automatically at deployment (NFR-69010). | M | I |
| DB-83008 | Time-series data shall be partitioned by time and instrument with compression and tiered retention (FR-20107). | M | I |
| DB-83009 | Database connections shall use TLS and least-privilege credentials per component, retrieved from the secret store. | M | I |
| DB-83010 | Indexes shall support all query patterns required by Part C response-time requirements, verified by performance tests. | M | T |
| DB-83011 | T1 deployments shall support an embedded or single-instance database configuration; T2 deployments shall use replicated databases with automatic failover (NFR-66003). | M | T |
| DB-83012 | Personal data columns shall be identifiable in schema metadata to support data subject requests (NFR-71004). | S | I |

---

*End of Chapter 83 – Database*
