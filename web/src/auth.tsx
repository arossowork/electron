import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { auth, type Me } from "./api/api";
import { session } from "./api/client";

type State = { status: "loading" } | { status: "anon" } | { status: "authed"; me: Me };

interface Ctx {
  state: State;
  /** Re-read /auth/me (after login, tenant switch). */
  refreshMe: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  const refreshMe = useCallback(async () => {
    try {
      setState({ status: "authed", me: await auth.me() });
    } catch {
      setState({ status: "anon" });
    }
  }, []);

  useEffect(() => {
    if (session.hasSession()) void refreshMe();
    else setState({ status: "anon" });
    // a cleared session (failed refresh, logout in this tab) sends the user to the login page
    return session.subscribe(() => {
      if (!session.hasSession()) setState({ status: "anon" });
    });
  }, [refreshMe]);

  const logout = useCallback(async () => {
    await auth.logout();
    setState({ status: "anon" });
  }, []);

  const value = useMemo(() => ({ state, refreshMe, logout }), [state, refreshMe, logout]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx {
  const c = useContext(AuthCtx);
  if (!c) throw new Error("AuthProvider missing");
  return c;
}

export function useMe(): Me {
  const { state } = useAuth();
  if (state.status !== "authed") throw new Error("not signed in");
  return state.me;
}

export const isAdmin = (role: string) => role === "owner" || role === "admin";
