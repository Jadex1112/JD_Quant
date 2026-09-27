# Chapter 67 – Scalability

## 67.1 Purpose

This chapter specifies how the platform shall scale along the dimensions listed in Chapter 2.10 and 8.9.

## 67.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-67001 | Stateless services (API gateway, analytics query, reporting, inference) shall scale horizontally by adding instances without configuration changes beyond instance count. | M | T |
| NFR-67002 | Market data ingestion shall scale horizontally by partitioning instruments across ingestion instances, supporting ≥ 20,000 instruments with 4× REF-S instances. | M | T |
| NFR-67003 | Strategy hosting shall scale horizontally by distributing deployments across hosting instances, supporting ≥ 2,000 RUNNING deployments with 8 hosting instances. | S | T |
| NFR-67004 | Order processing shall scale by partitioning by account; per-account ordering guarantees (FR-21023) shall be preserved. | S | T |
| NFR-67005 | Task Engine workers shall scale to ≥ 200 concurrent workers per installation. | S | T |
| NFR-67006 | Time-series storage shall scale to ≥ 500 TB with query performance of NFR-64010 maintained for recent (hot-tier) data. | S | T |
| NFR-67007 | Adding a workspace shall not require infrastructure changes up to 100 workspaces per installation. | M | T |
| NFR-67008 | Scaling operations (adding or removing instances) shall not interrupt live trading. | M | T |
| NFR-67009 | The architecture shall support multi-region deployment with region-local market data and trading paths (future scope, Chapter 2.11). | C | I |
| NFR-67010 | Capacity metrics and thresholds shall trigger scaling recommendations or automatic scaling where supported. | S | T |

---

*End of Chapter 67 – Scalability*
