"""The autopilot's analyst: an LLM that reviews each research cycle before the autopilot acts.

It returns a plain-language briefing and a list of concerns about the strategies selected for capital.
It never chooses trades: selection comes from the walk-forward research. When the owner allows it, a
high-severity concern vetoes a strategy — the analyst can only take risk away, never add it.

The same chat model also serves the trade monitor (`jdquant.ai.monitor`), which reviews open positions
and proposed entries every minute.

Providers:
- Claude, through the Anthropic SDK client the copilot already manages.
- Any OpenAI-compatible chat-completions endpoint (for example NVIDIA NIM at
  https://integrate.api.nvidia.com/v1 with a Nemotron model), called over plain HTTPS.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from jdquant.ai.prompts import AUTOPILOT_REVIEW, LlmCallLog, PromptTemplate

log = logging.getLogger(__name__)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
SEVERITIES = ("low", "medium", "high")


@dataclass
class Concern:
    instrument_id: str
    concern: str
    severity: str = "medium"


@dataclass
class AnalystReport:
    provider: str
    model: str
    summary: str
    concerns: list[Concern] = field(default_factory=list)


Analyst = Callable[[dict[str, Any]], AnalystReport | None]


def parse_report(text: str, provider: str, model: str) -> AnalystReport | None:
    """Read the model's JSON answer, tolerating reasoning tags and prose around it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match is None:
        return AnalystReport(provider, model, text) if text else None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return AnalystReport(provider, model, text)
    concerns = []
    for item in data.get("concerns") or []:
        if not isinstance(item, dict) or not item.get("concern"):
            continue
        severity = str(item.get("severity", "medium")).lower()
        concerns.append(
            Concern(
                str(item.get("instrument_id") or item.get("instrument") or ""),
                str(item["concern"])[:500],
                severity if severity in SEVERITIES else "medium",
            )
        )
    return AnalystReport(provider, model, str(data.get("summary") or "").strip(), concerns)


class ChatModel:
    """One LLM endpoint, called with a prompt template and a JSON payload; returns the answer text.

    Failures (network, HTTP errors, timeouts) return None and are logged, never raised: the autopilot
    must keep working when the model is unavailable.
    """

    provider: str = ""
    model: str = ""

    def ask(
        self,
        template: PromptTemplate,
        payload: dict[str, Any],
        *,
        max_tokens: int = 16384,
        timeout: float | None = None,
        thinking: bool = True,
    ) -> str | None:
        raise NotImplementedError


class OpenAICompatibleChat(ChatModel):
    """Any chat-completions endpoint, e.g. NVIDIA NIM (https://integrate.api.nvidia.com/v1)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        calls: LlmCallLog | None = None,
        http: httpx.Client | None = None,
        provider: str = "NVIDIA",
        timeout: float = 120.0,
    ):
        self.provider, self.model = provider, model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key, self._calls, self._timeout = api_key, calls, timeout
        self._http = http or httpx.Client(timeout=timeout)

    def ask(self, template, payload, *, max_tokens=16384, timeout=None, thinking=True):
        started = time.monotonic()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": template.text},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            # Analysis wants consistency, so a low temperature; the model still reasons before answering.
            "temperature": 0.2,
            "top_p": 0.95,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        try:
            response = self._http.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout or self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("%s model unavailable: %s", self.provider, exc)
            _record(self._calls, template, self.model, 0, 0, started, type(exc).__name__)
            return None
        usage = data.get("usage") or {}
        _record(
            self._calls,
            template,
            self.model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            started,
            "ok",
        )
        return message.get("content") or ""


class ClaudeChat(ChatModel):
    """Claude through the Anthropic SDK client the copilot already manages."""

    provider = "Claude"

    def __init__(self, copilot: Any):
        self._copilot = copilot
        self.model = copilot.model

    def ask(self, template, payload, *, max_tokens=4000, timeout=None, thinking=True):
        from jdquant.ai.copilot import FALLBACK_BETA

        started = time.monotonic()
        try:
            client = self._copilot._ensure_client()
            response = client.beta.messages.create(
                model=self._copilot.model,
                max_tokens=min(max_tokens, 4000),
                system=template.text,
                messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
                **({"timeout": timeout} if timeout else {}),
            )
        except Exception as exc:
            log.info("Claude unavailable: %s", exc)
            return None
        usage = getattr(response, "usage", None)
        _record(
            self._copilot.calls,
            template,
            self._copilot.model,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            started,
            getattr(response, "stop_reason", None) or "unknown",
        )
        return "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")


def analyst_for(chat: ChatModel) -> Analyst:
    """The research-cycle reviewer built on a chat model."""

    def review(briefing: dict[str, Any]) -> AnalystReport | None:
        text = chat.ask(AUTOPILOT_REVIEW, briefing)
        return None if text is None else parse_report(text, chat.provider, chat.model)

    review.chat = chat  # type: ignore[attr-defined]  # shared with the trade monitor
    return review


def openai_compatible_analyst(
    *,
    base_url: str,
    api_key: str,
    model: str,
    calls: LlmCallLog | None = None,
    http: httpx.Client | None = None,
    provider: str = "NVIDIA",
    timeout: float = 120.0,
) -> Analyst:
    return analyst_for(
        OpenAICompatibleChat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            calls=calls,
            http=http,
            provider=provider,
            timeout=timeout,
        )
    )


def claude_analyst(copilot: Any) -> Analyst:
    return analyst_for(ClaudeChat(copilot))


def chat_from_env(copilot: Any, calls: LlmCallLog | None = None) -> ChatModel | None:
    """JDQ_ANALYST: auto (NVIDIA if NVIDIA_API_KEY is set, else Claude if set up), nvidia, claude, none."""
    choice = os.environ.get("JDQ_ANALYST", "auto").lower()
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("JDQ_ANALYST_API_KEY")
    if choice == "none":
        return None
    if choice in ("nvidia", "openai") or (choice == "auto" and key):
        if not key:
            log.warning("JDQ_ANALYST=%s but no NVIDIA_API_KEY is set; the analyst is off", choice)
            return None
        return OpenAICompatibleChat(
            base_url=os.environ.get("JDQ_ANALYST_BASE_URL", NVIDIA_BASE_URL),
            api_key=key,
            model=os.environ.get("JDQ_ANALYST_MODEL", NVIDIA_DEFAULT_MODEL),
            calls=calls,
            provider="NVIDIA" if choice != "openai" else "OpenAI-compatible",
        )
    if choice == "auto" and not _claude_configured(copilot):
        return None  # no provider configured: show "not configured" rather than failing every cycle
    return ClaudeChat(copilot)


def analyst_from_env(copilot: Any, calls: LlmCallLog | None = None) -> Analyst | None:
    chat = chat_from_env(copilot, calls)
    return analyst_for(chat) if chat is not None else None


def _claude_configured(copilot: Any) -> bool:
    from jdquant.ai.copilot import _default_client

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    # A client injected by the host (tests, custom deployments) counts as configured.
    return (
        getattr(copilot, "_client", None) is not None
        or getattr(copilot, "_client_factory", _default_client) is not _default_client
    )


def _record(
    calls: LlmCallLog | None,
    template: PromptTemplate,
    model: str,
    tokens_in: int,
    tokens_out: int,
    started: float,
    outcome: str,
):
    if calls is None:
        return
    calls.record(
        user_id="autopilot",
        template=template,
        model=model,
        input_tokens=int(tokens_in or 0),
        output_tokens=int(tokens_out or 0),
        latency_ms=int((time.monotonic() - started) * 1000),
        outcome=outcome,
    )
