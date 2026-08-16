import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ---- types -------------------------------------------------------------
/** One button on an inbox card. The label names the outcome, not the mechanism. */
export interface InboxAction {
  label: string;
  value: string;
  tone: "accept" | "reject" | "neutral";
  confirm?: string | null;
}

/** One side of a duplicate: which copy, from where, carrying how much. */
export interface DuplicateSide {
  id: number;
  name: string;
  type: string;
  source: string | null;
  canonical_key: string | null;
  created_at: string | null;
  knowledge: number;
  stale: boolean;
}

export interface DuplicatePlan {
  action: "discard" | "merge" | "gone";
  effect: string;
  keep: DuplicateSide | null;
  drop: DuplicateSide[];
}

export interface InboxItem {
  /** Which source produced this. The named ones are what the resolver knows how to route;
   *  any plugin may register a source with an origin core has never heard of, so the type
   *  stays open and unknown origins fall through to the world-model review endpoint. */
  origin: "world_model" | "curator" | "curator_question" | "herald" | (string & {});
  id: number;
  created_at: string;
  prov_agent: string;
  prov_label: string;
  prov_uri?: string | null;
  /** What this is, in a sentence. */
  title?: string;
  /** What accepting will actually do — always shown before the buttons. */
  effect?: string;
  body?: string;
  actions?: InboxAction[];
  answerable?: boolean;
  // world_model
  item_type?: string;
  confidence?: number | null;
  plan?: DuplicatePlan;
  payload?: {
    kind?: string;
    value?: string;
    entity?: string;
    quote?: string;
    key?: string;
    source?: string;
    [k: string]: unknown;
  };
  // curator
  kind?: string;
  diff?: string;
}

export interface RunLog {
  ok: boolean;
  result: string;
  at: string;
  seconds: number;
}

/** One pass an agent runs: what it does, when, and how the last run went. */
export interface Job {
  name: string;
  label: string;
  description: string;
  agent: string;
  cron: string;
  enabled: boolean;
  limit: number | null;
  limit_default: number | null;
  next_run: string | null;
  last_run: string | null;
  running_since: string | null;
  queued_since: string | null;
  last_ok: boolean | null;
  last_result: string | null;
  last_seconds: number | null;
  run_count: number;
  runs: RunLog[];
}

export interface Metric {
  label: string;
  value: string | number;
}

export interface AgentSummary {
  pending?: number;
  metrics?: Metric[];
}

export interface AgentIdentity {
  name: string;
  title: string;
  tagline: string;
  blurb: string;
  icon: string;
  accent: "copper" | "fact" | "observation" | "idea";
  plugin: string;
}

/** An agent's card on the Agents view: identity plus the rolled-up state of its passes. */
export interface AgentCard extends AgentIdentity {
  summary: AgentSummary;
  job_count: number;
  paused: number;
  busy: boolean;
  failing: string[];
  next_run: string | null;
  last_run: string | null;
  jobs: { name: string; label: string; cron: string; enabled: boolean; next_run: string | null }[];
}

export interface Proposal {
  id: number;
  path: string;
  kind: string;
  diff: string;
  created_at: string;
  obsidian_uri?: string;
}

export interface Question {
  id: number;
  subject: string;
  question: string;
}

export interface RegistryEntity {
  id: number;
  name: string;
  type: string;
  mentions: number;
  status: string;
  note_path: string | null;
}

/** One row of Herald's outbox: queued · held (waiting on you) · sent · failed · cancelled. */
export interface MailMessage {
  id: number;
  kind: string;
  to_addr: string;
  subject: string;
  status: string;
  reason: string | null;
  created_at: string;
  sent_at: string | null;
}

/** Whether Herald can send at all, and as whom. */
export interface MailerStatus {
  ok: boolean;
  reason: string;
  from?: string;
  to?: string;
  /** False when Herald sends from your own address, so Gmail files its mail as self-sent. */
  dedicated_sender?: boolean;
}

