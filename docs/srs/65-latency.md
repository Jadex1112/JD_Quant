# Chapter 65 – Latency

## 65.1 Purpose

This chapter specifies internal latency budgets for time-sensitive paths. Latencies exclude external network time to venues unless stated. JD Quant AI is not designed for colocated ultra-low-latency trading (Chapter 2.9); targets are appropriate for systematic trading at millisecond scale.

## 65.2 Critical Path Latency Budget

The live order path from market data receipt to order handoff to the venue transport:

| Hop | Budget p50 | Budget p99 |
|---|---|---|
| Adapter receipt → normalized record published (MDE) | 100 μs | 1 ms |
| MDE publish → strategy callback start | 200 μs | 2 ms |
| Strategy callback (user code) | Strategy-dependent (limit FR-24021) | — |
| Order intent → OMS validated | 50 μs | 200 μs |
| OMS → RMS decision | 250 μs | 1 ms |
| RMS decision → EMS handoff | 50 μs | 200 μs |
| EMS → adapter transport write | 50 μs | 100 μs |
| **Total platform overhead (excluding strategy code)** | **≤ 1 ms** | **≤ 5 ms** |

## 65.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-65001 | Total platform overhead on the critical path shall not exceed 1 ms p50 and 5 ms p99 on REF-P at the sustained load model. | M | T |
| NFR-65002 | Each hop of 65.2 shall meet its budget. | S | T |
| NFR-65003 | Internal market data propagation p99 shall remain below 25 ms during 10× bursts (QAS-06). | M | T |
| NFR-65004 | Kill switch BLOCK_NEW shall take effect within 50 ms (FR-19041); pause within 100 ms (FR-19016). | M | T |
| NFR-65005 | Execution report (fill) received from adapter → `order.fill` published → position updated shall complete within 2 ms p99. | M | T |
| NFR-65006 | Order state updates shall reach the user interface within 250 ms p95 of state change (FR-21080). | M | T |
| NFR-65007 | Market data updates shall reach the user interface within 500 ms p95 of receipt. | M | T |
| NFR-65008 | Authorization checks shall add ≤ 1 ms p99 (FR-39010). | M | T |
| NFR-65009 | Online feature retrieval ≤ 5 ms p99 (AI-63006); online tabular model inference ≤ 10 ms p99 on CPU for reference models. | M | T |
| NFR-65010 | Latency measurements shall use monotonic high-resolution clocks and be exported as histograms per hop (FR-22061). | M | I |
| NFR-65011 | Garbage collection or runtime pauses on trading engine processes shall not exceed 10 ms p99.9. | S | T |

---

*End of Chapter 65 – Latency*
