import { Link } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import { useSessions } from "../api";
import { Loading } from "../components/bits";
import "./sessions.css";

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
                <MessageSquare size={16} />
                <span className="session-title">{s.title || `Session #${s.id}`}</span>
                {typeof s.message_count === "number" && (
                  <span className="session-meta">{s.message_count} msgs</span>
                )}
                <span className="session-date">{s.created_at || ""}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
