// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Question } from "../api/api";

const sendChat = vi.fn(async (_id: string, _text: string) => ({}));
const answer = vi.fn(async (_id: string, _a: Record<string, string>) => ({}));
vi.mock("../api/api", async (orig) => {
  const real = await orig<typeof import("../api/api")>();
  return { ...real, runs: { ...real.runs, sendChat: (id: string, t: string) => sendChat(id, t), answer: (id: string, a: Record<string, string>) => answer(id, a) } };
});

import { ChatPanel, ChatThread, DefaultBadge, DefaultedQuestions } from "./RunChat";
import { eventLine } from "./RunPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const msg = (over: Partial<ChatMessage>): ChatMessage => ({
  id: "m1",
  run_id: "r1",
  role: "user",
  body: "use a 5 V rail",
  user_id: "u1",
  stage: null,
  created_at: "2026-10-24T10:00:00Z",
  delivered_at: "2026-10-24T10:00:05Z",
  ...over,
});

const question = (over: Partial<Question> = {}): Question => ({
  id: "q1",
  run_id: "r1",
  stage: "requirements",
  status: "answered",
  questions: [{ id: "bat", text: "Battery?", options: ["CR2032", "AA"], recommended: "AA", critical: false }],
  answers: { bat: "AA" },
  answered_by: null,
  asked_at: "2026-10-24T10:00:00Z",
  answered_at: "2026-10-24T11:00:00Z",
  auto_answered: true,
  default_answers: { bat: "AA" },
  overridden_at: null,
  ...over,
});

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  sendChat.mockClear();
  answer.mockClear();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function setValue(el: HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLTextAreaElement ? "input" : "change", { bubbles: true }));
}

describe("ChatThread", () => {
  it("shows both sides and marks a message the agent has not read yet as queued", () => {
    const html = renderToStaticMarkup(
      <ChatThread
        messages={[
          msg({}),
          msg({ id: "m2", role: "agent", body: "5 V rail noted.", stage: "circuit", user_id: null }),
          msg({ id: "m3", body: "and a fuse", delivered_at: null }),
        ]}
      />,
    );
    expect(html).toContain("use a 5 V rail");
    expect(html).toContain("5 V rail noted.");
    expect(html).toContain("Agent (circuit)");
    expect(html.match(/data-testid="queued"/g)).toHaveLength(1);
    expect(html.indexOf("and a fuse")).toBeGreaterThan(html.indexOf("5 V rail noted."));
  });
  it("says so when there is no message", () => {
    expect(renderToStaticMarkup(<ChatThread messages={[]} />)).toContain("No message yet");
  });
});

describe("ChatPanel", () => {
  it("sends a message and clears the box", async () => {
    const onSent = vi.fn(async () => {});
    act(() => root.render(<ChatPanel runId="r1" messages={[]} active onSent={onSent} />));
    const send = host.querySelector("button[type=submit]") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const box = host.querySelector("textarea") as HTMLTextAreaElement;
    act(() => setValue(box, "  make it 5 V  "));
    expect(send.disabled).toBe(false);
    await act(async () => {
      send.click();
    });
    expect(sendChat).toHaveBeenCalledWith("r1", "make it 5 V");
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(box.value).toBe("");
  });
  it("shows an error from the API and keeps the text", async () => {
    sendChat.mockRejectedValueOnce(new Error("run already succeeded"));
    act(() => root.render(<ChatPanel runId="r1" messages={[]} active onSent={async () => {}} />));
    act(() => setValue(host.querySelector("textarea") as HTMLTextAreaElement, "late"));
    await act(async () => {
      (host.querySelector("button[type=submit]") as HTMLButtonElement).click();
    });
    expect(host.querySelector("[role=alert]")?.textContent).toContain("run already succeeded");
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("late");
  });
  it("is read-only once the run has ended", () => {
    act(() => root.render(<ChatPanel runId="r1" messages={[msg({})]} active={false} onSent={async () => {}} />));
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.textContent).toContain("read-only");
    expect(host.textContent).toContain("use a 5 V rail");
  });
});

describe("answered by default", () => {
  it("badges a defaulted question and not an overridden one", () => {
    expect(renderToStaticMarkup(<DefaultBadge q={question()} />)).toContain("answered by default");
    expect(renderToStaticMarkup(<DefaultBadge q={question({ auto_answered: false, overridden_at: "2026-10-24T12:00:00Z" })} />)).toBe("");
    const html = renderToStaticMarkup(
      <DefaultedQuestions qs={[question(), question({ id: "q2", auto_answered: false, status: "answered" })]} active onChanged={async () => {}} />,
    );
    expect(html.match(/data-testid="default-badge"/g)).toHaveLength(1);
    expect(renderToStaticMarkup(<DefaultedQuestions qs={[question({ auto_answered: false })]} active onChanged={async () => {}} />)).toBe("");
  });
  it("lets the person replace the default while the run is active", async () => {
    const onChanged = vi.fn(async () => {});
    act(() => root.render(<DefaultedQuestions qs={[question()]} active onChanged={onChanged} />));
    const btn = Array.from(host.querySelectorAll("button")).find((b) => b.textContent === "Replace the default") as HTMLButtonElement;
    expect(btn.disabled).toBe(true); // nothing changed yet
    const sel = host.querySelector("select") as HTMLSelectElement;
    expect(sel.value).toBe("AA");
    act(() => setValue(sel, "CR2032"));
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
    });
    expect(answer).toHaveBeenCalledWith("q1", { bat: "CR2032" });
    expect(onChanged).toHaveBeenCalled();
  });
  it("only shows what was used once the run has ended", () => {
    act(() => root.render(<DefaultedQuestions qs={[question()]} active={false} onChanged={async () => {}} />));
    expect(host.querySelector("select")).toBeNull();
    expect(host.querySelector("button")).toBeNull();
    expect(host.textContent).toContain("AA");
  });
});

describe("eventLine for the timeout policy", () => {
  const ev = (event: string, data: object) => ({ event, data: JSON.stringify({ data }) });
  it("describes the new events", () => {
    expect(eventLine(ev("question.auto_answered", {}))).toContain("recommended option was used");
    expect(eventLine(ev("question.waiting_critical", {}))).toContain("critical");
    expect(eventLine(ev("question.answered", { overrides_default: true }))).toContain("replaced the default");
    expect(eventLine(ev("question.answered", {}))).toBe("answers received");
  });
});
