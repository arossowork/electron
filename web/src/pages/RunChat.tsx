// The run page's chat with the agent and the answers that were defaulted after a timeout (#54, ADR 0001 7.7).
import { useRef, useEffect, useState, type FormEvent } from "react";
import { runs, type ChatMessage, type Question, type QuestionItem } from "../api/api";
import { ErrorBox, fmtTime } from "../components/ui";

/** The badge on a question nobody answered in time: the agent's recommended option was used. */
export function DefaultBadge({ q }: { q: Pick<Question, "auto_answered"> }) {
  if (!q.auto_answered) return null;
  return (
    <span className="badge warn" data-testid="default-badge" title="No answer in time: the agent's recommended option was used. You can still replace it.">
      answered by default
    </span>
  );
}

/** Questions that were answered by default, with the answer used and a form to replace it. */
export function DefaultedQuestions({ qs, active, onChanged }: { qs: Question[]; active: boolean; onChanged: () => Promise<void> }) {
  const shown = qs.filter((q) => q.auto_answered);
  if (!shown.length) return null;
  return (
    <section className="card" aria-label="Answered by default" data-testid="defaulted">
      <h2>Answered by default</h2>
      <p className="muted">
        Nobody answered in time, so the agent used its recommended option and went on.
        {active ? " Choose another one and the stage that asked, and the stages after it, run again." : ""}
      </p>
      {shown.map((q) => (
        <DefaultedQuestion key={q.id} q={q} active={active} onChanged={onChanged} />
      ))}
    </section>
  );
}

function DefaultedQuestion({ q, active, onChanged }: { q: Question; active: boolean; onChanged: () => Promise<void> }) {
  const items = q.questions as unknown as QuestionItem[];
  const used = (q.answers ?? {}) as Record<string, string>;
  const [vals, setVals] = useState<Record<string, string>>(() => ({ ...used }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>();
  const changed = items.some((i) => vals[i.id] !== used[i.id]);
  async function submit() {
    setBusy(true);
    setErr(undefined);
    try {
      await runs.answer(q.id, vals);
      await onChanged();
    } catch (x) {
      setErr(x);
    }
    setBusy(false);
  }
  return (
    <div>
      <h3>
        Stage {q.stage} <DefaultBadge q={q} />
      </h3>
      {items.map((i) => (
        <div key={i.id}>
          <label htmlFor={`d-${q.id}-${i.id}`}>{i.text}</label>
          {active && i.options?.length ? (
            <select id={`d-${q.id}-${i.id}`} value={vals[i.id] ?? ""} onChange={(e) => setVals({ ...vals, [i.id]: e.target.value })}>
              {i.options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : (
            <p id={`d-${q.id}-${i.id}`}>
              <strong>{used[i.id]}</strong>
            </p>
          )}
        </div>
      ))}
      <ErrorBox error={err} />
      {active && (
        <button className="primary" onClick={submit} disabled={busy || !changed}>
          Replace the default
        </button>
      )}
    </div>
  );
}

/** The thread: the person's messages (queued until the agent's next turn) and the agent's replies. */
export function ChatThread({ messages }: { messages: ChatMessage[] }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (el.current) el.current.scrollTop = el.current.scrollHeight;
  }, [messages.length]);
  if (!messages.length) return <p className="muted" data-testid="chat-empty">No message yet. The agent reads what you write between its turns.</p>;
  return (
    <div className="chat" ref={el} role="log" aria-label="Chat with the agent" tabIndex={0}>
      {messages.map((m) => (
        <div key={m.id} className={`msg ${m.role}`} data-testid={`msg-${m.role}`}>
          <div className="who">
            {m.role === "user" ? "You" : "Agent"}
            {m.stage ? ` (${m.stage})` : ""} <span className="muted">{fmtTime(m.created_at)}</span>
            {m.role === "user" && !m.delivered_at && (
              <span className="badge" data-testid="queued">
                queued
              </span>
            )}
          </div>
          <div className="body">{m.body}</div>
        </div>
      ))}
    </div>
  );
}

export function ChatPanel({ runId, messages, active, onSent }: { runId: string; messages: ChatMessage[]; active: boolean; onSent: () => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>();
  async function send(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    setErr(undefined);
    try {
      await runs.sendChat(runId, t);
      setText("");
      await onSent();
    } catch (x) {
      setErr(x);
    }
    setBusy(false);
  }
  return (
    <section className="card" aria-label="Chat" data-testid="chat">
      <h2>Chat with the agent</h2>
      <ChatThread messages={messages} />
      <ErrorBox error={err} />
      {active ? (
        <form onSubmit={send}>
          <label htmlFor="chat-text">Message</label>
          <textarea id="chat-text" rows={2} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} placeholder="Tell the agent something. It reads it at the end of its current turn." />
          <button className="primary" type="submit" disabled={busy || !text.trim()}>
            Send
          </button>
        </form>
      ) : (
        <p className="muted">The run has ended: the chat is read-only.</p>
      )}
    </section>
  );
}
