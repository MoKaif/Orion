import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export interface ProjectSettings {
  description?: string;
  blurb?: string;
  base?: string;
  verify?: string;
  release?: { versioning: string; version_path: string; tag_prefix: string; release_branch: string };
}
export interface ProjectObservation {
  id: number;
  observed_at: string;
  stale: boolean;
  configuration_stale: boolean;
  payload: {
    git: {
      states: string[]; head?: string; branch?: string; tracking?: string; origin?: string;
      staged: number; unstaged: number; untracked: number; conflicted: number;
      ahead: number | null; behind: number | null; error?: string;
      branches_without_tracking?: string[];
    };
    remote: { state: string; verified_at?: string; attempted_at?: string; error?: string; tracking_sha?: string };
    manifests: {
      stack: string[]; package_managers: string[];
      evidence: { path: string; sha256?: string; error?: string }[];
      build_status: string; test_status: string; dependency_health: string;
    };
    release: {
      status: string; version?: string; local_tag?: string; commits_since_tag: number | null;
      tag_is_ancestor?: boolean; version_tag_exists_locally?: boolean; changelog?: string;
      components: { path: string; name: string; version: string | null }[];
    };
    environment: { os: string; architecture: string; observed_at: string;
      tools: Record<string, { available: boolean; ready: boolean; version: string | null }> };
  };
}
export interface Project {
  id: string; name: string; path: string; status: string; revision: number;
  source: string; settings: ProjectSettings; observation: ProjectObservation | null;
}
interface Overview {
  projects: Project[]; refresh: { value: string; at: string } | null;
  execution: { enabled: boolean; reason: string }; capabilities: Record<string, boolean>;
}
export interface Task {
  id: number; repo: string; title: string; brief: string; rationale: string;
  acceptance: string; risk: string; status: string; created_at: string;
}
export interface Run {
  id: number; task_id: number; repo: string; title: string; status: string; verify: string;
  reported_status?: string; started_at: string; branch?: string; pr_url?: string;
  summary?: string; error?: string; verify_tail?: string;
  events?: { id: number; at: string; kind: string; text: string }[];
}

async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/plugins/maintainer${path}`, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : `Request failed (${response.status})`);
  }
  return response.json();
}
export function useProjects() {
  return useQuery({ queryKey: ["maintainer-projects"], queryFn: () => api<Overview>("/projects"),
    refetchInterval: 5000 });
}
export function useMaintainerTasks() {
  return useQuery({ queryKey: ["maintainer-tasks"], queryFn: () => api<Task[]>("/tasks?limit=60"),
    refetchInterval: 15000 });
}
export function useMaintainerRuns() {
  return useQuery({ queryKey: ["maintainer-runs"], queryFn: () => api<Run[]>("/runs?limit=60"),
    refetchInterval: 5000 });
}
export function useRun(id: number | null) {
  return useQuery({ queryKey: ["maintainer-run", id], queryFn: () => api<Run>(`/runs/${id}`),
    enabled: id !== null, refetchInterval: 5000 });
}
export function useMaintainerAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: { path: string; method?: string; body: unknown }) =>
      api(request.path, request.method || "POST", request.body),
    onSuccess: () => {
      for (const key of ["maintainer-projects", "maintainer-tasks", "maintainer-runs", "inbox", "agents"])
        void client.invalidateQueries({ queryKey: [key] });
    },
  });
}
