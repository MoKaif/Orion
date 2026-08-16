import { CSSProperties, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BookOpen,
  Bot,
  Compass,
  Mail,
  Play,
  Loader2,
  Pause,
  RotateCw,
  ExternalLink,
  Check,
  CircleAlert,
  GitPullRequest,
  LucideIcon,
} from "lucide-react";
import {
  InboxItem,
  Job,
  MaintainerRun,
  useAgentDetail,
  useResolveInbox,
  useRunJob,
  useUpdateJob,
} from "../api";
import { DiffBlock, Loading } from "../components/bits";
import { ShiftStrip } from "../components/shift";
import { agoText, describeCron, untilText } from "../cron";
import "./agent-detail.css";

const ICONS: Record<string, LucideIcon> = {
  "book-open": BookOpen,
  compass: Compass,
  mail: Mail,
  bot: Bot,
  "git-pull-request": GitPullRequest,
};

/** A run's one-line verdict: the diffstat when it stands up, the reason when it doesn't. */
function runVerdict(r: MaintainerRun) {
  if (r.status === "failed") return { tone: "failed", text: r.error || "failed" };
  if (r.verify === "failed") return { tone: "failed", text: "build failed" };
  if (r.verify === "blocked") return { tone: "idle", text: "verification unavailable" };
  if (r.files_changed === 0) return { tone: "idle", text: "no change needed" };
  return { tone: "ok", text: `+${r.insertions} −${r.deletions} in ${r.files_changed} files` };
}

function runState(job: Job) {
  if (job.running_since) return { tone: "busy", text: "running" };
  if (job.queued_since) return { tone: "queued", text: "queued" };
  if (!job.enabled) return { tone: "paused", text: "paused" };
  if (job.last_ok === false) return { tone: "failed", text: "last run failed" };
  if (job.last_ok === true) return { tone: "ok", text: `ran ${agoText(job.last_run)}` };
  return { tone: "idle", text: "never run" };
}

/** One pass: what it does, its controls, and how the last run went. */
function Pass({ job, agent }: { job: Job; agent: string }) {
  const run = useRunJob(agent);
  const update = useUpdateJob(agent);
  const [cron, setCron] = useState(job.cron);
  const [limit, setLimit] = useState(job.limit ?? 0);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = useRef(false);

  // Accept server values while the user isn't mid-edit, so polling never fights typing.
  useEffect(() => {
    if (!editing.current) {
      setCron(job.cron);
      setLimit(job.limit ?? 0);
    }
  }, [job.cron, job.limit]);

  const busy = !!job.running_since || !!job.queued_since || run.isPending;
  const state = runState(job);

  const flash = () => {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  };

  const save = (patch: { cron?: string; limit?: number; enabled?: boolean }) => {
    setError(null);
    update.mutate(
      { job: job.name, patch },
      {
        onSuccess: flash,
        onError: (e) => setError(e instanceof Error ? e.message : "Could not save that."),
      },
    );
  };

  const commitCron = () => {
    editing.current = false;
    const next = cron.trim();
    if (!next || next === job.cron) return setCron(job.cron);
    save({ cron: next });
  };

  const commitLimit = () => {
    editing.current = false;
    if (limit === job.limit || limit < 1) return setLimit(job.limit ?? 0);
    save({ limit });
  };

  return (
    <article className={`pass${job.enabled ? "" : " is-paused"}`}>
      <header className="pass-head">
        <h3>{job.label}</h3>
        <span className={`pass-state ps-${state.tone}`}>
          {(job.running_since || job.queued_since) && <Loader2 size={11} className="spin" />}
          {state.text}
        </span>
        <button
          className="btn btn-primary btn-sm pass-run"
          disabled={busy}
          onClick={() => run.mutate(job.name)}
        >
          {busy ? <Loader2 size={13} className="spin" /> : <Play size={13} />}
          {job.running_since ? "Running" : job.queued_since ? "Queued" : "Run now"}
        </button>
      </header>

      {job.description && <p className="pass-what">{job.description}</p>}

      <div className="pass-controls">
        <label className="ctl">
          <span className="ctl-label">Schedule</span>
          <input
            className="ctl-input cron"
            value={cron}
            spellCheck={false}
            aria-label={`${job.label} schedule, in cron`}
            onFocus={() => (editing.current = true)}
            onChange={(e) => setCron(e.target.value)}
            onBlur={commitCron}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") {
                setCron(job.cron);
                editing.current = false;
                e.currentTarget.blur();
              }
            }}
          />
          <span className="ctl-hint">
            {describeCron(cron)}
            {job.enabled && job.next_run ? ` · next ${untilText(job.next_run)}` : ""}
          </span>
        </label>

        {job.limit_default !== null && (
          <label className="ctl">
            <span className="ctl-label">Per run</span>
            <input
              className="ctl-input num"
              type="number"
              min={1}
              max={500}
              value={limit}
              aria-label={`${job.label} items per run`}
              onFocus={() => (editing.current = true)}
              onChange={(e) => setLimit(Number(e.target.value))}
              onBlur={commitLimit}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            />
            <span className="ctl-hint">notes each time</span>
          </label>
        )}

        <div className="pass-right">
          {saved && (
            <span className="saved">
              <Check size={11} /> saved
            </span>
          )}
          <button
            className="btn btn-sm"
            disabled={update.isPending}
            onClick={() => save({ enabled: !job.enabled })}
          >
            {job.enabled ? <Pause size={12} /> : <RotateCw size={12} />}
            {job.enabled ? "Pause" : "Resume"}
          </button>
        </div>
      </div>

      {error && <p className="pass-error">{error}</p>}
      {job.last_result && (
        <p className="pass-result" title={job.last_result}>
          <span>last run</span>
          <i className="leader" />
          <code>{job.last_result}</code>
        </p>
      )}
    </article>
  );
}

