# JD Quant AI

Institutional-grade, AI-driven quantitative trading platform.

This repository contains:

1. **[Volume 2 – Master Software Requirements Specification](docs/srs/README.md)**: 100 chapters and 1,109 uniquely identified requirements covering functional and non-functional requirements, interfaces, system behaviour, and acceptance criteria.
2. **The platform (`src/jdquant`)**: a single-node (T1, CON-009) modular monolith with a persistent trading core, an AI autopilot that researches, backtests and trades on its own, login and permissions, OANDA (spot gold XAU/USD and forex), Fyers (NSE stocks and ETFs, MCX commodities, NSE currency futures), Binance (crypto, including tokenized gold) and Alpaca connectivity, an AI trade monitor that reviews every trade each minute, live price charts, and a REST API.
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

Demo prices: start with `JDQ_DEMO_FEED=1` to stream random-walk quotes for every instrument without a live source, so the live charts move. Those prices are labelled **SIMULATED** everywhere.

Development: `npm run dev` in `web/` serves the UI with hot reload on port 5173 and proxies `/api` to the Python server on port 8000.

Tests and checks:

```bash
.venv/bin/pytest                     # 234 tests, traced to SRS acceptance criteria
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd web && npm run typecheck)
```

## How the AI trades (the autopilot)

Open **AI Autopilot**, pick what it may trade and a paper budget, and press **Run research now** (or let it run after every close). The universe can mix:

| Asset | Instruments | Venue | Session |
|---|---|---|---|
| **Gold (XAU/USD), silver** | Spot `OANDA:XAU_USD`, `OANDA:XAG_USD` | OANDA | Sunday 18:00 to Friday 17:00 New York, 1-hour daily break |
| **Forex** | EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF and the rest of OANDA's list | OANDA | Sunday 17:05 to Friday 17:00 New York |
| Stocks and ETFs (incl. gold and silver ETFs such as GOLDBEES) | NSE cash | Fyers | 09:15–15:30 IST |
| Commodities (gold, silver, crude oil…) | MCX futures, as rolling front months like `MCX:GOLDM1!` | Fyers | 09:00–23:30 IST |
| Currency (USDINR…) | NSE currency futures, e.g. `NSE:USDINR1!` | Fyers | 09:00–17:00 IST |
| Crypto | BTC/USDT, ETH/USDT… | Binance | 24/7 |
| Gold in US dollars (XAU/USD) | PAXG/USDT, a token backed by one troy ounce of gold | Binance | 24/7 |

**XAU/USD and forex are the main focus.** In the autopilot settings, **Focus: XAU/USD & forex** sets the universe to spot gold and the major pairs on 1-hour bars. Demo instruments for them exist from the start, so you can research and paper trade before connecting OANDA.

**Indian residents:** RBI rules allow forex trading only in INR pairs and EUR/USD, GBP/USD and USD/JPY on recognised Indian exchanges. Leveraged forex or gold trading with overseas brokers is not permitted under FEMA. Use an OANDA *practice* account (virtual money, live prices) to research and paper trade XAU/USD and forex. For real money, use NSE currency futures or MCX gold through Fyers. The connection form says the same.

Each cycle:

1. **Research, the way a systematic fund would.** For every instrument it tries about 30 strategies:
   - Trend: the multi-speed EWMAC trend forecast that CTA funds use, moving-average crossovers, MACD, Supertrend, time-series momentum. Futures may go short on these trend signals.
   - Mean reversion: RSI, Bollinger bands, RSI(2) pullbacks inside an uptrend.
   - Breakouts: price channels, volume-confirmed breakouts, the intraday opening range, and for gold and forex the London-open breakout of the Asian-session range (a day trade, flat before the New York rollover).
   - Machine learning: logistic models on price features, at two horizons.
   - Across baskets of three or more instruments: cross-sectional momentum (hold the strongest, go to cash when the whole basket is falling) and short-term reversal (buy recent losers that remain in an uptrend).

   Trend signals may go short on forex, gold and futures. Every position is sized to a volatility target (default 20% a year) and is never leveraged, so a volatile coin gets less money than a quiet currency pair. One ounce of gold (about $4,000) must fit in a single position's share of the budget; otherwise gold is skipped with the reason shown.
