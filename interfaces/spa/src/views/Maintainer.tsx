import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw, GitBranch, ArrowUpRight, Search, FolderGit2 } from "lucide-react";
import { agoText } from "../cron";
import { Project, Run, useProjects, useMaintainerTasks, useMaintainerRuns,
  useMaintainerAction, useRun } from "../maintainer-api";
import "./maintainer.css";

const label = (value: string) => value.replaceAll("_", " ");
const TABS = ["projects", "attention", "releases", "proposals", "executions"] as const;

function signals(project: Project): { text: string; tone: string }[] {
  const observation = project.observation;
  if (project.status !== "active") return [{ text: project.status, tone: "muted" }];
  if (!observation) return [{ text: "Not inspected", tone: "muted" }];
  const { git, remote, environment, manifests } = observation.payload;
  const result = [];
  if (observation.stale) result.push({ text: "Inspection stale", tone: "warn" });
  if (observation.configuration_stale) result.push({ text: "Configuration changed; inspect again", tone: "warn" });
  for (const state of git.states) {
    if (["unavailable", "diverged", "merge_in_progress", "rebase_in_progress", "cherry_pick_in_progress"].includes(state))
      result.push({ text: label(state), tone: "bad" });
    else if (["uncommitted_changes", "unpushed_commits", "cached_remote_updates", "no_tracking_branch", "missing_origin", "detached_head"].includes(state))
      result.push({ text: label(state), tone: "warn" });
  }
  if (remote.state === "tracking_ref_changed") result.push({ text: "Origin changed since last fetch", tone: "warn" });
  if (["unavailable", "stale", "tracking_branch_missing"].includes(remote.state))
    result.push({ text: `Remote ${label(remote.state)}`, tone: "warn" });
  for (const [stack, tool] of [[".NET", "dotnet"], ["Go", "go"], ["C++ / CMake", "cmake"]]) {
    if (manifests.stack.includes(stack) && !environment.tools[tool]?.ready)
      result.push({ text: `${tool} toolchain unavailable`, tone: "warn" });
  }
  return result;
}
function Badge({ text, tone = "muted" }: { text: string; tone?: string }) {
  return <span className={`mi-badge ${tone}`}>{text}</span>;
}
function Time({ at }: { at?: string | null }) {
  return <span title={at ? new Date(at).toLocaleString() : ""}>{at ? agoText(at) : "Never"}</span>;
}

function ProjectConfiguration({ project }: { project: Project }) {
  const policy = project.settings.release;
  const [status, setStatus] = useState(project.status);
  const [path, setPath] = useState(project.path);
  const [versionPath, setVersionPath] = useState(policy?.version_path || project.observation?.payload.release.components[0]?.path || "package.json");
  const [prefix, setPrefix] = useState(policy?.tag_prefix ?? "v");
  const [branch, setBranch] = useState(policy?.release_branch || project.settings.base || "main");
  const [versioning, setVersioning] = useState(policy?.versioning || "semantic");
  const action = useMaintainerAction();
  return <form className="mi-config" onSubmit={event => {
    event.preventDefault();
    action.mutate({ path: `/projects/${project.id}`, method: "PATCH", body: { revision: project.revision,
      status, path, settings: { release: { versioning, version_path: versionPath, tag_prefix: prefix,
        release_branch: branch } } } });
  }}>
    <h3>Project configuration</h3>
    <p>Explicit settings take priority over discovery. Saving a policy authorizes monitoring only.</p>
    <label>Status<select value={status} onChange={e => setStatus(e.target.value)}>
      {["active", "paused", "archived"].map(s => <option key={s}>{s}</option>)}</select></label>
    <label>Checkout path<input value={path} onChange={e => setPath(e.target.value)} required /></label>
    <label>Version policy<select value={versioning} onChange={e => setVersioning(e.target.value)}>
      <option value="semantic">Semantic versioning</option><option value="calendar">Calendar versioning</option>
      <option value="unversioned">Unversioned</option></select></label>
    <label>Version metadata path<input value={versionPath} onChange={e => setVersionPath(e.target.value)} required /></label>
    <label>Tag prefix<input value={prefix} onChange={e => setPrefix(e.target.value)} /></label>
    <label>Release branch<input value={branch} onChange={e => setBranch(e.target.value)} required /></label>
    <button className="btn" disabled={action.isPending}>Save configuration</button>
    {action.isError && <p role="alert" className="mi-error">{action.error.message}</p>}
    {action.isSuccess && <p role="status">Saved. Inspect again to collect facts under this configuration.</p>}
  </form>;
}

