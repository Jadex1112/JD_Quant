# JD Quant AI — Volume 2: Master Software Requirements Specification (SRS)

Version 1.0 · IEEE 29148-aligned · Implementation-independent

This directory holds the complete Master SRS for **JD Quant AI**, an institutional-grade, AI-driven quantitative trading platform. Chapters 1–14 were converted from the original Word draft (`source/JD_Quant_AI_SRS_ch01-14.docx`); chapters 15–100 complete the specification following the original table of contents.

## Reading Guide

| If you are… | Start with |
|---|---|
| New to the project | Chapters 1, 2, 7, 12 |
| Implementing a module | [Part C conventions](part-c-introduction.md), then the module chapter, then Parts E–F |
| Designing architecture (Volume 3) | Chapters 10, 15, 17, Part D, Part E, Part F |
| Testing | Chapter 18, module acceptance criteria, Part G |

## Requirement Identifiers

| Prefix | Meaning | Count |
|---|---|---|
| FR- | Functional requirement | 746 |
| AI- | Artificial intelligence requirement | 87 |
| NFR- | Non-functional requirement | 115 |
| API- | Interface / API requirement | 61 |
| OPS- | Operations requirement | 46 |
| SEC- | Security requirement | 21 |
| DB- | Database / storage requirement | 20 |
| UI- | User interface requirement | 13 |
| **Total** | | **1,109** |

In addition, the specification contains 133 module acceptance criteria (`AC-`), 12 platform acceptance scenarios (`PAC-`), constraints (`CON-`), assumptions (`ASM-`), dependencies (`DEP-`), business rules (`BR-`), and system invariants (`INV-`). Identifiers encode the chapter number: `FR-21014` is requirement 14 of Chapter 21 (see [Part C conventions](part-c-introduction.md)).

## Table of Contents

### Part A – Introduction

- [Chapter 1 – Purpose](01-purpose.md) *(original draft)*
- [Chapter 2 – Scope](02-scope.md) *(original draft)*
- [Chapter 3 – Definitions](03-definitions.md) *(original draft)*
- [Chapter 4 – Acronyms & Abbreviations](04-acronyms-abbreviations.md) *(original draft)*
- [Chapter 5 – References](05-references.md) *(original draft)*
- [Chapter 6 – Intended Audience](06-intended-audience.md) *(original draft)*
- [Chapter 7 – Product Perspective](07-product-perspective.md) *(original draft)*
- [Chapter 8 – Product Objectives](08-product-objectives.md) *(original draft)*
- [Chapter 9 – Business Goals](09-business-goals.md) *(original draft)*
- [Chapter 10 – Design Principles](10-design-principles.md) *(original draft)*

### Part B – Overall Description

- [Chapter 11 – Product Context](11-product-context.md) *(original draft)*
- [Chapter 12 – Major Capabilities](12-major-capabilities.md) *(original draft)*
- [Chapter 13 – User Classes and Personas](13-user-classes-and-personas.md) *(original draft)*
- [Chapter 14 – Operating Environment](14-operating-environment.md) *(original draft)*
- [Chapter 15 – Design and Implementation Constraints](15-design-and-implementation-constraints.md)
- [Chapter 16 – Assumptions](16-assumptions.md)
- [Chapter 17 – Dependencies](17-dependencies.md)
- [Chapter 18 – Quality Attributes](18-quality-attributes.md)

### Part C – Functional Requirements

