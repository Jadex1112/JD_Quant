# Chapter 59 – Inference Pipeline

## 59.1 Purpose

The Inference Pipeline Engine (IPE) serves model predictions to strategies, signals, risk, and analytics in real time and batch modes. It loads PRODUCTION and SHADOW model versions, retrieves online features, executes inference within latency budgets, records inference for audit (CON-145), and exposes predictions as signals.

## 59.2 Serving Modes

| Mode | Description | Latency Target |
|---|---|---|
| ONLINE_SYNC | Request/response from strategies via context.models | p99 ≤ 10 ms for tabular models on CPU (model-dependent; declared per version) |
| STREAMING | Continuous inference on feature updates, publishing signals | ≤ 50 ms from feature update to signal |
| BATCH | Scheduled inference over universes/time ranges (Task Engine) | Throughput-oriented |

## 59.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-59001 | The system shall load model versions in PRODUCTION and SHADOW stages, verify artifact checksums (AI-57002), and warm them up before accepting requests. | M | T |
| AI-59002 | The system shall serve inference in the modes of 59.2. | M (ONLINE_SYNC, BATCH), S (STREAMING) | T |
| AI-59003 | The system shall retrieve input features from the online Feature Store by the model's feature set, validating inputs against the model's input schema; invalid or missing inputs shall return an error, not a prediction. | M | T |
| AI-59004 | The system shall measure and publish p50/p99 inference latency per model version, and enforce CON-101 by rejecting synchronous use by strategies whose declared latency budget is below the measured p99. | M | T |
| AI-59005 | The system shall record every inference used in trading decisions with model id, version, feature snapshot reference, output, and timestamp (CON-145); other inferences sampled at a configurable rate. | M | T |
| AI-59006 | The system shall execute SHADOW versions in parallel with PRODUCTION on the same inputs without delivering shadow outputs to consumers (AI-57005). | M | T |
| AI-59007 | The system shall switch between versions (promotion or rollback) atomically without failed requests. | M | T |
| AI-59008 | The system shall publish predictions as signals when configured (Chapter 30). | M | T |
| AI-59009 | The system shall apply timeouts to inference (default 2× declared p99) and return a timeout error so the strategy can apply its fallback. | M | T |
| AI-59010 | The system shall scale inference replicas horizontally based on request load. | S | T |
| AI-59011 | The system shall run BATCH inference producing versioned prediction datasets usable in backtests. | M | T |
| AI-59012 | The system shall make inference in backtests use the model version and point-in-time features as of the simulated time (no future model versions unless explicitly configured for research). | M | T |

## 59.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-59001 | Given a missing required feature, then inference returns an INPUT_INVALID error and no prediction. | AI-59003 |
| AC-59002 | Given promotion during load of 1,000 requests/s, then zero requests fail. | AI-59007 |
| AC-59003 | Given a live order influenced by a model, then its inference record exists and is linked from the decision chain. | AI-59005, FR-38007 |

---

*End of Chapter 59 – Inference Pipeline*
