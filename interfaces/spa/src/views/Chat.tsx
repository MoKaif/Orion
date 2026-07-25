import { FormEvent, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { SendHorizonal, Loader2 } from "lucide-react";
import "./chat.css";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

export default function Chat() {
  const { sessionId: sessionParam } = useParams();
  const [sessionId, setSessionId] = useState<number | null>(
    sessionParam ? Number(sessionParam) : null,
  );
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionParam) return;
    fetch(`/chat/history?session_id=${sessionParam}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((h: Msg[]) => setMessages(h.filter((m) => m.role === "user" || m.role === "assistant")))
      .catch(() => {});
  }, [sessionParam]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);
    setStatus("thinking…");

    try {
      const res = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame.replace(/^data: /, "").trim();
          if (!line) continue;
          let ev: any;
          try {
            ev = JSON.parse(line);
          } catch {
            continue;
          }
          if (ev.type === "start" && ev.session_id) setSessionId(ev.session_id);
          else if (ev.type === "token")
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                role: "assistant",
                content: copy[copy.length - 1].content + ev.text,
              };
              return copy;
            });
          else if (ev.type === "mode") setStatus(`${ev.mode || ""} ${ev.specialist || ""}`.trim());
          else if (ev.type === "tool") setStatus(`using ${ev.tool || "a tool"}…`);
          else if (ev.type === "done") setStatus("");
        }
      }
    } catch {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", content: "⚠️ connection interrupted." };
        return copy;
      });
    } finally {
      setStreaming(false);
      setStatus("");
    }
  }

  return (
    <div className="chat-wrap">
      <header className="view-head">
        <div>
          <p className="eyebrow">Conversation</p>
          <h1 className="view-title">Chat</h1>
        </div>
        <p className="view-note">
          Orion consults your world model before answering, and mines each turn for new knowledge.
        </p>
      </header>

      <div className="chat-log">
        {messages.length === 0 && (
          <div className="empty-state">
            <span className="empty-glyph">◆</span>
            <p>Ask Orion anything. It knows what you've told it.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            <span className="bubble-who">{m.role === "user" ? "You" : "Orion"}</span>
            <div className="bubble-body">
              {m.content || (streaming && i === messages.length - 1 ? <em className="typing">…</em> : "")}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form className="chat-input" onSubmit={send}>
        {status && <span className="chat-status">{status}</span>}
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) send(e);
          }}
          placeholder="Message Orion…"
          rows={1}
        />
        <button className="btn btn-primary" disabled={streaming || !input.trim()}>
          {streaming ? <Loader2 size={15} className="spin" /> : <SendHorizonal size={15} />}
        </button>
      </form>
    </div>
  );
}
