import { Link } from "react-router-dom";
import { ArrowRight, Inbox as InboxIcon } from "lucide-react";
import { useInbox, useVitals, useWidgets } from "../api";
import { AgentChip, Stamp } from "../components/bits";
import "./dashboard.css";

export default function Dashboard() {
  const { data: inbox } = useInbox();
  const { data: vitals } = useVitals();
  const { data: widgets } = useWidgets();
  const preview = (inbox ?? []).slice(0, 4);

  const stat = (label: string, value: number | string | undefined) => (
    <div className="stat" key={label}>
      <span className="stat-num">{value ?? "—"}</span>
      <span className="stat-label">{label}</span>
    </div>
  );

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Mission control</p>
          <h1 className="view-title">Good to see you</h1>
        </div>
        <p className="view-note">
          Your world model, and everything Orion has been doing while you were away.
        </p>
      </header>

      <div className="stat-row">
        {stat("entities", vitals?.stats?.entities)}
        {stat("knowledge", vitals?.stats?.knowledge)}
        {stat("relationships", vitals?.stats?.relationships)}
        {stat("pending review", inbox?.length)}
      </div>

      <section className="card dash-inbox">
        <header className="card-head">
          <InboxIcon size={17} style={{ color: "var(--copper)" }} />
          <h2>Awaiting review</h2>
          <span className={`count-pill${inbox?.length ? " alert" : ""}`}>{inbox?.length ?? 0}</span>
          <Link to="/inbox" className="btn btn-sm btn-ghost" style={{ marginLeft: "auto" }}>
            open inbox <ArrowRight size={13} />
          </Link>
        </header>
        {preview.length === 0 ? (
          <p className="loading">Nothing to review. Orion hasn't inferred anything it isn't sure of.</p>
        ) : (
          <ul className="dash-preview">
            {preview.map((it) => (
              <li key={`${it.origin}-${it.id}`}>
                <AgentChip agent={it.prov_agent} />
                {it.origin === "curator" ? (
                  <span className="dash-line">
                    <span className={`kind-badge kind-${it.kind}`}>
                      {(it.kind || "grammar").replace("_", " ")}
                    </span>
                    {it.prov_label}
                  </span>
                ) : (
                  <span className="dash-line">
                    <Stamp kind={it.payload?.kind || "observation"} />
                    {it.payload?.value}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {widgets && widgets.length > 0 && (
        <div className="widget-grid">
          {widgets.map((w) => (
            <section className="card" key={w.name}>
              <header className="card-head">
                <h2>{w.title}</h2>
              </header>
              <div className="widget-body" dangerouslySetInnerHTML={{ __html: w.html }} />
            </section>
          ))}
        </div>
      )}
    </>
  );
}
