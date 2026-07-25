import { Activity, Boxes, Cpu, DollarSign, Wrench, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useVitals } from "../api";
import "./telemetry.css";

function providerDollars(usage: Record<string, unknown>): string {
  // usage may carry a per-day/per-model cost map; sum today's dollars if present.
  const today = (usage?.["today"] ?? usage) as Record<string, unknown> | undefined;
  let total = 0;
  const walk = (v: unknown) => {
    if (typeof v === "number") total += v;
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  if (today && typeof today === "object" && "cost" in today) walk((today as any).cost);
  return total > 0 ? `$${total.toFixed(3)}` : "$0.00";
}

export default function Telemetry({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { data: v } = useVitals();

  const dot = (up?: boolean) => (
    <span className={`dot ${up ? "up" : "down"}`} aria-hidden />
  );

  if (collapsed) {
    return (
      <aside className="tele tele-min">
        <button className="tele-toggle" onClick={onToggle} title="Show telemetry">
          <PanelRightOpen size={16} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="tele">
      <button className="tele-toggle tele-toggle-top" onClick={onToggle} title="Hide telemetry">
        <PanelRightClose size={16} />
      </button>
      <div className="tele-sec">
        <div className="tele-h">
          <Activity size={13} /> Providers
        </div>
        <ul className="ledger">
          <li>
            <span>{dot(v?.ollama_up)} Ollama</span>
            <i className="leader" />
            <b>{v?.ollama_up ? "local" : "down"}</b>
          </li>
          <li>
            <span>{dot(v?.deepseek_up)} DeepSeek</span>
            <i className="leader" />
            <b>{v?.deepseek_up ? "cloud brain" : "off"}</b>
          </li>
          <li>
            <span>{dot(v?.gemini_up)} Gemini</span>
            <i className="leader" />
            <b>{v?.gemini_up ? "ready" : "off"}</b>
          </li>
          <li>
            <span>{dot(v?.anthropic_up)} Anthropic</span>
            <i className="leader" />
            <b>{v?.anthropic_up ? "ready" : "off"}</b>
          </li>
        </ul>
      </div>

      <div className="tele-sec">
        <div className="tele-h">
          <Boxes size={13} /> World model
        </div>
        <ul className="ledger">
          {v?.stats &&
            Object.entries(v.stats)
              .slice(0, 5)
              .map(([k, n]) => (
                <li key={k}>
                  <span>{k.replace(/_/g, " ")}</span>
                  <i className="leader" />
                  <b>{n as number}</b>
                </li>
              ))}
        </ul>
      </div>

      <div className="tele-sec">
        <div className="tele-h">
          <Cpu size={13} /> Capacity
        </div>
        <ul className="ledger">
          <li>
            <span>
              <Wrench size={11} /> tools
            </span>
            <i className="leader" />
            <b>{v?.tools ?? "—"}</b>
          </li>
          <li>
            <span>specialists</span>
            <i className="leader" />
            <b>{v?.specialists ?? "—"}</b>
          </li>
          <li>
            <span>
              <DollarSign size={11} /> cost today
            </span>
            <i className="leader" />
            <b>{v ? providerDollars(v.usage) : "—"}</b>
          </li>
        </ul>
      </div>
    </aside>
  );
}
