import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtMoment } from "../lib/format.js";

// In the order the process reaches them (backend app/tasks.py).
const ORDER = [
  "odex_invoice_request",
  "odex_invoice_collect",
  "finance_mail",
  "cma_rtgs_payment",
  "cma_rtgs_deposit",
  "odex_deposit",
  "folder_review",
  "odex_do_request",
  "odex_edo",
];
// Ops approve these first: they confirm or move money.
const FOR_OPS = new Set(["cma_rtgs_payment", "cma_rtgs_deposit", "odex_deposit"]);
// These two have their own screen, Review and build.
const ON_REVIEW = new Set(["folder_review", "odex_do_request"]);
const STATUS = { pending: "Waiting", awaiting_approval: "Needs you", completed: "Done", failed: "Failed" };

function action(task) {
  if (ON_REVIEW.has(task.key)) return task.status === "awaiting_approval" || task.status === "failed" ? "review" : null;
  if (FOR_OPS.has(task.key)) return task.status === "awaiting_approval" || task.status === "failed" ? "Approve" : null;
  return task.status === "failed" ? "Try again" : null;
}

export default function Process({ shipment, onShipment, say, goTo }) {
  const [busy, setBusy] = useState(null);
  const steps = [...shipment.tasks].sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
  const moving = steps.some((t) => t.status === "pending" || t.status === "awaiting_approval");

  // The agent works in the background: follow it while something is still open.
  useEffect(() => {
    if (!moving) return undefined;
    const timer = setInterval(() => {
      api.shipment(shipment.id).then(onShipment).catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [moving, shipment.id, onShipment]);

  const act = async (task) => {
    setBusy(task.key);
    try {
      onShipment(await api.completeTask(shipment.id, task.key));
      say(FOR_OPS.has(task.key) ? `${task.title}: approved. The browser agent is on it.` : `${task.title}: started again.`);
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
        Each step on ODeX, on CMA CGM's RTGS page and by mail, in order. The browser agent runs a step when its mail
        arrives. The payments wait for your approval, and a step that failed can be started again.
      </p>
      {steps.length === 0 ? (
        <p className="hint">Nothing has started yet. The process starts when the MBL is attached.</p>
      ) : (
        <ol className="process">
          {steps.map((task) => {
            const next = action(task);
            return (
              <li key={task.key} className={`step ${task.status}`}>
                <div className="step-head">
                  <span className="step-title">{task.title}</span>
                  <span className={`badge step-status ${task.status}`}>{STATUS[task.status] || task.status}</span>
                </div>
                {task.note && <p className="step-note">{task.note}</p>}
                {task.status === "completed" && task.completed_at && (
                  <p className="hint">
                    {fmtMoment(task.completed_at)}
                    {task.completed_by ? ` · ${task.completed_by}` : ""}
                  </p>
                )}
                {next === "review" && (
                  <button type="button" className="btn" onClick={() => goTo("review")}>
                    Open Review and build
                  </button>
                )}
                {next && next !== "review" && (
                  <button type="button" className="btn btn-primary" disabled={busy === task.key} onClick={() => act(task)}>
                    {busy === task.key ? "Sending…" : next}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
