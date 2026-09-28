# JD Quant AI

Institutional-grade, AI-driven quantitative trading platform.

This repository contains:

1. **[Volume 2 – Master Software Requirements Specification](docs/srs/README.md)**: 100 chapters and 1,109 uniquely identified requirements covering functional and non-functional requirements, interfaces, system behaviour, and acceptance criteria.
2. **The platform (`src/jdquant`)**: a single-node (T1, CON-009) modular monolith with a persistent trading core, an AI autopilot that researches, backtests and trades on its own, login and permissions, broker connections like TradingView's (OANDA for spot gold XAU/USD and forex; Fyers, Zerodha Kite, Upstox, Angel One, Dhan and Kotak Neo for NSE stocks and ETFs, with MCX and currency futures on Fyers; Delta Exchange India for crypto perpetuals; Binance and Alpaca), leverage where the market allows it, a strategy lab where you type a strategy in plain words and the AI backtests it with a confidence score, an AI that can take trades each minute from the signals of walk-forward-tested strategies and learns from its losing trades, market intelligence (order books from several brokers, liquidity walls, order flow, VWAP, volume profile, structure, regime, options and futures positioning, a searchable event store and event graph, a scanner, news and replay), automated-trading controls (structured signals, pre-trade guards, circuit breakers, STOP ALL, broker reconciliation, a trade journal and execution quality), a strategy pipeline from development to live with versions and rollback, live price charts, and a REST API.
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
.venv/bin/pytest                     # 378 tests, traced to SRS acceptance criteria
.venv/bin/ruff check . && .venv/bin/ruff format --check .
(cd web && npm run typecheck)
```

## How the AI trades (the autopilot)

Open **AI Autopilot**, pick what it may trade and a paper budget, and press **Run research now** (or let it run after every close). The universe can mix:

| Asset | Instruments | Venue | Session |
|---|---|---|---|
| **Gold (XAU/USD), silver** | Spot `OANDA:XAU_USD`, `OANDA:XAG_USD` | OANDA | Sunday 18:00 to Friday 17:00 New York, 1-hour daily break |
| **Forex** | EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF and the rest of OANDA's list | OANDA | Sunday 17:05 to Friday 17:00 New York |
| Stocks and ETFs (incl. gold and silver ETFs such as GOLDBEES) | NSE cash | Fyers, Zerodha Kite, Upstox, Angel One or Dhan | 09:15–15:30 IST |
| Commodities (gold, silver, crude oil…) | MCX futures, as rolling front months like `MCX:GOLDM1!` | Fyers | 09:00–23:30 IST |
| Currency (USDINR…) | NSE currency futures, e.g. `NSE:USDINR1!` | Fyers | 09:00–17:00 IST |
| Crypto | BTC/USDT, ETH/USDT… spot; BTCUSD, ETHUSD… perpetual futures (long or short) | Binance; Delta Exchange India | 24/7 |
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

### Leverage

**Leverage** in the autopilot settings (default 1, meaning none) lets a position control more than its share of the budget, but never more than its market allows:

| Market | Most leverage used |
|---|---|
| Forex (OANDA) | 30× |
| Gold, silver and other metals (OANDA) | 20× |
| Futures (MCX, NSE currency) | 10× |
| Crypto perpetuals (Delta Exchange) | 10× (exchanges offer more; the platform stops here) |
| NSE stocks, intraday | 5× |
| NSE stocks for delivery, crypto spot | none |

Your broker may allow less. Leverage scales position size, and research, backtests and paper trading all use the same figure, so the reported drawdowns include it. It does not loosen any limit: sizes still follow the volatility target, shrink as capital protection tightens, and the loss floor, daily loss limit and drawdown halt are measured on the budget, not on the leveraged exposure. Leverage multiplies losses as much as gains. It is mainly useful for gold, where one unit can be larger than a small unlevered budget per position.

### The AI takes the trades, based on the tested strategies

By default the tested strategies take the trades by their own rules. With an AI model configured (see below), choose **the AI** under **Who takes the trades** to let it decide among the strategies' signals, as follows.

1. Research picks the instruments with evidence. For each, up to five of the strategies that passed every test form its panel.
2. The instrument gets an *AI trader* deployment on the `paper-ai` account. On every bar it works out what each panel strategy would hold now: long, short or flat.
3. Every minute (configurable, 30 s to 1 h), the AI receives, for each instrument:
   - what each tested strategy holds, with its out-of-sample record (Sharpe, returns, holdout, win rate, trades, luck-adjusted confidence);
   - the bid, ask and spread;
   - the last 30 one-minute bars, RSI, averages, average true range and the session;
   - its current position;
   - its lessons from past losses (below).

   It answers LONG, SHORT, FLAT or HOLD, with a size, a confidence and a one-sentence reason.
4. Code enforces the limits before any order:
   - Only a direction at least one tested strategy currently holds may be taken. A position whose direction no strategy holds any more is closed automatically.
   - Opening needs 70% confidence. Size is at most the volatility-targeted, unlevered position, scaled down by capital protection.
   - Nothing opens while the market is closed, past the intraday cutoff, after a loss-floor stop or drawdown halt, during the hour after a stop-out, or against a learned rule.
   - Stops and the trailing stop are checked on every bar.
5. Proven AI traders are promoted to armed live accounts under the same rules as strategies. Each live copy follows the same decisions, sized to its own capital.

Without an AI model, or with **the best tested strategy** chosen (the default), each instrument's best strategy trades on its own. The AI then reviews once a minute: in *advise* mode it only comments; in *act* mode it may close, halve or hold back trades it is at least 70% confident about. Either way it can never open or add to a position.

An AI's decisions cannot be backtested, so each one is scored against the price 15 and 60 minutes later, and the AI trader's own P&L is shown next to the strategies' backtested results. Keep it on paper until that record is convincing. A daily call budget (1,500 by default) caps the cost.

### Learning from losing trades

Every autopilot trade, whether opened by the AI or by a strategy, is recorded with the conditions at entry:
- what each tested strategy held and the share that agreed;
- the spread as a share of the one-minute range;
- RSI and minutes to the session cutoff;
- the reason given for the trade.

When a trade closes at a loss, the AI writes a post-mortem. It sees the entry conditions, the exit, how long the trade was held, and the best and worst move during it. It first decides whether the loss was **normal**: every sound strategy loses on a large share of its trades. Otherwise it names the mistake visible at entry (weak agreement, wide spread, too late in the session, chasing a stretched move, against the trend, a news-like spike) and writes a one-line lesson.

- Recent lessons for the instrument, and the rules learned so far, go into every later decision.
- A mistake that repeats **three times within 30 days** becomes a rule the code enforces on new entries. Its limit comes from the losing trades themselves, for example "no entry when the spread is 45% or more of the typical one-minute range". A rule lapses after 30 days without a new case and can be forgotten from the Autopilot page.
- Normal losses never create rules: reacting to every loss would fit the system to noise.

### The AI analyst (NVIDIA Nemotron or Claude)

After each research cycle, the analyst receives the leaderboard and selections: returns, Sharpe, drawdowns, charges, trade counts and the deployments already running. It answers with JSON: a plain-language summary and a list of concerns (overfitting, costs, concentration, regime risk…). The briefing appears on the Autopilot page with severity badges, and each call is logged with its tokens and latency.

The research-cycle analyst does not pick strategies; selection comes from the walk-forward tests. If you tick **Let a high-severity concern veto a strategy**, such a concern removes that strategy from the cycle. The analyst can only take risk away, never add it. If the analyst is unreachable, the cycle carries on without a briefing.

To use NVIDIA's hosted Nemotron (an OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`), set the key in the server's environment. Never put it in code or a file in the repository:

