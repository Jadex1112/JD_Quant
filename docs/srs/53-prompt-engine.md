# Chapter 53 – Prompt Engine

## 53.1 Purpose

The Prompt Engine manages all prompts, prompt templates, model routing, context assembly, output parsing, and evaluation for large language model (LLM) usage across JD Quant AI (AI Copilot, Research Assistant, report narratives, strategy drafting). It provides a governed, versioned, provider-neutral layer between platform features and LLM providers.

## 53.2 Domain Entities

### 53.2.1 PromptTemplate

| Attribute | Description |
|---|---|
| key | Unique identifier (e.g. `copilot.system`, `report.daily.narrative`) |
| version | Semantic version; immutable once published |
| content | Template text with typed variables |
| variables | Schema of variables |
| output_format | TEXT, JSON (with schema), CODE |
| model_policy | Preferred model class, fallback list, max tokens, temperature |
| safety_policy | Redaction rules, content filters |
| status | DRAFT, ACTIVE, DEPRECATED |
| evaluation_ref | Latest evaluation results |

### 53.2.2 ModelProvider

| Attribute | Description |
|---|---|
| provider | External API or self-hosted endpoint |
| models | Available models with context window, cost, latency class |
| credential_ref | Secret reference |
| data_policy | Whether data may leave the deployment boundary (CON-144) |
| status | ENABLED, DISABLED, DEGRADED |

## 53.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| AI-53001 | The system shall store all prompts used in production as versioned templates; features shall reference templates by key and never embed prompt text ad hoc. | M | I |
| AI-53002 | The system shall render templates with validated variables and reject rendering when required variables are missing or invalid. | M | T |
| AI-53003 | The system shall route requests to providers per the template's model policy, workspace data policy, availability, and quota, with automatic fallback on provider failure. | M | T |
| AI-53004 | The system shall assemble context within the model's context window by prioritizing and truncating context sections per template rules, recording what was included. | M | T |
| AI-53005 | The system shall validate structured (JSON) outputs against the declared schema, retrying with a repair instruction up to 2 times before returning a structured error. | M | T |
| AI-53006 | The system shall apply redaction rules to rendered prompts before sending to providers whose data policy disallows sensitive data. | M | T |
| AI-53007 | The system shall log every LLM call with template key and version, provider, model, token counts, latency, cost, and outcome (content retention per workspace policy). | M | T |
| AI-53008 | The system shall support offline evaluation of template versions against curated test sets with automated scoring (exact match, schema validity, rubric scoring by evaluator model, human rating) and compare versions before activation. | S | T |
| AI-53009 | The system shall support A/B rollout of template versions by percentage. | C | T |
| AI-53010 | The system shall track token usage and cost per workspace, user, feature, and template, enforcing budgets with alerts at 80% and hard stop at 100% (configurable). | M | T |
| AI-53011 | The system shall cache deterministic responses (temperature 0, identical rendered prompt and model) for a configurable TTL to reduce cost. | S | T |
| AI-53012 | The system shall require approval (AI Engineer role) to activate a new template version for templates used in trading-related features. | M | T |

## 53.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-53001 | Given the primary provider returns errors, then requests fall back to the next provider in the policy and the fallback is logged. | AI-53003 |
| AC-53002 | Given a JSON output that fails schema validation twice and succeeds on the third attempt, then the valid result is returned and retries are logged. | AI-53005 |
| AC-53003 | Given a workspace budget reached, then further LLM calls are refused with a budget error and the administrator is notified. | AI-53010 |

---

*End of Chapter 53 – Prompt Engine*
