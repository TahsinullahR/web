import { useEffect, useMemo, useRef, useState } from "react";
import {
  api, streamChat, type Conversation, type Message, type ModelInfo, type Health,
} from "./api";
import { Markdown } from "./components/Markdown";

type StreamState = { text: string; error: string | null };

export default function App() {
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (localStorage.getItem("nova-theme") as any) || "dark"
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  const [model, setModel] = useState<string>("openrouter/free");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stream, setStream] = useState<StreamState>({ text: "", error: null });
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const lastUserRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("nova-theme", theme);
  }, [theme]);

  // Initial load
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.listConversations().then(setConversations).catch(() => {});
    api
      .listModels()
      .then((d) => {
        setModels(d.models);
        setModelError(null);
        if (!localStorage.getItem("nova-model")) setModel(d.default_model || model);
      })
      .catch((e) => setModelError(String(e?.message ?? e)));
    const saved = localStorage.getItem("nova-model");
    if (saved) setModel(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, stream.text]);

  const active = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId]
  );

  async function refreshConversations() {
    setConversations(await api.listConversations());
  }

  async function newChat() {
    const c = await api.createConversation("New chat", model);
    await refreshConversations();
    setActiveId(c.id);
    setMessages([]);
    setStream({ text: "", error: null });
  }

  async function openChat(id: string) {
    setActiveId(id);
    setStream({ text: "", error: null });
    const msgs = await api.getMessages(id);
    setMessages(msgs);
    const c = conversations.find((x) => x.id === id);
    if (c?.model) {
      setModel(c.model);
      localStorage.setItem("nova-model", c.model);
    }
  }

  async function renameChat(c: Conversation) {
    const t = window.prompt("Rename conversation", c.title);
    if (!t || !t.trim()) return;
    await api.renameConversation(c.id, t.trim());
    await refreshConversations();
  }

  async function deleteChat(c: Conversation) {
    if (!window.confirm(`Delete "${c.title}"? This cannot be undone.`)) return;
    await api.deleteConversation(c.id);
    if (activeId === c.id) {
      setActiveId(null);
      setMessages([]);
    }
    await refreshConversations();
  }

  function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;
    setInput("");
    lastUserRef.current = content;
    setBusy(true);
    setStream({ text: "", error: null });
    let acc = "";
    let finished = false;
    const ctrl = streamChat(
      activeId!,
      content,
      model,
      {
        onDelta: (t) => {
          acc += t;
          setStream({ text: acc, error: null });
        },
        onDone: async () => {
          if (finished) return;
          finished = true;
          setBusy(false);
          abortRef.current = null;
          try {
            const msgs = await api.getMessages(activeId!);
            setMessages(msgs);
          } catch { /* offline */ }
          setStream({ text: "", error: null });
          refreshConversations();
        },
        onError: (code, message) => {
          setBusy(false);
          setStream({ text: acc, error: `[${code}] ${message}` });
          api.getMessages(activeId!).then(setMessages).catch(() => {});
          refreshConversations();
        },
      }
    );
    abortRef.current = ctrl;
  }

  async function regenerate() {
    if (busy || !activeId || messages.length === 0) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    try {
      await api.regenerateRemove(activeId);
    } catch { /* nothing to remove — fine */ }
    const withoutLast = (() => {
      const arr = [...messages];
      for (let i = arr.length - 1; i >= 0; i--)
        if (arr[i].role === "assistant") { arr.splice(i, 1); break; }
      return arr;
    })();
    setMessages(withoutLast);
    send(lastUser.content);
  }

  function stop() {
    abortRef.current?.abort();
  }

  const providerBadge = health
    ? health.provider_configured
      ? { cls: "ok", text: "OpenRouter configured" }
      : { cls: "warn", text: "Add OPENROUTER_API_KEY to backend/.env" }
    : { cls: "err", text: "Backend offline" };

  return (
    <div className="app">
      {/* Sidebar */}
      <aside className={`sidebar glass ${sidebarOpen ? "" : "collapsed"}`}>
        <div className="brand">
          <span className="logo">◈</span>
          {sidebarOpen && <strong>NOVA AI</strong>}
        </div>
        <button className="btn primary block" onClick={newChat}>
          ＋ {sidebarOpen ? "New chat" : ""}
        </button>
        <nav className="convo-list" aria-label="Conversations">
          {sidebarOpen &&
            conversations.map((c) => (
              <div
                key={c.id}
                className={`convo ${c.id === activeId ? "active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => openChat(c.id)}
                onKeyDown={(e) => e.key === "Enter" && openChat(c.id)}
              >
                <span className="convo-title">{c.title}</span>
                <span className="convo-actions">
                  <button title="Rename" onClick={(e) => { e.stopPropagation(); renameChat(c); }}>✎</button>
                  <button title="Delete" onClick={(e) => { e.stopPropagation(); deleteChat(c); }}>🗑</button>
                </span>
              </div>
            ))}
          {sidebarOpen && conversations.length === 0 && (
            <p className="muted small pad">No conversations yet.</p>
          )}
        </nav>
        <div className="sidebar-footer">
          <button
            className="btn ghost block"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label="Toggle theme"
          >
            {theme === "dark" ? "☀ Light" : "🌙 Dark"}
          </button>
          <span className={`badge ${providerBadge.cls}`} title={providerBadge.text}>
            {sidebarOpen ? providerBadge.text : "•"}
          </span>
        </div>
      </aside>

      {/* Main */}
      <main className="main">
        <header className="topbar glass">
          <button className="btn ghost" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Toggle sidebar">
            ☰
          </button>
          <div className="topbar-title">{active?.title ?? "NOVA AI"}</div>
          <label className="model-picker">
            <select
              value={model.startsWith("openrouter/free") ? "__free_router__" : model}
              onChange={(e) => {
                const v = e.target.value === "__free_router__" ? "openrouter/free" : e.target.value;
                setModel(v);
                localStorage.setItem("nova-model", v);
              }}
              disabled={!!modelError && models.length === 0}
              aria-label="Select model"
            >
              <option value="__free_router__">
                openrouter/free (auto · prefers free){modelError ? " ⚠" : ""}
              </option>
              {models.filter((m) => m.is_free).map((m) => (
                <option key={m.id} value={m.id}>
                  🆓 {m.name}{m.context_length ? ` · ${Math.round(m.context_length / 1024)}k ctx` : ""}
                </option>
              ))}
              {models.filter((m) => !m.is_free).slice(0, 50).map((m) => (
                <option key={m.id} value={m.id}>💿 {m.name}</option>
              ))}
            </select>
          </label>
        </header>

        {modelError && (
          <div className="notice err">Model catalogue unavailable: {modelError}. Chat still works with the default router once an API key is set.</div>
        )}

        <div className="chat-scroll" ref={scrollRef}>
          {!activeId ? (
            <div className="empty">
              <h1>◈ NOVA AI</h1>
              <p>All-in-one AI workspace. Phase 1: streaming multi-model chat with history.</p>
              <p className="muted small">
                Bengali · English · Banglish supported — ask in any of them.
              </p>
              <button className="btn primary" onClick={newChat}>Start a chat</button>
            </div>
          ) : (
            <>
              {messages.length === 0 && !busy && (
                <p className="muted center pad">Ask anything — e.g. “Amake ekta responsive business website er structure deo.”</p>
              )}
              {messages.map((m) => (
                <div key={m.id} className={`msg ${m.role}`}>
                  <div className="meta">
                    {m.role === "assistant" ? "NOVA" : "You"}
                    {m.model && m.role === "assistant" && <span className="tag">{m.model}</span>}
                    <button
                      className="btn tiny ghost"
                      onClick={() => navigator.clipboard.writeText(m.content)}
                      title="Copy message"
                    >⧉ Copy</button>
                  </div>
                  <Markdown text={m.content} />
                </div>
              ))}
              {(busy || stream.text) && (
                <div className="msg assistant">
                  <div className="meta">NOVA <span className="tag">{model}</span></div>
                  {stream.text ? <Markdown text={stream.text} /> : <span className="typing">▍ thinking…</span>}
                </div>
              )}
              {stream.error && <div className="notice err">{stream.error}</div>}
            </>
          )}
        </div>

        {activeId && (
          <footer className="composer glass">
            <textarea
              value={input}
              rows={1}
              placeholder={busy ? "Generating…" : "Message NOVA AI… (Enter to send, Shift+Enter newline)"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
              }}
              disabled={busy}
            />
            <div className="composer-btns">
              {busy ? (
                <button className="btn danger" onClick={stop}>■ Stop</button>
              ) : (
                <button className="btn primary" onClick={() => send(input)} disabled={!input.trim()}>
                  ➤ Send
                </button>
              )}
              <button
                className="btn ghost"
                onClick={regenerate}
                disabled={busy || !messages.some((m) => m.role === "assistant")}
                title="Regenerate last answer"
              >⟳ Regenerate</button>
            </div>
          </footer>
        )}
      </main>
    </div>
  );
}
