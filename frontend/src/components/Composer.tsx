import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";

const LIMIT = 4000;

export function Composer({
  value,
  onChange,
  onSend,
  busy,
  simulationLabel,
  onClearSimulation,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  busy: boolean;
  simulationLabel: string | null;
  onClearSimulation: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const length = value.trim().length;

  // grow with content, up to the CSS max-height
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!busy && length > 0) onSend();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      {simulationLabel && (
        <p className="composer__sim">
          Failure simulation is on: {simulationLabel}.{" "}
          <button type="button" className="link-button" onClick={onClearSimulation}>
            Turn off
          </button>
        </p>
      )}
      <div className="composer__row">
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={ref}
          data-testid="chat-input"
          className="composer__input"
          placeholder="Ask anything. Enter sends, Shift+Enter adds a line."
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button type="submit" className="composer__send" data-testid="send-button" disabled={busy || length === 0}>
          Send
        </button>
      </div>
      {length > LIMIT - 500 && (
        <p className={`composer__count ${length > LIMIT ? "is-over" : ""}`}>
          {length} / {LIMIT} characters{length > LIMIT ? ": the server will reject this message" : ""}
        </p>
      )}
    </form>
  );
}
