// Thin fetch wrapper around the platform API (/api/v1). Types come from schema.d.ts (npm run gen:api).
//
// Token handling (services/platform/README.md, "Auth flow"): login returns an access JWT (15 min) and an opaque,
// rotating refresh token in the JSON body (there is no cookie). The access token lives only in memory. The refresh
// token is kept in memory and in sessionStorage (per tab, gone when the tab closes) so a reload keeps the session.
// Trade-off: script on the page (XSS) can read sessionStorage, so the CSP in nginx.conf is the main defence; a
// stolen refresh token is detected by the API (reuse of a rotated token revokes the family).

import type { components } from "./schema";

export type TokenOut = components["schemas"]["TokenOut"];

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

const RT_KEY = "hwflow.refresh";
const API = "/api/v1";

let access: string | null = null;
let refreshToken: string | null = null;
let inflight: Promise<boolean> | null = null;
const listeners = new Set<() => void>();

function readStore(): string | null {
  try {
    return sessionStorage.getItem(RT_KEY);
  } catch {
    return null;
  }
}
function writeStore(v: string | null): void {
  try {
    if (v === null) sessionStorage.removeItem(RT_KEY);
    else sessionStorage.setItem(RT_KEY, v);
  } catch {
    /* storage blocked: the session then lasts until reload */
  }
}
function emit(): void {
  listeners.forEach((l) => l());
}

export const session = {
  /** True when a refresh token exists (a restore or a refresh may succeed). */
  hasSession: () => refreshToken !== null || readStore() !== null,
  accessToken: () => access,
  set(t: TokenOut): void {
    access = t.access_token;
    refreshToken = t.refresh_token;
    writeStore(t.refresh_token);
    emit();
  },
  clear(): void {
    access = null;
    refreshToken = null;
    inflight = null;
    writeStore(null);
    emit();
  },
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  /** Exchange the refresh token for new tokens. One call at a time: the token is single-use. */
  refresh(): Promise<boolean> {
    if (inflight) return inflight;
    const rt = refreshToken ?? readStore();
    if (!rt) return Promise.resolve(false);
    inflight = (async () => {
      try {
        const r = await fetch(`${API}/auth/refresh`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ refresh_token: rt }),
        });
        if (r.ok) {
          session.set((await r.json()) as TokenOut);
          return true;
        }
        if (r.status === 401 || r.status === 403 || r.status === 422) session.clear();
        return false;
      } catch {
        return false; // network error: keep the token, the caller may retry
      } finally {
        inflight = null;
      }
    })();
    return inflight;
  },
  /**
   * Open a session in another tenant of the user with the refresh token (POST /auth/switch-tenant), no password.
   * The refresh token is single-use, so this waits for a refresh in flight and blocks new ones meanwhile.
   * Throws ApiError: 403 (not a member) keeps the current session, 401 ends it.
   */
  async switchTenant(tenantId: string): Promise<void> {
    if (inflight) await inflight;
    const rt = refreshToken ?? readStore();
    if (!rt) throw new ApiError(401, "not signed in");
    let failure: ApiError | null = null;
    inflight = (async () => {
      try {
        const r = await fetch(`${API}/auth/switch-tenant`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ refresh_token: rt, tenant_id: tenantId }),
        });
        if (r.ok) {
          session.set((await r.json()) as TokenOut);
          return true;
        }
        if (r.status === 401) session.clear();
        failure = await errorOf(r);
        return false;
      } catch {
        failure = new ApiError(0, "network error");
        return false;
      } finally {
        inflight = null;
      }
    })();
    if (!(await inflight) && failure) throw failure;
  },
};

export interface RequestOpts {
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | number | undefined | null>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  auth?: boolean; // default true
}

export function buildUrl(path: string, query?: RequestOpts["query"]): string {
  const u = `${API}${path}`;
  if (!query) return u;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `${u}?${s}` : u;
}

async function errorOf(r: Response): Promise<ApiError> {
  let detail = r.statusText || `HTTP ${r.status}`;
  try {
    const b = await r.clone().json();
    const d = (b as { detail?: unknown }).detail;
    if (typeof d === "string") detail = d;
    else if (Array.isArray(d)) detail = d.map((x) => (x as { msg?: string }).msg ?? "invalid").join("; ");
    else if (d && typeof d === "object") detail = (d as { message?: string; error?: string }).message ?? (d as { error?: string }).error ?? detail;
  } catch {
    /* not JSON */
  }
  return new ApiError(r.status, detail);
}

/** fetch with the bearer token; on 401 refreshes once and retries. Returns the raw Response (2xx only). */
export async function rawFetch(method: string, path: string, o: RequestOpts = {}): Promise<Response> {
  const go = (): Promise<Response> => {
    const headers: Record<string, string> = { ...(o.headers ?? {}) };
    if (o.auth !== false && access) headers.authorization = `Bearer ${access}`;
    let body: BodyInit | undefined;
    if (o.form) body = o.form;
    else if (o.json !== undefined) {
      headers["content-type"] = "application/json";
      body = JSON.stringify(o.json);
    }
    return fetch(buildUrl(path, o.query), { method, headers, body, signal: o.signal });
  };
  if (o.auth !== false && !access && session.hasSession()) await session.refresh();
  let r = await go();
  if (r.status === 401 && o.auth !== false && session.hasSession()) {
    if (await session.refresh()) r = await go();
  }
  if (r.status === 401 && o.auth !== false) session.clear();
  if (!r.ok) throw await errorOf(r);
  return r;
}

export async function request<T>(method: string, path: string, o: RequestOpts = {}): Promise<T> {
  const r = await rawFetch(method, path, o);
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get("content-type") ?? "";
  if (ct.includes("json")) return (await r.json()) as T;
  return (await r.text()) as T;
}

export const get = <T>(path: string, o?: RequestOpts) => request<T>("GET", path, o);
export const post = <T>(path: string, json?: unknown, o?: RequestOpts) => request<T>("POST", path, { ...o, json });
export const put = <T>(path: string, json?: unknown, o?: RequestOpts) => request<T>("PUT", path, { ...o, json });
export const del = <T>(path: string, o?: RequestOpts) => request<T>("DELETE", path, o);
