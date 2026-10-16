import { useState } from "react";
import mascotSearch from "../assets/mascot-search.webp";
import mascotReady from "../assets/mascot-ready.webp";
import { FileIcon, ListCheckIcon, SearchIcon, ShipIcon } from "./Icons.jsx";
import Assistant from "./Assistant.jsx";
import { stageTone } from "./Route.jsx";

const TOPICS = [
  { id: "jobs", title: "Jobs and stages", icon: ShipIcon },
  { id: "docs", title: "Documents", icon: FileIcon },
  { id: "build", title: "Building the folder", icon: ListCheckIcon },
];

// Short answers; a list where the answer has steps or cases.
const QUESTIONS = [
  ["jobs", "How does a job get on the board?", "It opens when the pre-alert mail with the MBL arrives, or when you click New job. One job per MBL."],
  ["jobs", "How do I move a job to another stage?", ["Drag its card to another column.", "Or pick the stage at the top of the job.", "On Stages, a yellow bar offers the move when CMA CGM is ahead."]],
  ["jobs", "What does the Stages page show?", "Each stage with what ODeX and the CMA CGM RTGS site hold for the job: invoice, payments and UTRs, deposit, DO request and eDO, with their PDFs."],
  ["docs", "Why does a document say Missing or Check?", ["Missing: no file is attached yet.", "Check: the file does not match, such as another B/L, a copy used in another job, or an unreadable page."]],
  ["docs", "How do I attach or replace a file?", ["Open Documents and click Attach file, or drop the file on the row.", "To replace it, open the ⋯ menu on the row."]],
  ["docs", "Who adds the stamps?", "The app, on each document that needs one. Preview shows the stamped page; Original shows the file as it came."],
  ["docs", "Why do some jobs have more documents?", "HBL documents repeat once for each house bill."],
  ["build", "When can I build the folder?", ["Every document is ready.", "You looked at each one on Review and build.", "You ticked the box. Then Build the DO folder downloads the PDF."]],
  ["build", "Why is the folder out of date?", "A file or a detail changed after the build. Build it again before you upload it."],
  ["build", "Are my changes saved?", "Yes, by themselves. The top of the job says Saving…, then All changes saved."],
  ["build", "What happens when I change the fixed details?", "They print in every job's letters, so every shipment changes and built folders need building again."],
];

const ONE_LINE = {
  igm_filed: "DO invoice requested on ODeX",
  invoice_paid: "Finance paid the DO invoice",
  deposit_approved: "CMA CGM approved the deposit",
  folder_ready: "The DO folder PDF is built",
  do_requested: "DO request raised on ODeX",
  edo_received: "CMA CGM issued the eDO",
};

export default function Faq({ stages }) {
  const [topic, setTopic] = useState("all");
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const shown = QUESTIONS.filter(
    ([t, question, answer]) =>
      (topic === "all" || t === topic) && (!q || `${question} ${[answer].flat().join(" ")}`.toLowerCase().includes(q)),
  );
  const byId = Object.fromEntries(TOPICS.map((t) => [t.id, t]));

  return (
    <main className="faq">
      <header className="hero">
        <div className="hero-text">
          <h1>How can we help?</h1>
          <div className="hero-actions">
            <label className="search">
              <SearchIcon />
              <input
                type="search"
                placeholder="Search the questions"
                aria-label="Search the questions"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          </div>
        </div>
        <div />
        <img className="hero-mascot" src={mascotSearch} alt="" width="267" height="480" />
      </header>

      <div className="faq-layout">
        <div className="faq-main">
          <Assistant />
          <div className="faq-topics" role="tablist" aria-label="Topics">
            {[{ id: "all", title: "All" }, ...TOPICS].map((t) => {
              const n = t.id === "all" ? QUESTIONS.length : QUESTIONS.filter(([id]) => id === t.id).length;
              const TopicIcon = t.icon;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={topic === t.id}
                  className={`faq-topic${topic === t.id ? " active" : ""}`}
                  onClick={() => setTopic(t.id)}
                >
                  {TopicIcon && <TopicIcon size={15} />}
                  {t.title}
                  <span className="faq-count">{n}</span>
                </button>
              );
            })}
          </div>

          {shown.length === 0 ? (
            <div className="faq-empty">
              <img src={mascotSearch} alt="" width="267" height="480" />
              <strong>No question matches “{query}”</strong>
              <span>Try another word, or ask the customs ops team.</span>
            </div>
          ) : (
            <div className="faq-list">
              {shown.map(([t, question, answer]) => {
                const TopicIcon = byId[t].icon;
                return (
                  <details key={question} className={`faq-item ${t}`}>
                    <summary>
                      <span className="faq-icon">
                        <TopicIcon size={16} />
                      </span>
                      <span className="faq-q">{question}</span>
                      <span className="faq-chev" aria-hidden="true" />
                    </summary>
                    {Array.isArray(answer) ? (
                      <ul className="faq-a">
                        {answer.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="faq-a">{answer}</p>
                    )}
                  </details>
                );
              })}
            </div>
          )}
        </div>

        <aside className="faq-side">
          <section className="panel faq-flow">
            <h2>The DO flow at a glance</h2>
            <ol>
              {stages.map((s, i) => (
                <li key={s.id} style={stageTone(i)}>
                  <span className="faq-step">{i + 1}</span>
                  <span>
                    <strong>{s.name}</strong>
                    <small>{ONE_LINE[s.id]}</small>
                  </span>
                </li>
              ))}
            </ol>
          </section>
          <section className="faq-stuck">
            <img src={mascotReady} alt="" width="290" height="480" />
            <div>
              <strong>Still stuck?</strong>
              <p>Ask the customs ops team. Tell them the job no. and what you see on screen.</p>
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
