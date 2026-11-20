import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const health = {
  status: "ok",
  keys_configured: 2,
  cache_enabled: true,
  failure_simulation_enabled: true,
  keys: [
    { key_id: "key_1", model: "gemini/gemini-2.5-flash", status: "healthy", cooldown_remaining_s: 0, successes: 0, failures: 0 },
    { key_id: "key_2", model: "gemini/gemini-2.5-flash", status: "healthy", cooldown_remaining_s: 0, successes: 0, failures: 0 },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

let chatResponses: Array<() => Promise<Response>>;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  chatResponses = [];
  fetchMock = vi.fn((url: string) => {
    if (url.endsWith("/health")) return jsonResponse(health);
    const next = chatResponses.shift();
    if (!next) throw new Error("unexpected chat call");
    return next();
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

const liveReply = {
  session_id: "x",
  reply: "The capital of France is Paris.",
  metadata: {
    resolution_source: "LIVE_INFERENCE",
    model: "gemini/gemini-2.5-flash",
    latency_ms: 812,
    input_tokens: 42,
    output_tokens: 9,
    key_id: "key_2",
    fallback_attempts: 1,
    attempts: [{ key_id: "key_1", model: "gemini/gemini-2.5-flash", status: 429, error: "x", simulated: true }],
  },
};

describe("chat UI contract", () => {
  it("sends a message, clears the input and renders reply metadata", async () => {
    chatResponses.push(() => jsonResponse(liveReply));
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByTestId("chat-input");
    await user.type(input, "What is the capital of France?");
    await user.click(screen.getByTestId("send-button"));
    expect(input).toHaveValue("");

    const reply = await screen.findByTestId("assistant-message");
    expect(reply).toHaveTextContent("The capital of France is Paris.");
    const m = within(reply);
    expect(m.getByTestId("meta-source")).toHaveTextContent(/^LIVE_INFERENCE$/);
    expect(m.getByTestId("meta-model")).toHaveTextContent("gemini/gemini-2.5-flash");
    expect(m.getByTestId("meta-latency")).toHaveTextContent("812 ms");
    expect(m.getByTestId("meta-input-tokens")).toHaveTextContent(/^42$/);
    expect(m.getByTestId("meta-output-tokens")).toHaveTextContent(/^9$/);
    expect(m.getByText("key_2 answered")).toBeInTheDocument();

    const [, init] = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/chat"))!;
    const body = JSON.parse(init.body);
    expect(body.message).toBe("What is the capital of France?");
    expect(body.session_id).toMatch(/^s-/);
  });

  it("sends with Enter and keeps the same session id across turns", async () => {
    chatResponses.push(() => jsonResponse(liveReply), () => jsonResponse({ ...liveReply, metadata: { ...liveReply.metadata, resolution_source: "LLM_CACHE", key_id: null, fallback_attempts: 0, attempts: [] } }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "one{Enter}");
    await screen.findByTestId("assistant-message");
    await user.type(screen.getByTestId("chat-input"), "two{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("assistant-message")).toHaveLength(2));
    expect(screen.getAllByTestId("meta-source")[1]).toHaveTextContent(/^LLM_CACHE$/);

    const ids = fetchMock.mock.calls
      .filter(([u]) => String(u).endsWith("/chat"))
      .map(([, init]) => JSON.parse(init.body).session_id);
    expect(new Set(ids).size).toBe(1);
  });

  it("shows a 503 as a readable message in the chat", async () => {
    chatResponses.push(() => jsonResponse({ detail: "all providers unavailable", attempts: [] }, 503));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("HTTP 503");
    expect(alert).toHaveTextContent(/all providers unavailable/i);
    expect(screen.getByText("Send again")).toBeInTheDocument();
  });

  it("shows a 422 as a readable message in the chat", async () => {
    chatResponses.push(() =>
      jsonResponse({ detail: [{ type: "string_too_long", loc: ["body", "message"], msg: "String should have at most 4000 characters" }] }, 422),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("HTTP 422");
    expect(alert).toHaveTextContent("The message was rejected");
  });

  it("shows a network failure instead of a blank screen", async () => {
    chatResponses.push(() => Promise.reject(new TypeError("Failed to fetch")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the API");
  });

  it("sends the system prompt and the failure simulation header", async () => {
    chatResponses.push(() => jsonResponse(liveReply));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("system-prompt-input"), "Be brief.");
    await screen.findByText("Key pool");
    await waitFor(() => expect(screen.getAllByRole("group", { name: "key_1" })).toHaveLength(1));
    await user.click(within(screen.getByRole("group", { name: "key_1" })).getByLabelText("429"));
    await user.type(screen.getByTestId("chat-input"), "hi{Enter}");
    await screen.findByTestId("assistant-message");

    const [, init] = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/chat"))!;
    expect(init.headers["X-Simulate-Failure"]).toBe("key_1=429");
    expect(JSON.parse(init.body).system_prompt).toBe("Be brief.");
  });
});
