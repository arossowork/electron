import { useState, type FormEvent } from "react";
import { Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { auth } from "./api/api";
import { useAuth, useMe } from "./auth";
import { Dialog, ErrorBox, errText } from "./components/ui";
import { AcceptInvitePage, LoginPage, SignupPage, VerifyPage } from "./pages/AuthPages";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify" element={<VerifyPage />} />
      <Route element={<Protected />}>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:projectId" element={<ProjectPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function Protected() {
  const { state } = useAuth();
  if (state.status === "loading") return <main className="muted">Loading…</main>;
  if (state.status === "anon") return <Navigate to="/login" replace />;
  return <Shell />;
}

function Shell() {
  const me = useMe();
  const { logout } = useAuth();
  const nav = useNavigate();
  const [switchTo, setSwitchTo] = useState<string | null>(null);
  return (
    <div className="shell">
      <header>
        <Link className="brand" to="/projects">
          hwflow
        </Link>
        <Link to="/projects">Projects</Link>
        <Link to="/settings">Settings</Link>
        <span className="spacer" />
        {me.tenants.length > 1 ? (
          <select
            aria-label="Tenant"
            style={{ width: "auto" }}
            value={me.tenant_id}
            onChange={(e) => e.target.value !== me.tenant_id && setSwitchTo(e.target.value)}
          >
            {me.tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted">{me.tenants.find((t) => t.id === me.tenant_id)?.name}</span>
        )}
        <span className="muted" title={me.email}>
          {me.display_name || me.email} ({me.role})
        </span>
        <button
          onClick={async () => {
            await logout();
            nav("/login");
          }}
        >
          Sign out
        </button>
      </header>
      <main>
        <Outlet />
      </main>
      {switchTo && <SwitchTenant tenantId={switchTo} onClose={() => setSwitchTo(null)} />}
    </div>
  );
}

/** The API has no switch endpoint: a login with `tenant_id` issues tokens for that tenant, so ask for the password. */
function SwitchTenant({ tenantId, onClose }: { tenantId: string; onClose: () => void }) {
  const me = useMe();
  const { refreshMe } = useAuth();
  const nav = useNavigate();
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const name = me.tenants.find((t) => t.id === tenantId)?.name;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await auth.login(me.email, pw, tenantId);
      await refreshMe();
      onClose();
      nav("/projects");
    } catch (x) {
      setErr(errText(x));
      setBusy(false);
    }
  }
  return (
    <Dialog onClose={onClose}>
      <form onSubmit={submit}>
        <h2>Switch to {name}</h2>
        <p className="muted">Confirm your password to get a session for this tenant.</p>
        <label htmlFor="sw-pw">Password</label>
        <input id="sw-pw" type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || !pw}>
            Switch
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

