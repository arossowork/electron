import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { tenant } from "../api/api";
import { isAdmin, useMe } from "../auth";
import { Badge, ErrorBox, fmtTime, useLoad } from "../components/ui";

export function SettingsPage() {
  const me = useMe();
  const admin = isAdmin(me.role);
  return (
    <>
      <h1>Settings</h1>
      {admin ? (
        <>
          <ApiKey />
          <Members owner={me.role === "owner"} />
        </>
      ) : (
        <p className="muted">Only owners and admins manage the Claude key and the members of this tenant.</p>
      )}
    </>
  );
}

function ApiKey() {
  const status = useLoad(async () => {
    try {
      return await tenant.keyStatus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null; // no key stored
      throw e;
    }
  }, "key");
  const [key, setKey] = useState("");
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      await tenant.putKey(key);
      await status.reload();
    } catch (x) {
      setErr(x);
    }
    setKey(""); // the key is never kept in the page after the request
    setBusy(false);
  }
  async function remove() {
    if (!window.confirm("Delete the stored Claude API key? Runs that need it will wait.")) return;
    try {
      await tenant.deleteKey();
      await status.reload();
    } catch (x) {
      setErr(x);
    }
  }
  const s = status.data;
  return (
    <section className="card">
      <h2>Claude API key (BYOK)</h2>
      <p className="muted">
        Agent runs use your own Anthropic key. It is validated, encrypted at rest and never shown again.
      </p>
      <ErrorBox error={status.error} />
      {s ? (
        <p>
          Stored key ending in <code>…{s.last4}</code> <Badge value={s.status} /> <span className="muted">updated {fmtTime(s.rotated_at ?? s.created_at)}</span>
        </p>
      ) : (
        !status.loading && <div className="note">No key stored. Design runs cannot start the agent without one.</div>
      )}
      <form onSubmit={save}>
        <label htmlFor="apikey">{s ? "Replace key" : "Anthropic API key"}</label>
        <input id="apikey" type="password" autoComplete="off" spellCheck={false} placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || !key}>
            Save key
          </button>
          {s && (
            <button type="button" className="danger" onClick={remove}>
              Delete key
            </button>
          )}
        </div>
      </form>
    </section>
  );
}

function Members({ owner }: { owner: boolean }) {
  const members = useLoad(tenant.members, "members");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState<unknown>();
  const [sent, setSent] = useState("");
  async function invite(e: FormEvent) {
    e.preventDefault();
    setErr(undefined);
    setSent("");
    try {
      await tenant.invite(email, role);
      setSent(`If ${email} can be invited, an e-mail is on its way.`);
      setEmail("");
    } catch (x) {
      setErr(x);
    }
  }
  return (
    <section className="card">
      <h2>Members</h2>
      <ErrorBox error={members.error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>E-mail</th>
              <th>Name</th>
              <th>Role</th>
              <th>Since</th>
            </tr>
          </thead>
          <tbody>
            {members.data?.map((m) => (
              <tr key={m.user_id}>
                <td>{m.email}</td>
                <td>{m.display_name}</td>
                <td>
                  <Badge value={m.role} />
                </td>
                <td className="muted">{fmtTime(m.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form onSubmit={invite}>
        <h2 style={{ marginTop: 16 }}>Invite</h2>
        <div className="row">
          <div style={{ flex: 1, minWidth: 200 }}>
            <label htmlFor="inv-email">E-mail</label>
            <input id="inv-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label htmlFor="inv-role">Role</label>
            <select id="inv-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">member</option>
              {owner && <option value="admin">admin</option>}
            </select>
          </div>
        </div>
        <ErrorBox error={err} />
        {sent && <p className="muted">{sent}</p>}
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={!email}>
            Send invitation
          </button>
        </div>
      </form>
    </section>
  );
}
