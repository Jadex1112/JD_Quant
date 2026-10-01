# Chapter 57 – Model Manager

## 57.1 Purpose

The Model Management Engine (MME) is the registry and governance layer for all machine learning models in JD Quant AI. It stores model artifacts and metadata, tracks lineage and versions, manages lifecycle stages and promotion (CON-142), monitors production model health including drift, and coordinates rollback.

## 57.2 Domain Entities

### 57.2.1 Model

| Attribute | Description |
|---|---|
| name | Unique within workspace |
| task_type | CLASSIFICATION, REGRESSION, RANKING, FORECASTING, ANOMALY_DETECTION, REINFORCEMENT_LEARNING, CLUSTERING, LLM_ADAPTER |
| use_case | SIGNAL, REGIME, RISK, EXECUTION, ALLOCATION, RESEARCH |
| owner | Principal |
| trading_critical | Boolean: used in trading decisions (stricter governance) |

### 57.2.2 ModelVersion

| Attribute | Description |
|---|---|
| version | Monotonic integer or semantic version; immutable |
| artifact_ref | Content-addressed artifact (CON-028) |
| framework / runtime | Framework and version required to load |
| input_schema / output_schema | Feature names, types, shapes; output semantics |
| feature_set_ref | Feature Store feature set version (Chapter 63) |
| training_run_ref | Training job and configuration (Chapter 58) |
| dataset_versions | Training/validation/test dataset versions |
| metrics | Evaluation metrics per split |
| evaluation_report_ref | Report (required for promotion) |
| stage | NONE, STAGING, SHADOW, PRODUCTION, ARCHIVED |
| approvals | Approval records |
| model_card | Intended use, limitations, training data summary, ethical and risk considerations |

## 57.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-57001 | The system shall register model versions with the metadata of 57.2.2, rejecting registration without artifact, schemas, and lineage references. | M | T |
| AI-57002 | The system shall store artifacts immutably with checksums and verify checksums on load. | M | T |
| AI-57003 | The system shall manage stage transitions NONE → STAGING → SHADOW → PRODUCTION → ARCHIVED through the Model Promotion workflow (49.3); only one version per model and deployment target may be PRODUCTION. | M | T |
| AI-57004 | The system shall require for PRODUCTION: an evaluation report, a model card, out-of-sample metrics meeting configured thresholds, approval (CON-142), and a designated rollback version. | M | T |
| AI-57005 | The system shall support SHADOW stage in which the model receives live inputs and its predictions are recorded but not delivered to consumers, enabling live comparison against the PRODUCTION version. | M | T |
| AI-57006 | The system shall provide one-action rollback to the designated rollback version, taking effect in the Inference Pipeline within 30 s. | M | T |
| AI-57007 | The system shall compare versions side by side: metrics, feature importance, prediction distributions, and shadow performance. | M | D |
| AI-57008 | The system shall monitor PRODUCTION models for: input feature drift (Population Stability Index, Kolmogorov-Smirnov), prediction drift, and realized performance (when labels become available) over rolling windows. | M | T |
| AI-57009 | The system shall raise drift alerts when PSI exceeds configurable thresholds (default warning 0.1, alert 0.25) or performance falls below thresholds, and optionally trigger retraining workflows (Chapter 48). | M | T |
| AI-57010 | The system shall prevent deletion of model versions referenced by deployed strategies, retained signals, or inference records within retention. | M | T |
| AI-57011 | The system shall display lineage graphs linking model versions to datasets, feature sets, training runs, strategies, and deployments using them. | S | D |
| AI-57012 | The system shall provide explainability artifacts per version: global feature importance and support for per-prediction explanations (e.g. SHAP values) where the model type supports it. | S | T |

## 57.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-57001 | Given a model version without an evaluation report, then promotion to PRODUCTION is rejected. | AI-57004 |
| AC-57002 | Given a rollback command, then inference requests use the rollback version within 30 s. | AI-57006 |
| AC-57003 | Given an input feature with PSI 0.3 over the monitoring window, then a drift alert is raised. | AI-57008, AI-57009 |

---

*End of Chapter 57 – Model Manager*
