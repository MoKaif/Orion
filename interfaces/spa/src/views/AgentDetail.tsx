import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Play, Loader2, ExternalLink } from "lucide-react";
import { useAgentDetail, useResolveInbox, useRunAgent, InboxItem } from "../api";
import { DiffBlock, Loading } from "../components/bits";
import "./agent-detail.css";

export default function AgentDetail() {
  const { name = "" } = useParams();
  const { data, isLoading } = useAgentDetail(name);
  const run = useRunAgent();
  const resolve = useResolveInbox();

  if (isLoading || !data) return <Loading label="Loading agent…" />;
  const { job, runs, proposals, questions, entities } = data;
  const busy = !!job.running_since || run.isPending;
  const HUB_THRESHOLD = 3;

  const asItem = (p: (typeof proposals)[number]): InboxItem => ({
    origin: "curator",
    id: p.id,
    kind: p.kind,
    diff: p.diff,
    created_at: p.created_at,
    prov_agent: "Curator",
    prov_label: p.path,
  });

  return (
    <div>
      <header className="view-head">
        <div>
          <p className="eyebrow">Agent</p>
          <h1 className="view-title">{job.label}</h1>
        </div>
        <div className="agent-actions">
          <Link to="/agents" className="btn btn-sm">
            <ArrowLeft size={14} /> All agents
          </Link>
          <button
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() => run.mutate(job.name)}
          >
            {busy ? <Loader2 size={13} className="spin" /> : <Play size={13} />}
            {job.running_since ? "Running…" : "Run now"}
          </button>
        </div>
      </header>

      <section className="card">
        <header className="card-head">
          <h2>Status</h2>
          <span className={`run-badge ${job.running_since ? "running" : "idle"}`}>
            {job.running_since ? `running since ${job.running_since}` : "idle"}
          </span>
        </header>
        <dl className="agent-meta agent-meta-row">
          <div>
            <dt>schedule</dt>
            <dd>{job.cron}</dd>
          </div>
          <div>
            <dt>next run</dt>
            <dd>{job.next_run || "manual only"}</dd>
          </div>
          <div>
            <dt>last run</dt>
            <dd>{job.last_run || "never"}</dd>
          </div>
          <div>
            <dt>last result</dt>
            <dd>{job.last_result || "—"}</dd>
          </div>
        </dl>
      </section>

      {questions.length > 0 && (
        <section className="card" style={{ marginTop: 16 }}>
          <header className="card-head">
            <h2>Questions for you</h2>
            <span className="count-pill alert">{questions.length}</span>
          </header>
          <ul className="q-list">
            {questions.map((q) => (
              <li key={q.id} className="q-item">
                <span className="q-subject">{q.subject}</span>
                <p className="q-text">{q.question}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {proposals.length > 0 && (
        <section className="card" style={{ marginTop: 16 }}>
          <header className="card-head">
            <h2>Proposals awaiting review</h2>
            <span className="count-pill alert">{proposals.length}</span>
            <Link to="/inbox" className="btn btn-sm btn-ghost" style={{ marginLeft: "auto" }}>
              review in inbox <ExternalLink size={12} />
            </Link>
          </header>
          <div className="proposal-list">
            {proposals.slice(0, 12).map((p) => (
              <article className="proposal" key={p.id}>
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
        </section>
      )}

      {entities.length > 0 && (
        <section className="card" style={{ marginTop: 16 }}>
          <header className="card-head">
            <h2>Entity registry</h2>
            <span className="count-pill">{entities.length}</span>
          </header>
          <p className="registry-hint">
            The vault's cast, resolved from your notes. Each becomes a hub-note proposal once it's
            been seen {HUB_THRESHOLD}+ times — the bar shows progress.
          </p>
          <ul className="registry">
            {entities.map((e) => (
              <li className="reg-item" key={e.id}>
                <span className={`reg-type reg-${e.type}`}>{e.type}</span>
                <span className="reg-name">{e.name}</span>
                {e.note_path ? (
                  <span className="reg-badge">hub note ✓</span>
                ) : (
                  <span className="reg-track" title={`${e.mentions} of ${HUB_THRESHOLD} mentions`}>
                    <i style={{ width: `${Math.min(100, (e.mentions / HUB_THRESHOLD) * 100)}%` }} />
                  </span>
                )}
                <span className="reg-count">
                  {e.mentions} {e.mentions === 1 ? "mention" : "mentions"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card" style={{ marginTop: 16 }}>
        <header className="card-head">
          <h2>Run log</h2>
          <span className="count-pill">{runs.length}</span>
        </header>
        {runs.length === 0 ? (
          <p className="loading">No runs recorded yet.</p>
        ) : (
          <ol className="feed">
            {runs.map((r, i) => (
              <li className="feed-item" key={i}>
                <span className={`feed-type${r.ok ? "" : " failed"}`}>{r.ok ? "ok" : "failed"}</span>
                <span className="feed-detail">{r.result}</span>
                <i className="leader" />
                <time className="feed-time">
                  {r.at} · {r.seconds}s
                </time>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
