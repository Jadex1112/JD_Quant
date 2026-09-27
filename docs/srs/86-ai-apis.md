# Chapter 86 – AI APIs

## 86.1 Purpose

This chapter specifies interfaces to AI services: external and self-hosted LLM providers, model serving runtimes, and the AI-related public and internal APIs.

## 86.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-86001 | The Prompt Engine shall integrate LLM providers through a provider-neutral adapter interface supporting: chat/completion with system and user messages, tool/function calling with JSON schemas, streaming responses, token counting, and structured output. | M | T |
| API-86002 | The platform shall support at least one external LLM provider and at least one self-hosted inference option (ASM-064). | M | T |
| API-86003 | LLM adapters shall implement timeouts, retries with backoff for transient errors, and fallback per model policy (AI-53003). | M | T |
| API-86004 | LLM adapter credentials shall be stored in the secret store and scoped per workspace where workspaces use separate provider accounts. | M | T |
| API-86005 | The Inference Pipeline shall load models through runtime adapters for supported frameworks and a framework-neutral exchange format (CON-028). | M | T |
| API-86006 | The internal inference interface shall accept (model name, version or stage, entity keys or feature vector, request id) and return (prediction, model version used, feature staleness flags, latency). | M | T |
| API-86007 | AI Copilot tools shall be defined with JSON schemas, descriptions, permission requirements, and effect classification (52.3.2), registered in a tool registry that plugins may extend (AI_TOOL plugins, 47.2). | M | T |
| API-86008 | All AI API calls shall be logged per AI-53007 and FR-59005. | M | T |

---

*End of Chapter 86 – AI APIs*
