import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { SendHorizonal, Plus, Sparkles, Square } from "lucide-react";
import "./chat.css";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

export default function Chat() {
  const navigate = useNavigate();
  const { sessionId: sessionParam } = useParams();
  const [sessionId, setSessionId] = useState<number | null>(
    sessionParam ? Number(sessionParam) : null,
  );
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!sessionParam) return;
    fetch(`/chat/history?session_id=${sessionParam}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((h: Msg[]) => setMessages(h.filter((m) => m.role === "user" || m.role === "assistant")))
      .catch(() => {});
  }, [sessionParam]);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
  }, [messages, streaming]);

  function newChat() {
    requestRef.current?.abort();
    requestRef.current = null;
    setSessionId(null);
    setMessages([]);
    setInput("");
    setStreaming(false);
    setStatus("");
    navigate("/chat", { replace: true });
  }

  function stop() {
    setStatus("stopping…");
    requestRef.current?.abort();
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setStreaming(true);
    setStatus("consulting memory…");
    const controller = new AbortController();
    requestRef.current = controller;

    try {
      const res = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionId }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`chat request failed (${res.status})`);
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
          if (ev.type === "start" && ev.session_id) {
            setSessionId(ev.session_id);
            window.history.replaceState({}, "", `/chat/${ev.session_id}`);
          }
          else if (ev.type === "token")
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                role: "assistant",
                content: copy[copy.length - 1].content + ev.text,
              };
              return copy;
            });
          else if (ev.type === "context") setStatus(`${ev.mode || ""} · ${ev.specialist || "generalist"}`);
          else if (ev.type === "tool") setStatus(`using ${ev.tool || "a tool"}…`);
          else if (ev.type === "fallback") setStatus(`switched to ${ev.answered_by || "fallback"}…`);
          else if (ev.type === "error") throw new Error(ev.message || "chat failed");
          else if (ev.type === "done") setStatus("");
        }
      }
    } catch {
      if (!controller.signal.aborted) {
        setInput(text);
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (!last.content) last.content = "Connection interrupted — your message is restored below.";
          return copy;
        });
      }
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
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
        {sessionId && <button type="button" onClick={newChat} className="btn btn-sm chat-new"><Plus size={13}/> New chat</button>}
      </header>

      <div className="chat-log">
        {messages.length === 0 && (
          <div className="chat-empty">
            <span className="chat-orbit"><img src="/assets/orion-mark.svg" alt="" /></span>
            <p className="eyebrow">Your second brain</p>
            <h2>What are we thinking through?</h2>
            <p>Orion begins with your world model, then chooses the right specialist and tools.</p>
            <div className="chat-suggestions">
              {["What needs my attention today?", "What changed across my projects?", "Summarize what you know about my health", "Connect recent ideas I may have missed"].map((prompt) => (
                <button key={prompt} onClick={() => setInput(prompt)}><Sparkles size={12}/>{prompt}</button>
              ))}
            </div>
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
          onInput={(e) => {
            e.currentTarget.style.height = "auto";
            e.currentTarget.style.height = `${Math.min(e.currentTarget.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) send(e);
          }}
          placeholder="Message Orion…"
          rows={1}
        />
        <button
          type={streaming ? "button" : "submit"}
          className={`btn btn-primary${streaming ? " chat-stop" : ""}`}
          disabled={!streaming && !input.trim()}
          onClick={streaming ? stop : undefined}
          aria-label={streaming ? "Stop response" : "Send message"}
        >
          {streaming ? <Square size={13} fill="currentColor" /> : <SendHorizonal size={15} />}
        </button>
      </form>
    </div>
  );
}
