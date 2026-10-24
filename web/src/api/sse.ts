// Server-sent events over fetch streaming. EventSource cannot send an Authorization header, so the stream is read
// with fetch + ReadableStream and parsed here (WHATWG "event stream interpretation").

export interface SseMessage {
  id?: string;
  event: string;
  data: string;
  retry?: number;
}

/** Incremental parser: feed text chunks, get complete messages. Handles CR, LF and CRLF, split chunks, comments. */
export class SseParser {
  private buf = "";
  private skipLf = false;
  private id: string | undefined;
  private event = "";
  private data: string[] = [];
  private retry: number | undefined;
  /** Last event id seen (survives across messages, per spec). */
  lastEventId: string | undefined;

  feed(chunk: string): SseMessage[] {
    const out: SseMessage[] = [];
    let s = this.buf + chunk;
    if (this.skipLf && s.startsWith("\n")) s = s.slice(1);
    this.skipLf = false;
    let start = 0;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c !== "\n" && c !== "\r") continue;
      if (c === "\r") {
        if (i + 1 < s.length) {
          if (s[i + 1] === "\n") {
            this.line(s.slice(start, i), out);
            i++;
            start = i + 1;
            continue;
          }
        } else {
          // a trailing CR: the LF of a CRLF may arrive in the next chunk
          this.line(s.slice(start, i), out);
          this.skipLf = true;
          start = i + 1;
          continue;
        }
      }
      this.line(s.slice(start, i), out);
      start = i + 1;
    }
    this.buf = s.slice(start);
    return out;
  }

  private line(l: string, out: SseMessage[]): void {
    if (l === "") {
      if (this.data.length || this.event) {
        const m: SseMessage = { event: this.event || "message", data: this.data.join("\n") };
        if (this.id !== undefined) m.id = this.id;
        if (this.retry !== undefined) m.retry = this.retry;
        out.push(m);
      }
      this.event = "";
      this.data = [];
      this.retry = undefined;
      this.id = undefined;
      return;
    }
    if (l[0] === ":") return; // comment / keep-alive
    const k = l.indexOf(":");
    const field = k < 0 ? l : l.slice(0, k);
    let value = k < 0 ? "" : l.slice(k + 1);
    if (value[0] === " ") value = value.slice(1);
    switch (field) {
      case "event":
        this.event = value;
        break;
      case "data":
        this.data.push(value);
        break;
      case "id":
        if (!value.includes("\0")) {
          this.id = value;
          this.lastEventId = value;
        }
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
    }
  }
}

export type StreamState = "connecting" | "open" | "reconnecting" | "closed";

export interface FollowOptions {
  /** Path under /api/v1, e.g. `/runs/<id>/events`. */
  path: string;
  onMessage: (m: SseMessage) => void;
  onState?: (s: StreamState, info?: { attempt: number; delayMs: number; error?: string }) => void;
  /** Stop for good after a message for which this returns true (run.completed|failed|cancelled). */
  isTerminal?: (m: SseMessage) => boolean;
  signal: AbortSignal;
  lastEventId?: string;
  /** Returns the current access token (or null). */
  getToken: () => string | null;
  /** Called on a 401: refresh the session; true if a retry makes sense. */
  refresh?: () => Promise<boolean>;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

export function backoffMs(attempt: number, base = 500, max = 15000, random: () => number = Math.random): number {
  const exp = Math.min(max, base * 2 ** Math.max(0, attempt - 1));
  return Math.round(exp / 2 + random() * (exp / 2)); // 50-100 % of the exponential step ("equal jitter")
}

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(done, ms);
    function done() {
      clearTimeout(t);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

/**
 * Follow an SSE endpoint until a terminal event, an abort, or a fatal HTTP status (403, 404). Reconnects with
 * exponential backoff and jitter after a network error, a 5xx or a clean EOF, sending `Last-Event-ID` so the server
 * replays only what was missed. A 401 triggers one refresh, then the stream is retried.
 */
export async function follow(o: FollowOptions): Promise<void> {
  const f = o.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const sleep = o.sleep ?? abortableSleep;
  const parser = new SseParser();
  parser.lastEventId = o.lastEventId;
  let attempt = 0;
  let refreshed = false;
  o.onState?.("connecting");
  while (!o.signal.aborted) {
    let reason = "stream ended";
    try {
      const headers: Record<string, string> = { accept: "text/event-stream" };
      const tok = o.getToken();
      if (tok) headers.authorization = `Bearer ${tok}`;
      if (parser.lastEventId) headers["last-event-id"] = parser.lastEventId;
      const r = await f(`${import.meta.env.BASE_URL}api/v1${o.path}`, { headers, signal: o.signal });
      if (r.status === 401 && o.refresh && !refreshed) {
        refreshed = true;
        if (await o.refresh()) continue;
      }
      if (r.status === 401 || r.status === 403 || r.status === 404 || r.status === 422) {
        o.onState?.("closed", { attempt, delayMs: 0, error: `HTTP ${r.status}` });
        return;
      }
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      refreshed = false;
      attempt = 0;
      o.onState?.("open");
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const m of parser.feed(dec.decode(value, { stream: true }))) {
          if (m.retry !== undefined) o.baseDelayMs = m.retry;
          o.onMessage(m);
          if (o.isTerminal?.(m)) {
            o.onState?.("closed");
            await reader.cancel().catch(() => undefined);
            return;
          }
        }
      }
    } catch (e) {
      if (o.signal.aborted) break;
      reason = e instanceof Error ? e.message : String(e);
    }
    if (o.signal.aborted) break;
    attempt += 1;
    const delayMs = backoffMs(attempt, o.baseDelayMs, o.maxDelayMs, o.random);
    o.onState?.("reconnecting", { attempt, delayMs, error: reason });
    await sleep(delayMs, o.signal);
  }
  o.onState?.("closed");
}
