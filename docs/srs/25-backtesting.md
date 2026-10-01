# Chapter 25 – Backtesting

## 25.1 Purpose

The Backtesting Engine (BTE) simulates the execution of strategy versions against historical market data to estimate their behavior and performance. It provides event-driven, deterministic, point-in-time-correct simulation using the same strategy contract and execution simulator used by paper trading (CON-005, FR-22080), and supports parameter optimization, walk-forward analysis, and robustness testing through the Strategy Testing Engine (STE).

## 25.2 Scope and Actors

### In Scope

- Single backtests (one strategy version, one parameter set)
- Portfolio backtests (multiple strategies sharing capital)
- Parameter optimization (grid, random, Bayesian, genetic)
- Walk-forward analysis
- Monte Carlo and robustness analysis
- Result storage, comparison, and reproduction
- Distributed execution of backtest batches

### Actors

| Actor | Interaction |
|---|---|
| Quantitative Researcher | Configures, runs, and analyzes backtests |
| Task Engine | Schedules and executes backtest jobs |
| MDE | Provides dataset versions |
| Performance module | Computes metrics |

## 25.3 Domain Entities

### 25.3.1 BacktestConfiguration

| Attribute | Type | Constraints |
|---|---|---|
| strategy_version_id | Reference | Required |
| parameters | Structured document | Validated against schema |
| dataset_version_id | Reference | Required (CON-121); created automatically from range if not supplied |
| instrument_universe | List of InstrumentId | 1–5,000 |
| start / end | Timestamp | end > start; within dataset coverage |
| warmup_period | Duration | Default derived from strategy data requirements |
| resolution | Enum: TICK, QUOTE, BOOK, BAR | Must be supported by dataset |
| bar_interval | Interval | Required if BAR |
| initial_capital | Money | > 0 |
| base_currency | Currency | Required |
| fee_model | Reference → FeeSchedule or override | Required |
| slippage_model | Enum + params (FR-22084) | Default FIXED_BPS 1 |
| fill_model | Enum (FR-22081) | Default QUEUE for LIMIT, BOOK for MARKET |
| latency_model | Enum + params | Default fixed 50 ms |
| margin_model | Enum: NONE, SPOT, CROSS, ISOLATED + params | Default by asset class |
| funding_model | Enum: NONE, HISTORICAL | Default HISTORICAL for perpetuals |
| risk_profile_id | Reference → RMS profile | Optional; applies pre-trade risk rules in simulation |
| random_seed | Integer | Required; generated if not supplied |
| benchmark_id | Reference | Optional |

### 25.3.2 BacktestRun

| Attribute | Description |
|---|---|
| configuration snapshot | Complete, immutable copy of configuration |
| status | QUEUED, RUNNING, COMPLETED, FAILED, CANCELED |
| progress | Percentage of simulated time processed |
| engine_version | Version of BTE used |
| started_at / finished_at / duration | Timing |
| results | Reference to result set (25.3.3) |
| reproducibility_hash | Hash of configuration + dataset + artifact + engine version |

### 25.3.3 BacktestResult

| Component | Content |
|---|---|
| Orders | All simulated orders with full lifecycle |
| Fills | All simulated fills |
| Positions | Position history |
| Equity curve | NAV at each bar or configurable sampling interval |
| Metrics | Performance metrics per Chapter 32 |
| Trades | Round-trip trades (entry, exit, P&L, holding period, MAE, MFE) |
| Logs | Strategy log output |
| Signals | Signals generated |

## 25.4 Functional Requirements – Simulation Core

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25001 | The system shall simulate strategies by replaying dataset events in timestamp order through the strategy contract of Chapter 24 using a simulated clock (CON-010). | M | T |
| FR-25002 | The system shall merge events from multiple instruments and data types into a single time-ordered stream; ties shall be broken deterministically by (timestamp, data type priority, instrument_id, sequence). | M | T |
| FR-25003 | The system shall prevent look-ahead bias: a strategy at simulated time T shall only access data with timestamps ≤ T, and bar data only after the bar's close time (CON-122). | M | T |
| FR-25004 | The system shall route simulated orders through OMS logic and, where a risk profile is configured, RMS pre-trade checks identical to live. | M | T |
| FR-25005 | The system shall use the execution simulator (FR-22080 – FR-22086) with the configured fill, slippage, latency, and fee models. | M | T |
| FR-25006 | The system shall simulate margin, leverage, liquidation (when maintenance margin is breached), borrow costs, and funding payments per configured models. | M | T |
| FR-25007 | The system shall apply corporate actions and futures rolls per the dataset's definitions. | S | T |
| FR-25008 | The system shall simulate instrument trading sessions and halts from historical calendars and status data. | M | T |
| FR-25009 | The system shall produce bit-identical results for identical reproducibility hashes (QAS-09). | M | T |
| FR-25010 | The system shall process at least 1,000,000 events per second per core for bar- and trade-resolution simulations of a strategy with O(1) per-event logic. | M | T |
| FR-25011 | The system shall support a vectorized fast-path mode for bar-based signal research that computes approximate results without order simulation, clearly labeled as "approximate". | S | T |

