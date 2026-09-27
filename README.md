# JD Quant AI

Institutional-grade, AI-driven quantitative trading platform.

This repository contains:

1. **[Volume 2 – Master Software Requirements Specification](docs/srs/README.md)**: 100 chapters and 1,109 uniquely identified requirements covering functional and non-functional requirements, interfaces, system behaviour, and acceptance criteria.
2. **The platform (`src/jdquant`)**: a single-node (T1, CON-009) modular monolith with a persistent trading core, an AI autopilot that researches, backtests and trades on its own, login and permissions, Fyers (NSE), Binance and Alpaca connectivity, and a REST API.
3. **The web UI (`web/`)**: a React single-page app served by the same process.

## Quick start

Needs Python 3.11+ and, to build the UI, Node 22+.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
(cd web && npm ci && npm run build)          # builds into src/jdquant/web_dist
.venv/bin/python -m jdquant serve            # http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 and create the owner account on the first-run screen. The owner gets every role. Add more users under **Users & audit**, or from the command line:

```bash
.venv/bin/python -m jdquant create-user alice@example.com --roles QUANT_TRADER,RISK_MANAGER
```

A paper account (`paper-main`) and demo instruments exist from the start, so you can place orders, run strategies and backtest without connecting a venue. The REST API is documented at http://127.0.0.1:8000/docs.

Development: `npm run dev` in `web/` serves the UI with hot reload on port 5173 and proxies `/api` to the Python server on port 8000.

Tests and checks:

```bash
.venv/bin/pytest                     # 184 tests, traced to SRS acceptance criteria
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd web && npm run typecheck)
```

## How the AI trades (the autopilot)

Open **AI Autopilot**, pick the stocks it may trade and a paper budget, and press **Run research now** (or let it run after every NSE close). Each cycle:

1. **Research.** For every stock it tries 27 strategies (29 on intraday bars):
   - Trend: moving-average crossovers, MACD, Supertrend, time-series momentum.
   - Mean reversion: RSI, Bollinger bands, RSI(2) pullbacks inside an uptrend.
   - Breakouts: price channels, volume-confirmed breakouts, the intraday opening range.
   - Machine learning: logistic models on price features, at two horizons.
2. **Walk-forward backtest.** Each strategy trades history it was never tuned on, period by period. ML models are retrained only on earlier data. Indian charges (brokerage, STT, exchange, SEBI, stamp duty, GST, DP) and slippage are applied.
3. **Reject luck.** Trying many strategies guarantees some look good by chance. A strategy needs:
   - a Sharpe ratio above what the best of that many skill-less strategies would reach (deflated Sharpe, counting only bars with money at risk);
   - enough trades;
   - profits in most validation periods;
   - a profit in a final holdout period the selection never saw.

   In the tests, no pure-noise stock ever passes.
4. **Trade and watch.** Winners get capital on a separate `paper-ai` account, split by risk parity with a per-stock cap. Stops and a daily cutoff are applied:
   - a fixed stop, and optionally a trailing stop;
   - intraday positions are closed by 15:10 IST.

   The autopilot closes out a strategy when its edge fades on fresh data or it breaches its drawdown limit.
5. **Go live only when you say so.** A strategy with a clean paper record (default 10 days, 2 trades) moves to your Fyers account only while you have armed live trading with a capital cap. Arming is an MFA-verified action. Disarming sells the live positions.

Every action is logged as a decision with its reasons. With `ANTHROPIC_API_KEY` set, Claude writes a plain-language briefing of each cycle. Claude explains; the tested strategies decide. That is deliberate: an LLM choosing trades cannot be backtested honestly, because it has already read about the history it would be tested on.

Research, paper and live all run the same strategy code with the same parameters, so what was backtested is what trades. Risk limits and the kill switch apply to the autopilot exactly as to manual trading.

## Fyers

