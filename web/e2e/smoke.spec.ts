import { expect, test, type Page } from "@playwright/test";

// Screenshots for the PR: E2E_SHOTS=../docs/screenshots/web npx playwright test
const shots = process.env.E2E_SHOTS;
const shot = async (page: Page, name: string) => {
  if (shots) await page.screenshot({ path: `${shots}/${name}.png` });
};

// a failed test restarts the worker and reloads this module: keep the identity in the environment
process.env.E2E_STAMP ??= String(Date.now());
const stamp = process.env.E2E_STAMP;
const email = `e2e-${stamp}@example.com`;
const password = "e2e-long-password-123";

async function signup(page: Page) {
  await page.goto("/signup");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel(/^Password/).fill(password);
  await page.getByLabel("Your name").fill("E2E User");
  await page.getByLabel("Organisation").fill(`Org ${stamp}`);
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
}

test("signup, project, design upload, run queued, cancel", async ({ page }) => {
  await signup(page);
  await page.getByLabel("Name").fill("Demo board");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "Demo board" })).toBeVisible();

  // drag & drop is the same code path as the file chooser: the drop handler and the input both call send()
  await page.getByLabel("Message (optional)").fill("first cut");
  await page.getByTestId("file-input").setInputFiles({
    name: "design.yaml",
    mimeType: "application/x-yaml",
    buffer: Buffer.from("name: demo\nparts: []\n"),
  });
  await expect(page.getByText(/Version #1/)).toBeVisible();
  await expect(page.getByRole("row", { name: /first cut/ })).toBeVisible();
  await shot(page, "project");

  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page).toHaveURL(/\/runs\//);
  // No worker in this stack: the run stays queued and the page says so.
  await expect(page.getByRole("heading", { name: /Run/ }).getByText("queued")).toBeVisible();
  await expect(page.getByTestId("no-stages")).toContainText("Queued");
  await expect(page.getByTestId("stream-state")).toHaveText(/live/);
  await shot(page, "run-queued");

  // The API accepts the cancel (202) and signals the workflow; without a worker nothing flips the status.
  page.once("dialog", (d) => d.accept());
  const cancelled = page.waitForResponse((r) => r.url().endsWith("/cancel") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Cancel run" }).click();
  expect((await cancelled).status()).toBe(202);
});

test("settings: key status, members; sign out and in", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Claude API key (BYOK)" })).toBeVisible();
  await expect(page.getByText("No key stored")).toBeVisible();
  await expect(page.getByRole("cell", { name: email })).toBeVisible();
  await shot(page, "settings");
  // a reload keeps the session (refresh token in sessionStorage)
  await page.reload();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

// No workflow runs in the stack, so the question flow is exercised with mocked API answers.
test("question panel posts the answers (mocked)", async ({ page }) => {
  await signup(page);
  const runId = "01900000-0000-7000-8000-000000000001";
  const run = {
    id: runId, project_id: "p", design_version_id: "v", kind: "design", status: "waiting", verdict: null,
    options_hash: "x", parent_run_id: null, created_at: new Date().toISOString(), started_at: null, ended_at: null,
    workflow_id: "w", options: {}, error: null, artifacts: [],
    stages: [{ key: "requirements", stage: "requirements", seq: 1, status: "running", verdict: null, cached: false, attempt: 1, started_at: null, ended_at: null, seconds: null, summary: {}, error: null }],
  };
  let open = true;
  let posted: unknown;
  await page.route(`**/api/v1/runs/${runId}`, (r) => r.fulfill({ json: run }));
  await page.route(`**/api/v1/runs/${runId}/events`, (r) =>
    r.fulfill({ status: 200, contentType: "text/event-stream", body: `id: 1\nevent: question.asked\ndata: ${JSON.stringify({ data: {} })}\n\n` }),
  );
  await page.route(`**/api/v1/runs/${runId}/questions`, (r) =>
    r.fulfill({
      json: [{
        id: "q1", run_id: runId, stage: "requirements", status: open ? "open" : "answered",
        questions: [
          { id: "supply", text: "Which supply voltage?", options: ["3.3 V", "5 V"] },
          { id: "note", text: "Anything else?", default: "no" },
        ],
        answers: null, answered_by: null, asked_at: new Date().toISOString(), answered_at: null,
      }],
    }),
  );
  await page.route("**/api/v1/questions/q1/answer", (r) => {
    posted = r.request().postDataJSON();
    open = false;
    return r.fulfill({ json: { detail: "ok" } });
  });
  await page.goto(`/runs/${runId}`);
  await expect(page.getByTestId("questions")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send answers" })).toBeDisabled();
  await page.getByLabel("Which supply voltage?").selectOption("5 V");
  await shot(page, "questions");
  await page.getByRole("button", { name: "Send answers" }).click();
  await expect(page.getByTestId("questions")).toBeHidden();
  expect(posted).toEqual({ answers: { supply: "5 V", note: "no" } });
});
