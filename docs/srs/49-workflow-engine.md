# Chapter 49 – Workflow Engine

## 49.1 Purpose

The Workflow Engine (WFE) orchestrates multi-step processes composed of automated tasks and human approvals. It implements governance workflows (strategy promotion, live deployment approval, risk exceptions, model promotion, access requests) and user-defined pipelines (e.g. nightly data import → feature computation → retraining → evaluation → promotion proposal).

## 49.2 Domain Entities

### 49.2.1 WorkflowDefinition

| Attribute | Description |
|---|---|
| name, version | Identity; definitions are versioned and immutable once published |
| steps | Directed acyclic graph of steps |
| inputs | Parameter schema |
| timeout | Overall timeout |
| owner | Principal |

### 49.2.2 Step Types

| Type | Behavior |
|---|---|
| TASK | Submit a Task Engine job and wait for completion |
| ACTION | Execute an automation action (48.3) |
| APPROVAL | Wait for approval from designated approvers (users, roles; quorum N of M) with timeout |
| CONDITION | Branch on an expression over prior step outputs |
| PARALLEL | Execute branches concurrently; join on all or any |
| WAIT | Wait for duration or event |
| SUB_WORKFLOW | Invoke another workflow |
| NOTIFY | Send notification |

### 49.2.3 WorkflowInstance

| Attribute | Description |
|---|---|
| definition_id / version | Definition executed |
| inputs | Parameter values |
| status | RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED, CANCELED, TIMED_OUT |
| step_states | Per step: PENDING, RUNNING, SUCCEEDED, FAILED, SKIPPED, WAITING |
| outputs | Per step outputs |
| started_by / started_at / finished_at | Metadata |

## 49.3 Built-in Governance Workflows

| Workflow | Steps (summary) |
|---|---|
| Strategy Promotion to Live | Verify evidence (FR-24041/42) → risk review APPROVAL (Risk Manager) → optional compliance APPROVAL → set LIVE_APPROVED |
| Live Deployment Approval | Validate deployment → check automation policy → APPROVAL (non-author, CON-203) → mark READY |
| Risk Exception | Request → APPROVAL (Risk Manager, not requester) → apply exception → WAIT until expiry → revert → NOTIFY |
| Model Promotion | Evaluation report TASK → CONDITION metrics ≥ thresholds → APPROVAL (AI Engineer + Risk Manager for trading models) → deploy to shadow → WAIT shadow period → APPROVAL → promote |
| Access Request | Request role → APPROVAL (role owner / admin) → grant with expiry → NOTIFY |
| Kill Switch Release | Reason → APPROVAL (Risk Manager) with step-up MFA → release |

## 49.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-49001 | The system shall provide the built-in governance workflows of 49.3, customizable by administrators within constraints that preserve mandatory approvals (CON-203). | M | T |
| FR-49002 | The system shall allow users to define custom workflows using the step types of 49.2.2 through a visual editor and a declarative definition format. | S | T |
| FR-49003 | The system shall validate workflow definitions: acyclic graph, reachable steps, valid references, and permissions of the executing principal. | M | T |
| FR-49004 | The system shall persist workflow state durably after each step so that instances survive platform restarts and resume from the last completed step. | M | T |
| FR-49005 | The system shall present pending approvals to eligible approvers in an approvals inbox with context (the entity under approval, evidence, requester, diff) and support approve, reject with reason, and request changes. | M | D |
| FR-49006 | The system shall enforce approval quorum and exclusion of the requester/author from approving. | M | T |
| FR-49007 | The system shall apply approval timeouts with configured outcome (reject or escalate). | M | T |
| FR-49008 | The system shall support retry of failed TASK/ACTION steps with configured policy and manual retry by the owner. | M | T |
| FR-49009 | The system shall provide a workflow instance view showing the graph with step states, timings, outputs, and logs. | M | D |
| FR-49010 | The system shall allow workflows to be triggered manually, by schedule (Chapter 34), by automation (Chapter 48), or by API. | M | T |
| FR-49011 | The system shall audit all approval decisions with approver, decision, reason, and timestamp. | M | T |
| FR-49012 | The system shall pin running instances to the definition version with which they started. | M | T |

## 49.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-49001 | Given a workflow waiting for approval and a platform restart, then after restart the instance remains WAITING_APPROVAL and approval continues it. | FR-49004 |
| AC-49002 | Given a quorum of 2 approvers, when one approves, then the workflow continues waiting. | FR-49006 |

---

*End of Chapter 49 – Workflow Engine*
