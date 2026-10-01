# Chapter 24 – Strategy Framework

## 24.1 Purpose

The Strategy Framework, implemented by the Strategy Engine (SE), defines how trading strategies are authored, packaged, versioned, validated, hosted, and executed. It provides a uniform execution contract across backtesting, paper trading, and live trading (CON-005) and governs the promotion path of a strategy from research to production.

## 24.2 Scope and Actors

### In Scope

- Strategy definition, packaging, and versioning
- Strategy programming interface (lifecycle callbacks, context services)
- Parameter schemas
- Strategy hosting, isolation, and resource limits
- Strategy state persistence
- Strategy templates and library
- Promotion workflow (Research → Backtested → Paper → Live)
- No-code / visual rule strategies (Should)

### Actors

| Actor | Interaction |
|---|---|
| Quantitative Researcher | Authors and tests strategies |
| Quantitative Trader | Deploys and supervises strategies |
| Risk Manager | Approves promotion to live |
| Trading Engine | Controls lifecycle |
| Signal Engine | Receives signals produced by strategies |

## 24.3 Domain Entities

### 24.3.1 Strategy

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| description | Text | Optional |
| category | Enum: TREND, MEAN_REVERSION, MOMENTUM, ARBITRAGE, MARKET_MAKING, STATISTICAL, ML, RL, FACTOR, EVENT_DRIVEN, PORTFOLIO, HYBRID | Required |
| owner_id | Principal | Required |
| authoring_mode | Enum: CODE, VISUAL, TEMPLATE | Required |
| status | Enum: ACTIVE, ARCHIVED | Default ACTIVE |

### 24.3.2 StrategyVersion

| Attribute | Type | Constraints |
|---|---|---|
| strategy_id | Reference | Required |
| version | Semantic version (MAJOR.MINOR.PATCH) | Unique per strategy; immutable |
| artifact_ref | Content-addressed reference | Hash of packaged source and dependencies |
| runtime | String | Runtime identifier and version |
| parameter_schema | Schema document | Types, defaults, ranges, enumerations, descriptions |
| data_requirements | List of (data type, interval, warm-up length) | Declared subscriptions |
| instrument_constraints | Asset classes, venues | Where the strategy may be deployed |
| model_dependencies | List of (model_id, version constraint) | AI models used |
| supports_hot_reload | Boolean | Default false |
| promotion_stage | Enum: RESEARCH, BACKTESTED, PAPER_VALIDATED, LIVE_APPROVED, DEPRECATED | System-managed |
| evidence | List of references to backtest runs, paper deployments, approvals | System-managed |
| changelog | Text | Required for versions after 1.0.0 |

### 24.3.3 StrategyState

| Attribute | Description |
|---|---|
| deployment_id | Owner |
| state_blob | Serialized user-defined state |
| checkpoint_at | Timestamp |
| schema_version | For migration between strategy versions |

## 24.4 Strategy Programming Contract

The strategy programming interface shall provide the following lifecycle callbacks. Strategies implement any subset.

| Callback | Invoked When |
|---|---|
| on_init(context) | Once per start, before data subscription |
| on_warmup_complete(context) | After historical warm-up data has been replayed |
| on_bar(context, candle) | A subscribed candle closes |
| on_tick(context, trade) | A subscribed trade occurs |
| on_quote(context, quote) | A subscribed L1 quote changes |
| on_book(context, book) | A subscribed order book changes |
| on_signal(context, signal) | A subscribed external or model signal arrives |
| on_order_update(context, order) | An order owned by the deployment changes state |
| on_fill(context, fill) | An order owned by the deployment is filled |
| on_timer(context, timer_id) | A registered timer fires (simulated time in backtests) |
| on_session(context, event) | Session open/close event for a traded venue |
| on_stop(context) | Before the deployment stops |

The context shall provide the following services:

| Service | Capabilities |
|---|---|
| clock | Current time (simulated or real, CON-010) |
| data | Historical window access, latest values, indicator library |
| orders | Submit, modify, cancel orders owned by the deployment; query open orders |
| positions | Positions and P&L attributed to the deployment |
| portfolio | Read-only account balances and capital allocation |
| signals | Publish signals (Chapter 30) |
| models | Request inference from approved models (Chapter 59) |
| features | Read features from the Feature Store (Chapter 63) |
| state | Persist and restore user-defined state |
| timers | Register one-shot and recurring timers |
| log | Structured logging tagged with deployment identifier |
| params | Validated parameter values |

## 24.5 Functional Requirements – Authoring and Versioning

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-24001 | The system shall allow users to create strategies in CODE mode using the programming contract in 24.4. | M | T |
| FR-24002 | The system shall provide a strategy template library including at least: moving-average crossover, breakout, RSI mean reversion, Bollinger band reversion, pairs trading, grid trading, basic market making, and cross-sectional momentum. | M | D |
| FR-24003 | The system shall allow users to create strategies in VISUAL mode by composing conditions over indicators, signals, and positions into entry and exit rules, without code. | S | D |
| FR-24004 | The system shall create an immutable StrategyVersion on each publish, computing the artifact hash over source, dependency lockfile, and parameter schema. | M | T |
| FR-24005 | The system shall enforce semantic versioning: parameter schema changes that remove or retype parameters require a MAJOR increment. | M | T |
| FR-24006 | The system shall provide a diff between any two versions of a strategy covering source, parameters, and declared requirements. | M | D |
| FR-24007 | The system shall perform static validation on publish: syntax, declared callbacks, forbidden operations (direct network, file system, process, or clock access outside the context), and dependency allowlist compliance. | M | T |
| FR-24008 | The system shall allow a strategy version to declare model dependencies, and shall refuse deployment if a required model version is not deployed in the Inference Pipeline. | M | T |
| FR-24009 | The system shall provide a built-in indicator library of at least: SMA, EMA, WMA, DEMA, TEMA, RSI, MACD, Bollinger Bands, ATR, ADX, Stochastic, CCI, OBV, VWAP, Keltner Channel, Donchian Channel, Ichimoku, Parabolic SAR, Z-score, rolling volatility, rolling correlation, and linear regression slope. | M | T |
| FR-24010 | The system shall compute every library indicator identically in backtest, paper, and live modes for identical input sequences. | M | T |

