import { useState } from "react";
import { Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { auth } from "./api/api";
import { useAuth, useMe } from "./auth";
import { ErrorBox } from "./components/ui";
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
  const { logout, refreshMe } = useAuth();
  const nav = useNavigate();
  const [switchErr, setSwitchErr] = useState<unknown>();
  async function switchTo(tenantId: string) {
    setSwitchErr(undefined);
    try {
      await auth.switchTenant(tenantId);
      await refreshMe();
      nav("/projects");
    } catch (x) {
      setSwitchErr(x);
    }
  }
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
            onChange={(e) => e.target.value !== me.tenant_id && void switchTo(e.target.value)}
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
        <ErrorBox error={switchErr} />
        <Outlet />
      </main>
    </div>
  );
}

