import { useMemo, useState } from "react";
import { Check, X, ExternalLink, Quote, FileText } from "lucide-react";
import { InboxItem, useInbox, useResolveInbox } from "../api";
import { AgentChip, DiffBlock, Loading, Stamp, ConfMeter } from "../components/bits";
import "./inbox.css";

type Filter = "all" | "world_model" | "curator";

const KIND_LABEL: Record<string, string> = {
  grammar: "spelling / grammar fix",
  backlink: "new [[wikilink]]",
  entity_note: "new hub note",
};

export default function Inbox() {
  const { data, isLoading } = useInbox();
  const resolve = useResolveInbox();
  const [filter, setFilter] = useState<Filter>("all");

  const items = useMemo(
    () => (data ?? []).filter((it) => filter === "all" || it.origin === filter),
    [data, filter],
  );

  // group by source (agent + note), preserving newest-first order
  const groups = useMemo(() => {
    const map = new Map<string, { agent: string; label: string; uri?: string | null; items: InboxItem[] }>();
    for (const it of items) {
      const key = `${it.prov_agent}::${it.prov_label}`;
      if (!map.has(key))
        map.set(key, { agent: it.prov_agent, label: it.prov_label, uri: it.prov_uri, items: [] });
      map.get(key)!.items.push(it);
    }
    return [...map.values()];
  }, [items]);

  const counts = useMemo(() => {
    const wm = (data ?? []).filter((i) => i.origin === "world_model").length;
    const cu = (data ?? []).filter((i) => i.origin === "curator").length;
    return { all: wm + cu, world_model: wm, curator: cu };
  }, [data]);

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Knowledge lifecycle</p>
          <h1 className="view-title">The inbox</h1>
        </div>
        <p className="view-note">
          Everything Orion proposes waits here — knowledge it inferred and note edits the Curator
          drafted. Nothing is committed until you accept it.
        </p>
      </header>

      <div className="tabs">
        {(["all", "world_model", "curator"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`tab${filter === f ? " active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "Everything" : f === "world_model" ? "Knowledge" : "Note edits"}
            <span className="tab-count">{counts[f]}</span>
          </button>
        ))}
      </div>

      {isLoading ? (
        <Loading label="Reading the inbox…" />
      ) : groups.length === 0 ? (
        <div className="empty-state">
          <span className="empty-glyph">◈</span>
          <p>Nothing to review here. Every inference has been stamped.</p>
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
                  <article className="inbox-item" key={`${it.origin}-${it.id}`}>
                    {it.origin === "curator" ? (
                      <>
                        <div className="item-top">
                          <span className={`kind-badge kind-${it.kind}`}>
                            {(it.kind || "grammar").replace("_", " ")}
                          </span>
                          <span className="item-sub">
                            Curator proposes a {KIND_LABEL[it.kind || "grammar"] || "note edit"} —
                            applies only when you approve (it backs up first)
                          </span>
                        </div>
                        {it.diff && <DiffBlock diff={it.diff} />}
                      </>
                    ) : (
                      <>
                        <div className="item-claim">
                          <Stamp kind={it.payload?.kind || "observation"} />
                          <p className="claim-text">{it.payload?.value}</p>
                        </div>
                        {it.payload?.quote && (
                          <blockquote className="item-quote">
                            <Quote size={12} /> {it.payload.quote}
                          </blockquote>
                        )}
                        <p className="item-sub">
                          Record this {it.payload?.kind || "observation"} about{" "}
                          <strong>{it.payload?.entity}</strong>
                          {typeof it.confidence === "number" && (
                            <>
                              {" "}
                              · <ConfMeter value={it.confidence} />
                            </>
                          )}
                        </p>
                      </>
                    )}

                    <footer className="item-actions">
                      <button
                        className="btn btn-accept btn-sm"
                        disabled={resolve.isPending}
                        onClick={() => resolve.mutate({ item: it, action: "accept" })}
                      >
                        <Check size={14} />
                        {it.origin === "curator"
                          ? it.kind === "entity_note"
                            ? "Create note"
                            : "Apply"
                          : "Accept"}
                      </button>
                      <button
                        className="btn btn-reject btn-sm"
                        disabled={resolve.isPending}
                        onClick={() => resolve.mutate({ item: it, action: "reject" })}
                      >
                        <X size={14} /> Reject
                      </button>
                    </footer>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
