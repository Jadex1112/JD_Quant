# Chapter 55 – Portfolio Optimizer

## 55.1 Purpose

The Portfolio Optimizer, part of the AI Optimization Engine (AOE), computes target portfolio weights and capital allocations across instruments or strategies subject to objectives and constraints. It produces proposals consumed by the Portfolio Engine's rebalancing function (FR-23043) and never submits orders itself.

## 55.2 Optimization Methods

| Method | Description | Pri |
|---|---|---|
| EQUAL_WEIGHT | 1/N | M |
| INVERSE_VOLATILITY | Weights ∝ 1/σ | M |
| MEAN_VARIANCE | Markowitz: maximize μᵀw − λ wᵀΣw | M |
| MIN_VARIANCE | Minimize wᵀΣw | M |
| MAX_SHARPE | Maximize (μᵀw − rf) / √(wᵀΣw) | M |
| RISK_PARITY | Equal risk contribution | M |
| HIERARCHICAL_RISK_PARITY | Clustering-based allocation | S |
| BLACK_LITTERMAN | Market equilibrium blended with views (views may come from signals) | S |
| CVAR_MINIMIZATION | Minimize Conditional VaR over scenarios | S |
| KELLY_FRACTIONAL | Fractional Kelly sizing for strategy allocation | S |
| ML_ALLOCATION | Model-based allocation from the Model Manager | C |

## 55.3 Inputs and Constraints

| Input | Options |
|---|---|
| Universe | Instruments or strategies (deployments) |
| Expected returns | Historical mean, exponentially weighted, signal-derived, user-specified |
| Covariance | Sample, exponentially weighted, Ledoit-Wolf shrinkage, factor model |
| Lookback | Configurable |
| Constraints | Long-only, weight bounds per asset, group bounds (sector, asset class), leverage limit, turnover limit, max number of holdings, minimum trade size, target volatility |
| Costs | Transaction cost model (bps or impact model) included in objective |

## 55.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-55001 | The system shall run optimizations with the methods of 55.2 and constraints of 55.3. | M | T |
| FR-55002 | The system shall report infeasibility explicitly with the conflicting constraints identified where the solver supports it. | M | T |
| FR-55003 | The system shall output target weights, expected return, expected volatility, expected Sharpe, risk contributions per holding, turnover versus current weights, and estimated transaction costs. | M | T |
| FR-55004 | The system shall compute the efficient frontier for mean-variance problems and display it with the current and proposed portfolios. | S | D |
| FR-55005 | The system shall backtest an allocation method over history with periodic rebalancing (walk-forward estimation without look-ahead) and report metrics per Chapter 32. | M | T |
| FR-55006 | The system shall convert target weights into a rebalance proposal of orders for a live or paper portfolio, respecting instrument trading rules and minimum trade size. | M | T |
| FR-55007 | The system shall require approval of rebalance proposals unless covered by an automation policy (FR-23043), and submit approved orders through OMS/RMS like any other order. | M | T |
| FR-55008 | The system shall optimize capital allocation across strategies using their live or backtested return streams. | S | T |
| FR-55009 | The system shall complete an optimization of 500 assets with standard constraints within 10 s. | M | T |
| FR-55010 | The system shall record every optimization run with inputs, method, constraints, solver status, and outputs for audit and reproduction. | M | T |

## 55.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-55001 | Given a known 3-asset covariance matrix, then the MIN_VARIANCE solution matches the analytical solution within 1e-6. | FR-55001 |
| AC-55002 | Given long-only with max weight 20% and 4 assets, then the optimizer reports infeasibility. | FR-55002 |

---

*End of Chapter 55 – Portfolio Optimizer*