- [Part C introduction and conventions](part-c-introduction.md)
- [Chapter 19 – Trading Engine](19-trading-engine.md)
- [Chapter 20 – Market Data Engine](20-market-data-engine.md)
- [Chapter 21 – Order Management](21-order-management.md)
- [Chapter 22 – Execution Engine](22-execution-engine.md)
- [Chapter 23 – Portfolio Engine](23-portfolio-engine.md)
- [Chapter 24 – Strategy Framework](24-strategy-framework.md)
- [Chapter 25 – Backtesting](25-backtesting.md)
- [Chapter 26 – Paper Trading](26-paper-trading.md)
- [Chapter 27 – Risk Engine](27-risk-engine.md)
- [Chapter 28 – Position Engine](28-position-engine.md)
- [Chapter 29 – Inventory Engine](29-inventory-engine.md)
- [Chapter 30 – Signal Engine](30-signal-engine.md)
- [Chapter 31 – Analytics](31-analytics.md)
- [Chapter 32 – Performance Measurement](32-performance-measurement.md)
- [Chapter 33 – Notification](33-notification.md)
- [Chapter 34 – Scheduling](34-scheduling.md)
- [Chapter 35 – Task Engine](35-task-engine.md)
- [Chapter 36 – Logging](36-logging.md)
- [Chapter 37 – Reporting](37-reporting.md)
- [Chapter 38 – Audit](38-audit.md)
- [Chapter 39 – Permissions](39-permissions.md)
- [Chapter 40 – User Management](40-user-management.md)
- [Chapter 41 – Settings](41-settings.md)
- [Chapter 42 – Configuration](42-configuration.md)
- [Chapter 43 – Data Import](43-data-import.md)
- [Chapter 44 – Data Export](44-data-export.md)
- [Chapter 45 – Exchange Connectivity](45-exchange-connectivity.md)
- [Chapter 46 – Broker Connectivity](46-broker-connectivity.md)
- [Chapter 47 – Plugin System](47-plugin-system.md)
- [Chapter 48 – Automation](48-automation.md)
- [Chapter 49 – Workflow Engine](49-workflow-engine.md)
- [Chapter 50 – Scenario Simulator](50-scenario-simulator.md)
- [Chapter 51 – Recovery](51-recovery.md)
- [Chapter 52 – AI Copilot](52-ai-copilot.md)
- [Chapter 53 – Prompt Engine](53-prompt-engine.md)
- [Chapter 54 – Research Assistant](54-research-assistant.md)
- [Chapter 55 – Portfolio Optimizer](55-portfolio-optimizer.md)
- [Chapter 56 – Execution Optimizer](56-execution-optimizer.md)
- [Chapter 57 – Model Manager](57-model-manager.md)
- [Chapter 58 – Training Pipeline](58-training-pipeline.md)
- [Chapter 59 – Inference Pipeline](59-inference-pipeline.md)
- [Chapter 60 – Monitoring](60-monitoring.md)
- [Chapter 61 – Diagnostics](61-diagnostics.md)
- [Chapter 62 – Health Engine](62-health-engine.md)
- [Chapter 63 – Feature Store](63-feature-store.md)

### Part D – Non-Functional Requirements

- [Chapter 64 – Performance](64-performance.md)
- [Chapter 65 – Latency](65-latency.md)
- [Chapter 66 – Availability](66-availability.md)
- [Chapter 67 – Scalability](67-scalability.md)
- [Chapter 68 – Reliability](68-reliability.md)
- [Chapter 69 – Maintainability](69-maintainability.md)
- [Chapter 70 – Security](70-security.md)
- [Chapter 71 – Compliance](71-compliance.md)
- [Chapter 72 – Usability](72-usability.md)
- [Chapter 73 – Accessibility](73-accessibility.md)
- [Chapter 74 – Internationalization](74-internationalization.md)
- [Chapter 75 – Backup](75-backup.md)
- [Chapter 76 – Recovery (Non-Functional)](76-recovery-nfr.md)
- [Chapter 77 – Disaster Recovery](77-disaster-recovery.md)
- [Chapter 78 – Observability](78-observability.md)

### Part E – Interface Requirements

- [Chapter 79 – User Interface](79-user-interface.md)
- [Chapter 80 – REST APIs](80-rest-apis.md)
- [Chapter 81 – Internal APIs](81-internal-apis.md)
- [Chapter 82 – Message Bus](82-message-bus.md)
- [Chapter 83 – Database](83-database.md)
- [Chapter 84 – Exchange APIs](84-exchange-apis.md)
- [Chapter 85 – Broker APIs](85-broker-apis.md)
- [Chapter 86 – AI APIs](86-ai-apis.md)
- [Chapter 87 – Storage](87-storage.md)
- [Chapter 88 – External Services](88-external-services.md)

### Part F – System Behaviour

- [Chapter 89 – State Machines](89-state-machines.md)
- [Chapter 90 – Sequence Behaviour](90-sequence-behaviour.md)
- [Chapter 91 – Failure Behaviour](91-failure-behaviour.md)
- [Chapter 92 – Recovery Behaviour](92-recovery-behaviour.md)
- [Chapter 93 – Concurrency Behaviour](93-concurrency-behaviour.md)
- [Chapter 94 – Synchronization](94-synchronization.md)
- [Chapter 95 – Consistency](95-consistency.md)

### Part G – Acceptance Criteria

- [Chapter 96 – Requirement Traceability](96-requirement-traceability.md)
- [Chapter 97 – Verification](97-verification.md)
- [Chapter 98 – Validation](98-validation.md)
- [Chapter 99 – Testing Criteria](99-testing-criteria.md)
- [Chapter 100 – Acceptance Criteria](100-acceptance-criteria.md)