1. Create an API app at myapi.fyers.in. Set its redirect URL to `http://127.0.0.1:8000/api/v1/connections/oauth/callback`. If you serve the app elsewhere, set `JDQ_PUBLIC_URL` and use that host.
2. **Connections → Add connection → Fyers**. Enter the App ID and secret key, choose Delivery (CNC) or Intraday, then **Continue to Fyers** and sign in. You return to the app connected.
3. Optional: **PIN**. With your Fyers PIN stored (encrypted), expired daily sessions renew automatically for up to 15 days. Without it, you sign in each day.

NSE equities come from Fyers' public symbol master. Fyers prices feed the paper accounts too, so the autopilot can paper trade on real market data before any real order is placed. Fyers has no test environment, so real orders happen only when you trade on the Fyers account yourself or arm the autopilot.

## What it does

| Area | What you get |
|---|---|
| **Trading** | Market and limit orders with idempotency keys, modify (native replace or cancel-then-new) and cancel, cancel-all, fills and positions with FIFO/average cost and realized/unrealized P&L. |
| **Risk** | Scoped risk profiles (most restrictive limit wins), pre-trade checks that fail closed, daily loss → reduce-only, kill switches (block, cancel, flatten) with audited release. |
| **Autopilot** | Automated research, walk-forward backtesting, luck rejection, risk-parity allocation, paper deployment, monitoring, retirement and armed live promotion, with a decision log and Claude briefings (see above). |
| **Strategies** | Manual templates (MA crossover, RSI mean reversion, Bollinger reversion, Donchian breakout, ML signal) plus the autopilot strategy with 11 signals. Deployments go through approve → start → pause/resume → stop/flatten → retire. |
| **Backtesting** | Event-driven, no look-ahead, next-bar fills, fees and slippage, and a reproducibility hash. |
| **Persistence** | SQLite (WAL, full sync) holding orders, fills, positions, deployments, kill switches, risk state, users, audit and models. On restart the platform starts in RECOVERING mode, restores state, reconciles in-flight orders with venues and only then accepts orders. Failure drops it to SAFE mode. |
| **Security** | scrypt passwords, server-side sessions (HttpOnly cookie + CSRF token, 30-minute idle and 12-hour absolute limits), bearer tokens, scoped API keys (`X-API-Key`), TOTP MFA with recovery codes, step-up MFA for privileged actions, lockout, 12 built-in roles, and a hash-chained audit log you can verify. |
| **Venues** | Fyers (NSE equities, API v3), Binance Spot and Alpaca adapters: Fyers OAuth sign-in with unattended renewal, signed requests, clock-offset correction, rate limiting, error mapping, native order modify, and fill polling. Credentials and tokens are encrypted at rest and keys with withdrawal permission are refused. NSE fills carry Indian charges; an NSE calendar covers session hours and the intraday cutoff. |
| **AI** | A feature store that computes features the same way online and offline; logistic/ridge models trained on a purged time split; evaluation reports with cost-adjusted trading metrics; a model registry (staging → shadow → production gate, rollback, shadow scoring, drift alerts); a portfolio optimizer; and a Claude copilot that can read platform state and propose actions that you confirm before they run. |
| **Web UI** | Dashboard, trading ticket, markets, strategies, backtests with equity chart, risk and kill switches, AI models, connections, users and audit, and account settings (MFA, sessions, API keys). Light and dark themes, works down to phone width, and prompts for MFA step-up in place. |

## Configuration

All settings are environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `JDQ_DATA_DIR` | `data` | Database and key location (`:memory:` for a throwaway instance) |
| `JDQ_MASTER_KEY` | generated in `data/master.key` | Key that encrypts venue credentials and MFA secrets. Set it explicitly in production. |
| `JDQ_ENFORCE_MFA` | `1` | Require MFA (and a recent step-up) for privileged actions |
| `JDQ_COOKIE_SECURE` | `0` | Mark session cookies `Secure`. Turn it on behind HTTPS. |
| `JDQ_ALLOW_SETUP` | `1` | Allow the first-run owner setup |
| `JDQ_BACKGROUND_POLLING` / `JDQ_POLL_INTERVAL` | `1` / `2` | Poll connected venues for quotes and order updates |
| `ANTHROPIC_API_KEY` | unset | Enables the copilot. Without it the copilot panel says it isn't configured. |
| `JDQ_COPILOT_MODEL` / `JDQ_COPILOT_DAILY_TOKENS` | `claude-opus-5` / `2000000` | Copilot model and per-user daily token budget |
| `JDQ_WEB_DIR` | bundled `web_dist` | Serve the UI from another directory |
| `JDQ_PUBLIC_URL` | the request's host | Base URL used for the broker sign-in redirect |

