import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ApiError, get, post, type ConversationView } from "../api";
import { describe, useApp } from "../app-state";
import { Badge } from "./ui";

export function CopilotPanel({ onClose }: { onClose: () => void }) {
  const { run, notify } = useApp();
  const location = useLocation();
  const [conv, setConv] = useState<ConversationView | null>(null);
  const [list, setList] = useState<{ conversation_id: string; title: string }[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    get<{ conversation_id: string; title: string }[]>("/copilot/conversations").then(setList).catch(() => undefined);
  }, []);
  useEffect(() => bottom.current?.scrollIntoView({ block: "end" }), [conv]);

  const ensure = async (): Promise<ConversationView> => {
    if (conv) return conv;
    const created = await post<ConversationView>("/copilot/conversations");
    setConv(created);
    return created;
  };

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const message = text.trim();
    if (!message) return;
    setBusy(true);
    setText("");
    let before: ConversationView | null = conv;
    try {
      const c = await ensure();
      before = c;
      setConv({ ...c, transcript: [...c.transcript, { kind: "user", text: message, at: new Date().toISOString(), data: {} }] });
      setConv(await post<ConversationView>(`/copilot/conversations/${c.conversation_id}/messages`, { text: message, context: location.pathname }));
      setUnavailable("");
    } catch (err) {
      // The message was not delivered: drop the optimistic bubble and give the text back for a retry.
      setConv(before);
      if (err instanceof ApiError && err.code === "COPILOT_UNAVAILABLE") setUnavailable(err.detail);
      else notify(describe(err), true);
      setText(message);
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (actionId: string, approve: boolean) => {
    if (!conv) return;
    setBusy(true);
    const updated = await run(() =>
      post<ConversationView>(`/copilot/conversations/${conv.conversation_id}/actions/${actionId}:${approve ? "confirm" : "decline"}`),
    );
    if (updated) setConv(updated);
    setBusy(false);
  };

  const open = async (id: string) => {
    const loaded = await run(() => get<ConversationView>(`/copilot/conversations/${id}`));
    if (loaded) setConv(loaded);
  };

  return (
    <aside className="copilot" aria-label="AI Copilot">
      <header>
        <strong>AI Copilot</strong>
        <Badge kind="ai">AI-generated</Badge>
        <span style={{ flex: 1 }} />
        <button className="small" onClick={() => setConv(null)}>New</button>
        <button className="small" onClick={onClose} aria-label="Close copilot">✕</button>
      </header>
      <div className="messages" aria-live="polite">
        {!conv && (
          <div className="stack small muted">
            <p style={{ margin: 0 }}>
              Ask about positions, orders, risk or why a strategy isn't trading. Actions such as pausing a strategy are shown
              for your confirmation before anything happens. Answers are not investment advice.
            </p>
            {list.length > 0 && (
              <div>
                <h3>Recent</h3>
                {list.slice(0, 8).map((c) => (
                  <div key={c.conversation_id}>
                    <button className="link" onClick={() => open(c.conversation_id)}>
                      {c.title}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {unavailable && <div className="alert warn">{unavailable}</div>}
        {conv?.transcript.map((m, i) => (
          <div key={i} className={`msg ${m.kind}`}>
            {m.kind === "assistant" && (
              <div className="small muted" style={{ marginBottom: 4 }}>
                <Badge kind="ai">AI</Badge>
              </div>
            )}
            {m.text}
          </div>
        ))}
        {conv?.pending_actions.map((a) => (
          <div key={a.action_id} className="msg action" role="group" aria-label="Action awaiting confirmation">
            <strong>Confirm action</strong>
            <div style={{ margin: "6px 0" }}>{a.summary}</div>
            <div className="row">
              <button className="primary small" disabled={busy} onClick={() => resolve(a.action_id, true)}>
                Confirm
              </button>
              <button className="small" disabled={busy} onClick={() => resolve(a.action_id, false)}>
                Decline
              </button>
            </div>
          </div>
        ))}
        {busy && <div className="msg notice">Thinking…</div>}
        <div ref={bottom} />
      </div>
      <form onSubmit={send}>
        <label htmlFor="copilot-input" className="sr-only">Message the copilot</label>
        <textarea
          id="copilot-input"
          rows={2}
          value={text}
          placeholder="Why did my BTC strategy stop trading?"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              (e.currentTarget.form as HTMLFormElement).requestSubmit();
            }
          }}
          disabled={busy || Boolean(conv?.pending_actions.length)}
        />
        <button className="primary" type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
    </aside>
  );
}
