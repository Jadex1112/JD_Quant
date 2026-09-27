"""AI Copilot (Chapter 52): Claude with platform tools, scoped to the user's permissions.

State-changing tools never run inside the model loop. They are returned to the user as confirmation
cards, and the loop resumes only after the user approves or declines (AI-52004, CON-140).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from jdquant.ai.prompts import COPILOT_SYSTEM, LlmCallLog, redact
from jdquant.core.clock import Clock
from jdquant.core.errors import NotFoundError, PlatformError
from jdquant.persistence.codec import decode, encode
from jdquant.persistence.store import Store
from jdquant.security.audit import AuditLog
from jdquant.security.identity import Principal

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_STEPS = 10


class Effect(StrEnum):
    READ_ONLY = "READ_ONLY"
    STATE_CHANGING = "STATE_CHANGING"


@dataclass
class CopilotTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: str
    effect: Effect
    handler: Callable[[Principal, dict[str, Any]], Any]
    summarize: Callable[[dict[str, Any]], str] | None = None

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"


@dataclass
class PendingAction:
    action_id: str
    tool_use_id: str
    tool: str
    input: dict[str, Any]
    summary: str
    status: ActionStatus = ActionStatus.PENDING
    result: str | None = None


@dataclass
class TranscriptEntry:
    kind: str  # user | assistant | tool | action | notice
    text: str
    at: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    conversation_id: str
    user_id: str
    title: str
    created_at: datetime
    messages: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    pending: list[PendingAction] = field(default_factory=list)
    pending_results: list[dict[str, Any]] = field(default_factory=list)


class Copilot:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        audit: AuditLog,
        tools: list[CopilotTool],
        *,
        client_factory: Callable[[], Any] | None = None,
        model: str | None = None,
        daily_token_budget: int | None = None,
    ):
        self._store = store
        self._clock = clock
        self._audit = audit
        self.tools = {t.name: t for t in tools}
        self._client_factory = client_factory or _default_client
        self._client: Any = None
        self.model = model or os.environ.get("JDQ_COPILOT_MODEL", DEFAULT_MODEL)
        self.daily_token_budget = daily_token_budget or int(
            os.environ.get("JDQ_COPILOT_DAILY_TOKENS", "2000000")
        )
        self.calls = LlmCallLog(store, clock)

    # ---- conversations ----------------------------------------------------------------------

    def start(self, principal: Principal, title: str = "New conversation") -> Conversation:
        conv = Conversation(str(uuid.uuid4()), principal.user_id, title[:100], self._clock.now())
        self._save(conv)
        return conv

    def get(self, principal: Principal, conversation_id: str) -> Conversation:
        doc = self._store.get("copilot_conversation", conversation_id)
        if doc is None:
            raise NotFoundError("CONVERSATION_NOT_FOUND", "unknown conversation")
        conv = decode(Conversation, doc)
        if conv.user_id != principal.user_id:  # conversations are private to their owner
            raise NotFoundError("CONVERSATION_NOT_FOUND", "unknown conversation")
        return conv

    def list(self, principal: Principal) -> list[Conversation]:
        convs = [decode(Conversation, d) for d in self._store.all("copilot_conversation")]
        return sorted(
            (c for c in convs if c.user_id == principal.user_id), key=lambda c: c.created_at, reverse=True
        )

    def send(
        self, principal: Principal, conversation_id: str, text: str, *, context: str = ""
    ) -> Conversation:
        conv = self.get(principal, conversation_id)
        if any(a.status is ActionStatus.PENDING for a in conv.pending):
            raise PlatformError("COPILOT_ACTION_PENDING", "confirm or decline the pending actions first")
        if not text.strip():
            raise PlatformError("COPILOT_EMPTY_MESSAGE", "message is empty")
        now = self._clock.now()
        conv.transcript.append(TranscriptEntry("user", text, now))
        if len(conv.transcript) == 1:
            conv.title = text.strip()[:80]
        header = (
            f"[user: {principal.display_name}; roles: {', '.join(principal.roles)}; time: {now.isoformat()}"
        )
        header += f"; page: {context}]" if context else "]"
        conv.messages.append({"role": "user", "content": f"{header}\n{redact(text)}"})
        self._run(principal, conv)
        self._save(conv)
        return conv

    def resolve(
        self, principal: Principal, conversation_id: str, action_id: str, approve: bool
    ) -> Conversation:
        conv = self.get(principal, conversation_id)
        action = next((a for a in conv.pending if a.action_id == action_id), None)
        if action is None or action.status is not ActionStatus.PENDING:
            raise NotFoundError("ACTION_NOT_FOUND", "no such pending action")
        tool = self.tools[action.tool]
        if approve:
            action.status = ActionStatus.CONFIRMED
            action.result = self._execute(principal, tool, action.input, confirmed=True)
        else:
            action.status = ActionStatus.DECLINED
            action.result = json.dumps({"declined": True, "message": "The user declined this action."})
            self._audit.record(
                actor=principal.user_id,
                action=f"copilot.{tool.name}",
                category="AI",
                outcome="DENIED",
                reason="declined by user",
                data={"input": action.input},
            )
        conv.transcript.append(
            TranscriptEntry(
                "action",
                f"{'Confirmed' if approve else 'Declined'}: {action.summary}",
                self._clock.now(),
                {"action_id": action.action_id, "status": action.status.value, "result": action.result},
            )
        )
        if all(a.status is not ActionStatus.PENDING for a in conv.pending):
            results = conv.pending_results + [
                {"type": "tool_result", "tool_use_id": a.tool_use_id, "content": a.result or ""}
                for a in conv.pending
            ]
            conv.pending, conv.pending_results = [], []
            conv.messages.append({"role": "user", "content": results})
            self._run(principal, conv)
        self._save(conv)
        return conv

    # ---- model loop ---------------------------------------------------------------------------

    def _run(self, principal: Principal, conv: Conversation) -> None:
        for _ in range(MAX_STEPS):
            response = self._call(principal, conv)
            if response.stop_reason == "refusal":
                conv.transcript.append(
                    TranscriptEntry(
                        "notice", "The assistant declined to respond to this request.", self._clock.now()
                    )
                )
                return  # history is untouched; a later user message simply joins the same user turn
            content = [block.to_dict() for block in response.content]
            conv.messages.append({"role": "assistant", "content": content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    conv.transcript.append(
                        TranscriptEntry("assistant", block.text, self._clock.now(), {"ai_generated": True})
                    )
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return
            results: list[dict[str, Any]] = []
            confirm: list[PendingAction] = []
            for use in tool_uses:
                tool = self.tools.get(use.name)
                if tool is None:
                    results.append(_error(use.id, f"unknown tool {use.name}"))
                    continue
                args = use.input if isinstance(use.input, dict) else {}
                denial = self._denial(principal, tool)
                if denial:
                    results.append(_error(use.id, denial))
                elif tool.effect is Effect.READ_ONLY:
                    output = self._execute(principal, tool, args)
                    results.append({"type": "tool_result", "tool_use_id": use.id, "content": output})
                    conv.transcript.append(
                        TranscriptEntry("tool", f"Looked up: {tool.name}", self._clock.now(), {"input": args})
                    )
                else:
                    summary = tool.summarize(args) if tool.summarize else f"{tool.name} {json.dumps(args)}"
                    action = PendingAction(uuid.uuid4().hex[:12], use.id, tool.name, args, summary)
                    confirm.append(action)
                    conv.transcript.append(
                        TranscriptEntry(
                            "action",
                            f"Needs your confirmation: {summary}",
                            self._clock.now(),
                            {
                                "action_id": action.action_id,
                                "status": "PENDING",
                                "tool": tool.name,
                                "input": args,
                            },
                        )
                    )
            if confirm:
                conv.pending, conv.pending_results = confirm, results
                return
            conv.messages.append({"role": "user", "content": results})
        conv.transcript.append(TranscriptEntry("notice", "Stopped after too many steps.", self._clock.now()))

    def _call(self, principal: Principal, conv: Conversation) -> Any:
        used = self.calls.tokens_today(principal.user_id)
        if used >= self.daily_token_budget:
            raise PlatformError(
                "COPILOT_QUOTA_EXCEEDED", f"daily AI budget of {self.daily_token_budget} tokens used"
            )
        client = self._ensure_client()
        started = time.monotonic()
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[
                    {"type": "text", "text": COPILOT_SYSTEM.text, "cache_control": {"type": "ephemeral"}}
                ],
                tools=[t.definition() for t in self.tools.values()],
                messages=conv.messages,
                thinking={"type": "adaptive"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            self.calls.record(
                user_id=principal.user_id,
                template=COPILOT_SYSTEM,
                model=self.model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                outcome=type(exc).__name__,
            )
            log.warning("copilot call failed: %s", exc)
            raise PlatformError(
                "COPILOT_UNAVAILABLE", f"the AI service is unavailable ({type(exc).__name__})"
            ) from None
        usage = getattr(response, "usage", None)
        self.calls.record(
            user_id=principal.user_id,
            template=COPILOT_SYSTEM,
            model=getattr(response, "model", self.model),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome=response.stop_reason or "unknown",
        )
        return response

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                self._client = self._client_factory()
            except Exception as exc:
                raise PlatformError(
                    "COPILOT_UNAVAILABLE", "Claude is not configured; set ANTHROPIC_API_KEY on the server"
                ) from exc
        return self._client

    # ---- tools --------------------------------------------------------------------------------

    @staticmethod
    def _denial(principal: Principal, tool: CopilotTool) -> str | None:
        if not principal.can(tool.permission):
            return f"permission denied: the user lacks {tool.permission}"
        if tool.effect is not Effect.READ_ONLY and not principal.can("ai.copilot:execute_actions"):
            return "permission denied: the user may not execute actions through the copilot"
        return None

    def _execute(
        self, principal: Principal, tool: CopilotTool, args: dict[str, Any], *, confirmed: bool = False
    ) -> str:
        denial = self._denial(principal, tool)  # permissions are re-checked at execution time
        if denial:
            return json.dumps({"error": denial})
        try:
            result = tool.handler(principal, args)
            output = json.dumps(encode(result), default=str)
            outcome = "SUCCESS"
        except PlatformError as exc:
            output, outcome = json.dumps({"error": exc.code, "message": exc.message}), "FAILURE"
        except Exception as exc:  # a broken tool must not break the conversation
            log.exception("copilot tool %s failed", tool.name)
            output, outcome = json.dumps({"error": "TOOL_FAILED", "message": str(exc)}), "FAILURE"
        if confirmed or tool.effect is not Effect.READ_ONLY:
            self._audit.record(
                actor=principal.user_id,
                action=f"copilot.{tool.name}",
                category="AI",
                outcome=outcome,
                reason="confirmed by user via copilot",
                data={"input": args},
            )
        return output

    def _save(self, conv: Conversation) -> None:
        self._store.put("copilot_conversation", conv.conversation_id, encode(conv))


def _error(tool_use_id: str, message: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": message, "is_error": True}


def _default_client() -> Any:
    import anthropic

    return anthropic.Anthropic()