```bash
export NVIDIA_API_KEY=nvapi-...        # from build.nvidia.com
.venv/bin/python -m jdquant serve
```

The request uses `nvidia/nemotron-3-ultra-550b-a55b` with reasoning enabled (`enable_thinking`), a low temperature (0.2) for consistent reviews, and a non-streaming call; the reasoning text is discarded and only the JSON answer is kept. Without `NVIDIA_API_KEY`, Claude is used when `ANTHROPIC_API_KEY` is set. With neither, the page shows the analyst as not configured.

When the strategies trade on their own, research, paper and live run the same strategy code with the same parameters, so what was backtested is what trades. When the AI trades, it chooses among those same tested signals, and its own decisions are measured live. Risk limits and the kill switch apply to the autopilot exactly as to manual trading.

## Strategy lab: type a strategy, the AI backtests it

Open **Strategy lab**, pick an instrument and bar size, and either build the rules in the **visual builder** (indicators, comparisons, stops and targets picked from lists, no AI involved) or describe a strategy in plain words, for example *"buy gold when the 20-hour average crosses above the 50-hour average and RSI is under 70; exit on the opposite cross or a 1% stop"*.

1. **Translate.** The AI turns the text into rules: JSON built from a fixed set of indicators and conditions, never code, so nothing it writes can run arbitrary instructions. It lists every assumption it made and anything it could not express. Invalid rules go back to the AI once for repair. The rules are shown in plain English so you can check them before anything runs.
2. **Backtest.** The rules run on broker history when a broker is connected, otherwise on synthetic data labelled as such. The market's charges, spread, slippage, overnight financing and your leverage apply.
3. **Confidence.** The result comes with:
   - a **score** (0–100): the deflated Sharpe ratio, the probability that the edge is real after allowing for how many variations you have tried on this market, so tweaking a strategy until it looks good lowers its score;
   - the **probability of profit**, from resampling the trades 2,000 times;
   - the autopilot's checks: at least 10 trades, drawdown under 25%, profitable in most of the four periods and in the last one, charges under half the gross profit, and beating buy and hold.

   The grade is **High** (score 90 or more and every check passed), **Medium** (75 or more, at most one check failed) or **Low**. Synthetic data is always Low.
