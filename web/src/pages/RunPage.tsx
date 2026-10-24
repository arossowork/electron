import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  artifactPath,
  download,
  runs,
  TERMINAL_RUN,
  type Question,
  type QuestionItem,
  type Run,
} from "../api/api";
import { session } from "../api/client";
import { follow, type SseMessage, type StreamState } from "../api/sse";
import { Markdown } from "../components/Markdown";
import { Badge, ErrorBox, errText, fmtBytes, fmtTime, useLoad } from "../components/ui";

const TERMINAL_EVENTS = new Set(["run.completed", "run.failed", "run.cancelled"]);

/** One line of the live log for an event envelope (CloudEvents, ADR 0001 section 5). */
export function eventLine(m: SseMessage): string | null {
  let d: Record<string, unknown> = {};
  try {
    const env = JSON.parse(m.data) as { data?: Record<string, unknown> };
    d = env.data ?? {};
  } catch {
    return null;
  }
  const key = (d.key as string) ?? (d.stage as string) ?? "";
  switch (m.event) {
    case "stage.progress":
      return `${key}: ${String(d.line ?? "")}`;
    case "stage.started":
      return `${key} started`;
    case "stage.completed":
      return `${key} ${String(d.verdict ?? "done")}${d.cached ? " (cached)" : ""}`;
    case "stage.failed":
      return `${key} failed: ${String((d.error as { message?: string } | undefined)?.message ?? "")}`;
    case "question.asked":
      return "waiting for answers";
    case "question.answered":
      return "answers received";
    default:
      return m.event.startsWith("run.") ? m.event : null;
  }
}

