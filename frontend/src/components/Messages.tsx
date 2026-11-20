import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Attempt, ChatMetadata } from "../api";
import { FailedTrace, RouteTrace } from "./RouteTrace";

export type ChatItem =
  | { id: string; kind: "user"; content: string }
  | { id: string; kind: "assistant"; content: string; metadata: ChatMetadata }
  | { id: string; kind: "error"; content: string; status: number; attempts: Attempt[]; retryText: string }
  | { id: string; kind: "pending"; startedAt: number };


function Markdown({ text }: { text: string }) {
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function AssistantMessage({ content, metadata }: { content: string; metadata: ChatMetadata }) {
  const cached = metadata.resolution_source === "LLM_CACHE";
  return (
    <article className={`msg msg--assistant ${cached ? "is-cache" : "is-live"}`} data-testid="assistant-message">
      <Markdown text={content} />
      <dl className="meta">
        <div className="meta__item">
          <dt className="sr-only">Source</dt>
          <dd className={`source ${cached ? "source--cache" : "source--live"}`} data-testid="meta-source">
            {metadata.resolution_source}
          </dd>
        </div>
        <div className="meta__item">
          <dt>Model</dt>
          <dd data-testid="meta-model">{metadata.model}</dd>
        </div>
        <div className="meta__item">
          <dt>Latency</dt>
          <dd className="num" data-testid="meta-latency">
            {metadata.latency_ms} ms
          </dd>
        </div>
        <div className="meta__item">
          <dt>Tokens</dt>
          <dd>
            <span className="num" data-testid="meta-input-tokens">{metadata.input_tokens}</span>
            <span className="meta__unit"> in, </span>
            <span className="num" data-testid="meta-output-tokens">{metadata.output_tokens}</span>
            <span className="meta__unit"> out</span>
          </dd>
        </div>
      </dl>
      <RouteTrace metadata={metadata} />
    </article>
  );
}

function Pending({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(t);
  }, []);
  return (
    <div className="msg msg--pending" role="status" aria-live="polite">
      <span className="pulse" aria-hidden="true" />
      Waiting for the gateway <span className="num">{Math.round(now - startedAt)} ms</span>
    </div>
  );
}

export function MessageList({
  items,
  onRetry,
  onSuggestion,
}: {
  items: ChatItem[];
  onRetry: (text: string) => void;
  onSuggestion: (text: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items]);

  if (items.length === 0) {
    return (
      <div className="empty">
        <h2 className="empty__title">Every answer shows the route it took.</h2>
        <p className="empty__body">
          Each reply lists where it came from, which model and key answered, how long it took and the tokens used for
          that request. Ask the same first question in a new chat to see it come back from the cache.
        </p>
        <div className="suggestions">
          {["What is the capital of France?", "Explain HTTP 429 in one paragraph.", "My name is Nitya. Remember it."].map(
            (s) => (
              <button key={s} type="button" className="suggestion" onClick={() => onSuggestion(s)}>
                {s}
              </button>
            ),
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="messages" aria-live="polite">
      {items.map((item) => {
        switch (item.kind) {
          case "user":
            return (
              <div key={item.id} className="msg msg--user">
                {item.content}
              </div>
            );
          case "assistant":
            return <AssistantMessage key={item.id} content={item.content} metadata={item.metadata} />;
          case "pending":
            return <Pending key={item.id} startedAt={item.startedAt} />;
          case "error":
            return (
              <div key={item.id} className="msg msg--error" role="alert">
                <p className="msg__error-title">{item.status ? `HTTP ${item.status}` : "Network error"}</p>
                <p>{item.content}</p>
                <FailedTrace attempts={item.attempts} />
                {item.status !== 422 && (
                  <button type="button" className="link-button" onClick={() => onRetry(item.retryText)}>
                    Send again
                  </button>
                )}
              </div>
            );
        }
      })}
      <div ref={endRef} />
    </div>
  );
}
