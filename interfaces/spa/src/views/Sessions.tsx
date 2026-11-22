import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, MessageSquare } from "lucide-react";
import { useSessions } from "../api";
import { Loading } from "../components/bits";
import "./sessions.css";

function formatDate(value?: string) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export default function Sessions() {
  const [query, setQuery] = useState("");
  const { data, isLoading, isError, refetch } = useSessions();
  if (isLoading) return <Loading label="Loading sessions…" />;
  if (isError && !data) return <p role="alert">Could not load conversations. <button className="btn" onClick={() => refetch()}>Retry</button></p>;
  const sessions = data ?? [];
  const visible = sessions.filter(s => (s.title || `Session #${s.id}`).toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <>
      <header className="view-head">
        <div>
          <p className="eyebrow">Conversation</p>
          <h1 className="view-title">Sessions</h1>
        </div>
        <p className="view-note">Every conversation, and the knowledge each one grew.</p>
        <span className="view-count">{sessions.length} conversations</span>
      </header>

      <div className="directory-tools">
        <input type="search" aria-label="Search conversations" placeholder="Search conversation titles…" value={query} onChange={e => setQuery(e.target.value)} />
        <Link className="btn btn-primary" to="/chat"><MessageSquare size={14}/> New chat</Link>
      </div>
      {visible.length === 0 ? (
        <div className="empty-state">
          <span className="empty-glyph">◇</span>
          <p>{sessions.length ? "No conversations match your search." : "Your conversations will appear here."}</p>
        </div>
      ) : (
        <ul className="session-list">
          {visible.map((s) => (
            <li key={s.id}>
              <Link to={`/chat/${s.id}`} className="session-row">
                <span className="session-icon"><MessageSquare size={17} /></span>
                <span className="session-copy">
                  <span className="session-title">{s.title || `Session #${s.id}`}</span>
                  <span className="session-date">{formatDate(s.created_at)}</span>
                </span>
                <span className="session-meta">
                  {typeof s.message_count === "number" ? `${s.message_count} messages` : "Open"}
                </span>
                <ArrowUpRight className="session-arrow" size={17} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
