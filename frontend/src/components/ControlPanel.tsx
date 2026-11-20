import type { Health } from "../api";

export const STATUSES = ["429", "500", "502", "503"] as const;
export type SimStatus = (typeof STATUSES)[number] | "";
export type Simulation = Record<string, SimStatus>; // key_id or "*" -> status

export function simulationHeader(sim: Simulation): string {
  if (sim["*"]) return `*=${sim["*"]}`;
  return Object.entries(sim)
    .filter(([, s]) => s)
    .map(([k, s]) => `${k}=${s}`)
    .join(",");
}

function Segmented({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: SimStatus;
  onChange: (v: SimStatus) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="segmented" disabled={disabled}>
      <legend className="segmented__label">{label}</legend>
      <div className="segmented__options">
        {(["", ...STATUSES] as SimStatus[]).map((s) => (
          <label key={s || "ok"} className={`segmented__opt ${value === s ? "is-on" : ""} ${s ? "is-fail" : ""}`}>
            <input
              type="radio"
              name={`sim-${label}`}
              value={s}
              checked={value === s}
              onChange={() => onChange(s)}
            />
            {s || "Works"}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function ControlPanel({
  sessionId,
  onNewChat,
  systemPrompt,
  onSystemPrompt,
  sim,
  onSim,
  health,
  healthError,
}: {
  sessionId: string;
  onNewChat: () => void;
  systemPrompt: string;
  onSystemPrompt: (v: string) => void;
  sim: Simulation;
  onSim: (s: Simulation) => void;
  health: Health | null;
  healthError: string | null;
}) {
  const keyIds = health?.keys?.map((k) => k.key_id) ??
    Array.from({ length: Math.max(health?.keys_configured ?? 2, 1) }, (_, i) => `key_${i + 1}`);
  const simAllowed = health?.failure_simulation_enabled !== false;

  return (
    <div className="panel">
      <section className="panel__section">
        <h2 className="panel__title">This chat</h2>
        <p className="panel__hint">
          Session <code>{sessionId}</code>. History is stored on the server under this id.
        </p>
        <button type="button" className="button-secondary" onClick={onNewChat}>
          Start a new chat
        </button>
      </section>

      <section className="panel__section">
        <h2 className="panel__title">
          <label htmlFor="system-prompt">System prompt</label>
        </h2>
        <textarea
          id="system-prompt"
          data-testid="system-prompt-input"
          className="panel__textarea"
          rows={4}
          maxLength={4000}
          placeholder="Optional. For example: Answer like a patient tutor, in three sentences or fewer."
          value={systemPrompt}
          onChange={(e) => onSystemPrompt(e.target.value)}
        />
        <p className="panel__hint">Sent with your next message and kept for the rest of this chat.</p>
      </section>

      <section className="panel__section">
        <h2 className="panel__title">Simulate key failures</h2>
        <p className="panel__hint">
          {simAllowed
            ? "Makes the chosen keys fail with that HTTP status, for each message you send while it's set. The gateway should fall over to the next key."
            : "The backend has failure simulation switched off (ENABLE_FAILURE_SIMULATION)."}
        </p>
        {keyIds.map((id) => (
          <Segmented
            key={id}
            label={id}
            value={sim["*"] ? sim["*"] : (sim[id] ?? "")}
            disabled={!simAllowed || Boolean(sim["*"])}
            onChange={(v) => onSim({ ...sim, [id]: v })}
          />
        ))}
        <Segmented
          label="All keys"
          value={sim["*"] ?? ""}
          disabled={!simAllowed}
          onChange={(v) => onSim({ ...sim, "*": v })}
        />
        {simulationHeader(sim) && (
          <p className="panel__hint">
            Header sent: <code>X-Simulate-Failure: {simulationHeader(sim)}</code>
          </p>
        )}
      </section>

      <section className="panel__section">
        <h2 className="panel__title">Key pool</h2>
        {healthError && <p className="panel__hint is-bad">{healthError}</p>}
        {health?.keys && (
          <ul className="keys">
            {health.keys.map((k) => (
              <li key={k.key_id} className={`keys__row ${k.status === "healthy" ? "is-ok" : "is-cooling"}`}>
                <span className="keys__dot" aria-hidden="true" />
                <span className="keys__id">{k.key_id}</span>
                <span className="keys__model">{k.model}</span>
                <span className="keys__stat">
                  {k.status === "healthy" ? "Healthy" : `Cooling down, ${k.cooldown_remaining_s}s`}
                </span>
                <span className="keys__counts">
                  {k.successes} ok, {k.failures} failed
                </span>
              </li>
            ))}
          </ul>
        )}
        {health && (
          <p className="panel__hint">
            Response cache is {health.cache_enabled ? "on" : "off"}. Keys that hit a real rate limit rest briefly and are
            tried last.
          </p>
        )}
      </section>
    </div>
  );
}
