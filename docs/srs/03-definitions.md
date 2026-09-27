# Chapter 3 – Definitions

## 3.1 Purpose

This chapter defines the terminology used throughout the JD Quant AI documentation suite.

The purpose of these definitions is to establish a common vocabulary for stakeholders, architects, software engineers, AI engineers, quantitative researchers, testers, DevOps engineers, security engineers, technical writers, and future maintainers.

Unless otherwise specified, every occurrence of a defined term throughout all documentation volumes shall carry the meaning established in this chapter.

These definitions are implementation-independent and remain valid regardless of programming language, deployment environment, database technology, infrastructure provider, or supported exchange.

## 3.2 General Definitions

### Account

A logical representation of a user’s authenticated identity within the JD Quant AI platform. An account may be associated with one or more exchanges, brokers, portfolios, strategies, permissions, API credentials, and system preferences.

### Active Order

An order that has been submitted to a broker or exchange and has not yet reached a terminal state such as Filled, Cancelled, Rejected, or Expired.

### Algorithmic Trading

The automated execution of financial market transactions based on predefined quantitative rules, statistical models, artificial intelligence, machine learning models, or other systematic decision-making processes.

### Allocation

The distribution of available capital, margin, or portfolio value among one or more assets, strategies, or portfolios.

### Asset

A tradable financial instrument recognized by the platform. Assets may include, but are not limited to:

- Equities
- ETFs
- Futures
- Options
- Forex pairs
- Cryptocurrencies
- Commodities
- Bonds
- Digital assets

### Asset Class

A category of financial instruments sharing common characteristics, regulatory treatment, or market behavior.

Examples include:

- Equity
- Fixed Income
- Cryptocurrency
- Commodity
- Forex
- Derivative

### Audit Trail

A complete chronological record of system actions, user activities, configuration changes, AI decisions, trading operations, security events, and administrative actions that supports traceability, accountability, and forensic analysis.

### Authentication

The process of verifying the identity of a user, service, or system before access is granted.

### Authorization

The process of determining whether an authenticated entity has permission to perform a requested operation.

### Availability

The proportion of time during which a system remains operational and capable of performing its intended functions.

## 3.3 Trading Definitions

### Backtesting

The process of evaluating a trading strategy using historical market data to estimate how it would have performed under past market conditions.

### Benchmark

A reference index, portfolio, or performance metric used for comparison against the results of a trading strategy or investment portfolio.

### Bid

The highest price currently offered by a market participant to purchase a financial instrument.

### Ask

The lowest price currently offered by a market participant to sell a financial instrument.

### Bid-Ask Spread

The numerical difference between the best available ask price and the best available bid price.

### Broker

An external financial institution or service that executes trades on behalf of the user.

### Candle

A summarized representation of market activity during a defined time interval, consisting of:

- Open Price
- High Price
- Low Price
- Close Price
- Volume

### Capital

The amount of financial resources available for trading activities.

### Commission

A transaction fee charged by a broker or exchange for executing trades.

### Drawdown

The decline in portfolio value from a historical peak to a subsequent trough.

### Exchange

A marketplace where financial instruments are listed, traded, and priced.

### Execution

The process of converting a trading decision into one or more completed market transactions.

### Fill

The successful execution of all or part of an order.

### Historical Data

Previously recorded market information used for research, backtesting, analytics, and model training.

### Instrument

A uniquely identifiable tradable financial product supported by the platform.

### Leverage

The use of borrowed capital to increase trading exposure relative to available equity.

### Liquidity

The ability to buy or sell an asset with minimal impact on its market price.

### Market Data

Information describing current or historical market activity, including prices, volumes, trades, quotes, and order book updates.

### Market Order

An order requesting immediate execution at the best available market price.

### Limit Order

An order instructing execution only at a specified price or better.

### Stop Order

An order that becomes active when a specified trigger price is reached.

### Order

An electronic instruction requesting the purchase, sale, modification, or cancellation of a financial instrument.

### Order Book

A continuously updated collection of buy and sell orders organized by price level.

### Portfolio

A logical collection of financial positions managed as a single investment entity.

### Position

The quantity of a financial instrument currently held, whether long or short.

### Position Size

The quantity or monetary value allocated to an individual trade.

### Profit and Loss (P&L)

The financial result of trading activity.

P&L may be:

- Realized
- Unrealized
- Daily
- Monthly
- Lifetime

### Quote

The combination of the current best bid and best ask available in the market.

### Risk

The possibility of financial loss arising from market movement, operational failure, liquidity constraints, technology failures, or other uncertainties.

### Slippage

The difference between the expected execution price and the actual execution price.

### Strategy

A formally defined set of rules or models used to generate trading decisions.

### Symbol

The unique identifier assigned to a tradable financial instrument.

### Tick

The smallest individual market update received from an exchange or data provider.

### Tick Data

Time-sequenced individual market updates containing trades, quotes, or order book events.

### Trade

A completed transaction between buyers and sellers.

### Volatility

A statistical measure describing the magnitude of price fluctuations over time.

## 3.4 Artificial Intelligence Definitions

### Artificial Intelligence (AI)

Computer systems capable of performing tasks traditionally requiring human intelligence, including prediction, optimization, classification, reasoning, and decision support.

### Machine Learning (ML)

A subset of artificial intelligence in which algorithms learn patterns from data to improve predictive performance without explicit rule-based programming.

### Deep Learning

A branch of machine learning utilizing neural networks with multiple hidden layers.

### Reinforcement Learning

A machine learning paradigm in which an agent learns optimal actions through interactions with an environment using reward feedback.

### Feature

