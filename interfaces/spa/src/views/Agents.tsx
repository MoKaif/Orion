import { Link } from "react-router-dom";
import { BookOpen, Play, ChevronRight, Loader2, CircleAlert } from "lucide-react";
import { Job, useAgents, useRunAgent } from "../api";
import { Loading } from "../components/bits";
import "./agents.css";

function RunButton({ job }: { job: Job }) {
  const run = useRunAgent();
  const busy = !!job.running_since || run.isPending;
  return (
    <button
      className="btn btn-primary btn-sm"
      disabled={busy}
      onClick={() => run.mutate(job.name)}
    >
      {busy ? <Loader2 size={13} className="spin" /> : <Play size={13} />}
      {job.running_since ? "Running…" : "Run now"}
    </button>
  );
}

function StatusBadge({ job }: { job: Job }) {
  if (job.running_since) return <span className="run-badge running">running</span>;
  if (job.last_ok === false) return <span className="run-badge failed">failed</span>;
  return null;
}

export default function Agents() {
  const { data, isLoading } = useAgents();
  if (isLoading || !data) return <Loading label="Gathering agents…" />;

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Background life</p>
          <h1 className="view-title">Agents</h1>
        </div>
        <p className="view-note">
          Orion's own work while you're away. Each runs on its schedule — or right now, if you say
          so.
        </p>
      </header>

      {data.curator.jobs.length > 0 && (
        <section className="card curator-agent">
          <header className="curator-head">
            <span className="curator-mark">
              <BookOpen size={19} />
            </span>
            <div className="curator-title">
              <p className="eyebrow">Obsidian vault</p>
              <h2>Curator</h2>
            </div>
            {data.curator.pending > 0 && (
              <Link to="/inbox" className="curator-pending">
                <CircleAlert size={14} />
                {data.curator.pending} to review
                <ChevronRight size={14} />
              </Link>
            )}
          </header>
          <p className="curator-blurb">
            The vault's resident editor and memory-builder. Every pass below proposes — nothing
            touches a note or your world model until you approve it in the inbox.
          </p>

          <ul className="subagents">
            {data.curator.jobs.map((j) => (
              <li className="subagent" key={j.name}>
                <div className="subagent-main">
                  <span className="subagent-name">{j.label}</span>
                  <StatusBadge job={j} />
                  <span className="subagent-cron">
                    {j.cron} · next {j.next_run || "manual"}
                  </span>
                </div>
                <div className="subagent-actions">
                  <Link to={`/agents/${j.name}`} className="btn btn-sm">
                    Details
                  </Link>
                  <RunButton job={j} />
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.other.length > 0 && (
        <>
          <h3 className="section-label">Other agents</h3>
          <div className="grid">
            {data.other.map((j) => (
              <section className="card agent-card" key={j.name}>
                <header className="card-head">
                  <h2>{j.label}</h2>
                  <StatusBadge job={j} />
                </header>
                <dl className="agent-meta">
                  <div>
                    <dt>schedule</dt>
                    <dd>{j.cron}</dd>
                  </div>
                  <div>
                    <dt>next run</dt>
                    <dd>{j.next_run || "manual only"}</dd>
                  </div>
                  <div>
                    <dt>last run</dt>
                    <dd>{j.last_run || "never"}</dd>
                  </div>
                </dl>
                <footer className="agent-actions">
                  <Link to={`/agents/${j.name}`} className="btn btn-sm">
                    Details
                  </Link>
                  <RunButton job={j} />
                </footer>
              </section>
            ))}
          </div>
        </>
      )}
    </>
  );
}