2. **Walk-forward backtest with every charge.** Each strategy trades history it was never tuned on, period by period. ML models are retrained only on earlier data. Costs are those of the market it trades, plus slippage:
   - NSE cash: brokerage, STT, exchange, SEBI, stamp duty, GST and DP charges.
   - MCX: brokerage, CTT on sells, exchange, SEBI, stamp duty and GST.
   - Currency futures: brokerage, exchange, SEBI, stamp duty and GST.
   - Crypto: the exchange fee plus 18% GST. The 1% TDS on crypto sales is a tax credit, not a cost, so it is not deducted.
   - Forex and gold: half the typical spread on every fill, and overnight financing at each 17:00 New York rollover (three days on Wednesdays) from the interest-rate difference plus the broker's markup. Credits are never counted. Spreads and rates are approximations in `markets/forex.py`; edit them to match your account.

   A strategy whose charges would eat more than half its gross profit is rejected, and so is one too big for the instrument's daily traded value.
3. **Reject luck.** Trying many strategies guarantees some look good by chance. A strategy needs:
   - a Sharpe ratio above what the best of that many skill-less strategies would reach (deflated Sharpe, counting only bars with money at risk);
   - enough trades;
   - profits in most validation periods;
   - a profit in a final holdout period the selection never saw.

   In the tests, no pure-noise instrument ever passes.
4. **AI analyst review.** An LLM reads the cycle's results and writes a briefing with concerns, each rated low, medium or high (see below).
5. **Trade, protect and watch.** Winners get capital on a separate `paper-ai` account, split by risk with a cap per position. Instruments that share a basket are never double-counted. The book is protected at several levels:
   - Per position: a fixed stop, optionally a trailing stop, and intraday positions are closed before each market's cutoff.
   - Loss floor: the book may lose at most 10% of its budget. Once it is in profit, the floor rises to keep half of the best gain. New positions shrink as equity nears the floor. At the floor everything goes to cash until you press **Reset protection**.
   - Daily loss limit (3% of the budget per account): the risk engine then allows only reducing orders.
   - Book drawdown halt: at 10% below its peak the book goes to cash and pauses for five days.
   - Futures roll to the next contract three days before expiry.

   The autopilot closes out a strategy when its edge fades on fresh data or it breaches its own drawdown limit.
6. **Go live only when you say so.** A strategy with a clean paper record (default 10 days, 2 trades) moves to a real account only while you have armed that account with a capital cap. Each broker account is armed separately. Futures on an account need their own opt-in. Arming is an MFA-verified action. Disarming closes the live positions.

These limits bound losses; they cannot guarantee profits. Markets can gap through stops, and a strategy that passed every test can still stop working.

### The AI trade monitor: every trade, every minute

Once a minute (configurable, 30 s to 1 h), the AI looks at every open autopilot position and every entry a strategy wants to make. It receives:
- the bid, ask and spread;
- the last 30 one-minute bars;
- RSI, 20/50-period averages, average true range and 15/60-minute change;
- the trading session and time to the cutoff;
- the position's entry price, profit or loss and time held;
- the strategy and its stops;
- its own previous verdict.

It answers HOLD, REDUCE or EXIT for positions and APPROVE or REJECT for entries, each with a confidence and a one-sentence reason citing the numbers.

- **Advise** (default): verdicts are shown next to each position, and the strategies trade exactly as backtested.
- **Act**: verdicts at or above the confidence floor (70%) take effect. EXIT closes the position, REDUCE halves it, and neither re-enters for an hour. New entries wait up to three minutes for approval, and a confident REJECT drops the entry. The AI can only take risk away; it never opens, adds to or reverses a position. If the model does not answer, entries go ahead (or are skipped, if you choose).

The AI's judgement cannot be backtested, so each verdict is scored against the price 15 and 60 minutes later. The page shows how often it was right, and what its actions earned or cost in rupees (loss avoided minus gain missed). Switch to act only once that record is convincing. A daily call budget (1,500 by default) caps the cost.

### The AI analyst (NVIDIA Nemotron or Claude)

After each research cycle, the analyst receives the leaderboard and selections: returns, Sharpe, drawdowns, charges, trade counts and the deployments already running. It answers with JSON: a plain-language summary and a list of concerns (overfitting, costs, concentration, regime risk…). The briefing appears on the Autopilot page with severity badges, and each call is logged with its tokens and latency.

The analyst does **not** pick trades, because an LLM's trade choices cannot be backtested honestly: it has already read about the history it would be tested on. If you tick **Let a high-severity concern veto a strategy**, such a concern removes that strategy from the cycle. The analyst can only take risk away, never add it. If the analyst is unreachable, the cycle carries on without a briefing.

To use NVIDIA's hosted Nemotron (an OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`), set the key in the server's environment. Never put it in code or a file in the repository:

```bash
export NVIDIA_API_KEY=nvapi-...        # from build.nvidia.com
.venv/bin/python -m jdquant serve
```

