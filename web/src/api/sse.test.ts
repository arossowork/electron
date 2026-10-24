import { describe, expect, it, vi } from "vitest";
import { backoffMs, follow, SseParser, type SseMessage } from "./sse";

describe("SseParser", () => {
  it("parses id, event and multi-line data", () => {
    const p = new SseParser();
    const out = p.feed("id: 3\nevent: stage.started\ndata: a\ndata: b\n\n");
    expect(out).toEqual([{ id: "3", event: "stage.started", data: "a\nb" }]);
    expect(p.lastEventId).toBe("3");
  });

  it("handles chunks split anywhere, CRLF and comments", () => {
    const p = new SseParser();
    const text = ": keep-alive\r\n\r\nid: 1\r\nevent: x\r\ndata: {\"a\":1}\r\n\r\nid: 2\r\ndata: y\r\n\r\n";
    for (let size = 1; size <= text.length; size++) {
      const q = new SseParser();
      const got: SseMessage[] = [];
      for (let i = 0; i < text.length; i += size) got.push(...q.feed(text.slice(i, i + size)));
      expect(got.map((m) => [m.id, m.event, m.data])).toEqual([
        ["1", "x", '{"a":1}'],
        ["2", "message", "y"],
      ]);
    }
    expect(p.feed(text)).toHaveLength(2);
  });

  it("handles a bare CR as a line end and a CR at the chunk end", () => {
    const p = new SseParser();
    expect(p.feed("data: a\r")).toEqual([]);
    expect(p.feed("\n\r")).toEqual([{ event: "message", data: "a" }]);
  });

  it("keeps lastEventId across messages and reads retry", () => {
    const p = new SseParser();
    const [m] = p.feed("retry: 2500\nid: 9\ndata: x\n\n");
    expect(m.retry).toBe(2500);
    p.feed("data: y\n\n");
    expect(p.lastEventId).toBe("9");
  });

  it("drops an incomplete trailing message", () => {
    const p = new SseParser();
    expect(p.feed("id: 1\ndata: partial")).toEqual([]);
  });
});

describe("backoffMs", () => {
  it("grows exponentially, is capped and jittered between 50 and 100 percent", () => {
    expect(backoffMs(1, 500, 15000, () => 0)).toBe(250);
    expect(backoffMs(1, 500, 15000, () => 1)).toBe(500);
    expect(backoffMs(3, 500, 15000, () => 1)).toBe(2000);
    expect(backoffMs(20, 500, 15000, () => 1)).toBe(15000);
  });
});

function streamOf(...chunks: string[]): Response {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(c) {
        for (const x of chunks) c.enqueue(enc.encode(x));
        c.close();
      },
    }),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );
}

describe("follow", () => {
  const base = () => ({ path: "/runs/r/events", getToken: () => "tok", random: () => 0, sleep: vi.fn(async () => undefined) });

  it("resumes with Last-Event-ID after the stream ends, backs off, and stops at a terminal event", async () => {
    const calls: Array<Record<string, string>> = [];
    const responses = [
      () => streamOf("id: 1\nevent: stage.started\ndata: {}\n\n", "id: 2\nevent: stage.progress\ndata: {}\n\n"),
      () => {
        throw new TypeError("network down");
      },
      () => streamOf("id: 3\nevent: run.completed\ndata: {}\n\n"),
    ];
    const fetchImpl = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      calls.push(init?.headers as Record<string, string>);
      return responses.shift()!();
    });
    const seen: string[] = [];
    const states: string[] = [];
    const b = base();
    await follow({
      ...b,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      signal: new AbortController().signal,
      onMessage: (m) => seen.push(`${m.id}:${m.event}`),
      onState: (s) => states.push(s),
      isTerminal: (m) => m.event === "run.completed",
    });
    expect(seen).toEqual(["1:stage.started", "2:stage.progress", "3:run.completed"]);
    expect(calls[0]["last-event-id"]).toBeUndefined();
    expect(calls[0].authorization).toBe("Bearer tok");
    expect(calls[1]["last-event-id"]).toBe("2");
    expect(calls[2]["last-event-id"]).toBe("2");
    expect(b.sleep).toHaveBeenCalledTimes(2);
    expect(states).toEqual(["connecting", "open", "reconnecting", "reconnecting", "open", "closed"]);
  });

  it("refreshes once on 401 and retries", async () => {
    let token = "old";
    const refresh = vi.fn(async () => ((token = "new"), true));
    const fetchImpl = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string>).authorization;
      return auth === "Bearer new" ? streamOf("id: 1\nevent: run.failed\ndata: {}\n\n") : new Response("", { status: 401 });
    });
    const seen: string[] = [];
    await follow({
      ...base(),
      getToken: () => token,
      refresh,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      signal: new AbortController().signal,
      onMessage: (m) => seen.push(m.event),
      isTerminal: (m) => m.event === "run.failed",
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(["run.failed"]);
  });

  it("gives up on 404 and stops when aborted", async () => {
    const states: string[] = [];
    await follow({
      ...base(),
      fetchImpl: (async () => new Response("", { status: 404 })) as unknown as typeof fetch,
      signal: new AbortController().signal,
      onMessage: () => undefined,
      onState: (s) => states.push(s),
    });
    expect(states.at(-1)).toBe("closed");

    const ac = new AbortController();
    const fetchImpl = vi.fn(async () => new Response("", { status: 503 }));
    await follow({
      ...base(),
      sleep: async () => ac.abort(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
      signal: ac.signal,
      onMessage: () => undefined,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
