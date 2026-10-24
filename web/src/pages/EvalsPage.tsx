import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { evals } from "../api/api";
import { useMe } from "../auth";
import { Markdown } from "../components/Markdown";
import { Badge, ErrorBox, errText, fmtTime, useLoad } from "../components/ui";

/** Evaluations of the tenant, and the report of the selected one (the same Markdown rendering as a run report). */
export function EvalsPage() {
  const { evalId } = useParams();
  const list = useLoad(evals.list, "evals");
  return (
    <>
      <h1>Evaluations</h1>
      <ErrorBox error={list.error} />
      <div className="card">
        {list.loading && !list.data ? (
          <p className="muted">Loading…</p>
        ) : list.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Suite</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <Link to={`/evals/${e.id}`}>{e.suite}</Link>
                    </td>
                    <td>
                      <Badge value={e.status} />
                    </td>
                    <td>
                      <Badge value={e.verdict} />
                    </td>
                    <td className="muted">{fmtTime(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No evaluation yet. Start one with POST /api/v1/evals or `evals/harness.py platform run`.</p>
        )}
      </div>
      {evalId ? <EvalDetail key={evalId} evalId={evalId} onPromoted={list.reload} /> : null}
    </>
  );
}

function EvalDetail({ evalId, onPromoted }: { evalId: string; onPromoted: () => Promise<void> }) {
  const me = useMe();
  const detail = useLoad(() => evals.get(evalId), `eval:${evalId}`);
  const report = useLoad(() => evals.report(evalId), `eval-report:${evalId}`);
  const [err, setErr] = useState<unknown>();
  const [done, setDone] = useState<string>();
  const e = detail.data;
  async function promote() {
    setErr(undefined);
    try {
      const b = await evals.promote(evalId, "");
      setDone(b.name);
      await onPromoted();
    } catch (x) {
      setErr(x);
    }
  }
  return (
    <>
      <section className="card">
        <h2>{e ? `${e.suite} ` : ""}evaluation</h2>
        <ErrorBox error={detail.error} />
        {e ? (
          <p>
            <Badge value={e.status} /> <Badge value={e.verdict} />{" "}
            <span className="muted">{e.children.length} design runs</span>
          </p>
        ) : null}
        {e?.children.length ? (
          <ul>
            {e.children.map((c) => (
              <li key={c.id}>
                <Link to={`/runs/${c.id}`}>{c.id.slice(0, 8)}</Link> <Badge value={c.status} />{" "}
                <Badge value={c.verdict} />
              </li>
            ))}
          </ul>
        ) : null}
        {e?.status === "succeeded" && (me.role === "owner" || me.role === "admin") ? (
          <div className="row">
            <button onClick={promote} disabled={!!done}>
              Promote as baseline
            </button>
            {done ? <span className="muted">Promoted as “{done}”.</span> : null}
          </div>
        ) : null}
        <ErrorBox error={err} />
      </section>
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
    </>
  );
}