export function RunPage() {
  const { runId = "" } = useParams();
  const run = useLoad(() => runs.get(runId), `run:${runId}`);
  const questions = useLoad(() => runs.questions(runId), `q:${runId}`);
  const [log, setLog] = useState<string[]>([]);
  const [stream, setStream] = useState<{ state: StreamState; info?: string }>({ state: "connecting" });
  const reloads = useRef({ run: run.reload, q: questions.reload });
  reloads.current = { run: run.reload, q: questions.reload };

  useEffect(() => {
    const ac = new AbortController();
    let t: ReturnType<typeof setTimeout> | undefined;
    setLog([]);
    void follow({
      path: `/runs/${runId}/events`,
      signal: ac.signal,
      getToken: session.accessToken,
      refresh: session.refresh,
      isTerminal: (m) => TERMINAL_EVENTS.has(m.event),
      onState: (s, i) => setStream({ state: s, info: i?.error && s === "reconnecting" ? `${i.error}, retry in ${Math.round(i.delayMs / 1000)}s` : undefined }),
      onMessage: (m) => {
        const l = eventLine(m);
        if (l) setLog((old) => [...old.slice(-199), l]);
        if (m.event !== "stage.progress") {
          clearTimeout(t);
          t = setTimeout(() => {
            void reloads.current.run();
            void reloads.current.q();
          }, 250);
        }
      },
    });
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [runId]);

  const r = run.data;
  const done = !!r && TERMINAL_RUN.has(r.status);
  const open = (questions.data ?? []).filter((q) => q.status === "open");

  return (
    <>
      <p>{r && <Link to={`/projects/${r.project_id}`}>Project</Link>} /</p>
      <h1>
        Run {runId.slice(0, 8)} {r && <Badge value={r.status} />} {r && <Badge value={r.verdict} />}
      </h1>
      <ErrorBox error={run.error} />
      {r && <Overview run={r} onChanged={run.reload} stream={stream} />}
      {open.map((q) => (
        <QuestionPanel
          key={q.id}
          q={q}
          onAnswered={async () => {
            await questions.reload();
            await run.reload();
          }}
        />
      ))}
      {r && <Stages run={r} />}
      {log.length > 0 && (
        <section className="card">
          <h2>Live log</h2>
          <Log lines={log} />
        </section>
      )}
      {done && <Results runId={runId} />}
    </>
  );
}

function Overview({ run, onChanged, stream }: { run: Run; onChanged: () => Promise<void>; stream: { state: StreamState; info?: string } }) {
  const [err, setErr] = useState<unknown>();
  const active = !TERMINAL_RUN.has(run.status);
  async function cancel() {
    if (!window.confirm("Cancel this run?")) return;
    try {
      await runs.cancel(run.id);
      await onChanged();
    } catch (x) {
      setErr(x);
    }
  }
  return (
    <section className="card">
      <div className="row">
        <span>
          kind <strong>{run.kind}</strong>
        </span>
        <span className="muted">created {fmtTime(run.created_at)}</span>
        {run.ended_at && <span className="muted">ended {fmtTime(run.ended_at)}</span>}
        <span className="spacer" />
        {active && (
          <span className="muted" aria-live="polite" data-testid="stream-state">
            {stream.state === "open" ? "live" : stream.state}
            {stream.info ? ` (${stream.info})` : ""}
          </span>
        )}
        {active && (
          <button className="danger" onClick={cancel}>
            Cancel run
          </button>
        )}
      </div>
      {run.error && <div className="err">{JSON.stringify(run.error)}</div>}
      <ErrorBox error={err} />
    </section>
  );
}

function Stages({ run }: { run: Run }) {
  const st = run.stages;
  if (!st.length)
    return (
      <section className="card">
        <h2>Stages</h2>
        <p className="muted" data-testid="no-stages">
          {run.status === "queued" ? "Queued: waiting for a worker." : "No stage has started yet."}
        </p>
      </section>
    );
  const finished = st.filter((s) => ["succeeded", "failed", "skipped", "cancelled"].includes(s.status)).length;
  return (
    <section className="card">
      <h2>
        Stages{" "}
        <span className="muted">
          {finished}/{st.length}
        </span>
      </h2>
      <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={st.length} aria-valuenow={finished}>
        <div style={{ width: `${(100 * finished) / st.length}%` }} />
      </div>
      <ul className="stages">
        {st.map((s) => (
          <li key={s.key}>
            <span className="name">{s.key}</span>
            {s.cached && <span className="badge">cached</span>}
            <Badge value={s.verdict ?? s.status} />
            <span className="muted">{s.seconds != null ? `${s.seconds.toFixed(1)} s` : ""}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Log({ lines }: { lines: string[] }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (el.current) el.current.scrollTop = el.current.scrollHeight;
  }, [lines]);
  return (
    <div className="log" ref={el} tabIndex={0}>
      {lines.join("\n")}
    </div>
  );
}

function QuestionPanel({ q, onAnswered }: { q: Question; onAnswered: () => Promise<void> }) {
  const items = q.questions as unknown as QuestionItem[];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(items.map((i) => [i.id, i.default ?? ""])));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>();
  const missing = items.some((i) => !vals[i.id] && i.default === undefined);
  async function submit() {
    setBusy(true);
    setErr(undefined);
    try {
      const answers = Object.fromEntries(items.filter((i) => vals[i.id] !== "").map((i) => [i.id, vals[i.id]]));
      await runs.answer(q.id, answers);
      await onAnswered();
    } catch (x) {
      setErr(x);
    }
    setBusy(false);
  }
  return (
    <section className="card" aria-label="Open questions" data-testid="questions">
      <h2>
        The run needs your answers <span className="muted">(stage {q.stage})</span>
      </h2>
      {items.map((i) => (
        <div key={i.id}>
          <label htmlFor={`q-${q.id}-${i.id}`}>{i.text}</label>
          {i.options?.length ? (
            <select id={`q-${q.id}-${i.id}`} value={vals[i.id]} onChange={(e) => setVals({ ...vals, [i.id]: e.target.value })}>
              {i.default === undefined && <option value="">Choose…</option>}
              {i.options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input id={`q-${q.id}-${i.id}`} value={vals[i.id]} onChange={(e) => setVals({ ...vals, [i.id]: e.target.value })} />
          )}
        </div>
      ))}
      <ErrorBox error={err} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={submit} disabled={busy || missing}>
          Send answers
        </button>
      </div>
    </section>
  );
}

function Results({ runId }: { runId: string }) {
  const report = useLoad(() => runs.report(runId), `report:${runId}`);
  const arts = useLoad(() => runs.artifacts(runId), `arts:${runId}`);
  const [err, setErr] = useState<unknown>();
  return (
    <>
      <section className="card">
        <h2>Report</h2>
        {report.loading && report.data === undefined ? (
          <p className="muted">Loading…</p>
        ) : report.error ? (
          <p className="muted">No report: {errText(report.error)}</p>
        ) : (
          <Markdown text={String(report.data ?? "")} />
        )}
      </section>
      <section className="card">
        <h2>Artifacts</h2>
        <ErrorBox error={err ?? arts.error} />
        {arts.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Stage</th>
                  <th>Size</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {arts.data.map((a) => (
                  <tr key={a.name}>
                    <td>
                      <code>{a.name}</code>
                    </td>
                    <td className="muted">{a.stage_key ?? ""}</td>
                    <td className="muted">{fmtBytes(a.size)}</td>
                    <td>
                      <button
                        onClick={async () => {
                          setErr(undefined);
                          try {
                            await download(artifactPath(runId, a.name), a.name.split("/").pop() ?? a.name);
                          } catch (x) {
                            setErr(x);
                          }
                        }}
                      >
                        Download
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No artifact.</p>
        )}
      </section>
    </>
  );
}
