import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getHealth, newSessionId, sendChat, type Health } from "./api";
import { Composer } from "./components/Composer";
import { ControlPanel, simulationHeader, type Simulation } from "./components/ControlPanel";
import { MessageList, type ChatItem } from "./components/Messages";

let seq = 0;
const uid = () => `m${++seq}`;

export default function App() {
  // A new session starts on every page load.
  const [sessionId, setSessionId] = useState(newSessionId);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [sim, setSim] = useState<Simulation>({});
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
      setHealthError(null);
    } catch (e) {
      setHealthError(e instanceof ApiError ? e.message : "Health check failed.");
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    const t = window.setInterval(refreshHealth, 15000);
    return () => window.clearInterval(t);
  }, [refreshHealth]);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      setDraft(""); // input is empty right after sending
      const pendingId = uid();
      setItems((prev) => [
        ...prev,
        { id: uid(), kind: "user", content: message },
        { id: pendingId, kind: "pending", startedAt: performance.now() },
      ]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const res = await sendChat(
          { sessionId, message, systemPrompt, simulateFailure: simulationHeader(sim) || undefined },
          controller.signal,
        );
        setItems((prev) =>
          prev.map((it) =>
            it.id === pendingId ? { id: pendingId, kind: "assistant", content: res.reply, metadata: res.metadata } : it,
          ),
        );
      } catch (e) {
        if (controller.signal.aborted) return;
        const err = e instanceof ApiError ? e : new ApiError(0, "Something went wrong while sending.");
        setItems((prev) =>
          prev.map((it) =>
            it.id === pendingId
              ? {
                  id: pendingId,
                  kind: "error",
                  content: err.message,
                  status: err.status,
                  attempts: err.attempts,
                  retryText: message,
                }
              : it,
          ),
        );
      } finally {
        if (abortRef.current === controller) {
          setBusy(false);
          abortRef.current = null;
        }
        refreshHealth();
      }
    },
    [busy, sessionId, systemPrompt, sim, refreshHealth],
  );

  const newChat = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setItems([]);
    setSessionId(newSessionId());
    setPanelOpen(false);
  };

  const simHeader = simulationHeader(sim);
  const online = health?.status === "ok";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg className="brand__mark" viewBox="0 0 32 32" aria-hidden="true">
            <circle cx="7" cy="16" r="4" />
            <path d="M11 16h10" />
            <circle cx="25" cy="16" r="4" />
          </svg>
          <span className="brand__name">Lyftr Relay</span>
        </div>
        <p className={`status ${online ? "is-ok" : health || healthError ? "is-bad" : ""}`} aria-live="polite">
          <span className="status__dot" aria-hidden="true" />
          <span className="status__text">
            {online
              ? `API online with ${health?.keys_configured} keys`
              : healthError
                ? "API unreachable"
                : health
                  ? "API degraded"
                  : "Checking API"}
          </span>
        </p>
        <button
          type="button"
          className="topbar__panel-toggle"
          aria-expanded={panelOpen}
          aria-controls="control-rail"
          onClick={() => setPanelOpen((o) => !o)}
        >
          {panelOpen ? "Close controls" : "Controls"}
        </button>
      </header>

      <main className="chat">
        <div className="chat__scroll">
          <MessageList items={items} onRetry={send} onSuggestion={(s) => setDraft(s)} />
        </div>
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={() => send(draft)}
          busy={busy}
          simulationLabel={simHeader || null}
          onClearSimulation={() => setSim({})}
        />
      </main>

      <aside id="control-rail" className={`rail ${panelOpen ? "is-open" : ""}`} aria-label="Chat controls">
        <ControlPanel
          sessionId={sessionId}
          onNewChat={newChat}
          systemPrompt={systemPrompt}
          onSystemPrompt={setSystemPrompt}
          sim={sim}
          onSim={setSim}
          health={health}
          healthError={healthError}
        />
      </aside>
    </div>
  );
}
