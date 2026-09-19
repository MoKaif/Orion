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
  const { data, isLoading } = useSessions();
  if (isLoading) return <Loading label="Loading sessions…" />;
  const sessions = data ?? [];

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

      {sessions.length === 0 ? (
        <div className="empty-state">
          <span className="empty-glyph">◇</span>
          <p>No sessions yet. Start a chat and it will appear here.</p>
        </div>
      ) : (
        <ul className="session-list">
          {sessions.map((s) => (
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
