# Chapter 64 – Performance

## 64.1 Purpose

This chapter begins Part D – Non-Functional Requirements. It specifies throughput, capacity, and resource-efficiency requirements. Latency requirements are specified in Chapter 65. All requirements use the identifier format `NFR-CCNNN`.

## 64.2 Reference Environments

Performance requirements are stated against the following reference environments unless otherwise noted:

| Profile | Specification |
|---|---|
| REF-S (Single node) | 16 vCPU (≥ 3.0 GHz), 64 GB RAM, NVMe SSD, 1 Gbps network, Linux |
| REF-P (Production cluster) | 3+ application nodes of REF-S, dedicated database nodes (16 vCPU, 128 GB RAM, NVMe), 10 Gbps internal network |
| REF-G (GPU worker) | REF-S plus one data-center GPU with ≥ 24 GB memory |

## 64.3 Load Model

| Dimension | Release 1.0 Target (REF-P) |
|---|---|
| Named users per installation | 200 |
| Concurrent interactive users | 50 |
| Concurrently RUNNING deployments | 500 |
| Instruments with live market data subscriptions | 5,000 |
| Market data messages ingested | 200,000 msg/s sustained, 1,000,000 msg/s burst (60 s) |
| Orders submitted | 500 orders/s sustained, 2,000 orders/s burst (10 s) |
| Fills processed | 2,000 fills/s |
| Concurrent backtest jobs | 32 (scales with workers) |
| Historical data stored | 50 TB compressed (ASM-022) |

## 64.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-64001 | The system shall sustain the load model of 64.3 on REF-P while meeting all latency requirements of Chapter 65. | M | T |
| NFR-64002 | The system shall ingest and distribute 200,000 market data msg/s sustained without loss for non-conflated subscribers (QAS-06). | M | T |
| NFR-64003 | The system shall process 500 orders/s end-to-end (intake, risk, routing to adapter) sustained on REF-P, excluding venue rate limits. | M | T |
| NFR-64004 | The Backtesting Engine shall process ≥ 1,000,000 events/s per core for simple strategies (FR-25010). | M | T |
| NFR-64005 | A 1-year, 1-minute bar backtest of one instrument with a simple strategy shall complete in ≤ 10 s on REF-S. | M | T |
| NFR-64006 | Optimization throughput shall scale linearly (≥ 80% efficiency) with the number of workers up to 64 workers. | S | T |
| NFR-64007 | Interactive API requests (non-analytic reads) shall complete within 200 ms p95 and 500 ms p99 under the load model. | M | T |
| NFR-64008 | User interface initial load shall complete within 3 s on a 20 Mbps connection; subsequent navigation within 1 s p95. | M | T |
| NFR-64009 | Real-time UI views shall render updates at up to 10 updates/s per widget without dropping below 30 frames per second on reference client hardware (4-core CPU, 8 GB RAM, modern browser). | S | T |
| NFR-64010 | Historical queries: 1 year of 1-minute candles for one instrument within 2 s (FR-20103); 1 day of trades for a liquid instrument (≈ 5 million trades) streamed within 10 s. | M | T |
| NFR-64011 | Steady-state memory usage of each trading engine process shall not grow by more than 5% over 7 days of continuous operation under constant load (no leaks). | M | T |
| NFR-64012 | Trading engine processes shall use no more than 70% of allocated CPU at the sustained load model, leaving headroom for bursts. | M | T |
| NFR-64013 | Report generation: standard daily report for 50 accounts within 5 minutes (FR-37013). | M | T |
| NFR-64014 | Data import: ≥ 10 million CSV candle rows per minute (FR-43013). | S | T |

## 64.5 Performance Testing

Performance requirements shall be verified by automated load tests executed at least before every minor release, using recorded or synthetic market data at the load model rates, with results tracked over time to detect regressions greater than 10%.

---

*End of Chapter 64 – Performance*