An individual measurable characteristic used as input to a machine learning model.

### Feature Engineering

The process of creating, transforming, selecting, or combining variables to improve model performance.

### Feature Store

A centralized repository containing reusable engineered features for AI models.

### Dataset

An organized collection of observations used for AI training, validation, testing, or inference.

### Training

The process of optimizing model parameters using historical data.

### Validation

The evaluation of model performance using data not directly involved during training.

### Testing

The final evaluation of a trained model using independent datasets to estimate real-world performance.

### Inference

The process of applying a trained AI model to new data to generate predictions or recommendations.

### Model

A mathematical representation capable of producing predictions, classifications, forecasts, or decisions.

### Model Drift

Performance degradation caused by changes in market conditions or input data distributions.

### Hyperparameter

A configuration value controlling model training rather than being learned from the training data.

### Explainability

The ability to understand and communicate why an AI model produced a specific output.

### Confidence Score

A quantitative measure representing the estimated certainty of a model prediction.

## 3.5 Risk Management Definitions

### Exposure

The total financial value subject to market risk.

### Gross Exposure

The total absolute value of all open positions.

### Net Exposure

The directional market exposure after offsetting long and short positions.

### Margin

Collateral required to maintain leveraged positions.

### Maintenance Margin

The minimum collateral required to avoid forced liquidation.

### Liquidation

Automatic closure of positions when margin requirements are no longer satisfied.

### Value at Risk (VaR)

A statistical estimate of the maximum expected portfolio loss over a specified period at a defined confidence level.

### Stress Test

A simulation evaluating portfolio performance under extreme market conditions.

### Risk Limit

A predefined threshold beyond which trading activity becomes restricted or prohibited.

### Kill Switch

A safety mechanism capable of immediately stopping one or more trading activities in response to predefined conditions.

## 3.6 System Architecture Definitions

### Component

A modular software unit responsible for performing a specific set of functions within the system.

### Module

A logical grouping of related software components.

### Service

A deployable software unit exposing defined functionality through interfaces or APIs.

### Interface

A formally defined communication boundary between software components.

### API

An Application Programming Interface providing standardized communication between software systems.

### Adapter

A software component responsible for translating data or communication protocols between incompatible systems.

### Event

A significant occurrence within the system that may trigger one or more actions.

### Event Bus

A messaging mechanism enabling asynchronous communication between components through events.

### Workflow

A predefined sequence of coordinated operations executed to achieve a business objective.

### Pipeline

An ordered series of processing stages through which data flows.

### Scheduler

A system responsible for initiating jobs based on time, dependencies, or events.

### Job

A discrete unit of work executed by the system.

### Task

A smaller executable activity that contributes to a larger job or workflow.

## 3.7 Data Definitions

### Data Lake

A centralized repository capable of storing structured, semi-structured, and unstructured data in its original format.

### Data Warehouse

A structured repository optimized for analytics, reporting, and business intelligence.

### Time Series

A sequence of observations ordered chronologically.

### Metadata

Information describing other data, including origin, ownership, timestamps, quality metrics, schema versions, and processing history.

### Schema

The formal structure defining data organization and validation rules.

### Data Integrity

The assurance that stored data remains accurate, complete, consistent, and unaltered except through authorized operations.

### Data Retention

Policies governing the duration for which information is preserved before archival or deletion.

## 3.8 Security Definitions

### Encryption

The transformation of information into a secure format requiring authorized decryption.

### Secret

Sensitive information such as passwords, API keys, private keys, or tokens requiring secure storage.

### Token

A digitally generated credential used for authentication or authorization.

### Session

A temporary authenticated interaction between a user and the platform.

### Multi-Factor Authentication (MFA)

An authentication mechanism requiring two or more independent verification factors.

### Least Privilege

A security principle granting only the minimum permissions necessary to perform required functions.

### Role-Based Access Control (RBAC)

An authorization model in which permissions are assigned to roles rather than directly to users.

## 3.9 Operational Definitions

### Availability Zone

An isolated deployment environment designed to improve resilience and fault tolerance.

### Backup

A protected copy of data created for recovery purposes.

### Disaster Recovery

The coordinated process of restoring system functionality after catastrophic failure.

### Failover

Automatic or manual transition from a failed system component to a redundant component.

### Health Check

A diagnostic procedure used to determine whether a component is operating correctly.

### Monitoring

Continuous observation of system health, performance, availability, and operational status.

### Observability

The capability to understand internal system behavior through metrics, logs, traces, and events.

### Recovery Point Objective (RPO)

The maximum acceptable amount of data loss following a failure.

### Recovery Time Objective (RTO)

The maximum acceptable duration required to restore service after a disruption.

## 3.10 Documentation Definitions

### Requirement

A documented capability or constraint that the system shall satisfy.

### Functional Requirement

A requirement describing what the system must do.

### Non-Functional Requirement

A requirement describing how well the system must perform or the constraints under which it must operate.

### Acceptance Criteria

Measurable conditions that must be satisfied before a requirement is considered complete.

### Traceability

The ability to track each requirement through design, implementation, testing, deployment, and maintenance.

### Version

A uniquely identifiable release of a document, configuration, software component, or AI model.

## 3.11 Definition Governance

The terminology defined in this chapter shall serve as the authoritative glossary for the entire JD Quant AI documentation suite.

Any new technical term introduced in future chapters or subsequent documentation volumes that is not defined herein shall be added through the project’s formal documentation change-control process. Definitions shall remain version-controlled, uniquely identifiable, and backward-compatible wherever practical to ensure consistent interpretation across all engineering, testing, operational, and governance activities.

---

*End of Chapter 3 – Definitions*
