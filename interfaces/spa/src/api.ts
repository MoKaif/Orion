import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ---- types -------------------------------------------------------------
export interface InboxItem {
  origin: "world_model" | "curator";
  id: number;
  created_at: string;
  prov_agent: string;
  prov_label: string;
  prov_uri?: string | null;
  // world_model
  item_type?: string;
  confidence?: number | null;
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

export interface Job {
  name: string;
  label: string;
  cron: string;
  next_run: string | null;
  last_run: string | null;
  running_since: string | null;
  last_ok: boolean | null;
  last_result: string | null;
}

export interface AgentsData {
  curator: { pending: number; jobs: Job[] };
  other: Job[];
}

export interface RunLog {
  ok: boolean;
  result: string;
  at: string;
  seconds: number;
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

export interface AgentDetail {
  job: Job;
  runs: RunLog[];
  proposals: Proposal[];
  questions: Question[];
  entities: RegistryEntity[];
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

// ---- queries -----------------------------------------------------------
export const useInbox = () =>
  useQuery({ queryKey: ["inbox"], queryFn: () => getJSON<InboxItem[]>("/api/inbox") });

export const useAgents = () =>
  useQuery({
    queryKey: ["agents"],
    queryFn: () => getJSON<AgentsData>("/api/agents"),
    refetchInterval: 8000,
  });

export const useAgentDetail = (name: string) =>
  useQuery({
    queryKey: ["agent", name],
    queryFn: () => getJSON<AgentDetail>(`/api/agents/${name}`),
    refetchInterval: (q) => (q.state.data?.job.running_since ? 5000 : false),
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
/** Resolve one inbox item (world-model review or Curator proposal) and refresh the queue. */
export function useResolveInbox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { item: InboxItem; action: "accept" | "reject" }) => {
      const { item, action } = v;
      if (item.origin === "curator") {
        // curator uses apply|reject
        const a = action === "accept" ? "apply" : "reject";
        await postForm(`/plugins/curator/proposals/${item.id}`, { action: a });
      } else {
        await postForm(`/reviews/${item.id}`, { action });
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useRunAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => postForm(`/jobs/${name}/run`, {}),
    onSuccess: (_d, name) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent", name] });
    },
  });
}
