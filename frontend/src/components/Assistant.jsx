import { useEffect, useRef, useSyncExternalStore } from "react";
import { api } from "../api.js";
import mascot from "../assets/mascot-search.webp";

// Each chat lives here, not in the panel: one for each job and one for the Help page. The panel
// goes away when the person opens another section or page; the chat must still be there when
// they come back, with an answer that arrived while they were away.
const chats = new Map();
const listeners = new Set();
const NEW_CHAT = { turns: [], busy: false, draft: "" };

const chatOf = (key) => chats.get(key) || NEW_CHAT;

function change(key, update) {
  chats.set(key, { ...chatOf(key), ...update(chatOf(key)) });
  listeners.forEach((listener) => listener());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const ABOUT_A_JOB = [
  "Where does this job stand, and what is left?",
  "Why hasn't the next step started?",
  "Did the browser agent fail on this job?",
];
const ABOUT_ANYTHING = [
  "Which jobs are late, and who are they waiting on?",
  "We sent a pre-alert today but no job opened. Why?",
  "Is every part of the app working?",
];

// **bold** and `code` in a line of the answer.
function inline(text) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    return part;
  });
}

// The model writes short paragraphs and "- " lists; nothing else is drawn.
function Answer({ text }) {
  const blocks = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) {
      if (!list) {
        list = { ordered: /^\d/.test(line), items: [] };
        blocks.push(list);
      }
      list.items.push(item[1]);
    } else {
      list = null;
      if (line) blocks.push(line);
    }
  }
  return blocks.map((block, i) => {
    if (typeof block === "string") return <p key={i}>{inline(block)}</p>;
    const List = block.ordered ? "ol" : "ul";
    return (
      <List key={i}>
        {block.items.map((item, j) => (
          <li key={j}>{inline(item)}</li>
        ))}
      </List>
    );
  });
}

// Questions and answers that went through, oldest first: the context of the next question.
function history(turns) {
  const done = [];
  turns.forEach((turn, i) => {
    const answer = turns[i + 1];
    if (turn.role === "user" && answer && !answer.error) done.push(turn, answer);
  });
  return done.map(({ role, text }) => ({ role, text }));
}

export default function Assistant({ shipment }) {
  const key = shipment ? `job:${shipment.id}` : "help";
  const { turns, busy, draft: question } = useSyncExternalStore(subscribe, () => chatOf(key));
  const threadRef = useRef(null);
  const kept = useRef(turns.length); // the turns already there when the panel opened: they don't fade in again

  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [turns, busy]);

  const ask = async (text) => {
    const q = text.trim();
    if (!q || chatOf(key).busy) return;
    const before = history(chatOf(key).turns);
    change(key, (chat) => ({ turns: [...chat.turns, { role: "user", text: q }], busy: true, draft: "" }));
    let answer;
    try {
      const reply = await api.ask(q, shipment ? shipment.id : null, before);
      answer = { role: "assistant", text: reply.answer, checked: reply.checked, model: reply.model };
    } catch (err) {
      answer = { role: "assistant", text: err.message, error: true };
    }
    change(key, (chat) => ({ turns: [...chat.turns, answer], busy: false }));
  };

  return (
    <section className="panel ask">
      <header className="ask-head">
        <img src={mascot} alt="" width="267" height="480" />
        <div>
          <h2>{shipment ? `Ask about job ${shipment.job_no}` : "Ask the assistant"}</h2>
          <p>
            It checks the jobs, the mail log, the mailbox, the browser agent and the workflow engine, then answers.
            It changes nothing.
          </p>
        </div>
        {turns.length > 0 && !busy && (
          <button
            type="button"
            className="btn btn-quiet btn-sm ask-clear"
            onClick={() => {
              kept.current = 0;
              change(key, () => NEW_CHAT);
            }}
          >
            Start again
          </button>
        )}
      </header>
      <div className="ask-thread" ref={threadRef} aria-live="polite">
        {turns.length === 0 && (
          <div className="ask-starters">
            {(shipment ? ABOUT_A_JOB : ABOUT_ANYTHING).map((s) => (
              <button key={s} type="button" className="ask-chip" onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <p key={i} className={`ask-turn user${i >= kept.current ? " fresh" : ""}`}>
              {turn.text}
            </p>
          ) : (
            <div key={i} className={`ask-turn assistant${turn.error ? " error" : ""}${i >= kept.current ? " fresh" : ""}`}>
              <Answer text={turn.text} />
              {turn.checked && turn.checked.length > 0 && (
                <small className="ask-checked">
                  Checked {turn.checked.join(", ")}
                  {turn.model ? ` · ${turn.model}` : ""}
                </small>
              )}
            </div>
          ),
        )}
        {busy && (
          <p className="ask-turn assistant pending fresh">
            <span className="ask-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            Checking…
          </p>
        )}
      </div>
      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
      >
        <textarea
          className="input"
          rows={2}
          value={question}
          maxLength={2000}
          placeholder={shipment ? "Ask about this job" : "Ask about a job, a mail or the app"}
          aria-label="Your question"
          onChange={(e) => change(key, () => ({ draft: e.target.value }))}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask(question);
            }
          }}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}
