import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { projects, type DesignVersion } from "../api/api";
import { follow } from "../api/sse";
import { session } from "../api/client";
import { Badge, ErrorBox, fmtTime, useLoad } from "../components/ui";
import { isAdmin, useMe } from "../auth";

/** Map dropped files to the multipart parts of POST /design-versions. Exported for tests. */
export function buildUploadForm(files: File[], message: string): { form: FormData; used: string[]; ignored: string[] } {
  const form = new FormData();
  const used: string[] = [];
  const ignored: string[] = [];
  for (const f of files) {
    const n = f.name.toLowerCase();
    if (n === "design.yaml" || n === "design.yml") form.set("design", f, "design.yaml");
    else if (n === "requirements.yaml" || n === "requirements.yml") form.set("requirements", f, "requirements.yaml");
    else if (n.endsWith(".tar.gz") || n.endsWith(".tgz")) form.set("bundle", f, f.name);
    else if (n.endsWith(".md")) form.append("notes", f, f.name);
    else {
      ignored.push(f.name);
      continue;
    }
    used.push(f.name);
  }
  if (message) form.set("message", message);
  return { form, used, ignored };
}

export function ProjectPage() {
  const { projectId = "" } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const project = useLoad(() => projects.get(projectId), `p:${projectId}`);
  const versions = useLoad(() => projects.versions(projectId), `v:${projectId}`);
  const runs = useLoad(() => projects.runs(projectId), `r:${projectId}`);
  const [pick, setPick] = useState("");
  const [kind, setKind] = useState<"build" | "design">("build");
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);

  // Any event of the project (run state, stages) refreshes the runs table.
  const reloadRuns = useRef(runs.reload);
  reloadRuns.current = runs.reload;
  useEffect(() => {
    const ac = new AbortController();
    let t: ReturnType<typeof setTimeout> | undefined;
    void follow({
      path: `/projects/${projectId}/events`,
      signal: ac.signal,
      getToken: session.accessToken,
      refresh: session.refresh,
      onMessage: () => {
        clearTimeout(t);
        t = setTimeout(() => void reloadRuns.current(), 300);
      },
    });
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [projectId]);

  const vlist = versions.data?.items ?? [];
  const chosen = pick || vlist[0]?.id || "";

  async function start() {
    setBusy(true);
    setErr(undefined);
    try {
      const r = await projects.startRun(projectId, { kind, design_version_id: chosen, options: {} });
      nav(`/runs/${r.id}`);
    } catch (x) {
      setErr(x);
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Delete this project and its runs?")) return;
    try {
      await projects.remove(projectId);
      nav("/projects");
    } catch (x) {
      setErr(x);
    }
  }

  return (
    <>
      <p>
        <Link to="/projects">Projects</Link> /
      </p>
      <h1>{project.data?.name ?? "Project"}</h1>
      <ErrorBox error={project.error} />
      <div className="grid2">
        <section className="card">
          <h2>Design versions</h2>
          <Uploader projectId={projectId} onDone={async (v) => (await versions.reload(), setPick(v.id))} />
          <ErrorBox error={versions.error} />
          {vlist.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Message</th>
                    <th>Hash</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {vlist.map((v) => (
                    <tr key={v.id}>
                      <td>{v.seq}</td>
                      <td>{v.message || <span className="muted">-</span>}</td>
                      <td>
                        <code>{v.content_sha256.slice(0, 10)}</code>
                      </td>
                      <td className="muted">{fmtTime(v.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No version yet. Drop design.yaml to create the first one.</p>
          )}
        </section>

        <section className="card">
          <h2>Start a run</h2>
          <label htmlFor="ver">Design version</label>
          <select id="ver" value={chosen} onChange={(e) => setPick(e.target.value)} disabled={!vlist.length}>
            {vlist.map((v) => (
              <option key={v.id} value={v.id}>
                #{v.seq} {v.message || v.content_sha256.slice(0, 8)}
              </option>
            ))}
          </select>
          <label htmlFor="kind">Kind</label>
          <select id="kind" value={kind} onChange={(e) => setKind(e.target.value as "build" | "design")}>
            <option value="build">build (check, simulate, layout)</option>
            <option value="design">design (agent)</option>
          </select>
          <ErrorBox error={err} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={start} disabled={busy || !chosen}>
              Start run
            </button>
            {isAdmin(me.role) && (
              <button className="danger" onClick={remove}>
                Delete project
              </button>
            )}
          </div>
        </section>
      </div>

      <section className="card">
        <h2>Runs</h2>
        <ErrorBox error={runs.error} />
        {runs.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/runs/${r.id}`}>
                        <code>{r.id.slice(0, 8)}</code>
                      </Link>
                    </td>
                    <td>{r.kind}</td>
                    <td>
                      <Badge value={r.status} />
                    </td>
                    <td>
                      <Badge value={r.verdict} />
                    </td>
                    <td className="muted">{fmtTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No run yet.</p>
        )}
      </section>
    </>
  );
}

function Uploader({ projectId, onDone }: { projectId: string; onDone: (v: DesignVersion) => void | Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>();
  const [info, setInfo] = useState("");

  async function send(list: FileList | File[]) {
    const { form, used, ignored } = buildUploadForm(Array.from(list), msg);
    setErr(undefined);
    setInfo("");
    if (!used.length) {
      setErr(new Error("Drop design.yaml, requirements.yaml, notes (.md) or a .tar.gz bundle."));
      return;
    }
    setBusy(true);
    try {
      const v = await projects.upload(projectId, form);
      setInfo(`Version #${v.seq} (${used.join(", ")})${ignored.length ? `; ignored: ${ignored.join(", ")}` : ""}`);
      setMsg("");
      await onDone(v);
    } catch (x) {
      setErr(x);
    }
    setBusy(false);
  }
  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer.files.length) void send(e.dataTransfer.files);
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <label htmlFor="vmsg">Message (optional)</label>
      <input id="vmsg" maxLength={2000} value={msg} onChange={(e) => setMsg(e.target.value)} />
      <div
        className={`drop${over ? " over" : ""}`}
        style={{ marginTop: 8 }}
        data-testid="dropzone"
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => (e.preventDefault(), setOver(true))}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
      >
        {busy ? "Uploading…" : "Drop design.yaml, requirements.yaml, notes or a .tar.gz here, or click to choose"}
      </div>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        data-testid="file-input"
        onChange={(e) => {
          if (e.target.files?.length) void send(e.target.files);
          e.target.value = "";
        }}
      />
      {info && <p className="muted">{info}</p>}
      <ErrorBox error={err} />
    </div>
  );
}
