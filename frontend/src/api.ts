// Thin, typed client for the three backend endpoints.

export type ResolutionSource = "LIVE_INFERENCE" | "LLM_CACHE";

export interface Attempt {
  key_id: string;
  model: string;
  status: number | null;
  error: string;
  simulated: boolean;
}

export interface ChatMetadata {
  resolution_source: ResolutionSource;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
  attempts?: Attempt[];
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  metadata: ChatMetadata;
}

export interface KeyHealth {
  key_id: string;
  model: string;
  status: "healthy" | "cooling_down";
  cooldown_remaining_s: number;
  successes: number;
  failures: number;
}

export interface Health {
  status: string;
  keys_configured: number;
  cache_enabled: boolean;
  failure_simulation_enabled?: boolean;
  models?: string[];
  keys?: KeyHealth[];
}

declare global {
  interface Window {
    __APP_CONFIG__?: { API_BASE_URL?: string };
  }
}

export function apiBaseUrl(): string {
  const runtime = window.__APP_CONFIG__?.API_BASE_URL;
  const buildTime = import.meta.env.VITE_API_BASE_URL as string | undefined;
  return (runtime || buildTime || "http://localhost:8000").replace(/\/+$/, "");
}

/** An error the UI can show as-is: what happened, and the raw status for context. */
export class ApiError extends Error {
  status: number;
  attempts: Attempt[];
  constructor(status: number, message: string, attempts: Attempt[] = []) {
    super(message);
    this.status = status;
    this.attempts = attempts;
  }
}

function describeDetail(status: number, detail: unknown): string {
  if (status === 503) {
    return "Every API key failed for this message (all providers unavailable). It wasn't saved, so you can send it again.";
  }
  if (status === 422 && Array.isArray(detail)) {
    const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
    const field = first?.loc?.filter((p) => p !== "body").join(".") || "request";
    return `The ${field} was rejected: ${first?.msg ?? "invalid value"}. Messages must be 1–4000 characters.`;
  }
  if (typeof detail === "string") return detail;
  return `The server answered with HTTP ${status}.`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, init);
  } catch {
    throw new ApiError(0, `Can't reach the API at ${apiBaseUrl()}. Check that the backend is running.`);
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body: fall through with null */
  }
  if (!res.ok) {
    const b = (body ?? {}) as { detail?: unknown; attempts?: Attempt[] };
    throw new ApiError(res.status, describeDetail(res.status, b.detail), b.attempts ?? []);
  }
  return body as T;
}

export function sendChat(
  args: { sessionId: string; message: string; systemPrompt?: string; simulateFailure?: string },
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (args.simulateFailure) headers["X-Simulate-Failure"] = args.simulateFailure;
  const payload: Record<string, string> = { session_id: args.sessionId, message: args.message };
  if (args.systemPrompt && args.systemPrompt.trim()) payload.system_prompt = args.systemPrompt.trim();
  return request<ChatResponse>("/chat", { method: "POST", headers, body: JSON.stringify(payload), signal });
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function newSessionId(): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(36).slice(2, 14);
  return `s-${rand}`;
}
