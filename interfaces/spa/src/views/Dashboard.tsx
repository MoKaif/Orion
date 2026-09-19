import { Link } from "react-router-dom";
import { ArrowRight, Bot, BrainCircuit, Inbox as InboxIcon, MessageSquare, Radio } from "lucide-react";
import { useInbox, useVitals, useWidgets } from "../api";
import { AgentChip, Stamp } from "../components/bits";
import "./dashboard.css";

export default function Dashboard() {
  const { data: inbox } = useInbox();
  const { data: vitals } = useVitals();
  const { data: widgets } = useWidgets();
  const preview = (inbox ?? []).slice(0, 5);
  const online = [vitals?.ollama_up, vitals?.deepseek_up, vitals?.gemini_up, vitals?.anthropic_up].filter(Boolean).length;
  const today = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  const stats = [
    ["Entities", vitals?.stats?.entities, "people, projects and concepts"],
    ["Knowledge", vitals?.stats?.knowledge, "facts and observations retained"],
    ["Events", vitals?.stats?.events, "moments in your world model"],
    ["Sessions", vitals?.stats?.sessions, "conversations remembered"],
  ];
  const providers = [
    { name: "Ollama", up: vitals?.ollama_up, role: "local" },
    { name: "DeepSeek", up: vitals?.deepseek_up, role: "cloud" },
    { name: "Gemini", up: vitals?.gemini_up, role: "fallback" },
    { name: "Anthropic", up: vitals?.anthropic_up, role: "fallback" },
  ];

  return (
    <div className="dashboard">
      <section className="dash-hero">
        <div className="dash-hero-copy">
          <p className="eyebrow">{today}</p>
          <h1>The world remembers.</h1>
          <p>So you can move forward. Orion keeps your knowledge, agents and decisions in one living system.</p>
          <div className="dash-actions">
            <Link className="btn btn-primary" to="/chat"><MessageSquare size={14}/> Ask Orion</Link>
            <Link className="btn" to="/agents"><Bot size={14}/> View agents</Link>
          </div>
        </div>
        <div className="dash-orbit" aria-hidden="true"><img src="/brand/orion-mark.svg" alt="" /></div>
        <div className="dash-live"><Radio size={13}/><span>{online} providers online</span><i/><b>{inbox?.length ?? 0} waiting</b></div>
      </section>

      <section className="stat-row" aria-label="World model summary">
        {stats.map(([label, value, note]) => (
          <div className="stat" key={String(label)}><span className="stat-num">{value ?? "—"}</span><span className="stat-label">{label}</span><span className="stat-note">{note}</span></div>
        ))}
      </section>

      <div className="dash-layout">
        <section className="card dash-inbox">
          <header className="card-head">
            <span className="section-icon"><InboxIcon size={16}/></span>
            <div><p>Review queue</p><h2>Waiting for you</h2></div>
            <span className={`count-pill${inbox?.length ? " alert" : ""}`}>{inbox?.length ?? 0}</span>
            <Link to="/inbox" className="btn btn-sm btn-ghost">Open inbox <ArrowRight size={12}/></Link>
          </header>
          {preview.length === 0 ? (
            <div className="dash-clear"><BrainCircuit size={22}/><div><b>Everything is clear</b><span>No decisions or inferred knowledge need your review.</span></div></div>
          ) : (
            <ul className="dash-preview">
              {preview.map((it) => (
                <li key={`${it.origin}-${it.id}`}>
                  <AgentChip agent={it.prov_agent}/>
                  <span className="dash-line">
                    {it.origin === "curator" ? (
                      <span className={`kind-badge kind-${it.kind}`}>
                        {(it.kind || "grammar").replace("_", " ")}
                      </span>
                    ) : (
                      <Stamp kind={it.payload?.kind || "observation"} />
                    )}
                    <span>{it.title || it.payload?.value || it.prov_label}</span>
                  </span>
                  <ArrowRight size={13}/>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="dash-system card">
          <header className="card-head"><span className="section-icon"><Radio size={16}/></span><div><p>System</p><h2>Capacity</h2></div></header>
          <div className="system-grid">
            <div><b>{vitals?.tools ?? "—"}</b><span>Tools</span></div><div><b>{vitals?.specialists ?? "—"}</b><span>Specialists</span></div><div><b>{online}/4</b><span>Providers</span></div><div><b>{vitals?.stats?.relationships ?? "—"}</b><span>Links</span></div>
          </div>
          <ul className="provider-list">
            {providers.map(({ name, up, role }) => (
              <li key={name}><i className={up ? "up" : ""}/><span>{name}</span><em>{up ? role : "offline"}</em></li>
            ))}
          </ul>
        </section>
      </div>

      {widgets && widgets.length > 0 && (
        <section className="dash-agents">
          <header className="dash-section-head"><div><p className="eyebrow">Live from your agents</p><h2>What changed</h2></div><Link to="/agents">All agents <ArrowRight size={13}/></Link></header>
          <div className="widget-grid">
            {widgets.map((w) => <article className={`card widget-card widget-${w.name}`} key={w.name}><header className="card-head"><h3>{w.title}</h3><span className="widget-pulse"/></header><div className="widget-body" dangerouslySetInnerHTML={{__html:w.html}}/></article>)}
          </div>
        </section>
      )}
    </div>
  );
}