export default function AgentDetail() {
  const { name = "" } = useParams();
  const { data, isLoading, isError } = useAgentDetail(name);
  const resolve = useResolveInbox();

  if (isLoading) return <Loading label="Opening agent…" />;
  if (isError || !data)
    return (
      <div className="empty-state">
        <span className="empty-glyph">◷</span>
        No agent called “{name}”. <Link to="/agents">Back to agents</Link>
      </div>
    );

  const { agent, summary, jobs } = data;
  const proposals = data.proposals ?? [];
  const questions = data.questions ?? [];
  const entities = data.entities ?? [];
  const mail = data.mail ?? [];
  const mailer = data.mailer;
  const maintainer = data.maintainer;
  const queue = (data.queue ?? []).filter((t) => t.status === "proposed");
  const prs = data.prs ?? [];
  const codeRuns = data.runs ?? [];
  const threshold = data.hub_threshold ?? 3;
  const Icon = ICONS[agent.icon] ?? Bot;
  const metrics = summary.metrics ?? [];

  const activity = jobs
    .flatMap((j) => j.runs.map((r) => ({ ...r, job: j.label })))
    .sort((a, b) => (a.at < b.at ? 1 : -1))
    .slice(0, 14);

  const asItem = (p: (typeof proposals)[number]): InboxItem => ({
    origin: "curator",
    id: p.id,
    kind: p.kind,
    diff: p.diff,
    created_at: p.created_at,
    prov_agent: agent.title,
    prov_label: p.path,
  });

  return (
    <div className="agent-page" style={{ "--accent": `var(--${agent.accent})` } as CSSProperties}>
      <Link to="/agents" className="back">
        <ArrowLeft size={13} /> Agents
      </Link>

      <header className="ap-head">
        <span className="ap-mark">
          <Icon size={22} strokeWidth={1.8} />
        </span>
        <div className="ap-id">
          <p className="ap-tagline">{agent.tagline || agent.plugin}</p>
          <h1>{agent.title}</h1>
          <p className="ap-blurb">{agent.blurb}</p>
        </div>
      </header>

      <section className="ap-strip">
        {metrics.map((m) => (
          <div className="ap-metric" key={m.label}>
            <b>{m.value}</b>
            <span>{m.label}</span>
          </div>
        ))}
        <div className="ap-shift">
          <ShiftStrip jobs={jobs} />
        </div>
      </section>

      {mailer && !mailer.ok && (
        <p className="ap-blocked">
          <CircleAlert size={14} /> Not sending yet — {mailer.reason} Until then every letter
          below is composed and filed, but nothing leaves the machine.
        </p>
      )}
      {mailer?.ok && mailer.dedicated_sender === false && (
        <p className="ap-blocked ap-note">
          <CircleAlert size={14} /> Sending from your own address to your own address, so Gmail
          files these as mail you sent yourself. Give Herald its own Google account
          (GMAIL_SENDER_ADDRESS) to fix that.
        </p>
      )}

      {maintainer && !maintainer.ok && (
        <p className="ap-blocked">
          <CircleAlert size={14} /> {maintainer.reason} Until then Maintainer can read your
          projects and propose work, but nothing can be built.
        </p>
      )}

      <h2 className="ap-section">
        Passes <span className="count-pill">{jobs.length}</span>
      </h2>
      <div className="pass-list">
        {jobs.map((j) => (
          <Pass key={j.name} job={j} agent={agent.name} />
        ))}
      </div>

      {questions.length > 0 && (
        <>
          <h2 className="ap-section">
            Questions for you <span className="count-pill alert">{questions.length}</span>
          </h2>
          <ul className="q-list card">
            {questions.map((q) => (
              <li key={q.id} className="q-item">
                <span className="q-subject">{q.subject}</span>
                <p className="q-text">{q.question}</p>
                <Link to="/inbox" className="btn btn-sm">
                  Answer in the inbox <ExternalLink size={12} />
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}

      {proposals.length > 0 && (
        <>
          <h2 className="ap-section">
            Waiting for you <span className="count-pill alert">{proposals.length}</span>
            <Link to="/inbox" className="btn btn-sm btn-ghost ap-section-act">
              review in inbox <ExternalLink size={12} />
            </Link>
          </h2>
          <div className="proposal-list">
            {proposals.slice(0, 12).map((p) => (
              <article className="proposal card" key={p.id}>
                <header className="proposal-head">
                  <span className={`kind-badge kind-${p.kind}`}>
                    {(p.kind || "grammar").replace("_", " ")}
                  </span>
                  <span className="proposal-path">{p.path}</span>
                  {p.obsidian_uri && (
                    <a className="group-open" href={p.obsidian_uri}>
                      open <ExternalLink size={12} />
                    </a>
                  )}
                </header>
                <DiffBlock diff={p.diff} />
                <footer className="item-actions">
                  <button
                    className="btn btn-accept btn-sm"
                    disabled={resolve.isPending}
                    onClick={() => resolve.mutate({ item: asItem(p), action: "accept" })}
                  >
                    {p.kind === "entity_note" ? "Create note" : "Apply"}
                  </button>
                  <button
                    className="btn btn-reject btn-sm"
                    disabled={resolve.isPending}
                    onClick={() => resolve.mutate({ item: asItem(p), action: "reject" })}
                  >
                    Reject
                  </button>
                </footer>
              </article>
            ))}
          </div>
        </>
      )}

      {entities.length > 0 && (
        <>
          <h2 className="ap-section">
            Who it knows <span className="count-pill">{entities.length}</span>
          </h2>
          <div className="card">
            <p className="registry-hint">
              The cast of your vault, resolved from your notes. Each becomes a hub-note proposal
              once it has been seen {threshold} times.
            </p>
            <ul className="registry">
              {entities.map((e) => (
                <li className="reg-item" key={e.id}>
                  <span className={`reg-type reg-${e.type}`}>{e.type}</span>
                  <span className="reg-name">{e.name}</span>
                  {e.note_path ? (
                    <span className="reg-badge">hub note ✓</span>
                  ) : (
                    <span className="reg-track" title={`${e.mentions} of ${threshold} mentions`}>
                      <i style={{ width: `${Math.min(100, (e.mentions / threshold) * 100)}%` }} />
                    </span>
                  )}
                  <span className="reg-count">
                    {e.mentions} {e.mentions === 1 ? "mention" : "mentions"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      {mail.length > 0 && (
        <>
          <h2 className="ap-section">
            The mail log <span className="count-pill">{mail.length}</span>
            {mailer?.to && <span className="ap-section-note">to {mailer.to}</span>}
          </h2>
          <div className="card">
            <ul className="mail-log">
              {mail.map((m) => (
                <li className="mail-row" key={m.id}>
                  <span className={`mail-state ms-${m.status}`}>{m.status}</span>
                  <span className="mail-subject" title={m.reason ?? undefined}>
                    {m.subject}
                  </span>
                  <i className="leader" />
                  <time className="mail-time">{agoText(m.sent_at ?? m.created_at)}</time>
                </li>
              ))}
            </ul>
            {mail.some((m) => m.status === "held") && (
              <p className="registry-hint">
                Held messages are addressed outside your own account, so they wait for you.{" "}
                <Link to="/inbox">Review them in the inbox</Link> — nothing is sent until you say
                so, and mail cannot be recalled afterwards.
              </p>
            )}
          </div>
        </>
      )}

      {prs.length > 0 && (
        <>
          <h2 className="ap-section">
            Pull requests waiting on you <span className="count-pill alert">{prs.length}</span>
            <span className="ap-section-note">Orion cannot merge — that stays your hand</span>
          </h2>
          <div className="card">
            <ul className="pr-list">
              {prs.map((r) => {
                const v = runVerdict(r);
                return (
                  <li className="pr-row" key={r.id}>
                    <span className={`pr-state pv-${v.tone}`}>{r.repo}</span>
                    <a className="pr-title" href={r.pr_url ?? "#"} target="_blank" rel="noreferrer">
                      {r.title} <ExternalLink size={11} />
                    </a>
                    <i className="leader" />
                    <span className="pr-stat">{v.text}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}

      {queue.length > 0 && (
        <>
          <h2 className="ap-section">
            Work it wants to start <span className="count-pill alert">{queue.length}</span>
            <Link to="/inbox" className="btn btn-sm btn-ghost ap-section-act">
              approve in inbox <ExternalLink size={12} />
            </Link>
          </h2>
          <div className="card">
            <ul className="q-list">
              {queue.slice(0, 10).map((t) => (
                <li key={t.id} className="q-item">
                  <span className="q-subject">
                    {t.repo} · {t.risk} risk
                  </span>
                  <p className="q-text">
                    <b>{t.title}</b>
                    {t.rationale ? ` — ${t.rationale}` : ""}
                  </p>
                </li>
              ))}
            </ul>
            <p className="registry-hint">
              Nothing here has run. Approving one hands it to Codex on your own machine, in
              a throwaway worktree, and opens a pull request you review on GitHub.
            </p>
          </div>
        </>
      )}

      {codeRuns.length > 0 && (
        <>
          <h2 className="ap-section">
            The work log <span className="count-pill">{codeRuns.length}</span>
          </h2>
          <div className="card">
            <ul className="mail-log">
              {codeRuns.map((r) => {
                const v = runVerdict(r);
                return (
                  <li className="mail-row" key={r.id}>
                    <span className={`mail-state ms-${v.tone === "ok" ? "sent" : v.tone}`}>
                      {r.repo}
                    </span>
                    <span className="mail-subject" title={r.summary || undefined}>
                      {r.title}
                    </span>
                    <i className="leader" />
                    <span className="pr-stat">{v.text}</span>
                    <time className="mail-time">
                      {agoText(r.ended_at ?? r.started_at)}
                      {r.duration_s ? ` · ${Math.round(r.duration_s / 60)}m` : ""}
                    </time>
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}

      <h2 className="ap-section">Recent runs</h2>
      <div className="card">
        {activity.length === 0 ? (
          <p className="loading">Nothing has run yet. Run a pass above to see it here.</p>
        ) : (
          <ol className="feed">
            {activity.map((r, i) => (
              <li className="feed-item" key={`${r.at}-${i}`}>
                <span className={`feed-type${r.ok ? "" : " failed"}`}>{r.ok ? "ok" : "failed"}</span>
                <span className="feed-detail">
                  <b>{r.job}</b> {r.result}
                </span>
                <i className="leader" />
                <time className="feed-time">
                  {agoText(r.at)} · {r.seconds}s
                </time>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
