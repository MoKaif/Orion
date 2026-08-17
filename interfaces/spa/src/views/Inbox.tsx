import { useMemo, useState } from "react";
import { ExternalLink, Quote, FileText, ArrowRight, Trash2, HelpCircle } from "lucide-react";
import { DuplicatePlan, InboxAction, InboxItem, useInbox, useResolveInbox } from "../api";
import { AgentChip, DiffBlock, Loading, Stamp, ConfMeter } from "../components/bits";
import "./inbox.css";

type Filter = "all" | "knowledge" | "edits" | "questions";

const MATCH: Record<Filter, (i: InboxItem) => boolean> = {
  all: () => true,
  knowledge: (i) => i.origin === "world_model",
  edits: (i) => i.origin === "curator",
  questions: (i) => i.origin === "curator_question",
};

const TAB_LABEL: Record<Filter, string> = {
  all: "Everything",
  knowledge: "Knowledge",
  edits: "Note edits",
  questions: "Questions",
};

/** The two sides of a duplicate, so the choice is visible rather than described. */
function DuplicateCard({ plan }: { plan: DuplicatePlan }) {
  if (plan.action === "gone") return null;
  const sides = [
    ...(plan.keep ? [{ ...plan.keep, role: "keep" as const }] : []),
    ...plan.drop.map((d) => ({ ...d, role: "drop" as const })),
  ];
  return (
    <ul className="dup">
      {sides.map((s) => (
        <li className={`dup-side dup-${s.role}`} key={s.id}>
          <span className="dup-role">
            {s.role === "keep" ? "keeps" : plan.action === "discard" ? "deleted" : "merged in"}
          </span>
          <span className="dup-path" title={s.source || s.canonical_key || s.name}>
            {s.source || s.canonical_key || s.name}
          </span>
          {s.stale && (
            <span className="dup-flag">
              <Trash2 size={11} /> in trash
            </span>
          )}
          <i className="leader" />
          <span className="dup-count">
            {s.knowledge} {s.knowledge === 1 ? "fact" : "facts"}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Free-text reply, for questions that aren't a yes/no. */
function AnswerBox({ item, onSend }: { item: InboxItem; onSend: (a: string) => void }) {
  const [text, setText] = useState("");
  return (
    <form
      className="answer"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onSend(item.origin === "curator_memory" ? text : text.trim());
        setText("");
      }}
    >
      <textarea
        className="answer-input"
        value={text}
        rows={item.origin === "curator_memory" ? 5 : 2}
        placeholder={item.origin === "curator_memory"
          ? "Write it as you remember it. Curator will preserve these exact words…"
          : "or answer in your own words…"}
        aria-label={`Answer: ${item.title}`}
        onChange={(e) => setText(e.target.value)}
      />
      <button className="btn btn-sm" type="submit" disabled={!text.trim()}>
        Send
      </button>
    </form>
  );
}

function Actions({
  item,
  pending,
  onAct,
}: {
  item: InboxItem;
  pending: boolean;
  onAct: (a: string) => void;
}) {
  const [armed, setArmed] = useState<string | null>(null);
  const actions: InboxAction[] = item.actions?.length
    ? item.actions
    : [
        { label: "Accept", value: "accept", tone: "accept" },
        { label: "Reject", value: "reject", tone: "reject" },
      ];

  return (
    <footer className="item-actions">
      {actions.map((a) => {
        const needsConfirm = !!a.confirm && armed !== a.value;
        const cls =
          a.tone === "accept" ? "btn-accept" : a.tone === "reject" ? "btn-reject" : "btn-ghost";
        return (
          <button
            key={a.value}
            className={`btn ${cls} btn-sm${armed === a.value ? " armed" : ""}`}
            disabled={pending}
            title={a.confirm ?? undefined}
            onClick={() => {
              if (needsConfirm) return setArmed(a.value);
              setArmed(null);
              onAct(a.value);
            }}
          >
            {armed === a.value ? "Click again to confirm" : a.label}
          </button>
        );
      })}
      {armed && (
        <button className="btn btn-ghost btn-sm" onClick={() => setArmed(null)}>
          Cancel
        </button>
      )}
    </footer>
  );
}

function Card({ item }: { item: InboxItem }) {
  const resolve = useResolveInbox();
  const act = (action: string) => resolve.mutate({ item, action });
  const isQuestion = item.origin === "curator_question" || item.origin === "curator_memory";

  return (
    <article className={`inbox-item item-${item.origin}`}>
      <div className="item-top">
        {isQuestion ? (
          <span className="kind-badge kind-question">
            <HelpCircle size={11} /> question
          </span>
        ) : item.origin === "curator" ? (
          <span className={`kind-badge kind-${item.kind}`}>
            {(item.kind || "grammar").replace("_", " ")}
          </span>
        ) : item.item_type === "knowledge" ? (
          <Stamp kind={item.payload?.kind || "observation"} />
        ) : (
          <span className={`kind-badge kind-${item.item_type}`}>
            {(item.item_type || "notice").replace("_", " ")}
          </span>
        )}
        {item.title && <h3 className="item-title">{item.title}</h3>}
        {typeof item.confidence === "number" && item.item_type === "knowledge" && (
          <ConfMeter value={item.confidence} />
        )}
      </div>

      {/* the claim itself */}
      {item.item_type === "knowledge" && item.payload?.value && (
        <p className="claim-text">{item.payload.value}</p>
      )}
      {item.body && <p className="item-body">{item.body}</p>}
      {item.payload?.quote && (
        <blockquote className="item-quote">
          <Quote size={12} /> {item.payload.quote}
        </blockquote>
      )}
      {item.plan && <DuplicateCard plan={item.plan} />}
      {item.diff && <DiffBlock diff={item.diff} />}

      {/* the part that was missing: what happens when you say yes */}
      {item.effect && (
        <p className="item-effect">
          <ArrowRight size={13} />
          <span>{item.effect}</span>
        </p>
      )}

      <Actions item={item} pending={resolve.isPending} onAct={act} />
      {isQuestion && <AnswerBox item={item} onSend={act} />}
      {resolve.isError && (
        <p className="item-error">
          {resolve.error instanceof Error ? resolve.error.message : "That didn't go through."}
        </p>
      )}
    </article>
  );
}

export default function Inbox() {
  const { data, isLoading } = useInbox();
  const [filter, setFilter] = useState<Filter>("all");

  const items = useMemo(() => (data ?? []).filter(MATCH[filter]), [data, filter]);

  // group by where it came from, preserving newest-first order
  const groups = useMemo(() => {
    const map = new Map<
      string,
      { agent: string; label: string; uri?: string | null; items: InboxItem[] }
    >();
    for (const it of items) {
      const key = `${it.prov_agent}::${it.prov_label}`;
      if (!map.has(key))
        map.set(key, { agent: it.prov_agent, label: it.prov_label, uri: it.prov_uri, items: [] });
      map.get(key)!.items.push(it);
    }
    return [...map.values()];
  }, [items]);

  const counts = useMemo(() => {
    const all = data ?? [];
    return {
      all: all.length,
      knowledge: all.filter(MATCH.knowledge).length,
      edits: all.filter(MATCH.edits).length,
      questions: all.filter(MATCH.questions).length,
    };
  }, [data]);

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Waiting on you</p>
          <h1 className="view-title">The inbox</h1>
        </div>
        <p className="view-note">
          Everything Orion wants to change or asks about waits here. Each card says what it will
          do before you agree to it — nothing happens until you do.
        </p>
      </header>

      <div className="tabs">
        {(["all", "knowledge", "edits", "questions"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`tab${filter === f ? " active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {TAB_LABEL[f]}
            <span className="tab-count">{counts[f]}</span>
          </button>
        ))}
      </div>

      {isLoading ? (
        <Loading label="Reading the inbox…" />
      ) : groups.length === 0 ? (
        <div className="empty-state">
          <span className="empty-glyph">◈</span>
          <p>Nothing waiting. Orion will queue anything it wants to change here.</p>
        </div>
      ) : (
        <div className="inbox-groups">
          {groups.map((g) => (
            <section className="inbox-group" key={`${g.agent}-${g.label}`}>
              <header className="group-head">
                <AgentChip agent={g.agent} />
                <span className="group-src">
                  <FileText size={13} /> from {g.label}
                </span>
                <span className="group-count">
                  {g.items.length} {g.items.length === 1 ? "item" : "items"}
                </span>
                {g.uri && (
                  <a className="group-open" href={g.uri} title="Open in Obsidian">
                    open <ExternalLink size={12} />
                  </a>
                )}
              </header>
              <div className="item-list">
                {g.items.map((it) => (
                  <Card key={`${it.origin}-${it.id}`} item={it} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