function ProjectDetail({ project }: { project: Project }) {
  const observation = project.observation;
  const p = observation?.payload;
  return <section className="mi-detail">
    <div className="mi-section-head"><h2>{project.name}</h2><Badge text={project.status} /></div>
    <p>{project.settings.description || project.settings.blurb || "Discovered in your development workspace."}</p>
    <code className="mi-path">{project.path}</code>
    <p className="mi-meta">Registered via {label(project.source)} · configuration revision {project.revision}</p>
    {p ? <>
      <div className="mi-facts">
        <div><h3>Git synchronization</h3>
          <p><GitBranch size={14} /> {p.git.branch || "Unknown branch"} → {p.git.tracking || "No tracking branch"}</p>
          <code>{p.git.head?.slice(0, 12) || "No commit available"}</code>
          <p>{p.git.staged ?? 0} staged · {p.git.unstaged ?? 0} unstaged · {p.git.untracked ?? 0} untracked · {p.git.conflicted ?? 0} conflicted</p>
          <p>Cached counts: {p.git.ahead ?? "?"} ahead / {p.git.behind ?? "?"} behind</p>
          <p>Remote: {label(p.remote.state)} · verified <Time at={p.remote.verified_at} /></p>
          {p.remote.error && <p className="mi-error">{p.remote.error}</p>}
          <p className="mi-meta">Counts use local tracking refs. Remote verification never fetches or changes this checkout.</p>
          <p className="mi-path">{p.git.origin || "No origin configured"}</p>
          {p.git.error && <p className="mi-error">{p.git.error}</p>}
          {!!p.git.branches_without_tracking?.length && <p>Branches without tracking: {p.git.branches_without_tracking.join(", ")}</p>}
        </div>
        <div><h3>Versions and verification</h3>
          {p.release.components.map(c => <p key={c.path}><strong>{c.version || "Unversioned"}</strong> <code>{c.path}</code></p>)}
          {!p.release.components.length && <p>No supported version metadata found.</p>}
          <p>Highest matching local tag: {p.release.local_tag || "None"}</p>
          <p>Commits since tag: {p.release.commits_since_tag ?? "Unknown"}</p>
          <p>Build: unknown · Tests: unknown · Dependencies: not assessed</p>
          <p className="mi-meta">Version files are working-tree observations. Local tags do not establish published releases or release readiness.</p>
        </div>
      </div>
      <details><summary>Environment and evidence</summary>
        <p>{p.manifests.stack.join(" · ") || "Stack not identified"}</p>
        <p>{p.environment.os} {p.environment.architecture} · Orion service environment, observed <Time at={p.environment.observed_at} /></p>
        <div className="mi-signals">{Object.entries(p.environment.tools).map(([name, tool]) =>
          <Badge key={name} tone={tool.ready ? "good" : "warn"} text={`${name}: ${tool.version || (tool.available ? "not ready" : "missing")}`} />)}</div>
        <p className="mi-meta">Tool presence does not establish build readiness or execution isolation.</p>
        {p.manifests.evidence.map(e => <p key={e.path}><code>{e.path}</code> · {e.error || `SHA256 ${e.sha256?.slice(0, 16)}`}</p>)}
      </details>
    </> : <p>No observations yet. Use Inspect projects to collect local facts.</p>}
    <ProjectConfiguration key={`${project.id}-${project.revision}`} project={project} />
  </section>;
}

