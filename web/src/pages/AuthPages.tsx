import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { auth } from "../api/api";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";

function Frame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="narrow">
      <div className="card">
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

export function LoginPage() {
  const { state, refreshMe } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  if (state.status === "authed") return <Navigate to="/projects" replace />;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      await auth.login(email, pw);
      await refreshMe();
      nav("/projects");
    } catch (x) {
      setErr(x);
      setBusy(false);
    }
  }
  return (
    <Frame title="Sign in">
      <form onSubmit={submit}>
        <label htmlFor="email">E-mail</label>
        <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password" required value={pw} onChange={(e) => setPw(e.target.value)} />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy}>
            Sign in
          </button>
          <Link to="/signup">Create an account</Link>
        </div>
      </form>
    </Frame>
  );
}

export function SignupPage() {
  const { refreshMe } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({ email: "", password: "", tenant_name: "", display_name: "" });
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      await auth.signup(f);
      // With HWPLATFORM_DEV_AUTO_VERIFY the account exists already and login works; otherwise it answers 401.
      try {
        await auth.login(f.email, f.password);
        await refreshMe();
        nav("/projects");
        return;
      } catch (x) {
        if (!(x instanceof ApiError) || x.status !== 401) throw x;
      }
      setSent(true);
    } catch (x) {
      setErr(x);
    }
    setBusy(false);
  }
  if (sent)
    return (
      <Frame title="Check your e-mail">
        <p>
          We sent a verification link to <strong>{f.email}</strong>. Open it, or paste the token below.
        </p>
        <VerifyForm />
      </Frame>
    );
  return (
    <Frame title="Create an account">
      <form onSubmit={submit}>
        <label htmlFor="s-email">E-mail</label>
        <input id="s-email" type="email" autoComplete="username" required value={f.email} onChange={set("email")} />
        <label htmlFor="s-pw">Password (at least 12 characters)</label>
        <input id="s-pw" type="password" autoComplete="new-password" required minLength={12} value={f.password} onChange={set("password")} />
        <label htmlFor="s-name">Your name</label>
        <input id="s-name" autoComplete="name" value={f.display_name} onChange={set("display_name")} />
        <label htmlFor="s-tenant">Organisation</label>
        <input id="s-tenant" required value={f.tenant_name} onChange={set("tenant_name")} />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy}>
            Sign up
          </button>
          <Link to="/login">I have an account</Link>
        </div>
      </form>
    </Frame>
  );
}

function VerifyForm({ initial = "" }: { initial?: string }) {
  const { refreshMe } = useAuth();
  const nav = useNavigate();
  const [token, setToken] = useState(initial);
  const [err, setErr] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const auto = useRef(false);
  async function run() {
    setBusy(true);
    setErr(undefined);
    try {
      await auth.verify(token.trim());
      await refreshMe();
      nav("/projects");
    } catch (x) {
      setErr(x);
      setBusy(false);
    }
  }
  useEffect(() => {
    if (initial && !auto.current) {
      auto.current = true;
      void run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
    >
      <label htmlFor="token">Verification token</label>
      <input id="token" value={token} onChange={(e) => setToken(e.target.value)} required />
      <ErrorBox error={err} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" disabled={busy || !token}>
          Verify
        </button>
        <Link to="/login">Sign in</Link>
      </div>
    </form>
  );
}

export function VerifyPage() {
  const [q] = useSearchParams();
  return (
    <Frame title="Verify your e-mail">
      <VerifyForm initial={q.get("token") ?? ""} />
    </Frame>
  );
}

/** Reached from the invitation e-mail; the route is protected, so the invited user signs in first. */
export function AcceptInvitePage() {
  const [q] = useSearchParams();
  const { refreshMe } = useAuth();
  const nav = useNavigate();
  const [token, setToken] = useState(q.get("token") ?? "");
  const [err, setErr] = useState<unknown>();
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await auth.acceptInvite(token.trim());
      await refreshMe();
      nav("/settings");
    } catch (x) {
      setErr(x);
    }
  }
  return (
    <div className="card narrow">
      <h1>Accept invitation</h1>
      <form onSubmit={submit}>
        <label htmlFor="inv">Invitation token</label>
        <input id="inv" value={token} onChange={(e) => setToken(e.target.value)} required />
        <ErrorBox error={err} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary">Join</button>
        </div>
      </form>
    </div>
  );
}