Autopilot settings (universe, budget, bar size, stops and the evidence it requires) are edited on the **AI Autopilot** page and stored in the database.

## Architecture

```text
   Web UI (web/)  ──►  REST API (api/): sessions, RBAC, CSRF, audit
                           │
   Strategy runner ──intent──▶ OMS (oms/) ──▶ RMS (risk/)   pre-trade, fail-closed
   (strategy/)              │        ▲
        ▲                   ▼        │ execution reports
   market data      Account router (connectivity/)
   (marketdata/)      ├─ paper → simulated venue (execution/)
        ▲             └─ live  → Fyers / Binance / Alpaca adapters ◀── poller
   AI autopilot (autopilot/): research → walk-forward backtests → deploy / retire / promote
        │
   Event bus (core/events) ── persistence subscribers ──▶ SQLite journal (persistence/)
        │                                                    ▲
        └── fills ──▶ Position engine (positions/)           └─ recovery on restart
```

| Package | SRS chapters | Contents |
|---|---|---|
| `core` | 15, 82, 89 | Clock abstraction (CON-010), Decimal-only money (CON-022), event bus with critical and isolated subscribers, declarative state machines |
| `marketdata` | 20 | Instrument registry, candle aggregation, reference prices, staleness detection, synthetic data |
| `oms` | 21 | Order state machine, validation, idempotency, modify/replace, fill de-duplication, UNKNOWN resolution |
| `risk` | 27 | Risk profiles and limits, projections, reducing-order waivers, kill-switch escalation |
| `execution` | 22.8 | Deterministic simulated venue |
| `positions` | 28 | Cost basis, lots, P&L, position flips |
| `trading` | 19 | Accounts, deployments, kill switches, platform modes |
| `strategy` | 24 | Strategy contract, templates, strategy runner |
| `backtest`, `analytics` | 25, 31–32 | Backtester and metric library |
| `persistence` | 51, 83, 92 | SQLite store with migrations, journal, recovery |
| `security` | 39–40, 42 | Passwords, TOTP, sessions, API keys, roles, encrypted secrets, audit chain |
| `connectivity` | 45–46 | Venue adapter contract, Fyers, Binance and Alpaca adapters, broker OAuth, account router, connection manager, poller |
| `markets` | — | Indian equity charges and the NSE session calendar |
| `autopilot` | 24–25, 57 | Autopilot strategy, candidate generation, walk-forward research with deflated Sharpe, and the controller |
| `ai` | 52–59, 63 | Features, training, model registry, optimizer, prompts, copilot and its tools |
| `api` | 80 | `/api/v1` REST resources with problem-details errors; serves the web UI |

## Known gaps

- The UI refreshes by polling every 2–5 seconds; there is no WebSocket streaming yet.
- API keys are random secrets stored only as hashes and sent as a header. The SRS also describes HMAC request signing, which would need the server to keep a recoverable secret; that was not implemented.
- OCO/bracket orders, execution algorithms, NAV and capital flows, reporting, and notifications (Chapters 21.8, 22.6, 23, 33, 37) are not built yet.
- Single node only (T1). There is no clustering or failover.
- The autopilot is long-only and trades each stock independently. Cross-sectional rotation (ranking stocks and holding the top few) and market-neutral pairs are natural next steps. Pairs are limited to intraday on NSE cash, because shares cannot be shorted overnight.
- The NSE holiday list is not bundled; add holidays to the calendar, or cycles simply find no new bars on those days.
- Fyers endpoints follow the official `fyers-apiv3` SDK. They were verified against a simulated Fyers server, because fyers.in is unreachable from the build environment.
- Paper trading needs live prices: without a Fyers connection, NSE deployments wait for quotes and research uses synthetic demo data (the page says so).
