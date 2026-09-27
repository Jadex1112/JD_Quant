# Chapter 79 – User Interface

## 79.1 Purpose

This chapter begins Part E – Interface Requirements and specifies the user interface requirements (identifier format `UI-79NNN`). Visual design, component library, and detailed screen layouts are specified in Volume 8.

## 79.2 Client Platforms

| Client | Description | Pri |
|---|---|---|
| Web application | Primary client, modern evergreen browsers (latest two major versions of Chromium-based, Firefox, Safari) | M |
| Desktop application | Packaged web application with native notifications and multi-window support (13.21) | S |
| Mobile companion | Monitoring, alerts, kill switch (Chapter 2.11 future scope) | C |
| CLI | Command-line client for automation over the public API | S |

## 79.3 Information Architecture

| Area | Primary Screens |
|---|---|
| Home | Role dashboard, alerts summary, status |
| Markets | Watchlists, instrument detail with charts, order book, trades, instrument search |
| Trading | Order ticket, blotter (working orders), fills, positions, accounts, deployments, kill switch panel |
| Strategies | Strategy list, editor (code and visual), versions, promotion status, templates |
| Research | Backtests, optimizations, walk-forward, research projects, datasets, notebooks |
| Portfolio | Portfolios, NAV/performance, holdings, allocation, rebalancing proposals, optimizer |
| Risk | Risk dashboard, profiles and limits, breaches, exceptions, scenarios/stress tests |
| AI | Models, training runs, features, signals, AI Copilot panel (global) |
| Analytics | Dashboards, execution quality, attribution, reports |
| Operations | Health map, monitoring dashboards, jobs, schedules, automation, workflows/approvals, logs, diagnostics, incidents |
| Administration | Users, roles, workspaces, connections, settings, plugins, audit |

## 79.4 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| UI-79001 | The UI shall provide the areas and screens of 79.3, shown or hidden per the user's permissions (FR-39013). | M | D |
| UI-79002 | The UI shall provide a global header with workspace selector, environment indicator (e.g. PRODUCTION/STAGING), LIVE/PAPER context indicator, notification center, AI Copilot toggle, platform status indicator, and kill switch access (FR-19045). | M | D |
| UI-79003 | The UI shall provide a professional charting component with candlesticks, OHLC, line, and area series; overlays of library indicators; drawing tools; multiple timeframes; trade and order markers; and synchronized crosshairs across panels. | M | D |
| UI-79004 | The order ticket shall support all order types and flags of Chapter 21, show pre-trade cost estimate (FR-56003), what-if risk check (FR-27084), available balance, and resulting position, and require confirmation per NFR-72003. | M | D |
| UI-79005 | The blotter, positions, and fills grids shall update in real time (NFR-65006), support sorting, filtering, column configuration, grouping, and export. | M | D |
| UI-79006 | The UI shall support dockable, resizable panels and multi-monitor layouts (desktop), saved as named layouts. | S | D |
| UI-79007 | The strategy editor shall provide syntax highlighting, autocompletion of the context API, inline validation errors, version diff, and one-click backtest. | M | D |
| UI-79008 | The UI shall connect to real-time streams via a single multiplexed streaming connection per client with automatic reconnection and state resynchronization, showing a visible indicator when disconnected or stale. | M | T |
| UI-79009 | The UI shall render all timestamps in the user's timezone (NFR-74004) and all monetary values with currency and instrument precision (NFR-74006). | M | T |
| UI-79010 | The UI shall meet accessibility (Chapter 73) and usability (Chapter 72) requirements. | M | T |
| UI-79011 | The UI shall provide global search across instruments, strategies, deployments, orders (by id), portfolios, reports, and help. | S | D |
| UI-79012 | The UI shall never store credentials or tokens in persistent browser storage accessible to scripts; session tokens shall be held in secure, HTTP-only cookies or equivalent. | M | T |
| UI-79013 | The UI shall display AI-generated content with a distinct AI label (CON-143). | M | D |

---

*End of Chapter 79 – User Interface*
