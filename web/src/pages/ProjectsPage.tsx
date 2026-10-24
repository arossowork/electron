import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { projects } from "../api/api";
import { ErrorBox, fmtTime, useLoad } from "../components/ui";

export function ProjectsPage() {
  const list = useLoad(projects.list, "projects");
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      const p = await projects.create(name.trim());
      nav(`/projects/${p.id}`);
    } catch (x) {
      setErr(x);
      setBusy(false);
    }
  }
  return (
    <>
      <h1>Projects</h1>
      <ErrorBox error={list.error} />
      <div className="card">
        {list.loading && !list.data ? (
          <p className="muted">Loading…</p>
        ) : list.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Slug</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <Link to={`/projects/${p.id}`}>{p.name}</Link>
                    </td>
                    <td className="muted">{p.slug}</td>
                    <td className="muted">{fmtTime(p.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No project yet. Create the first one below.</p>
        )}
      </div>
      <form className="card" onSubmit={create}>
        <h2>New project</h2>
        <label htmlFor="pname">Name</label>
        <input id="pname" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || !name.trim()}>
            Create project
          </button>
        </div>
      </form>
    </>
  );
}
