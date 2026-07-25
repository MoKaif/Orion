/* Orion Archive — client behaviour.
   HTMX handles fragment views + review actions; this file owns the two things HTMX
   can't: SSE chat streaming and the command palette. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* ---- toasts ---------------------------------------------------------- */
  function toast(msg, kind = "") {
    const t = document.createElement("div");
    t.className = "toast " + kind;
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 4200);
  }

  /* ---- theme ----------------------------------------------------------- */
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("orion-theme");
  if (savedTheme) root.setAttribute("data-theme", savedTheme);
  document.addEventListener("click", (e) => {
    if (e.target.closest("#theme-toggle")) {
      const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      localStorage.setItem("orion-theme", next);
    }
  });

  /* ---- nav active state ------------------------------------------------ */
  function setActive(el) {
    $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b === el));
  }
  document.addEventListener("click", (e) => {
    const item = e.target.closest(".nav-item");
    if (item) setActive(item);
  });
  // keep highlight in sync when a view is loaded by something other than a nav click
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target && e.detail.target.id === "view") {
      const path = (e.detail.requestConfig && e.detail.requestConfig.path) || "";
      const match = $$(".nav-item").find((b) => path.startsWith(b.getAttribute("hx-get")));
      if (match) setActive(match);
      if (path.includes("/ui/chat")) initChat();
    }
    if (e.detail.target && e.detail.target.id === "telemetry") syncBadge();
  });

  function syncBadge() {
    const src = $("#tele-pending-src");
    const badge = $("#rail-review-badge");
    if (!src || !badge) return;
    const n = parseInt(src.dataset.pending || "0", 10);
    badge.hidden = !(n > 0);
    badge.textContent = n;
  }

  /* ---- chat streaming -------------------------------------------------- */
  let chatBusy = false;
  function initChat() {
    const chat = $("#chat");
    if (!chat) return;
    const input = $("#composer-input");
    const form = $("#composer");
    const log = $("#chat-log");
    if (!form || form.dataset.bound) { input && input.focus(); return; }
    form.dataset.bound = "1";

    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 180) + "px";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
    input.focus();

    async function sendMessage() {
      const text = input.value.trim();
      if (!text || chatBusy) return;
      const hello = $(".chat-hello"); if (hello) hello.remove();
      chatBusy = true;
      $("#composer-send").disabled = true;
      input.value = ""; input.style.height = "auto";

      appendMessage("user", text);
      const meta = document.createElement("div"); meta.className = "turn-meta"; log.appendChild(meta);
      const bubble = appendMessage("assistant", "");
      bubble.classList.add("streaming");
      scroll();

      const sid = chat.dataset.session ? Number(chat.dataset.session) : null;
      try {
        const res = await fetch("/chat/stream", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, session_id: sid }),
        });
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const frames = buf.split("\n\n"); buf = frames.pop();
          for (const f of frames) {
            const line = f.replace(/^data: /, "").trim();
            if (line) handleEvent(JSON.parse(line), bubble, meta);
          }
        }
      } catch (err) {
        bubble.textContent = "Connection lost mid-thought. " + err.message;
        toast("Chat stream failed", "err");
      } finally {
        bubble.classList.remove("streaming");
        chatBusy = false; $("#composer-send").disabled = false; input.focus();
      }
    }

    function handleEvent(ev, bubble, meta) {
      switch (ev.type) {
        case "start":
          if (!chat.dataset.session) chat.dataset.session = ev.session_id; break;
        case "context":
          meta.appendChild(chip(`mode <b>${ev.mode}</b>`, "chip-mode"));
          meta.appendChild(chip(`via <b>${ev.specialist}</b>`));
          meta.appendChild(chip(`${ev.known} known`)); break;
        case "token":
          bubble.textContent += ev.text; scroll(); break;
        case "tool":
          meta.appendChild(chip(`tool <b>${ev.tool}</b> ${ev.ok ? "ok" : "failed"}`, "chip-tool")); break;
        case "fallback":
          meta.appendChild(chip(`fallback → <b>${ev.answered_by}</b>`, "chip-fallback"));
          toast(`Primary model unavailable — answered by ${ev.answered_by}`, "warn"); break;
        case "confirm":
          renderConfirm(ev, bubble); break;
        case "review_queued":
          meta.appendChild(chip("queued for review", "chip-review"));
          toast("Orion inferred something — queued for your review", "warn"); break;
      }
    }

    function renderConfirm(ev, bubble) {
      const box = document.createElement("div"); box.className = "confirm";
      box.innerHTML = `<p>Orion wants to run <code>${ev.tool}</code> with
        <code>${escapeHtml(JSON.stringify(ev.args))}</code>. This action needs your approval.</p>
        <div class="confirm-actions">
          <button class="btn btn-accept">Approve &amp; run</button>
          <button class="btn btn-reject">Cancel</button></div>`;
      bubble.after(box);
      const [ok, no] = box.querySelectorAll("button");
      const decide = async (approve) => {
        box.querySelectorAll("button").forEach((b) => (b.disabled = true));
        const r = await fetch(`/confirm/${ev.id}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approve }),
        }).then((x) => x.json());
        box.innerHTML = `<p>${approve ? "Ran" : "Cancelled"} <code>${ev.tool}</code>.</p>`;
        if (approve && r.output) toast(`${ev.tool}: ${String(r.output).slice(0, 80)}`, "ok");
      };
      ok.onclick = () => decide(true);
      no.onclick = () => decide(false);
    }

    function appendMessage(role, text) {
      const wrap = document.createElement("div"); wrap.className = "msg msg-" + role;
      const b = document.createElement("div"); b.className = "bubble"; b.textContent = text;
      wrap.appendChild(b); log.appendChild(wrap); return b;
    }
    function chip(html, cls = "") { const c = document.createElement("span"); c.className = "chip " + cls; c.innerHTML = html; return c; }
    function scroll() { log.scrollTop = log.scrollHeight; }
  }

  function escapeHtml(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  /* ---- command palette ------------------------------------------------- */
  const commands = [
    { label: "Go to Desk", ico: "▤", key: "G D", run: () => nav("dashboard") },
    { label: "New chat", ico: "❯", key: "G C", run: () => nav("chat") },
    { label: "Open Inbox", ico: "◈", key: "G R", run: () => nav("reviews") },
    { label: "Open Agents", ico: "◇", key: "G A", run: () => nav("agents") },
    { label: "Open Threads", ico: "≡", key: "G S", run: () => nav("sessions") },
    { label: "Ingest Obsidian vault", ico: "⬇", run: () => post("/ingest/vault", "Vault ingest started") },
    { label: "Run Curator scan now", ico: "✎", run: () => post("/plugins/curator/scan?limit=5", "Curator scan finished — check the Desk widget") },
    { label: "Run consolidation now", ico: "◐", run: () => post("/jobs/consolidate/run", "Consolidation ran") },
    { label: "Run weekly briefing now", ico: "◑", run: () => post("/jobs/weekly_briefing/run", "Briefing generated") },
    { label: "Toggle theme", ico: "◐", run: () => $("#theme-toggle").click() },
  ];
  function nav(view) {
    const btn = $$(".nav-item").find((b) => b.dataset.view === view);
    if (btn) btn.click();
  }
  async function post(url, okMsg) {
    try {
      const r = await fetch(url, { method: "POST" }).then((x) => x.json());
      toast(r.error ? r.error : okMsg, r.error ? "err" : "ok");
      if (!r.error) htmx.trigger("#telemetry", "load");
    } catch { toast("Request failed", "err"); }
  }

  const palette = $("#palette"), pInput = $("#palette-input"), pList = $("#palette-list");
  let pSel = 0, pFiltered = commands;
  function openPalette() {
    palette.hidden = false; pInput.value = ""; drawPalette(commands); pInput.focus();
  }
  function closePalette() { palette.hidden = true; }
  function drawPalette(items) {
    pFiltered = items; pSel = 0;
    pList.innerHTML = items.map((c, i) =>
      `<li class="palette-item ${i === 0 ? "sel" : ""}" data-i="${i}">
        <span class="pico">${c.ico || "›"}</span><span>${c.label}</span>
        ${c.key ? `<span class="pkey">${c.key}</span>` : ""}</li>`).join("");
  }
  function runSel() {
    const c = pFiltered[pSel]; if (!c) return;
    closePalette(); c.run();
  }
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette.hidden ? openPalette() : closePalette(); return; }
    if (palette.hidden) return;
    if (e.key === "Escape") closePalette();
    else if (e.key === "ArrowDown") { e.preventDefault(); pSel = Math.min(pSel + 1, pFiltered.length - 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); pSel = Math.max(pSel - 1, 0); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); runSel(); }
  });
  function paint() { $$(".palette-item", pList).forEach((el, i) => el.classList.toggle("sel", i === pSel)); const s = $(".palette-item.sel", pList); s && s.scrollIntoView({ block: "nearest" }); }
  pInput && pInput.addEventListener("input", () => {
    const q = pInput.value.toLowerCase();
    drawPalette(commands.filter((c) => c.label.toLowerCase().includes(q)));
  });
  pList && pList.addEventListener("click", (e) => { const li = e.target.closest(".palette-item"); if (li) { pSel = +li.dataset.i; runSel(); } });
  document.addEventListener("click", (e) => {
    if (e.target.closest("#palette-open")) openPalette();
    if (e.target === palette) closePalette();
  });

  // handle keyboard "enter" on session rows (accessibility)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("session")) e.target.click();
  });

  window.Orion = { initChat, toast };
})();
