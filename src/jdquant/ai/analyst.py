"""The autopilot's analyst: an LLM that reviews each research cycle before the autopilot acts.

It returns a plain-language briefing and a list of concerns about the strategies selected for capital.
It never chooses trades: selection comes from the walk-forward research. When the owner allows it, a
high-severity concern vetoes a strategy — the analyst can only take risk away, never add it.

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

from jdquant.ai.prompts import AUTOPILOT_REVIEW, LlmCallLog

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
    client = http or httpx.Client(timeout=timeout)
    url = base_url.rstrip("/") + "/chat/completions"

    def review(briefing: dict[str, Any]) -> AnalystReport | None:
        started = time.monotonic()
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": AUTOPILOT_REVIEW.text},
                {"role": "user", "content": json.dumps(briefing, default=str)},
            ],
            # Analysis wants consistency, so a low temperature; the model still reasons before answering.
            "temperature": 0.2,
            "top_p": 0.95,
            "max_tokens": 16384,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        try:
            response = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("%s analyst unavailable: %s", provider, exc)
            _record(calls, model, 0, 0, started, type(exc).__name__)
            return None
        usage = data.get("usage") or {}
        _record(calls, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), started, "ok")
        return parse_report(message.get("content") or "", provider, model)

    return review


def claude_analyst(copilot: Any) -> Analyst:
    from jdquant.ai.copilot import FALLBACK_BETA

    def review(briefing: dict[str, Any]) -> AnalystReport | None:
        started = time.monotonic()
        try:
            client = copilot._ensure_client()
            response = client.beta.messages.create(
                model=copilot.model,
                max_tokens=4000,
                system=AUTOPILOT_REVIEW.text,
                messages=[{"role": "user", "content": json.dumps(briefing, default=str)}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            log.info("Claude analyst unavailable: %s", exc)
            return None
        usage = getattr(response, "usage", None)
        _record(
            copilot.calls,
            copilot.model,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            started,
            getattr(response, "stop_reason", None) or "unknown",
        )
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return parse_report(text, "Claude", copilot.model)

    return review


def analyst_from_env(copilot: Any, calls: LlmCallLog | None = None) -> Analyst | None:
    """JDQ_ANALYST: auto (NVIDIA if NVIDIA_API_KEY is set, else Claude if set up), nvidia, claude, none."""
    choice = os.environ.get("JDQ_ANALYST", "auto").lower()
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("JDQ_ANALYST_API_KEY")
    if choice == "none":
        return None
    if choice in ("nvidia", "openai") or (choice == "auto" and key):
        if not key:
            log.warning("JDQ_ANALYST=%s but no NVIDIA_API_KEY is set; the analyst is off", choice)
            return None
        return openai_compatible_analyst(
            base_url=os.environ.get("JDQ_ANALYST_BASE_URL", NVIDIA_BASE_URL),
            api_key=key,
            model=os.environ.get("JDQ_ANALYST_MODEL", NVIDIA_DEFAULT_MODEL),
            calls=calls,
            provider="NVIDIA" if choice != "openai" else "OpenAI-compatible",
        )
    if choice == "auto" and not _claude_configured(copilot):
        return None  # no provider configured: show "not configured" rather than failing every cycle
    return claude_analyst(copilot)


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
    calls: LlmCallLog | None, model: str, tokens_in: int, tokens_out: int, started: float, outcome: str
):
    if calls is None:
        return
    calls.record(
        user_id="autopilot",
        template=AUTOPILOT_REVIEW,
        model=model,
        input_tokens=int(tokens_in or 0),
        output_tokens=int(tokens_out or 0),
        latency_ms=int((time.monotonic() - started) * 1000),
        outcome=outcome,
    )
