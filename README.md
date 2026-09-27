# JD Quant AI

Institutional-grade, AI-driven quantitative trading platform.

This repository contains:

1. **[Volume 2 – Master Software Requirements Specification](docs/srs/README.md)**: 100 chapters and 1,109 uniquely identified requirements covering functional and non-functional requirements, interfaces, system behaviour, and acceptance criteria.
2. **The platform implementation (`src/jdquant`)**: a single-node (T1, CON-009) modular monolith. It implements the safety-critical trading core, the strategy framework, a deterministic backtester, and a REST API for paper trading. Each engine maps to the SRS chapter that specifies it.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                   # 67 tests, traced to SRS acceptance criteria
.venv/bin/python examples/backtest_demo.py         # compare strategy templates on synthetic data
.venv/bin/uvicorn jdquant.api.app:app --reload     # REST API at http://127.0.0.1:8000/docs
```

Paper trading through the API:

```bash
curl -X POST localhost:8000/api/v1/market-data/quotes -H 'content-type: application/json' \
  -d '{"instrument_id":"BINANCE:ETHUSDT","bid_price":"3000","bid_size":"5","ask_price":"3000.5","ask_size":"5"}'
curl -X POST localhost:8000/api/v1/orders -H 'content-type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"account_id":"paper-main","instrument_id":"BINANCE:ETHUSDT","side":"BUY","order_type":"MARKET","quantity":"1.5"}'
curl localhost:8000/api/v1/positions
```

## Architecture

```text
            REST API (api/)  ─────────────────────────────┐
                 │                                        │
   Strategy host (strategy/) ──intent──▶ OMS (oms/) ──▶ RMS (risk/)   pre-trade, fail-closed
                 ▲                        │      ▲
            market data                   ▼      │ execution reports
          (marketdata/)          Execution simulator (execution/)
                 │                        │
                 └──── Event bus (core/events) ◀── fills ──▶ Position Engine (positions/)
                                          │
                        Trading Engine (trading/): deployments, kill switches, eligibility
```

| Package | SRS chapter | Implemented |
|---|---|---|
| `core` | 15, 82, 89 | Clock abstraction (CON-010), Decimal-only money (CON-022), event bus with envelope and dead letters, declarative state machines |
| `marketdata` | 20 | Instrument registry, UTC-aligned candle aggregation, reference price rule (BR-20-04), staleness detection, synthetic data |
| `oms` | 21 | Full order state machine, validation, idempotency, fail-closed risk integration, fill de-duplication, ack timeout → UNKNOWN resolution, cancel semantics |
| `risk` | 27 | Scoped risk profiles with the most restrictive limit winning, conservative projections, reducing-order waivers, stale data, order rate, daily loss → REDUCE_ONLY, kill-switch escalation |
| `execution` | 22.8 | Deterministic simulated venue: immediate or next-bar fills, queue-style limits, maker/taker fees, slippage |
| `positions` | 28 | FIFO/AVERAGE cost basis, lots, realized/unrealized P&L, position flips |
| `trading` | 19 | Accounts, deployment lifecycle state machine, kill switches (block/cancel/flatten), maintenance mode, four-eyes approval |
| `strategy` | 24 | Strategy contract and context, parameter schemas, indicator library, 4 templates, error isolation |
| `backtest` | 25 | Event-driven bar backtester: no look-ahead, next-bar fills, reproducibility hash, round trips |
| `analytics` | 31–32 | Shared metric library (TWR, CAGR, Sharpe, Sortino, drawdown, Calmar, trade statistics) |
| `api` | 80 | `/api/v1` resources for instruments, quotes, orders, positions, kill switches, strategy templates, backtests; Decimal-as-string and problem-details errors |

## Roadmap (not yet implemented)

Planned next, in priority order:

1. Persistence: a transactional database, the event store, and a recovery sequence (Chapters 51, 83, 92).
2. Authentication and RBAC (Chapters 39–40). The API currently runs as a single local user.
3. Order modification (FR-21024), OCO/bracket orders, and execution algorithms (Chapters 21.8, 22.6).
4. Live exchange and broker adapters with the conformance suite (Chapters 45–46).
5. Portfolio Engine NAV and capital flows, reporting, notifications, and audit hash-chain (Chapters 23, 33, 37, 38).
6. The AI stack: Feature Store, training/inference pipelines, Model Manager, and Copilot (Chapters 52–59, 63).
7. User interface (Chapter 79 and Volume 8).