4. **AI review.** The AI explains the result in plain language and suggests what to test next.
5. **Paper trade.** One click deploys the rules on a paper account, where they trade live prices. Real money comes only through the autopilot's promotion and arming rules.

A high score is evidence, not a promise: markets change, and one instrument's past can mislead.

## Paper trading a backtest

On **Backtests**, after a run press **Paper trade this…**. It deploys the same strategy and parameters on a paper account, so you can watch the backtested strategy trade live prices with simulated money before trusting it. The backtest form also chooses the data (broker history or synthetic) and the charges model of the market.

## Live charts

**Markets** (opening on XAU/USD) and **Trading** show a candlestick chart (1m, 5m, 15m, 1h, 1D) for the selected instrument, with your fills marked as arrows. The chart loads broker history when available, then updates with every quote through a server-sent event stream (`/api/v1/market-data/stream`). Each chart is badged as **LIVE**, **SIMULATED** (demo feed) or **OFFLINE**.

## OANDA (XAU/USD and forex)

1. In the OANDA hub, open **Manage API Access** and generate a token. Note your account ID (e.g. `101-001-1234567-001`).
2. **Connections → Add connection → OANDA**. Keep **Practice account** unless you are permitted to trade live. Enter the token and account ID, then **Add and test**.
3. Gold, silver and every currency pair on the account are loaded. Prices stream to the charts and paper accounts, and hourly or minute history feeds the autopilot's research.

Orders are fill-or-kill market orders or limit orders, tagged with the platform's order ID so they can be looked up and cancelled. The adapter follows OANDA's v20 REST API as published in its official `v20-python` SDK. It was verified against a simulated OANDA server, because OANDA's hosts are unreachable from the build environment.

## Brokers

**Connections → Add connection** lists the brokers like TradingView does, filtered by India or Global:

| Broker | Markets | How it signs in |
|---|---|---|
| **Fyers** | NSE stocks and ETFs, MCX commodity futures, NSE currency futures | Sign in with Fyers; with the PIN stored, renews by itself for up to 15 days |
| **Zerodha Kite** | NSE stocks and ETFs | Sign in with Kite once a day (Kite sessions end at 06:00 IST) |
| **Upstox** | NSE stocks and ETFs | Sign in with Upstox once a day (sessions end at 03:30 IST) |
| **Angel One** | NSE stocks and ETFs | Signs in by itself each day with the client code, PIN and TOTP secret |
| **Dhan** | NSE stocks and ETFs, option chains, 20- and 200-level depth | Client ID and a 24-hour access token from web.dhan.co; paste a new one with **Rotate keys** |
| **Kotak Neo** | NSE and BSE stocks, full market depth | Signs in by itself each day with the consumer key, mobile number, client code (UCC), MPIN and TOTP secret |
| **OANDA** | Spot gold and silver, forex | API token and account ID; practice account by default |
| **Delta Exchange India** | Crypto perpetual futures, long or short | API key and secret; testnet by default |
| **Binance** | Crypto spot | API key and secret; testnet by default |
| **Alpaca** | US stocks | API key and secret; paper by default |

