import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, buildUrl, get, session } from "./client";

const tok = (a: string, r: string) => ({ access_token: a, refresh_token: r, token_type: "bearer" as const, expires_in: 900, tenant_id: "t", user_id: "u" });
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

beforeEach(() => session.clear());
afterEach(() => vi.unstubAllGlobals());

describe("buildUrl", () => {
  it("adds the prefix and skips empty query values", () => {
    expect(buildUrl("/x", { a: 1, b: "", c: undefined, d: "y z" })).toBe("/api/v1/x?a=1&d=y+z");
    expect(buildUrl("/x")).toBe("/api/v1/x");
  });
});

describe("session", () => {
  it("keeps the refresh token in sessionStorage and the access token only in memory", () => {
    session.set(tok("A", "R"));
    expect(sessionStorage.getItem("hwflow.refresh")).toBe("R");
    expect(JSON.stringify(sessionStorage)).not.toContain("A\"");
    expect(session.accessToken()).toBe("A");
    session.clear();
    expect(sessionStorage.getItem("hwflow.refresh")).toBeNull();
    expect(session.hasSession()).toBe(false);
  });

  it("refreshes at most once for concurrent callers (the token is single-use)", async () => {
    session.set(tok("A", "R1"));
    const f = vi.fn(async () => json(tok("B", "R2")));
    vi.stubGlobal("fetch", f);
    const r = await Promise.all([session.refresh(), session.refresh(), session.refresh()]);
    expect(r).toEqual([true, true, true]);
    expect(f).toHaveBeenCalledTimes(1);
    expect(session.accessToken()).toBe("B");
    expect(sessionStorage.getItem("hwflow.refresh")).toBe("R2");
  });

  it("clears the session when the refresh token is refused, keeps it on a network error", async () => {
    session.set(tok("A", "R"));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    expect(await session.refresh()).toBe(false);
    expect(session.hasSession()).toBe(true);
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "invalid refresh token" }, 401)));
    expect(await session.refresh()).toBe(false);
    expect(session.hasSession()).toBe(false);
  });
});

describe("request", () => {
  it("sends the bearer token, refreshes on 401 and retries once", async () => {
    session.set(tok("old", "R1"));
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        if (url.endsWith("/auth/refresh")) return json(tok("new", "R2"));
        const a = (init.headers as Record<string, string>).authorization;
        seen.push(a);
        return a === "Bearer new" ? json({ ok: true }) : json({ detail: "expired" }, 401);
      }),
    );
    expect(await get("/auth/me")).toEqual({ ok: true });
    expect(seen).toEqual(["Bearer old", "Bearer new"]);
  });

  it("turns error bodies into ApiError with the detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "quota_exceeded" }, 429)));
    await expect(get("/x", { auth: false })).rejects.toMatchObject({ status: 429, detail: "quota_exceeded" });
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: [{ msg: "bad a" }, { msg: "bad b" }] }, 422)));
    const e = (await get("/x", { auth: false }).catch((x) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.detail).toBe("bad a; bad b");
  });

  it("returns text for text/markdown", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("# hi", { headers: { "content-type": "text/markdown" } })));
    expect(await get<string>("/runs/1/report", { auth: false })).toBe("# hi");
  });
});