The request uses `nvidia/nemotron-3-ultra-550b-a55b` with reasoning enabled (`enable_thinking`), a low temperature (0.2) for consistent reviews, and a non-streaming call; the reasoning text is discarded and only the JSON answer is kept. Without `NVIDIA_API_KEY`, Claude is used when `ANTHROPIC_API_KEY` is set. With neither, the page shows the analyst as not configured.

Research, paper and live all run the same strategy code with the same parameters, so what was backtested is what trades. Risk limits and the kill switch apply to the autopilot exactly as to manual trading.

## Live charts

**Markets** (opening on XAU/USD) and **Trading** show a candlestick chart (1m, 5m, 15m, 1h, 1D) for the selected instrument, with your fills marked as arrows. The chart loads broker history when available, then updates with every quote through a server-sent event stream (`/api/v1/market-data/stream`). Each chart is badged as **LIVE**, **SIMULATED** (demo feed) or **OFFLINE**.

## OANDA (XAU/USD and forex)

1. In the OANDA hub, open **Manage API Access** and generate a token. Note your account ID (e.g. `101-001-1234567-001`).
2. **Connections → Add connection → OANDA**. Keep **Practice account** unless you are permitted to trade live. Enter the token and account ID, then **Add and test**.
3. Gold, silver and every currency pair on the account are loaded. Prices stream to the charts and paper accounts, and hourly or minute history feeds the autopilot's research.

Orders are fill-or-kill market orders or limit orders, tagged with the platform's order ID so they can be looked up and cancelled. The adapter follows OANDA's v20 REST API as published in its official `v20-python` SDK. It was verified against a simulated OANDA server, because OANDA's hosts are unreachable from the build environment.

## Fyers

1. Create an API app at myapi.fyers.in. Set its redirect URL to `http://127.0.0.1:8000/api/v1/connections/oauth/callback`. If you serve the app elsewhere, set `JDQ_PUBLIC_URL` and use that host.
2. **Connections → Add connection → Fyers**. Enter the App ID and secret key, choose Delivery (CNC) or Intraday, then **Continue to Fyers** and sign in. You return to the app connected.
3. Optional: **PIN**. With your Fyers PIN stored (encrypted), expired daily sessions renew automatically for up to 15 days. Without it, you sign in each day.

NSE equities and ETFs, MCX commodity futures and NSE currency futures (front contracts within 120 days) come from Fyers' public symbol masters. MCX order quantities are sent in lots and currency futures in units, following the Fyers masters; place a one-lot test order before arming futures. Fyers prices feed the paper accounts too, so the autopilot can paper trade on real market data before any real order is placed. Fyers has no test environment, so real orders happen only when you trade on the Fyers account yourself or arm the autopilot.

## What it does

| Area | What you get |
|---|---|
| **Trading** | Market and limit orders with idempotency keys, modify (native replace or cancel-then-new) and cancel, cancel-all, fills and positions with FIFO/average cost and realized/unrealized P&L. |
| **Risk** | Scoped risk profiles (most restrictive limit wins), pre-trade checks that fail closed, daily loss → reduce-only, kill switches (block, cancel, flatten) with audited release. |
| **Autopilot** | Automated multi-asset research, walk-forward backtesting with market-specific charges, luck rejection, volatility-targeted allocation, loss floor and drawdown halts, futures rolling, paper deployment, monitoring, retirement and per-account live promotion, with a decision log and an LLM analyst (see above). |
| **Strategies** | Manual templates (MA crossover, RSI mean reversion, Bollinger reversion, Donchian breakout, ML signal) plus the autopilot strategy (12 signals, long and short) and a cross-sectional rotation strategy. Deployments go through approve → start → pause/resume → stop/flatten → retire. |
| **Backtesting** | Event-driven, no look-ahead, next-bar fills, fees and slippage, and a reproducibility hash. |
| **Persistence** | SQLite (WAL, full sync) holding orders, fills, positions, deployments, kill switches, risk state, users, audit and models. On restart the platform starts in RECOVERING mode, restores state, reconciles in-flight orders with venues and only then accepts orders. Failure drops it to SAFE mode. |
| **Security** | scrypt passwords, server-side sessions (HttpOnly cookie + CSRF token, 30-minute idle and 12-hour absolute limits), bearer tokens, scoped API keys (`X-API-Key`), TOTP MFA with recovery codes, step-up MFA for privileged actions, lockout, 12 built-in roles, and a hash-chained audit log you can verify. |
| **Venues** | OANDA (spot gold and forex, v20), Fyers (NSE equities, API v3), Binance Spot and Alpaca adapters: Fyers OAuth sign-in with unattended renewal, signed requests, clock-offset correction, rate limiting, error mapping, native order modify, and fill polling. Credentials and tokens are encrypted at rest and keys with withdrawal permission are refused. Fills carry the charges of their market (NSE, MCX, currency, crypto); per-market session calendars cover trading hours and intraday cutoffs. |
| **AI** | A feature store that computes features the same way online and offline; logistic/ridge models trained on a purged time split; evaluation reports with cost-adjusted trading metrics; a model registry (staging → shadow → production gate, rollback, shadow scoring, drift alerts); a portfolio optimizer; and a Claude copilot that can read platform state and propose actions that you confirm before they run. |
| **Web UI** | Dashboard, trading ticket, markets, strategies, live candlestick charts, AI autopilot, backtests with equity chart, risk and kill switches, AI models, connections, users and audit, and account settings (MFA, sessions, API keys). Dark theme by default (light available), works down to phone width, and prompts for MFA step-up in place. |

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
| `NVIDIA_API_KEY` | unset | Makes NVIDIA Nemotron the autopilot's analyst |
| `JDQ_ANALYST` | `auto` | Model for the cycle analyst and the per-minute trade monitor: `auto` (NVIDIA if its key is set, else Claude if configured), `nvidia`, `openai`, `claude` or `none` |
| `JDQ_ANALYST_BASE_URL` / `JDQ_ANALYST_MODEL` / `JDQ_ANALYST_API_KEY` | NVIDIA endpoint / `nvidia/nemotron-3-ultra-550b-a55b` / unset | Point the analyst at any OpenAI-compatible endpoint |
| `JDQ_DEMO_FEED` | `0` | Stream simulated quotes for instruments with no live source (labelled SIMULATED) |
| `JDQ_WEB_DIR` | bundled `web_dist` | Serve the UI from another directory |
| `JDQ_PUBLIC_URL` | the request's host | Base URL used for the broker sign-in redirect |

