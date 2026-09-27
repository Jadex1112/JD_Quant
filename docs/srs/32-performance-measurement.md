# Chapter 32 – Performance Measurement

## 32.1 Purpose

This chapter specifies how JD Quant AI measures investment and trading performance. It defines the canonical metric set and calculation formulas used consistently by backtesting, paper trading, live monitoring, analytics, reporting, and optimization. A single, shared definition of each metric prevents divergence between modules.

## 32.2 Return Calculation

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-32001 | The system shall compute periodic simple returns r_t = (NAV_t − NAV_{t−1} − F_t) / (NAV_{t−1}), where F_t is the net external capital flow during the period assumed to occur at period end (default) or start (configurable). | M | T |
| FR-32002 | The system shall compute time-weighted return (TWR) by geometrically linking sub-period returns split at every capital flow: TWR = Π(1 + r_i) − 1. | M | T |
| FR-32003 | The system shall compute money-weighted return (MWR) as the internal rate of return of capital flows and ending NAV. | M | T |
| FR-32004 | The system shall compute log returns ln(1 + r_t) for statistical metrics where specified. | M | T |
| FR-32005 | The system shall annualize using periods-per-year: 252 for session-based daily data, 365 for continuous (24×7) markets, configurable per portfolio. | M | T |

## 32.3 Canonical Metric Set

| ID | Metric | Definition |
|---|---|---|
| FR-32010 | Total Return | NAV_end / NAV_start − 1 (flow-adjusted via TWR) |
| FR-32011 | CAGR | (1 + Total Return)^(365.25 / days) − 1 |
| FR-32012 | Annualized Volatility | stdev(r_t) × √P, sample standard deviation, P = periods per year |
| FR-32013 | Sharpe Ratio | (mean(r_t − rf_t) / stdev(r_t − rf_t)) × √P; rf configurable (default 0) |
| FR-32014 | Sortino Ratio | (mean(r_t − MAR) / downside deviation) × √P; downside deviation = √(mean(min(0, r_t − MAR)²)) |
| FR-32015 | Maximum Drawdown | max over t of (peak_t − NAV_t) / peak_t, peak_t = max NAV up to t |
| FR-32016 | Max Drawdown Duration | Longest time from a peak to recovery above that peak (or to end if unrecovered) |
| FR-32017 | Calmar Ratio | CAGR / Maximum Drawdown |
| FR-32018 | Omega Ratio | Σ max(0, r_t − τ) / Σ max(0, τ − r_t), τ default 0 |
| FR-32019 | Profit Factor | Gross profit of round trips / \|gross loss of round trips\| |
| FR-32020 | Win Rate | Winning round trips / total round trips |
| FR-32021 | Expectancy | Mean net P&L per round trip |
| FR-32022 | Payoff Ratio | Average win / \|average loss\| |
| FR-32023 | Alpha, Beta | OLS regression of r_t − rf on benchmark excess returns; alpha annualized |
| FR-32024 | Tracking Error | stdev(r_t − b_t) × √P |
| FR-32025 | Information Ratio | annualized mean(r_t − b_t) / Tracking Error |
| FR-32026 | Value at Risk / CVaR | As in FR-27060, FR-27061 over the return series |
| FR-32027 | Skewness, Kurtosis | Sample skewness and excess kurtosis of r_t |
| FR-32028 | Turnover | Σ traded notional / average NAV, annualized |
| FR-32029 | Exposure Time | Fraction of periods with non-zero position |
| FR-32030 | Recovery Factor | Net profit / Maximum drawdown amount |
| FR-32031 | Ulcer Index | √(mean(drawdown_t²)) in percent |
| FR-32032 | Deflated Sharpe Ratio | Per Bailey & López de Prado, using the number of trials (FR-25065) |
| FR-32033 | Fees and Funding Ratio | (Fees + funding) / gross P&L |

All metrics above are Priority M except FR-32018, FR-32031, and FR-32032 (S). Verification: T.

## 32.4 Metric Computation Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-32050 | The system shall implement every metric in a single shared metric library used by all modules. | M | I |
| FR-32051 | The system shall validate the metric library against reference values computed by an independent implementation for a published reference dataset, to at least 1e-9 relative tolerance. | M | T |
| FR-32052 | The system shall report metrics as "insufficient data" when fewer than a minimum number of periods are available (default 30 for ratio metrics). | M | T |
| FR-32053 | The system shall compute metrics for arbitrary periods: day, week, month, quarter, year, year-to-date, inception-to-date, and custom ranges. | M | T |
| FR-32054 | The system shall compute rolling metrics (for example 30-day rolling Sharpe) with configurable windows. | M | T |
| FR-32055 | The system shall present metrics with their definitions accessible from the user interface (tooltip or help link). | M | D |
| FR-32056 | The system shall compute net-of-fee and gross-of-fee variants of return metrics. | M | T |
| FR-32057 | The system shall produce live performance tracking for each deployment updated at least every minute. | M | T |
| FR-32058 | The system shall compare live deployment performance against the backtested expectation (Monte Carlo band from FR-25082) and flag degradation when live performance falls below the 5th percentile band. | S | T |

## 32.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-32001 | Given the reference dataset, then all metric values match reference values within 1e-9 relative tolerance. | FR-32051 |
| AC-32002 | Given a NAV series with a mid-period deposit, then TWR is unaffected by the deposit amount. | FR-32002 |
| AC-32003 | Given 10 daily returns, then Sharpe is reported as insufficient data. | FR-32052 |

---

*End of Chapter 32 – Performance Measurement*