/** One piece of work Maintainer wants to do, or has done. */
export interface MaintainerTask {
  id: number;
  repo: string;
  title: string;
  rationale: string;
  risk: string;
  status: string;
  created_at: string;
}

/** One attempt at a task: what Codex did, whether it stood up, what it cost. */
export interface MaintainerRun {
  id: number;
  task_id: number;
  repo: string;
  title: string;
  status: string;
  branch: string | null;
  pr_url: string | null;
  pr_state: string | null;
  files_changed: number;
  insertions: number;
  deletions: number;
  turns: number;
  cost_usd: number;
  duration_s: number;
  /** passed | failed | blocked | skipped — whether configured verification could run/pass. */
  verify: string;
  summary: string;
  error: string | null;
  started_at: string;
  ended_at: string | null;
}

/** Whether Maintainer can work, and which link is broken when it cannot. */
export interface MaintainerStatus {
  ok: boolean;
  /** ready | no token | no workspace | no git | waiting */
  state: string;
  reason: string;
  runner: string | null;
  runner_seen: string | null;
  repos: string[];
  workspace: string;
}

export interface MaintainerRepo {
  name: string;
  base: string;
  blurb: string;
  verify: string;
  enabled: boolean;
}

export interface TreasurerStatus {
  ok: boolean;
  state: "ready" | "cached" | "waiting";
  reason: string;
  source: string;
  cached_at: string | null;
  latest_transaction_at: string | null;
  active_insights: number;
}

export interface FinanceFigures {
  income: number;
  spending: number;
  investing: number;
  refunds: number;
  net: number;
  savings_rate: number | null;
}

export interface FinanceSnapshot {
  as_of: string;
  cached_at: string;
  latest_transaction_at: string | null;
  transaction_count: number;
  quality: { unmapped_count: number; unknown_flow_count: number; unknown_flow_ratio: number };
  periods: Record<string, FinanceFigures>;
  categories_last_7d: Record<string, number>;
  categories_month_to_date: Record<string, number>;
  recurring: { merchant: string; amount: number; last_seen: string; payments: number }[];
}

export interface FinanceInsight {
  id: number;
  kind: string;
  scope: string;
  title: string;
  detail: string;
  confidence: number;
  severity: string;
  method: string;
  review_state: string;
  first_seen_at: string;
  evidence: Record<string, unknown>;
  llm?: { hypothesis?: string | null; suggestion?: string | null } | null;
}

export interface FinanceModel {
  method: string;
  samples: number;
  trained_through: string | null;
  metrics: { available?: boolean; validation_mae?: number; interval?: number[] };
  created_at: string;
}

/** An agent's own page. The optional panels are contributed by the agent itself. */
export interface AgentDetail {
  agent: AgentIdentity;
  summary: AgentSummary;
  jobs: Job[];
  proposals?: Proposal[];
  questions?: Question[];
  entities?: RegistryEntity[];
  hub_threshold?: number;
  mail?: MailMessage[];
  held?: MailMessage[];
  mailer?: MailerStatus;
  maintainer?: MaintainerStatus;
  queue?: MaintainerTask[];
  prs?: MaintainerRun[];
  runs?: MaintainerRun[];
  repos?: MaintainerRepo[];
  treasurer?: TreasurerStatus;
  finance_snapshot?: FinanceSnapshot;
  finance_insights?: FinanceInsight[];
  finance_model?: FinanceModel | null;
}

export interface JobPatch {
  enabled?: boolean;
  cron?: string;
  limit?: number;
}

export interface Vitals {
  stats: Record<string, number>;
  usage: Record<string, unknown>;
  ollama_up: boolean;
  deepseek_up: boolean;
  anthropic_up: boolean;
  gemini_up: boolean;
  tools: number;
  specialists: number;
  jobs: { name: string; last_run: string | null }[];
}

export interface Session {
  id: number;
  title?: string;
  created_at?: string;
  message_count?: number;
  [k: string]: unknown;
}

export interface Widget {
  name: string;
  title: string;
  html: string;
}

