import type { Attempt, ChatMetadata } from "../api";

/**
 * The path a request took through the gateway, drawn as nodes on a line:
 *   cache ✓                       (LLM_CACHE)
 *   key_1 ✕ 429 ── key_2 ✓        (LIVE_INFERENCE after one failover)
 */
export function RouteTrace({ metadata }: { metadata: ChatMetadata }) {
  if (metadata.resolution_source === "LLM_CACHE") {
    return (
      <ol className="trace" aria-label="Request route">
        <li className="trace__node trace__node--cache">
          <span className="trace__dot" aria-hidden="true" />
          Cache hit, no key used
        </li>
      </ol>
    );
  }

  const failed: Attempt[] = metadata.attempts ?? [];
  return (
    <ol className="trace" aria-label="Request route">
      {failed.map((a, i) => (
        <li key={`${a.key_id}-${i}`} className="trace__node trace__node--failed" title={a.error}>
          <span className="trace__dot" aria-hidden="true" />
          {a.key_id}
          <span className="trace__status">
            {a.status ?? "error"}
            {a.simulated ? " simulated" : ""}
          </span>
        </li>
      ))}
      {failed.length === 0 && metadata.fallback_attempts > 0 && (
        <li className="trace__node trace__node--failed">
          <span className="trace__dot" aria-hidden="true" />
          {metadata.fallback_attempts} failed
        </li>
      )}
      <li className="trace__node trace__node--ok">
        <span className="trace__dot" aria-hidden="true" />
        {metadata.key_id ?? "key"} answered
      </li>
    </ol>
  );
}

export function FailedTrace({ attempts }: { attempts: Attempt[] }) {
  if (!attempts.length) return null;
  return (
    <ol className="trace" aria-label="Keys tried">
      {attempts.map((a, i) => (
        <li key={`${a.key_id}-${i}`} className="trace__node trace__node--failed" title={a.error}>
          <span className="trace__dot" aria-hidden="true" />
          {a.key_id}
          <span className="trace__status">
            {a.status ?? "error"}
            {a.simulated ? " simulated" : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
