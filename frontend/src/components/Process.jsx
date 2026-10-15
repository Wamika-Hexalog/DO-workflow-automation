import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtMoment } from "../lib/format.js";

// Ops approve these first: they confirm or move money.
const FOR_OPS = new Set(["cma_rtgs_payment", "cma_rtgs_deposit", "odex_deposit"]);
// These two have their own screen, Review and build.
const ON_REVIEW = new Set(["folder_review", "odex_do_request"]);
const WHO = { ops: "you", finance: "Finance", cma: "CMA CGM" };
const STATE = { done: "Done", needs_you: "Needs you", failed: "Failed", pending: "Waiting", not_started: "Not started" };

// How long a step has waited: 45 min, 3 h 10 min, 2 d 4 h.
function waited(since, now) {
  const minutes = Math.max(0, Math.floor((now - new Date(since)) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} h ${minutes % 60} min` : `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

function action(step) {
  if (!step.task_key || (step.state !== "needs_you" && step.state !== "failed")) return null;
  if (ON_REVIEW.has(step.task_key)) return "review";
  if (FOR_OPS.has(step.task_key)) return "Approve";
  return step.state === "failed" ? "Try again" : null;
}

export default function Process({ shipment, onShipment, say, goTo }) {
  const [busy, setBusy] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const moving = shipment.process.some((s) => ["pending", "needs_you", "waiting"].includes(s.state));

  // The agent works in the background: follow it while something is still open.
  useEffect(() => {
    if (!moving) return undefined;
    const timer = setInterval(() => {
      api.shipment(shipment.id).then(onShipment).catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [moving, shipment.id, onShipment]);

  // The clocks tick each minute.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);

  const act = async (step) => {
    setBusy(step.task_key);
    try {
      onShipment(await api.completeTask(shipment.id, step.task_key));
      say(FOR_OPS.has(step.task_key) ? `${step.title}: approved. The browser agent is on it.` : `${step.title}: started again.`);
    } catch (err) {
      say(err.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section>
      <h3 className="section-title">Process</h3>
      <p className="section-sub">
        Every step of the job, in order, as PROCESS.md numbers it. The browser agent runs a step when its mail arrives,
        the payments wait for your approval, and a step that failed can be started again. A step that waits on you,
        Finance or CMA CGM shows how long it has waited.
      </p>
      <ol className="process">
        {shipment.process.map((step) => {
          const next = action(step);
          const label = step.state === "waiting" ? `Waiting on ${WHO[step.waiting_on]}` : STATE[step.state];
          return (
            <li key={step.number} className={`step ${step.state}`}>
              <div className="step-head">
                <span className="step-title">
                  <span className="step-no">{step.number}</span>
                  {step.title}
                </span>
                <span className={`badge step-status ${step.state}`}>{label}</span>
              </div>
              {step.note && step.state !== "not_started" && <p className="step-note">{step.note}</p>}
              {step.waiting_on && step.since && (
                <p className="step-clock">
                  Waiting on {WHO[step.waiting_on]} for {waited(step.since, now)} · since {fmtMoment(step.since)}
                </p>
              )}
              {step.state === "done" && step.at && (
                <p className="hint">
                  {fmtMoment(step.at)}
                  {step.by ? ` · ${step.by}` : ""}
                </p>
              )}
              {next === "review" && (
                <button type="button" className="btn" onClick={() => goTo("review")}>
                  Open Review and build
                </button>
              )}
              {next && next !== "review" && (
                <button type="button" className="btn btn-primary" disabled={busy === step.task_key} onClick={() => act(step)}>
                  {busy === step.task_key ? "Sending…" : next}
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