// ---- fetch helpers -----------------------------------------------------
async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json() as Promise<T>;
}

async function postForm(url: string, body: Record<string, string>): Promise<void> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
}

/** Send JSON and surface the server's own message on failure — it's written for the user. */
async function sendJSON<T>(url: string, method: "POST" | "PATCH", body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok || (data && typeof data === "object" && "error" in data)) {
    const msg = data && typeof data === "object" && "error" in data ? String(data.error) : null;
    throw new Error(msg || `${url} -> ${r.status}`);
  }
  return data as T;
}

// ---- queries -----------------------------------------------------------
export const useInbox = () =>
  useQuery({ queryKey: ["inbox"], queryFn: () => getJSON<InboxItem[]>("/api/inbox") });

export const useAgents = () =>
  useQuery({
    queryKey: ["agents"],
    queryFn: () => getJSON<AgentCard[]>("/api/agents"),
    refetchInterval: 8000,
  });

const busy = (jobs?: Job[]) => !!jobs?.some((j) => j.running_since || j.queued_since);

export const useAgentDetail = (name: string) =>
  useQuery({
    queryKey: ["agent", name],
    queryFn: () => getJSON<AgentDetail>(`/api/agents/${name}`),
    // poll only while something is actually working
    refetchInterval: (q) => (busy(q.state.data?.jobs) ? 3000 : false),
  });

export const useVitals = () =>
  useQuery({
    queryKey: ["vitals"],
    queryFn: () => getJSON<Vitals>("/api/vitals"),
    refetchInterval: 15000,
  });

export const useSessions = () =>
  useQuery({ queryKey: ["sessions"], queryFn: () => getJSON<Session[]>("/sessions") });

export const useWidgets = () =>
  useQuery({ queryKey: ["widgets"], queryFn: () => getJSON<Widget[]>("/api/widgets") });

// ---- mutations ---------------------------------------------------------
/** Resolve one inbox item, whatever kind it is, and refresh the queue.
 *
 * `action` is the raw value from the card's own button (accept/reject/apply/yes/no/…), so a
 * plugin can offer outcomes core has never heard of. */
export function useResolveInbox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { item: InboxItem; action: string }) => {
      const { item, action } = v;
      if (item.origin === "curator_question") {
        await postForm(`/plugins/curator/questions/${item.id}`, { answer: action });
      } else if (item.origin === "curator") {
        // curator proposals use apply|reject
        const a = action === "accept" ? "apply" : action === "reject" ? "reject" : action;
        await postForm(`/plugins/curator/proposals/${item.id}`, { action: a });
      } else if (item.origin === "herald") {
        // a held message: send|cancel. Nothing leaves the machine until this call.
        const a = action === "accept" ? "send" : action === "reject" ? "cancel" : action;
        await postForm(`/plugins/herald/outbox/${item.id}`, { action: a });
      } else if (item.origin === "maintainer") {
        // a brief: approve|reject. Approving only queues the work for the host runner —
        // the pull request it opens is still yours to merge or close.
        await postForm(`/plugins/maintainer/tasks/${item.id}`, { action });
      } else if (item.origin === "treasurer") {
        await postForm(`/plugins/finance/insights/${item.id}`, { action });
      } else {
        await postForm(`/reviews/${item.id}`, { action });
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      // prefix match: whichever agent owned the item gets its page refreshed
      qc.invalidateQueries({ queryKey: ["agent"] });
      qc.invalidateQueries({ queryKey: ["vitals"] });
    },
  });
}

/** Queue one of an agent's passes. Returns immediately; the page polls for progress. */
export function useRunJob(agent: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (job: string) =>
      sendJSON<{ queued: boolean }>(`/api/agents/${agent}/jobs/${job}/run`, "POST"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

/** Retune one pass — pause it, reschedule it, or change how much it does per run. */
export function useUpdateJob(agent: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ job, patch }: { job: string; patch: JobPatch }) =>
      sendJSON<Job>(`/api/agents/${agent}/jobs/${job}`, "PATCH", patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