function ExecutionDetail({ id }: { id: number }) {
  const { data, isError, isLoading } = useRun(id);
  if (isLoading) return <p>Loading execution…</p>;
  if (isError || !data) return <p role="alert">Execution details unavailable.</p>;
  return <div className="mi-run-detail">
    {data.summary && <p>{data.summary}</p>}
    {data.error && <p className="mi-error">{data.error}</p>}
    {data.reported_status && <p>Historical runner reported “{data.reported_status}”; failed or blocked verification has been reassessed.</p>}
    {data.verify_tail && <pre>{data.verify_tail}</pre>}
    <details><summary>Execution events ({data.events?.length || 0})</summary>
      {data.events?.map(e => <p key={e.id}><Time at={e.at} /> · {e.kind} · {e.text}</p>)}</details>
  </div>;
}
function Execution({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  return <article className="mi-card">
    <button className="mi-run-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
      <strong>{run.repo} · {run.title}</strong><span>{open ? "Hide details" : "Show details"}</span></button>
    <div className="mi-signals"><Badge text={run.status} tone={run.status === "failed" ? "bad" : "muted"} />
      <Badge text={`Verification ${run.verify}`} tone={run.verify === "passed" ? "good" : "warn"} />
      <Time at={run.started_at} /></div>
    <p className="mi-meta">{run.branch || "No branch recorded"} {run.pr_url && <a href={run.pr_url} target="_blank" rel="noreferrer">Pull request ↗</a>}</p>
    {open && <ExecutionDetail id={run.id} />}
  </article>;
}

export default function Maintainer() {
  const { data, isLoading, isError, refetch } = useProjects();
  const tasks = useMaintainerTasks();
  const runs = useMaintainerRuns();
  const action = useMaintainerAction();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const requested = params.get("tab") || "projects";
  const tab = TABS.find(t => t === requested) || "projects";
  const projects = data?.projects || [];
  const selected = projects.find(p => p.id === params.get("project"));
  const visible = projects.filter(p => `${p.name} ${p.path} ${p.observation?.payload.manifests.stack.join(" ")}`.toLowerCase().includes(query.toLowerCase()));
  const attention = projects.filter(p => signals(p).some(s => ["bad", "warn"].includes(s.tone)));
  const openTasks = tasks.data?.filter(t => ["proposed", "approved", "running", "claimed"].includes(t.status)) || [];
  const inspect = (options: { discover?: boolean; remote?: boolean }) =>
    action.mutate({ path: "/projects/refresh", body: options });
  const select = (id: string) => setParams({ tab: "projects", project: id });

  return <div className="maintainer-page">
    <header className="mi-header"><div><p className="eyebrow">Engineering workspace</p>
      <h1>Maintainer</h1><p>Repository facts, versions and work that needs attention.</p></div>
      <Link to="/agents/maintainer">Agent schedule <ArrowUpRight size={14} /></Link></header>
    <div className="mi-toolbar">
      <button className="btn" disabled={action.isPending} onClick={() => inspect({})}><RefreshCw size={14} /> Inspect projects</button>
      <button className="btn" disabled={action.isPending} onClick={() => inspect({ remote: true })}>Verify remotes</button>
      <button className="btn" disabled={action.isPending} onClick={() => inspect({ discover: true })}><FolderGit2 size={14} /> Discover projects</button>
      <span className="mi-meta">{data?.refresh ? `${data.refresh.value} · ${agoText(data.refresh.at)}` : "No inspection recorded"}</span>
    </div>
    {action.isError && <p className="mi-error" role="alert">{action.error.message}</p>}
    {action.isSuccess && <p role="status" className="mi-meta">Request accepted. Observations update automatically below.</p>}
    {data && <p className="mi-notice">{data.execution.reason} Monitoring does not modify your checkouts.</p>}
    {isLoading ? <p>Loading project registry…</p> : isError ? <p role="alert">Unable to load Maintainer. <button className="btn" onClick={() => void refetch()}>Retry</button></p> : <>
      <div className="mi-totals">
        <div><strong>{projects.length}</strong><span>Registered projects</span></div>
        <div><strong>{attention.length}</strong><span>Need attention</span></div>
        <div><strong>{projects.filter(p => p.observation?.payload.remote.state === "synchronized" && !p.observation.stale).length}</strong><span>Tracking branch verified in sync</span></div>
        <div><strong>{projects.filter(p => !p.observation || p.observation.stale).length}</strong><span>Missing or stale inspection</span></div>
      </div>
      <nav className="mi-tabs" aria-label="Maintainer sections">{TABS.map(t =>
        <button key={t} className={tab === t ? "active" : ""} aria-current={tab === t ? "page" : undefined}
          onClick={() => setParams({ tab: t })}>{label(t)}{t === "proposals" ? ` (${openTasks.length})` : ""}</button>)}</nav>
      {tab === "projects" && <>
        {selected && <><button className="mi-text-link" onClick={() => setParams({ tab: "projects" })}>← All projects</button>
          <ProjectDetail project={selected} /></>}
        {!selected && <><label className="mi-search"><Search size={15} /><input aria-label="Find projects" value={query} onChange={e => setQuery(e.target.value)} placeholder="Find project, path or stack" /></label>
        {!projects.length && <p>Start with Discover projects. Maintainer searches only the configured development workspace.</p>}
        <div className="mi-project-grid">{visible.map(project => <button key={project.id}
          className="mi-project" onClick={() => select(project.id)}>
          <div className="mi-section-head"><h2>{project.name}</h2><span>{project.status}</span></div>
          <p>{project.observation?.payload.manifests.stack.join(" · ") || "Stack not yet identified"}</p>
          <p><GitBranch size={13} /> {project.observation?.payload.git.branch || "Unknown branch"}</p>
          <div className="mi-signals">{signals(project).map(s => <Badge key={s.text} {...s} />)}</div>
          <p className="mi-meta">Observed <Time at={project.observation?.observed_at} /> · remote {label(project.observation?.payload.remote.state || "not_verified")}</p>
        </button>)}</div>
        </>}
      </>}
      {tab === "attention" && <>
        <p className="mi-meta">Observed problems and pending work. No AI health score; uncollected build, dependency and upstream health remain unknown.</p>
        {!attention.length && <p>No actionable local findings in current observations. This does not establish overall project health.</p>}
        {attention.map(p => <article className="mi-card" key={p.id}><button className="mi-text-link" onClick={() => select(p.id)}>{p.name} ↗</button>
          <div className="mi-signals">{signals(p).map(s => <Badge key={s.text} {...s} />)}</div></article>)}
      </>}
      {tab === "releases" && <>
        <p className="mi-meta">Version metadata and local tags. Release publication, remote-release reconciliation and readiness verification are not enabled.</p>
        <div className="mi-table-wrap"><table><thead><tr><th>Project</th><th>Version metadata</th><th>Local tag</th><th>Changes since tag</th><th>Policy</th></tr></thead><tbody>
          {projects.map(p => <tr key={p.id}><td><button className="mi-text-link" onClick={() => select(p.id)}>{p.name}</button></td>
            <td>{p.observation?.payload.release.components.map(c => <div key={c.path}><code>{c.version || "?"}</code><small>{c.path}</small></div>) || "Not inspected"}</td>
            <td>{p.observation?.payload.release.local_tag || "None observed"}</td><td>{p.observation?.payload.release.commits_since_tag ?? "Unknown"}</td>
            <td>{p.settings.release?.versioning || "Needs configuration"}</td></tr>)}
        </tbody></table></div>
      </>}
      {tab === "proposals" && <>
        <p className="mi-notice">These are preserved legacy briefs. Evidence validation, previews and revision-bound approval are still being built; execution is paused.</p>
        {tasks.isError && <p role="alert">Proposals unavailable.</p>}
        {tasks.isLoading && <p>Loading proposals…</p>}
        {tasks.isSuccess && !openTasks.length && <p>No proposals awaiting review.</p>}
        {openTasks.map(t => <article className="mi-card" key={t.id}><div className="mi-section-head"><h2>{t.title}</h2><Badge text={t.status} /></div>
          <p className="mi-meta">{t.repo} · {t.risk} risk · <Time at={t.created_at} /></p><p>{t.rationale}</p>
          <details><summary>Read proposal</summary><p className="mi-prose">{t.brief}</p><h3>Acceptance criteria</h3><p className="mi-prose">{t.acceptance || "Not supplied"}</p></details>
          {t.status === "proposed" && <button className="btn" disabled={action.isPending} onClick={() =>
            action.mutate({ path: `/tasks/${t.id}`, body: { action: "reject" } })}>Dismiss proposal</button>}
        </article>)}
      </>}
      {tab === "executions" && <>
        <p className="mi-meta">Historical work and verification evidence. New automated executions are paused.</p>
        {runs.isError && <p role="alert">Executions unavailable.</p>}{runs.isLoading && <p>Loading executions…</p>}
        {runs.isSuccess && !runs.data.length && <p>No execution history.</p>}
        {runs.data?.map(run => <Execution key={run.id} run={run} />)}
      </>}
    </>}
  </div>;
}
