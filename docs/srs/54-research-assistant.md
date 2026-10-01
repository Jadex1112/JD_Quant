# Chapter 54 – Research Assistant

## 54.1 Purpose

The Research Assistant, part of the AI Research Engine (ARE), supports quantitative researchers in generating, testing, and documenting trading hypotheses. Unlike the conversational AI Copilot (Chapter 52), the Research Assistant runs structured, multi-step research tasks — hypothesis generation, data exploration, feature ideation, backtest orchestration, and results interpretation — producing reproducible research notebooks and reports.

## 54.2 Domain Entities

### 54.2.1 ResearchProject

| Attribute | Description |
|---|---|
| title, objective | Research question |
| owner, collaborators | Principals |
| datasets | Dataset versions used |
| hypotheses | List of Hypothesis |
| experiments | Linked backtests, optimizations, training runs |
| notebook | Ordered research log entries (text, charts, tables, code cells, AI summaries) |
| status | ACTIVE, CONCLUDED, ARCHIVED |

### 54.2.2 Hypothesis

| Attribute | Description |
|---|---|
| statement | e.g. "Funding rate extremes predict 8h mean reversion in BTC perpetual" |
| rationale | Economic intuition |
| test_plan | Data, method, metrics, significance level |
| origin | USER or AI_SUGGESTED |
| result | UNTESTED, SUPPORTED, REJECTED, INCONCLUSIVE with evidence links |

## 54.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-54001 | The system shall allow users to create research projects with objectives, datasets, and collaborators. | M | T |
| AI-54002 | The system shall generate candidate hypotheses from a research objective and available data, each with rationale and a proposed test plan, labeled AI_SUGGESTED. | S | T |
| AI-54003 | The system shall perform exploratory data analysis on selected datasets on request: summary statistics, distributions, missing data, autocorrelation, stationarity tests (ADF, KPSS), and correlation with forward returns, presented as notebook entries. | M | T |
| AI-54004 | The system shall propose features for a hypothesis and, upon user approval, register them as feature definitions in the Feature Store (Chapter 63). | S | T |
| AI-54005 | The system shall orchestrate experiments from a test plan by submitting backtests or training jobs on user confirmation, linking results to the hypothesis. | S | T |
| AI-54006 | The system shall interpret experiment results: statistical significance of performance (e.g. t-statistic of mean return, bootstrap confidence intervals), multiple-testing adjustments, overfitting warnings (e.g. large in-sample/out-of-sample gap, low trade count), and regime dependence. | M | T |
| AI-54007 | The system shall flag common research pitfalls automatically: look-ahead bias indicators, survivorship bias in universes, data snooping (trials count), unrealistic cost assumptions. | M | T |
| AI-54008 | The system shall produce a research report summarizing objective, hypotheses, methods, results, and conclusions with links to reproducible runs; AI-authored sections labeled (CON-143). | M | T |
| AI-54009 | The system shall retrieve and summarize external research content (papers, articles) supplied by the user, with citations, treating content as untrusted data (AI-52014). | C | T |
| AI-54010 | The system shall record every AI step (prompt template, model, inputs, outputs) in the project notebook for reproducibility. | M | T |
| AI-54011 | The system shall allow a concluded project to spawn a strategy draft in RESEARCH promotion stage with lineage to the project. | S | T |

## 54.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-54001 | Given an optimization with 1,000 trials and a best Sharpe of 2.0 in-sample and 0.3 out-of-sample, then the assistant flags probable overfitting. | AI-54006, AI-54007 |
| AC-54002 | Given a universe built from currently listed instruments only for a 10-year backtest, then a survivorship-bias warning is shown. | AI-54007 |

---

*End of Chapter 54 – Research Assistant*
