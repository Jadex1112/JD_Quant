# Chapter 87 – Storage

## 87.1 Purpose

This chapter specifies interface requirements for file and object storage used for datasets, artifacts, reports, exports, and backups.

## 87.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| DB-87001 | The platform shall access object storage through an abstraction supporting S3-compatible APIs and local file system storage (T1), without functional differences (CON-009, CON-027). | M | T |
| DB-87002 | Stored objects shall be content-addressed or versioned, with SHA-256 checksums verified on read for artifacts and datasets. | M | T |
| DB-87003 | Datasets shall be stored in columnar format (Parquet) partitioned by date and instrument for efficient range scans. | M | I |
| DB-87004 | Object keys shall follow a documented layout: `{workspace}/{domain}/{entity}/{id}/{version}/...`. | M | I |
| DB-87005 | Object storage access shall use per-component credentials with least privilege; user downloads shall use short-lived signed URLs (default 15 min for interactive, 24 h for exports). | M | T |
| DB-87006 | Storage lifecycle policies shall implement retention and tiering (hot, cool, archive) per data class. | S | I |
| DB-87007 | Storage quotas per workspace shall be configurable with alerts at 80% and enforcement at 100% for user-generated data. | S | T |
| DB-87008 | Deletion of objects referenced by retained records (CON-125) shall be prevented by reference checks. | M | T |

---

*End of Chapter 87 – Storage*
