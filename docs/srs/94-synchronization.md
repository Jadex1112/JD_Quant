# Chapter 94 – Synchronization

## 94.1 Purpose

This chapter specifies synchronization between the platform and external systems, between internal replicas, and of time.

## 94.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-94001 | The platform shall synchronize orders, fills, balances, and positions with venues per the intervals of Chapters 21, 23, and 28, with the venue as source of truth (CON-042). | M | T |
| FR-94002 | The platform shall synchronize instrument reference data per FR-20002 and trading calendars at least daily. | M | T |
| FR-94003 | Host clocks shall be synchronized to reference time sources (CON-165); the platform shall measure offset continuously and expose it as a metric. | M | T |
| FR-94004 | For each venue, the platform shall maintain the measured offset between venue time and local time, and use exchange_ts for ordering venue events (BR-20-01). | M | T |
| FR-94005 | Caches (instrument metadata, permissions, configuration, risk profiles) shall be invalidated on change events and refreshed within the propagation targets of their chapters (e.g. FR-39011 5 s, FR-42004 10 s). | M | T |
| FR-94006 | UI clients shall resynchronize state after reconnect using snapshot-plus-delta with sequence numbers (API-81005), detecting and repairing gaps. | M | T |
| FR-94007 | Online and offline feature stores shall be verified for parity (AI-63010). | M | T |
| FR-94008 | The DR site shall synchronize transactional data within RPO targets (OPS-77001) and report replication lag. | M | T |

---

*End of Chapter 94 – Synchronization*
