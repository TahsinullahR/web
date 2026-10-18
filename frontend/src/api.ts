/** API client — talks only to our backend (proxied at /api). No keys here. */

export interface Conversation {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  model: string;
  created_at: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  context_length: number | null;
  is_free: boolean;
  architecture?: { modality?: string; input_modalities?: string[] };
}

export interface Health {
  status: string;
  provider_configured: boolean;
  default_model: string;
  db: string;
  version: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = body?.error?.message ?? body?.detail ?? msg;
      if (typeof msg !== "string") msg = JSON.stringify(msg);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then(json<Health>),

  listConversations: () =>
    fetch("/api/conversations").then(json<Conversation[]>),

  createConversation: (title = "New chat", model = "") =>
    fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, model }),
    }).then(json<Conversation>),

  renameConversation: (id: string, title: string) =>
    fetch(`/api/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(json<Conversation>),

  deleteConversation: (id: string) =>
    fetch(`/api/conversations/${id}`, { method: "DELETE" }).then((r) => {
      if (!r.ok) throw new Error(`Delete failed (${r.status})`);
    }),

  getMessages: (id: string) =>
    fetch(`/api/conversations/${id}/messages`).then(json<Message[]>),

  listModels: () =>
    fetch("/api/models").then(
      json<{ models: ModelInfo[]; total: number; free_count: number; default_model: string }>
    ),

  regenerateRemove: (conversationId: string) =>
    fetch(`/api/chat/${conversationId}/regenerate`, { method: "POST" }).then(
      json<{ deleted_message_id: number }>
    ),
};

/**
 * Stream a chat completion via fetch + SSE parsing (POST needs fetch, not EventSource).
 * Returns an abort controller so the user can stop generation.
 */
export function streamChat(
  conversationId: string,
  content: string,
  model: string,
  handlers: {
    onDelta: (t: string) => void;
    onDone: () => void;
    onError: (code: string, message: string) => void;
  }
): AbortController {
  const ctrl = new AbortController();
  (async () => {
    try {
      const res = await fetch(`/api/chat/${conversationId}/stream`, {
        method: "POST",
        signal: ctrl.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, model }),
      });
      if (!res.ok || !res.body) {
        let msg = `HTTP ${res.status}`;
        try {
          const b = await res.json();
          msg = b?.error?.message ?? b?.detail ?? msg;
        } catch { /* ignore */ }
        handlers.onError("request_failed", String(msg));
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.startsWith("data: ") ? part.slice(6) : null;
          if (!line) continue;
          let evt: any;
          try { evt = JSON.parse(line); } catch { continue; }
          if (evt.delta) handlers.onDelta(evt.delta);
          else if (evt.done) handlers.onDone();
          else if (evt.error)
            handlers.onError(evt.error.code ?? "error", evt.error.message ?? "Unknown error");
        }
      }
      handlers.onDone();
    } catch (e: any) {
      if (e?.name === "AbortError") handlers.onDone(); // user pressed Stop
      else handlers.onError("network", String(e?.message ?? e));
    }
  })();
  return ctrl;
}
