import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../components/Markdown";
import { buildUploadForm } from "./ProjectPage";
import { eventLine } from "./RunPage";

const file = (name: string) => new File(["x"], name);

describe("buildUploadForm", () => {
  it("maps dropped files to multipart parts and reports ignored ones", () => {
    const { form, used, ignored } = buildUploadForm(
      [file("design.yaml"), file("requirements.yaml"), file("a.md"), file("b.md"), file("pack.tar.gz"), file("x.png")],
      "first",
    );
    expect(used).toHaveLength(5);
    expect(ignored).toEqual(["x.png"]);
    expect((form.get("design") as File).name).toBe("design.yaml");
    expect((form.get("requirements") as File).name).toBe("requirements.yaml");
    expect(form.getAll("notes")).toHaveLength(2);
    expect((form.get("bundle") as File).name).toBe("pack.tar.gz");
    expect(form.get("message")).toBe("first");
  });
});

describe("renderMarkdown", () => {
  it("renders tables and code", () => {
    const h = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n`x`");
    expect(h).toContain("<table>");
    expect(h).toContain("<code>x</code>");
  });
  it("removes scripts, handlers, javascript: links and images", () => {
    const h = renderMarkdown(
      '<script>alert(1)</script><img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1)) <b onclick="x()">b</b> ![i](http://e/x.png) [ok](https://e.com)',
    );
    expect(h).not.toMatch(/<script|onerror|onclick|javascript:|<img/i);
    expect(h).toContain('href="https://e.com"');
    expect(h).toContain('rel="noopener noreferrer"');
  });
});

describe("eventLine", () => {
  const msg = (event: string, data: object) => ({ event, data: JSON.stringify({ data }) });
  it("describes stage and run events", () => {
    expect(eventLine(msg("stage.progress", { key: "layout", line: "attempt 2" }))).toBe("layout: attempt 2");
    expect(eventLine(msg("stage.completed", { key: "sim", verdict: "PASS", cached: true }))).toBe("sim PASS (cached)");
    expect(eventLine(msg("run.completed", {}))).toBe("run.completed");
    expect(eventLine({ event: "x", data: "not json" })).toBeNull();
  });
});

describe("evals client", () => {
  it("exposes the page and the endpoints", async () => {
    const { EvalsPage } = await import("./EvalsPage");
    const { evals } = await import("../api/api");
    expect(typeof EvalsPage).toBe("function");
    expect(Object.keys(evals).sort()).toEqual(["baselines", "get", "list", "promote", "report"]);
  });
});