Autopilot settings (universe, budget, bar size, stops, capital protection, charges limit, analyst veto, the ₹/USDT rate and the evidence it requires) are edited on the **AI Autopilot** page and stored in the database.

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
| `marketdata` | 20 | Instrument registry (incl. futures expiry), candle aggregation, reference prices, staleness detection, live one-minute bars and quote streaming, synthetic data |
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
| `markets` | — | Charges for NSE cash, MCX, currency futures, crypto, and forex spread and financing; per-market sessions (incl. forex 24/5) and asset groups |
| `autopilot` | 24–25, 57 | Autopilot strategy, candidate generation, walk-forward research with deflated Sharpe, the controller, and the per-minute AI trade monitor |
| `ai` | 52–59, 63 | Features, training, model registry, optimizer, prompts, copilot and its tools, the autopilot analyst |
| `api` | 80 | `/api/v1` REST resources with problem-details errors; serves the web UI |

## Known gaps

- Charts stream quotes live; the rest of the UI refreshes by polling every 2–5 seconds.
- API keys are random secrets stored only as hashes and sent as a header. The SRS also describes HMAC request signing, which would need the server to keep a recoverable secret; that was not implemented.
- OCO/bracket orders, execution algorithms, NAV and capital flows, reporting, and notifications (Chapters 21.8, 22.6, 23, 33, 37) are not built yet.
- Single node only (T1). There is no clustering or failover.
- Shorting is limited to futures; NSE cash and crypto spot are long-only (shares cannot be shorted overnight). Market-neutral pairs are a natural next step.
- Market making and latency arbitrage (as run by firms like Jane Street) need co-location and exchange-level connectivity, which retail REST APIs cannot provide, so they are not attempted.
- The MCX lots convention follows the Fyers symbol master but was not confirmed against a real account; futures stay off until you opt in per account.
- Crypto and forex are sized from the INR budget. The ₹/USD (and ₹/USDT) rate is set in the settings. Other currencies convert through the dollar at live OANDA prices when available, otherwise at defaults.
- Paper trading charges the spread but not overnight financing (backtests charge both). Live OANDA accounts book financing themselves.
- The trade monitor sends one request per minute while positions are open. With a large reasoning model this can take tens of seconds and many tokens; turn reasoning off or lengthen the interval if needed.
- The NSE holiday list is not bundled; add holidays to the calendar, or cycles simply find no new bars on those days.
- Fyers endpoints follow the official `fyers-apiv3` SDK, OANDA the official `v20-python` SDK, and the NVIDIA analyst NVIDIA's OpenAI-compatible API. All were verified against simulated servers, because fyers.in, OANDA and integrate.api.nvidia.com are unreachable from the build environment.
- Paper trading needs live prices: without a Fyers connection, NSE deployments wait for quotes and research uses synthetic demo data (the page says so).
