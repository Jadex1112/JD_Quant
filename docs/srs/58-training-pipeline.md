# Chapter 58 – Training Pipeline

## 58.1 Purpose

The Training Pipeline Engine (TPE) executes reproducible model training: dataset assembly from the Feature Store, labeling, time-aware splitting, training, hyperparameter optimization, evaluation, and registration of resulting model versions with the Model Manager.

## 58.2 Domain Entities

### 58.2.1 TrainingConfiguration

| Attribute | Description |
|---|---|
| model_name | Target model (57.2.1) |
| feature_set_ref | Feature set version |
| label_definition | e.g. forward return over horizon h, sign of forward return, triple-barrier label, volatility target |
| universe / time_range | Instruments and period |
| split | Method: TIME_SERIES_HOLDOUT, WALK_FORWARD, PURGED_KFOLD, COMBINATORIAL_PURGED; purge and embargo durations |
| algorithm | Algorithm family and hyperparameters (e.g. gradient boosting, linear, random forest, neural network, sequence models, RL agent) |
| hpo | Search space, method (RANDOM, BAYESIAN), trials, objective metric |
| resources | CPU/GPU/memory |
| random_seed | Required for reproducibility |
| sample_weights | Optional (e.g. uniqueness weighting) |

### 58.2.2 TrainingRun

| Attribute | Description |
|---|---|
| configuration snapshot | Immutable |
| status | QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELED |
| metrics | Per split and per fold |
| artifacts | Model artifact, preprocessing pipeline, evaluation plots |
| environment | Runtime, library versions, hardware |
| logs | Training logs and learning curves |

## 58.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-58001 | The system shall assemble training datasets point-in-time correctly from the Feature Store (CON-122), aligning labels so that no label information is available at feature time. | M | T |
| AI-58002 | The system shall support the label definitions of 58.2.1 and user-defined label functions. | M | T |
| AI-58003 | The system shall support time-aware splitting methods with purge and embargo to prevent leakage between train and test sets. | M | T |
| AI-58004 | The system shall train models for the supported algorithm families with pluggable framework adapters. | M | T |
| AI-58005 | The system shall run hyperparameter optimization with the configured search method and trials, in parallel across workers (Chapter 35). | M | T |
| AI-58006 | The system shall compute evaluation metrics appropriate to task type: classification (accuracy, precision, recall, F1, ROC-AUC, log loss, calibration), regression (MAE, RMSE, R², IC), and trading-relevant metrics (strategy backtest of predictions with costs via Chapter 25). | M | T |
| AI-58007 | The system shall generate an evaluation report including metrics, confusion matrices or residual plots, feature importance, stability across folds, and a naïve baseline comparison. | M | T |
| AI-58008 | The system shall record full reproducibility information (configuration, data versions, code version, environment, seed) and produce bit-identical or statistically equivalent results on re-run, documented per algorithm family. | M | T |
| AI-58009 | The system shall register successful runs as model versions in stage NONE with lineage (AI-57001). | M | T |
| AI-58010 | The system shall support scheduled and drift-triggered retraining (AI-57009) using the latest data with the same configuration, producing a candidate version for the promotion workflow. | S | T |
| AI-58011 | The system shall support GPU training where available and fall back to CPU (ASM-062). | M | T |
| AI-58012 | The system shall checkpoint long training runs and resume after failure (FR-35011). | S | T |
| AI-58013 | The system shall support reinforcement learning training in simulated market environments built on the backtesting engine, with episode configuration, reward definition, and evaluation against baselines. | C | T |
| AI-58014 | The system shall stream training progress (epoch, loss, validation metric) to the user in real time. | M | D |

## 58.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-58001 | Given a label with horizon 1 day and a purge of 1 day, then no training sample's label window overlaps any test sample's feature timestamp range. | AI-58003 |
| AC-58002 | Given a completed training run, then a model version exists with lineage to feature set, datasets, and run. | AI-58009 |

---

*End of Chapter 58 – Training Pipeline*