For the sign-in brokers (Fyers, Kite, Upstox), set the app's redirect URL to `http://127.0.0.1:8000/api/v1/connections/oauth/callback` (or your `JDQ_PUBLIC_URL`); the sign-in dialog shows the exact URL. Every credential and session token is stored encrypted.

The same NSE stock through several brokers is one instrument (`NSE:RELIANCE-EQ`), so charts, research and positions line up; each broker keeps its own reference for it. Choose Delivery (CNC) or Intraday per connection. Orders carry the platform's order ID as a tag, so the order book can be matched up after a restart. Zerodha, Upstox, Angel One and Dhan have no test environment: real orders happen only when you trade on the account yourself or arm the autopilot. MCX and currency futures are traded through Fyers only.

## Market intelligence

**Market intelligence** follows the instruments on its watchlist (up to 50) and shows what the order book and the tape are doing. It works the same for Indian stocks, MCX, crypto and XAU/USD; with `JDQ_DEMO_FEED=1`, instruments without a broker get a simulated order book, badged SIMULATED everywhere.

- **Market data from several brokers.** Dhan, Fyers and Kotak Neo stream over their WebSockets (Dhan's market feed plus 20- or 200-level depth for up to five instruments, Fyers' data socket, Neo's full depth); other brokers are polled for depth. Every book is normalized (bids, asks, orders per level, last trade, volume, open interest) but each broker's book stays separate: quantities are never added across brokers. The **data quality** view compares brokers and says whether they agree, lag each other (TIMING) or disagree (DISCREPANCY).
- **Order book intelligence.** A level much larger than typical is tracked as a **liquidity wall**: when it appeared, how long it lasted, peak size and orders, how much traded at that price while it was there, and how it ended: **consumed** (traded away), **withdrawn** (cancelled without trades), **reduced**, or **migrated** (reappearing at a nearby price). For example, if ₹263 shows 585,858 shares on the bid and then vanishes with only a few thousand traded, the event says 99% of the displayed quantity was removed without matching trades. It never says who did it or why: market data carries no participant identity, so every such event states that it **does not establish manipulation**.
- **Order flow.** Delta and cumulative delta (CVD), buy/sell aggression, top-of-book imbalance, large trades, sweeps, absorption, exhaustion, and liquidity pulled or stacked near the touch. Trades are inferred from volume changes between book updates (the aggressor from where they printed against the quote), and the page says so.
- **Price analysis.** Session VWAP with ±1/±2 SD bands and reclaims or rejections, the volume profile (POC, value area, high- and low-volume nodes), previous-day and opening-range levels, support and resistance zones, swings with breaks of structure and changes of character on eight timeframes, and a **regime** (trending, range-bound, breakout, high or low volatility, mean reverting) with the evidence and a confidence.
- **Options and futures.** Option chains from Fyers or Dhan (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY): open interest and its change, IV and Greeks (Black-Scholes when the broker sends none), put-call ratio, max pain, the 25-delta skew and the expected move. Futures show long build-up, short build-up, short covering or long unwinding, the basis to spot, and calendar spreads to the other expiries (contango or backwardation).
- **Events.** Everything above becomes a timestamped event in a searchable store (by instrument, category, severity and text; also streamed live). Events are linked into an **event graph** by timing rules, for example *liquidity pulled → sweep → wall withdrawn → breakout*: a sequence, not proof of cause. **Explain** describes an event in plain language: with an AI model configured it uses the model, otherwise the platform's own template.
- **Scanner** across a universe of up to 1,000 instruments: unusual volume for the time of day, big moves, gaps, breakouts and breakdowns, walls, aggressive buying or selling, OI and IV expansion, liquidity withdrawal and news.
- **News and corporate events.** RSS or Atom feeds and manual headlines are matched to instruments; **Market reaction** shows the price from 5 minutes before to 60 minutes after, the volume change and the events around it. Results, dividends, splits, bonus issues and board meetings (added by hand or imported from CSV) can warn about or block automated entries on those days.
- **Recording and replay.** After you acknowledge the data-use notice, the order books and trades of watched instruments are recorded locally (kept 30 days by default). **Replay** plays a recorded session through fresh copies of the engines, and an order-book strategy can be tested on it.

Market data is licensed by the exchanges through your brokers. Recordings stay on your machine for your own analysis: do not redistribute them, and check each broker's API terms.

## Automated trading: bot control

The rule is: **strategies decide by explicit rules, the risk engine can veto any order, only the execution engine sends orders, and the AI advises and explains.** **Bot control** shows it working.

- **Signals.** Every automated order is a signal with its reason codes (for example `VWAP_RECLAIM`, `POSITIVE_DELTA`), confidence, stop and target, followed through to BLOCKED (with the risk check or guard that said no), SUBMITTED, FILLED, CANCELLED or REJECTED.
- **Strategies on order flow.** Three templates use the live features: *order-flow momentum*, *liquidity wall bounce* and *VWAP reclaim*. Every strategy can attach exits: stop, target, trailing stop, break-even, time exit, volatility exit and signal reversal.
- **Pre-trade guards** on top of the risk limits: duplicate orders, maximum open positions, exposure per sector or asset group, a stop required for automated entries, expected slippage for the order's size (from the visible book), corporate events, a live broker that is not signed in or keeps failing (API failure protection), and **margin**: a new position's margin (its value divided by the market's leverage cap; the full value in cash markets) must fit the broker's free funds, fetched every minute. Exits are never blocked.
- **Circuit breakers** trip kill switches by themselves: a stale feed or failed broker login, repeated broker rejections, slippage far above normal, N losing trades in a row (pauses that strategy), and a position mismatch with the broker (stops the account).
- **STOP ALL** triggers a global kill switch that blocks new orders, and optionally cancels working orders or closes every position.
- **Reconciliation** compares expected positions with each live broker every minute; **All positions** lists every account separately, never netting brokers.
- **Trade journal.** Fills are paired into round trips with the reasons in and out and the market at entry (regime, VWAP, flow, walls). Patterns by strategy, reason, instrument, hour, weekday and regime show what works; **Review this trade** writes a post-trade review.
- **Execution quality**: slippage against the price expected at submission, fill ratio and time to fill, by broker, strategy and instrument.

## Strategy pipeline

Strategies are registered with an ID (`STR-001`) and versions (`1.0`, `1.1`, …). Every version moves through gates that the code enforces:

```text
DEVELOPMENT → BACKTEST → VALIDATION → PAPER → APPROVED → LIVE
```

| Gate | Requirement |
|---|---|
| Backtest | Runs on broker history, or on synthetic data labelled *not evidence* |
| Validation | Walk-forward on at least 400 bars of real broker history: most periods profitable, the last one profitable, enough trades |
| Paper | Deployed on a paper account with live prices |
| Approved | A long enough paper record (5 days and 5 trades by default), then a person approves; with several users, not the author |
| Live | Deployed on a live account by a person; the account's limits, guards and breakers apply |

A new version starts again at DEVELOPMENT while the live one keeps trading. **Roll back** stops the live version and redeploys the previous approved one. Strategy-lab results can be added to the pipeline with one click. Nothing the AI writes can skip a gate, and passing them is evidence, not a guarantee.

## AI director

The copilot has read-only tools over these records, so it can answer questions such as *why did the bot take this trade?*, *why was this signal rejected?*, *which strategy is in drawdown?*, *where were the liquidity walls?* and *how much slippage are we paying?* from the signals, journal, execution records, event store and pipeline. These tools cannot place or change anything.

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
| **Autopilot** | Automated multi-asset research with optional leverage, walk-forward backtesting with market-specific charges, luck rejection, volatility-targeted allocation, loss floor and drawdown halts, futures rolling, paper deployment, monitoring, retirement and per-account live promotion, with a decision log and an LLM analyst (see above). |
| **Strategies** | Manual templates (MA crossover, RSI mean reversion, Bollinger reversion, Donchian breakout, ML signal) plus the autopilot strategy (12 signals, long and short) and a cross-sectional rotation strategy. Deployments go through approve → start → pause/resume → stop/flatten → retire. |
| **Backtesting** | Event-driven, no look-ahead, next-bar fills, fees, slippage and financing, a reproducibility hash, and one-click paper trading of a run. The strategy lab backtests strategies typed in plain words and reports a luck-adjusted confidence. |
| **Persistence** | SQLite (WAL, full sync) holding orders, fills, positions, deployments, kill switches, risk state, users, audit and models. On restart the platform starts in RECOVERING mode, restores state, reconciles in-flight orders with venues and only then accepts orders. Failure drops it to SAFE mode. |
| **Security** | scrypt passwords, server-side sessions (HttpOnly cookie + CSRF token, 30-minute idle and 12-hour absolute limits), bearer tokens, scoped API keys (`X-API-Key`), TOTP MFA with recovery codes, step-up MFA for privileged actions, lockout, 12 built-in roles, and a hash-chained audit log you can verify. |
| **Venues** | OANDA (spot gold and forex, v20), Fyers (NSE equities and futures, API v3), Zerodha Kite, Upstox, Angel One, Dhan, Kotak Neo (NSE equities; WebSocket depth from Dhan, Fyers and Neo), Delta Exchange India (crypto perpetuals), Binance Spot and Alpaca adapters: OAuth sign-in (Fyers, Kite, Upstox), TOTP sign-in (Angel One), Fyers unattended renewal with unattended renewal, signed requests, clock-offset correction, rate limiting, error mapping, native order modify, and fill polling. Credentials and tokens are encrypted at rest and keys with withdrawal permission are refused. Fills carry the charges of their market (NSE, MCX, currency, crypto); per-market session calendars cover trading hours and intraday cutoffs. |
| **AI** | A feature store that computes features the same way online and offline; logistic/ridge models trained on a purged time split; evaluation reports with cost-adjusted trading metrics; a model registry (staging → shadow → production gate, rollback, shadow scoring, drift alerts); a portfolio optimizer; and a Claude copilot that can read platform state and propose actions that you confirm before they run. |
| **Web UI** | Dashboard, bot control, trading ticket, markets, market intelligence, strategies, strategy pipeline, live candlestick charts, AI autopilot, strategy lab, backtests with equity chart, risk and kill switches, AI models, connections, users and audit, and account settings (MFA, sessions, API keys). Dark theme by default (light available), works down to phone width, and prompts for MFA step-up in place. |

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
        ▲             └─ live  → broker adapters ◀── poller
   AI autopilot (autopilot/): research → walk-forward backtests → deploy / retire / promote
        │
   Event bus (core/events) ── persistence subscribers ──▶ SQLite journal (persistence/)
        │                                                    ▲
        └── fills ──▶ Position engine (positions/)           └─ recovery on restart
```

| Package | SRS chapters | Contents |
|---|---|---|
| `core` | 15, 82, 89 | Clock abstraction (CON-010), Decimal-only money (CON-022), event bus with critical and isolated subscribers, declarative state machines |
| `marketdata` | 20 | Instrument registry (incl. futures expiry), candle aggregation, reference prices, staleness detection, live one-minute bars and quote streaming, synthetic data, normalized order books and ticks, the market-data hub, the recorder |
| `intelligence` | — | Order-book walls, order flow, VWAP, volume profile, structure, regime, options and futures analytics, events and the event graph, scanner, data quality, news and corporate events, replay |
| `oms` | 21 | Order state machine, validation, idempotency, modify/replace, fill de-duplication, UNKNOWN resolution |
| `risk` | 27 | Risk profiles and limits, projections, reducing-order waivers, kill-switch escalation, pre-trade guards, circuit breakers and reconciliation |
| `execution` | 22.8 | Deterministic simulated venue |
| `positions` | 28 | Cost basis, lots, P&L, position flips |
| `trading` | 19 | Accounts, deployments, kill switches, platform modes, signal log, trade journal, execution quality |
| `strategy` | 24 | Strategy contract, templates (incl. order-flow strategies), exit engine, strategy runner, versioned strategy pipeline |
| `backtest`, `analytics` | 25, 31–32 | Backtester and metric library |
| `persistence` | 51, 83, 92 | SQLite store with migrations, journal, recovery |
| `security` | 39–40, 42 | Passwords, TOTP, sessions, API keys, roles, encrypted secrets, audit chain |
| `connectivity` | 45–46 | Venue adapter contract, OANDA, Fyers, Zerodha Kite, Upstox, Angel One, Dhan, Kotak Neo, Delta Exchange, Binance and Alpaca adapters, WebSocket feeds (Dhan, Fyers, Neo), broker OAuth, account router, connection manager, poller |
| `markets` | — | Charges for NSE cash, MCX, currency futures, crypto, and forex spread and financing; per-market sessions (incl. forex 24/5) and asset groups |
| `autopilot` | 24–25, 57 | Autopilot strategies (incl. the AI trader), the strategy lab, candidate generation, walk-forward research with deflated Sharpe, the controller, the per-minute AI trader and monitor, and lessons from losing trades |
| `ai` | 52–59, 63 | Features, training, model registry, optimizer, prompts, copilot and its tools (incl. the read-only AI director), the autopilot analyst |
| `api` | 80 | `/api/v1` REST resources with problem-details errors; serves the web UI |

## Known gaps

- Charts stream quotes live; the rest of the UI refreshes by polling every 2–5 seconds.
- API keys are random secrets stored only as hashes and sent as a header. The SRS also describes HMAC request signing, which would need the server to keep a recoverable secret; that was not implemented.
- OCO/bracket orders, execution algorithms, NAV and capital flows, reporting, and notifications (Chapters 21.8, 22.6, 23, 33, 37) are not built yet.
- Single node only (T1). There is no clustering or failover.
- Shorting is limited to futures, forex, metals and crypto perpetuals; NSE cash and crypto spot are long-only (shares cannot be shorted overnight). Market-neutral pairs are a natural next step.
- Market making and latency arbitrage (as run by firms like Jane Street) need co-location and exchange-level connectivity, which retail REST APIs cannot provide, so they are not attempted.
- The MCX lots convention follows the Fyers symbol master but was not confirmed against a real account; futures stay off until you opt in per account.
- Crypto and forex are sized from the INR budget. The ₹/USD (and ₹/USDT) rate is set in the settings. Other currencies convert through the dollar at live OANDA prices when available, otherwise at defaults.
- Paper trading charges the spread but not overnight financing (backtests charge both). Live OANDA accounts book financing themselves.
- The trade monitor sends one request per minute while positions are open. With a large reasoning model this can take tens of seconds and many tokens; turn reasoning off or lengthen the interval if needed.
- The NSE holiday list is not bundled; add holidays to the calendar, or cycles simply find no new bars on those days.
- Fyers endpoints follow the official `fyers-apiv3` SDK, OANDA the official `v20-python` SDK, Zerodha `pykiteconnect`, Upstox `upstox-python-sdk`, Angel One `smartapi-python`, Dhan `dhanhq`, Delta Exchange `delta-rest-client`, and the NVIDIA analyst NVIDIA's OpenAI-compatible API. All were verified against simulated servers, because the brokers' hosts and integrate.api.nvidia.com are unreachable from the build environment. Place a small test order on each new broker before arming it.
- MCX and currency futures are supported through Fyers only; the other Indian brokers trade NSE stocks and ETFs.
- The WebSocket feeds and the Kotak Neo adapter follow the brokers' official SDKs (`dhanhq`, `fyers-apiv3`, `kotakneoapi`) and were verified against local servers speaking those wire formats, not against the brokers themselves. Kotak Neo's REST quote format is not published, so its quotes are parsed on a best-effort basis and it supplies no candle history (use another broker for history).
- Order flow is estimated: trades are inferred from volume changes between book updates, so small trades between snapshots merge, and no feed identifies participants. Wall and flow events describe displayed quotes and trades; they are not evidence of anyone's intent.
- The margin guard uses the market's leverage cap as the margin rate; brokers' actual margins (SPAN plus exposure for F&O) differ, so the broker's own check remains the final word. Orders are never re-routed to another broker automatically: if a live broker fails, new entries on that account stop and you decide where to trade.
- Walk-forward validation in the strategy pipeline needs real broker history; synthetic data can only be backtested. Replay backtests fill against the recorded book, which your own orders would have changed.
- Paper trading needs live prices: without a Fyers connection, NSE deployments wait for quotes and research uses synthetic demo data (the page says so).