## 24.6 Functional Requirements – Hosting and Execution

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-24020 | The system shall host each deployment in an isolated execution context such that a failure or resource exhaustion in one deployment does not affect others (CON-013 applies to user strategy code). | M | T |
| FR-24021 | The system shall enforce per-deployment resource limits: CPU time per callback (default 100 ms, configurable), memory (default 1 GB), open orders (default 200), and orders per second (default 20). | M | T |
| FR-24022 | The system shall transition a deployment to FAILED if an unhandled error occurs in callbacks more than N times within a window (default 3 in 60 s); single errors shall be logged and the event skipped. | M | T |
| FR-24023 | The system shall deliver events to a deployment sequentially (one callback at a time per deployment) in timestamp order. | M | T |
| FR-24024 | The system shall report per-deployment callback latency (p50, p99, max), event queue depth, and last event time. | M | T |
| FR-24025 | The system shall detect a deployment whose event queue exceeds a threshold (default 10,000) and apply its configured backpressure policy: CONFLATE (keep latest market data per instrument, never drop order/fill events) or PAUSE. | M | T |
| FR-24026 | The system shall checkpoint strategy state at a configurable interval (default 60 s), on stop, and on demand, and restore it on start (FR-19014). | M | T |
| FR-24027 | The system shall restrict order operations to orders owned by the deployment and instruments in its universe. | M | T |
| FR-24028 | The system shall attach to every order submitted by a strategy the deployment identifier, strategy version, and triggering signal identifier where applicable. | M | T |

## 24.7 Functional Requirements – Promotion Workflow

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-24040 | The system shall track the promotion stage of each strategy version: RESEARCH → BACKTESTED → PAPER_VALIDATED → LIVE_APPROVED. | M | T |
| FR-24041 | The system shall promote to BACKTESTED when at least one completed backtest of that exact version meets the workspace's promotion criteria (default: minimum 250 trades or 1 year of data, out-of-sample period ≥ 20%, and user acknowledgment of the results). | M | T |
| FR-24042 | The system shall promote to PAPER_VALIDATED when the exact version has run in paper trading for at least the configured minimum duration (default 14 days) without FAILED transitions and the user acknowledges the comparison report between paper and backtest results. | M | T |
| FR-24043 | The system shall promote to LIVE_APPROVED upon approval by an authorized approver (CON-203), recording approver, timestamp, and justification. | M | T |
| FR-24044 | The system shall allow workspace administrators to configure promotion criteria, including waiving the paper stage for PATCH versions whose artifact diff affects no trading logic as declared and approved by a reviewer. | S | T |
| FR-24045 | The system shall produce a paper-versus-backtest comparison report covering return, trade count, win rate, average slippage, and signal agreement rate over the same period. | M | T |
| FR-24046 | The system shall allow marking a version DEPRECATED, preventing new deployments while leaving existing ones running with a warning. | M | T |

## 24.8 Business Rules

| ID | Rule |
|---|---|
| BR-24-01 | A StrategyVersion is immutable once published; any change creates a new version. |
| BR-24-02 | Promotion evidence is valid only for the exact artifact hash evaluated. |
| BR-24-03 | Strategy code shall access time, data, orders, and state only through the context services. |
| BR-24-04 | Indicators are computed on closed bars unless the strategy explicitly subscribes to in-progress bars. |

## 24.9 Events

| Event | Description |
|---|---|
| `strategy.version.published` | New version created |
| `strategy.promotion.changed` | Promotion stage changed |
| `strategy.error` | Callback error (with stack trace reference) |
| `strategy.backpressure` | Queue threshold exceeded |
| `strategy.checkpoint` | State checkpoint written |

## 24.10 Error Conditions

| Code | Condition |
|---|---|
| `STRATEGY_VALIDATION_FAILED` | Static validation failure |
| `FORBIDDEN_OPERATION` | Use of a prohibited operation |
| `PARAMETER_INVALID` | Parameter outside schema |
| `MODEL_DEPENDENCY_UNAVAILABLE` | Required model not deployed |
| `RESOURCE_LIMIT_EXCEEDED` | CPU, memory, or order limit exceeded |
| `ORDER_NOT_OWNED` | Operation on an order owned by another deployment |

## 24.11 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-24001 | Given a strategy that reads the operating-system clock directly, then publishing fails with FORBIDDEN_OPERATION. | FR-24007 |
| AC-24002 | Given a moving-average crossover strategy run on the same data in backtest and replay-driven paper mode, then generated signals are identical. | FR-24010, CON-005 |
| AC-24003 | Given a callback exceeding its CPU limit three times in 60 s, then the deployment becomes FAILED and its open orders are handled per stop policy. | FR-24021, FR-24022 |
| AC-24004 | Given version 1.2.0 with paper evidence, when 1.2.1 is published with changed logic, then 1.2.1 starts at RESEARCH. | BR-24-02 |

---

*End of Chapter 24 – Strategy Framework*
