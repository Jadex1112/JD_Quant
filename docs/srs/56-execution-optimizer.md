# Chapter 56 – Execution Optimizer

## 56.1 Purpose

The Execution Optimizer, part of the AOE, improves execution quality by recommending execution algorithms and parameters, predicting transaction costs, and learning from historical execution quality data (Chapter 31). It advises the EMS; it does not route orders itself.

## 56.2 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-56001 | The system shall estimate pre-trade transaction cost for a proposed order (spread cost, market impact, fees) using a configurable impact model calibrated on historical execution data and market liquidity analytics (FR-31042). | M | T |
| FR-56002 | The system shall recommend an execution algorithm (Chapter 22.6) and parameters (duration, participation, limit offset) for a proposed order based on size relative to average volume, volatility, spread, urgency, and historical performance of algorithms on the instrument. | S | T |
| FR-56003 | The system shall display pre-trade cost estimates and recommendations in the order ticket and in rebalance proposals. | S | D |
| FR-56004 | The system shall calibrate impact model coefficients periodically (default weekly) per instrument class from recorded ExecutionQualityRecords, retaining calibration history. | S | T |
| FR-56005 | The system shall evaluate recommendation quality by comparing realized costs against estimates and against the baseline algorithm (DIRECT), reporting estimation error and savings. | S | T |
| FR-56006 | The system shall optionally, when enabled per deployment, apply recommended parameters automatically to algorithmic orders within bounds configured by the trader. | C | T |
| FR-56007 | The system shall provide optimal limit-price offset recommendations for passive execution balancing fill probability against price improvement, using fill-probability models trained on historical order outcomes. | C | T |
| FR-56008 | The system shall record every recommendation and whether it was applied, for audit and evaluation. | M | T |

## 56.3 Business Rules

| ID | Rule |
|---|---|
| BR-56-01 | Default impact model: cost_bps = half_spread_bps + η × σ_daily_bps × √(Q / ADV), with η calibrated per instrument class (default 1.0). |
| BR-56-02 | Recommendations never override risk limits, automation policy, or trader-specified hard constraints. |

## 56.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-56001 | Given Q/ADV = 1%, σ = 200 bps, half spread 2 bps, η = 1, then estimated cost = 22 bps. | FR-56001, BR-56-01 |

---

*End of Chapter 56 – Execution Optimizer*
