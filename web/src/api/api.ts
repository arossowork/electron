// Typed endpoint functions. Shapes come from the generated schema.d.ts.
import { del, get, post, put, rawFetch, session, type TokenOut } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type Tenant = S["TenantOut"];
export type Member = S["MemberOut"];
export type Project = S["ProjectOut"];
export type DesignVersion = S["DesignVersionOut"];
export type DesignVersionDetail = S["DesignVersionDetailOut"];
export type Run = S["RunOut"];
export type RunSummary = S["RunSummaryOut"];
export type Stage = S["StageOut"];
export type EvalSummary = S["EvalSummaryOut"];
export type EvalDetail = S["EvalOut"];
export type Baseline = S["BaselineOut"];
export type Artifact = S["ArtifactOut"];
export type Question = S["QuestionOut"];
export type ChatMessage = S["ChatMessageOut"];
export type LlmKey = S["LlmKeyOut"];
export type CredentialType = NonNullable<S["LlmKeyIn"]["credential_type"]>;
export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
}
/** One entry of QuestionOut.questions (ADR 0001, `question.asked`). */
export interface QuestionItem {
  id: string;
  text: string;
  options?: string[];
  /** The agent's pick; an unanswered non-critical question gets it after the timeout. */
  recommended?: string;
  /** Critical: the run waits for the person without a limit. */
  critical?: boolean;
  default?: string;
  why?: string;
}

export const auth = {
  signup: (b: S["SignupIn"]) => post<S["AcceptedOut"]>("/auth/signup", b, { auth: false }),
  verify: async (token: string) => {
    const t = await post<TokenOut>("/auth/verify", { token }, { auth: false });
    session.set(t);
    return t;
  },
  login: async (email: string, password: string, tenant_id?: string) => {
    const t = await post<TokenOut>("/auth/login", { email, password, tenant_id }, { auth: false });
    session.set(t);
    return t;
  },
  logout: async () => {
    try {
      await post("/auth/logout");
    } catch {
      /* the session is dropped locally in any case */
    }
    session.clear();
  },
  /** Session in another tenant of the user, from the refresh token (no password). */
  switchTenant: (tenant_id: string) => session.switchTenant(tenant_id),
  me: () => get<Me>("/auth/me"),
  acceptInvite: (token: string) => post<S["AcceptInviteOut"]>("/auth/accept-invite", { token }),
};

export const tenant = {
  get: () => get<Tenant>("/tenant"),
  members: () => get<Member[]>("/tenant/members"),
  invite: (email: string, role: string) => post<S["AcceptedOut"]>("/tenant/members", { email, role }),
  keyStatus: () => get<LlmKey>("/tenant/llm-keys/anthropic"),
  putKey: (api_key: string, credential_type: CredentialType = "api_key") =>
    put<LlmKey>("/tenant/llm-keys/anthropic", { api_key, credential_type }),
  deleteKey: () => del<void>("/tenant/llm-keys/anthropic"),
};

export const projects = {
  list: () => get<Project[]>("/projects"),
  create: (name: string) => post<Project>("/projects", { name }),
  get: (id: string) => get<Project>(`/projects/${id}`),
  remove: (id: string) => del<void>(`/projects/${id}`),
  versions: (id: string) => get<Page<DesignVersion>>(`/projects/${id}/design-versions`, { query: { limit: 50 } }),
  upload: (id: string, form: FormData) => post<DesignVersion>(`/projects/${id}/design-versions`, undefined, { form }),
  runs: (id: string) => get<Page<RunSummary>>(`/projects/${id}/runs`, { query: { limit: 50 } }),
  startRun: (id: string, body: S["RunIn"]) => post<Run>(`/projects/${id}/runs`, body),
};

export const runs = {
  get: (id: string) => get<Run>(`/runs/${id}`),
  cancel: (id: string) => post<unknown>(`/runs/${id}/cancel`),
  report: (id: string) => get<string>(`/runs/${id}/report`),
  artifacts: (id: string) => get<Artifact[]>(`/runs/${id}/artifacts`),
  questions: (id: string) => get<Question[]>(`/runs/${id}/questions`),
  answer: (qid: string, answers: Record<string, string>) => post<unknown>(`/questions/${qid}/answer`, { answers }),
  chat: (id: string) => get<ChatMessage[]>(`/runs/${id}/chat`),
  sendChat: (id: string, text: string) => post<ChatMessage>(`/runs/${id}/chat`, { text }),
};

export const evals = {
  list: () => get<Page<EvalSummary>>("/evals?limit=100"),
  get: (id: string) => get<EvalDetail>(`/evals/${id}`),
  report: (id: string) => get<string>(`/evals/${id}/report`),
  baselines: () => get<Baseline[]>("/evals/baselines"),
  promote: (id: string, name: string) => post<Baseline>(`/evals/${id}/promote-baseline`, { name }),
};

/**
 * Download through fetch so the Authorization header is sent. The API answers 302 to a presigned URL (the browser
 * follows it and drops the header for another origin) or streams the bytes when no public S3 URL is set.
 */
export async function download(path: string, filename: string): Promise<void> {
  const r = await rawFetch("GET", path);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const artifactPath = (runId: string, name: string) => `/runs/${runId}/artifacts/${name.split("/").map(encodeURIComponent).join("/")}`;

export const TERMINAL_RUN = new Set(["succeeded", "failed", "cancelled"]);
