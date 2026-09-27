# Chapter 50 – Scenario Simulator

## 50.1 Purpose

The Scenario Simulator evaluates portfolios and strategies under hypothetical or historical stress conditions. It answers "what happens to my portfolio if…" through instantaneous shock scenarios, historical stress replays, and simulated market paths, supporting risk management (Chapter 27), portfolio decisions, and strategy robustness analysis.

## 50.2 Scenario Types

| Type | Description |
|---|---|
| SHOCK | Instantaneous price, volatility, correlation, FX, or funding-rate shocks applied to current holdings (e.g. "BTC −30%, ETH −40%, USD/INR +5%") |
| HISTORICAL | Replay of returns from a historical window onto current holdings (e.g. March 2020 crash, 2022 crypto deleveraging) |
| FACTOR | Shocks to factors propagated to holdings via betas/factor loadings |
| MONTE_CARLO | Simulated paths from a statistical model (e.g. multivariate returns with fitted covariance, optional fat tails via Student-t) |
| STRATEGY_STRESS | Run a strategy through synthetic or historical stressed market data in the backtester (gaps, halts, liquidity drought, spread widening, exchange outage) |
| OPERATIONAL | Simulated failures: venue disconnect, stale data, delayed fills — evaluated in a sandbox environment |

## 50.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-50001 | The system shall allow users to define SHOCK scenarios with per-instrument, per-asset-class, or per-factor shocks, and save them to a scenario library. | M | T |
| FR-50002 | The system shall provide a built-in library of historical stress scenarios for supported asset classes, each defined by a date window. | M | T |
| FR-50003 | The system shall compute, for each scenario and portfolio: P&L, NAV change, post-scenario exposure, leverage, margin ratio, liquidation events, and limit breaches that would occur. | M | T |
| FR-50004 | The system shall revalue derivatives under scenarios using appropriate models (linear for futures/perpetuals; option pricing model with shocked volatility for options). | M (linear), S (options) | T |
| FR-50005 | The system shall run MONTE_CARLO scenarios with configurable paths (default 10,000), horizon, and model, reporting P&L distribution percentiles and probability of breaching limits. | S | T |
| FR-50006 | The system shall run STRATEGY_STRESS scenarios by transforming dataset versions (e.g. inject gap of −X%, widen spread ×N, remove liquidity) and executing backtests on the transformed data. | S | T |
| FR-50007 | The system shall run scheduled stress tests (default daily) on all live portfolios with the workspace's designated scenario set and include results in the risk report (FR-27083). | M | T |
| FR-50008 | The system shall allow risk limits on scenario outcomes (e.g. "worst historical scenario loss ≤ 20% NAV") evaluated after each scheduled run. | S | T |
| FR-50009 | The system shall execute OPERATIONAL scenarios only in PAPER or sandbox environments and never against live accounts. | S | T |
| FR-50010 | The system shall present scenario results in comparison tables and charts across scenarios and portfolios. | M | D |

## 50.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-50001 | Given a portfolio with 1 BTC long at 50,000 and a −30% BTC shock, then scenario P&L = −15,000 in quote currency. | FR-50003 |
| AC-50002 | Given a leveraged position whose liquidation price is crossed in the scenario, then the result reports the liquidation event. | FR-50003 |
| AC-50003 | Given an attempt to run an OPERATIONAL scenario on a live account, then it is rejected. | FR-50009 |

---

*End of Chapter 50 – Scenario Simulator*
