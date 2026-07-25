import { Loader2 } from "lucide-react";
import "./bits.css";

const KIND_LABEL: Record<string, string> = { fact: "Fact", observation: "Obs", idea: "Idea" };

/** A rubber-stamp classification mark for a knowledge kind. */
export function Stamp({ kind }: { kind: string }) {
  const k = kind || "observation";
  return (
    <span className={`stamp stamp-${k}`} title={k}>
      {KIND_LABEL[k] ?? k.slice(0, 4)}
    </span>
  );
}

/** Which agent produced this, as a small tilted stamp. */
export function AgentChip({ agent }: { agent: string }) {
  return <span className={`agent-chip agent-${agent.toLowerCase()}`}>{agent}</span>;
}

/** A syntax-highlighted unified diff. */
export function DiffBlock({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="diffview">
      {lines.map((line, i) => {
        let cls = "";
        if (line.startsWith("@@")) cls = "dl-hunk";
        else if (line.startsWith("+++") || line.startsWith("---")) cls = "dl-meta";
        else if (line.startsWith("+")) cls = "dl-add";
        else if (line.startsWith("-")) cls = "dl-del";
        return (
          <span key={i} className={`dl ${cls}`}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading">
      <Loader2 size={14} className="spin" style={{ verticalAlign: "-2px", marginRight: 8 }} />
      {label}
    </div>
  );
}

export function ConfMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="confmeter" title={`confidence ${pct}%`}>
      <span className="track">
        <i style={{ width: `${pct}%` }} />
      </span>
      <b>{pct}%</b>
    </span>
  );
}
