# Chapter 35 – Task Engine

## 35.1 Purpose

The Task Engine executes asynchronous, long-running, and resource-intensive jobs (CON-102): backtests, optimizations, model training, batch inference, data imports and exports, report generation, and maintenance tasks. It manages queueing, prioritization, resource allocation, retries, progress reporting, cancellation, and result storage across a pool of workers.

## 35.2 Domain Entities

### 35.2.1 Job

| Attribute | Type | Description |
|---|---|---|
| job_type | Enum: BACKTEST, OPTIMIZATION, WALK_FORWARD, MONTE_CARLO, TRAINING, BATCH_INFERENCE, DATA_IMPORT, DATA_EXPORT, REPORT, RECONCILIATION, MAINTENANCE, WORKFLOW_STEP, CUSTOM | Required |
| payload | Structured document | Validated per type |
| priority | Integer 0–9 | Default by type; live-trading-support jobs ≥ 8 (CON-104) |
| queue | String | Named queue (e.g. `research`, `ai`, `data`, `system`) |
| resource_request | (cpu, memory, gpu, disk) | Default by type |
| submitted_by | Principal | Required |
| parent_job_id | Reference | For child jobs (e.g. optimization trials) |
| status | QUEUED, SCHEDULED, RUNNING, SUCCEEDED, FAILED, CANCELING, CANCELED, TIMED_OUT, RETRYING | Lifecycle |
| progress | 0–100 plus message | Updated by worker |
| attempts / max_attempts | Integer | Default max 3 for idempotent types, 1 otherwise |
| timeout | Duration | Default by type |
| result_ref | Reference | Stored output |
| error | Code, message, diagnostics reference | On failure |
| queued_at / started_at / finished_at | Timestamp | Timing |

### 35.2.2 Worker

| Attribute | Description |
|---|---|
| worker_id | Unique identifier |
| capabilities | Job types, GPU availability, resources |
| status | IDLE, BUSY, DRAINING, OFFLINE |
| heartbeat_at | Last heartbeat |

## 35.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-35001 | The system shall accept job submissions from platform modules and authorized users, validate payloads, and return a job identifier immediately. | M | T |
| FR-35002 | The system shall dispatch jobs to workers whose capabilities and free resources satisfy the job's resource request, in priority order and FIFO within a priority. | M | T |
| FR-35003 | The system shall enforce per-user and per-workspace quotas on concurrent jobs and resources (CON-103), queuing jobs beyond quota. | M | T |
| FR-35004 | The system shall apply fair-share scheduling among users within the same priority to prevent starvation. | S | T |
| FR-35005 | The system shall preempt or defer research, training, and batch jobs when live-trading host resources exceed configured thresholds (CON-104). | S | T |
| FR-35006 | The system shall detect worker failure by missing heartbeats (default 30 s) and re-queue its RUNNING jobs according to their retry policy. | M | T |
| FR-35007 | The system shall retry failed idempotent jobs with exponential backoff up to max_attempts. | M | T |
| FR-35008 | The system shall enforce job timeouts, transitioning to TIMED_OUT and releasing resources. | M | T |
| FR-35009 | The system shall support cancellation of QUEUED and RUNNING jobs; running jobs receive a cancellation signal and are force-terminated after a grace period (default 30 s). | M | T |
| FR-35010 | The system shall support parent-child jobs: a parent completes when all children reach a terminal state, and canceling a parent cancels its children. | M | T |
| FR-35011 | The system shall support checkpointing for long jobs (training, optimization) so that a retried job resumes from its last checkpoint. | S | T |
| FR-35012 | The system shall stream job progress and logs to the submitter in real time. | M | T |
| FR-35013 | The system shall store job results with a configurable retention per type and delete expired results unless pinned. | M | T |
| FR-35014 | The system shall provide a job monitor listing jobs with filters by type, status, user, queue, and time, including queue depth and estimated wait time. | M | D |
| FR-35015 | The system shall execute each job with the permissions of the submitting principal. | M | T |
| FR-35016 | The system shall scale worker pools automatically based on queue depth where the deployment supports autoscaling. | C | T |
| FR-35017 | The system shall support job dependencies (job B starts when job A succeeds) used by the Workflow Engine. | M | T |

## 35.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-35001 | Given a worker killed during a RUNNING backtest, then the job is re-queued within 30 s and completes on another worker. | FR-35006, FR-35007 |
| AC-35002 | Given a user quota of 4 concurrent jobs and 6 submissions, then 4 run and 2 remain QUEUED. | FR-35003 |
| AC-35003 | Given cancellation of an optimization with 500 child trials, then all children reach CANCELED or a terminal state and the parent becomes CANCELED. | FR-35009, FR-35010 |

---

*End of Chapter 35 – Task Engine*
