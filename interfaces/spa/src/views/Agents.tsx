import { CSSProperties } from "react";
import { Link } from "react-router-dom";
import {
  BookOpen,
  Bot,
  Compass,
  Mail,
  ArrowRight,
  Loader2,
  CircleAlert,
  PauseCircle,
  GitPullRequest,
  LucideIcon,
} from "lucide-react";
import { AgentCard, useAgents } from "../api";
import { Loading } from "../components/bits";
import { ShiftStrip } from "../components/shift";
import { agoText, untilText } from "../cron";
import "./agents.css";

/** Agents name their icon when they register; core stays out of the picture. */
const ICONS: Record<string, LucideIcon> = {
  "book-open": BookOpen,
  compass: Compass,
  mail: Mail,
  bot: Bot,
  "git-pull-request": GitPullRequest,
};

/** One state line per agent, in priority order — never two competing signals at once. */
function statusOf(a: AgentCard) {
  if (a.failing.length)
    return { tone: "failed", icon: CircleAlert, text: "last run failed" } as const;
  if (a.busy) return { tone: "busy", icon: Loader2, text: "working" } as const;
  const pending = a.summary.pending ?? 0;
  if (pending)
    return { tone: "waiting", icon: CircleAlert, text: `${pending} waiting for you` } as const;
  if (a.job_count > 0 && a.paused === a.job_count)
    return { tone: "paused", icon: PauseCircle, text: "all passes paused" } as const;
  return { tone: "idle", icon: null, text: "idle" } as const;
}

function Card({ agent }: { agent: AgentCard }) {
  const Icon = ICONS[agent.icon] ?? Bot;
  const status = statusOf(agent);
  const StatusIcon = status.icon;
  const metrics = agent.summary.metrics ?? [];

  return (
    <Link
      to={`/agents/${agent.name}`}
      className="agent-card"
      style={{ "--accent": `var(--${agent.accent})` } as CSSProperties}
    >
      <header className="ac-head">
        <span className="ac-mark">
          <Icon size={18} strokeWidth={1.9} />
        </span>
        <div className="ac-name">
          <p className="ac-tagline">{agent.tagline || agent.plugin}</p>
          <h2>{agent.title}</h2>
        </div>
        <span className={`ac-status st-${status.tone}`}>
          {StatusIcon && <StatusIcon size={12} className={status.tone === "busy" ? "spin" : ""} />}
          {status.text}
        </span>
      </header>

      <p className="ac-blurb">{agent.blurb}</p>

      {metrics.length > 0 && (
        <dl className="ac-metrics">
          {metrics.map((m) => (
            <div key={m.label}>
              <dt>{m.label}</dt>
              <dd>{m.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <ShiftStrip jobs={agent.jobs} />

      <footer className="ac-foot">
        <span className="ac-count">
          {agent.job_count} {agent.job_count === 1 ? "pass" : "passes"}
          {agent.paused > 0 && <em> · {agent.paused} paused</em>}
        </span>
        <span className="ac-when">
          {agent.next_run ? `next ${untilText(agent.next_run)}` : "manual only"}
          {agent.last_run ? ` · ran ${agoText(agent.last_run)}` : ""}
        </span>
        <ArrowRight className="ac-go" size={15} />
      </footer>
    </Link>
  );
}

export default function Agents() {
  const { data, isLoading } = useAgents();
  if (isLoading || !data) return <Loading label="Gathering agents…" />;

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Night shift</p>
          <h1 className="view-title">Agents</h1>
        </div>
        <p className="view-note">
          The work Orion does while you're away. Open one to run a pass by hand or change when it
          works.
        </p>
      </header>

      {data.length === 0 ? (
        <div className="empty-state">
          <span className="empty-glyph">◷</span>
          No agents are registered yet. A plugin adds one by calling <code>add_agent</code>.
        </div>
      ) : (
        <div className="agent-grid">
          {data.map((a) => (
            <Card key={a.name} agent={a} />
          ))}
        </div>
      )}
    </>
  );
}