## 25.5 Functional Requirements – Run Management

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25020 | The system shall execute backtests asynchronously through the Task Engine (CON-102) and report progress at least every 2 seconds. | M | T |
| FR-25021 | The system shall allow cancellation of a running backtest, retaining partial results marked CANCELED. | M | T |
| FR-25022 | The system shall validate configuration before queueing: dataset coverage, parameter schema, fee schedule availability, and resolution support; all errors shall be reported together. | M | T |
| FR-25023 | The system shall allow re-running any historical run from its stored configuration snapshot. | M | T |
| FR-25024 | The system shall allow users to tag, annotate, star, and archive runs. | M | T |
| FR-25025 | The system shall enforce per-user concurrency quotas (default 4 concurrent runs) (CON-103). | M | T |

## 25.6 Functional Requirements – Results and Analysis

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25040 | The system shall compute the full metric set of Chapter 32 for each completed run. | M | T |
| FR-25041 | The system shall present an interactive result view: equity curve, drawdown chart, monthly return heatmap, trade list, trade markers on price charts, exposure over time, and metric table. | M | D |
| FR-25042 | The system shall compute round-trip trades with FIFO matching and report maximum adverse excursion (MAE) and maximum favorable excursion (MFE). | M | T |
| FR-25043 | The system shall allow side-by-side comparison of up to 10 runs with metric deltas and overlaid equity curves. | M | D |
| FR-25044 | The system shall compare results against the configured benchmark (alpha, beta, tracking error, information ratio). | M | T |
| FR-25045 | The system shall show breakdowns of performance by instrument, month, weekday, hour, long/short, and signal type. | S | D |
| FR-25046 | The system shall export results in CSV, JSON, and Parquet formats (CON-180). | M | T |

## 25.7 Functional Requirements – Optimization

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25060 | The system shall run parameter optimizations over parameter ranges declared in the schema using GRID, RANDOM, BAYESIAN, and GENETIC search methods. | M (GRID, RANDOM), S (others) | T |
| FR-25061 | The system shall allow selection of the objective metric (for example Sharpe, Sortino, CAGR, Calmar, profit factor) and constraints (for example max drawdown ≤ X, trade count ≥ N). | M | T |
| FR-25062 | The system shall require the user to define an in-sample and out-of-sample split; out-of-sample results shall be computed only for the top-N configurations selected on in-sample data. | M | T |
| FR-25063 | The system shall execute optimization trials in parallel across available workers and scale to at least 10,000 trials per optimization. | M | T |
| FR-25064 | The system shall present parameter sensitivity visualizations (heatmaps for two-parameter slices, parallel coordinates for multi-parameter). | S | D |
| FR-25065 | The system shall report the number of trials tested alongside results and compute the deflated Sharpe ratio to account for multiple testing. | S | T |

## 25.8 Functional Requirements – Walk-Forward and Robustness

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25080 | The system shall perform walk-forward analysis with configurable in-sample window, out-of-sample window, step, and anchored or rolling mode, stitching out-of-sample segments into a combined equity curve. | M | T |
| FR-25081 | The system shall compute walk-forward efficiency = annualized out-of-sample return / annualized in-sample return. | M | T |
| FR-25082 | The system shall perform Monte Carlo analysis by resampling trade sequences (with and without replacement) for a configurable number of iterations (default 1,000), reporting distributions of return and drawdown with 5th/50th/95th percentiles. | M | T |
| FR-25083 | The system shall support robustness tests: parameter perturbation (±N%), fee and slippage stress (×2, ×3), random entry delay, and data noise injection. | S | T |
| FR-25084 | The system shall support combinatorial purged cross-validation for ML-based strategies with configurable purge and embargo periods. | C | T |

## 25.9 Functional Requirements – Portfolio Backtests

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-25100 | The system shall run multiple strategy versions simultaneously against a shared simulated account with configured capital allocations. | M | T |
| FR-25101 | The system shall report per-strategy and combined metrics, return correlation between strategies, and diversification benefit. | M | T |
| FR-25102 | The system shall apply portfolio-level risk rules in simulation. | S | T |

## 25.10 Business Rules

| ID | Rule |
|---|---|
| BR-25-01 | Simulated fills never occur at prices that did not exist in the data at the simulated time, except when a slippage model deliberately adds cost. |
| BR-25-02 | A limit BUY order under the QUEUE model fills only when the market trades strictly below the limit price, or at the limit price after the estimated queue ahead has traded. |
| BR-25-03 | Market orders in BAR resolution fill at the next bar's open plus slippage, never at the signal bar's close. |
| BR-25-04 | Results are labeled with all simplifying assumptions in effect (for example "bar resolution", "no latency"). |

## 25.11 Error Conditions

| Code | Condition |
|---|---|
| `DATASET_COVERAGE_INSUFFICIENT` | Requested range not covered |
| `RESOLUTION_UNSUPPORTED` | Dataset lacks the requested resolution |
| `QUOTA_EXCEEDED` | User concurrency quota exceeded |
| `STRATEGY_RUNTIME_ERROR` | Strategy failed during simulation |

## 25.12 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-25001 | Given a strategy that attempts to read the next bar during on_bar, then no future data is returned. | FR-25003 |
| AC-25002 | Given a run re-executed from its snapshot, then all orders, fills, and metrics are identical. | FR-25009, FR-25023 |
| AC-25003 | Given a 100-trial grid optimization with 20% out-of-sample, then only the top-N in-sample configurations are evaluated out-of-sample and reported separately. | FR-25062 |
| AC-25004 | Given a signal on bar t in BAR resolution, then the market order fills at the open of bar t+1. | BR-25-03 |

---

*End of Chapter 25 – Backtesting*
