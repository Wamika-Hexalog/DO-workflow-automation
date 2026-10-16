import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtMoment } from "../lib/format.js";
import { CheckIcon } from "./Icons.jsx";

const money = (n) => (n == null || n === "" ? "" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`);
const live = (items) => items.filter((i) => i.status !== "Rejected");
const paysDeposit = (p) => p.invoices.some((r) => r.security_deposit);

// What the portals and the app hold for each of the six stages, and whether it is reached.
function stageFacts(shipment, portal) {
  const p = portal && portal.available ? portal : { invoice_requests: [], rtgs_requests: [], deposits: [], do_requests: [] };
  const invoicePay = p.rtgs_requests.filter((r) => !paysDeposit(r));
  const depositPay = p.rtgs_requests.filter(paysDeposit);
  const built = shipment.folder && !shipment.folder.stale;
  return {
    igm_filed: { reached: true, records: [] },
    invoice_paid: {
      reached: invoicePay.some((r) => r.status === "Receipt Sent"),
      records: [...p.invoice_requests.map((r) => ["invoice", r]), ...invoicePay.map((r) => ["payment", r])],
    },
    deposit_approved: {
      reached: p.deposits.some((d) => d.status === "Deposit Approved"),
      records: [...depositPay.map((r) => ["payment", r]), ...p.deposits.map((r) => ["deposit", r])],
    },
    folder_ready: { reached: Boolean(built), records: [] },
    do_requested: { reached: live(p.do_requests).length > 0, records: p.do_requests.map((r) => ["do", r]) },
    edo_received: { reached: p.do_requests.some((r) => r.status === "DO Issued"), records: [] },
  };
}

const KIND = { invoice: "ODeX DO invoice", payment: "RTGS payment", deposit: "ODeX security deposit", do: "ODeX DO request" };

function Record({ kind, item, files, shipmentId }) {
  const facts = {
    invoice: [["Invoice no.", item.invoice_no, true], ["Amount", money(item.amount)]],
    payment: [["UTR", item.utr, true], ["Amount", money(item.amount)], ["Transferred", item.transfer_date]],
    deposit: [["UTR", item.utr, true], ["Amount", money(item.amount)], ["Bank", item.bank_name], ["Refund to", item.refund_to]],
    do: [["eDO no.", item.edo_no, true]],
  }[kind].filter(([, v]) => v);
  const fileIds = [item.invoice_file, item.slip_file, item.edo_file, ...(item.receipts || [])].filter((f) => f && files[f]);
  const tone = item.status === "Rejected" ? "error" : item.waiting ? "missing" : "ready";
  return (
    <div className="rec">
      <div className="rec-head">
        <span className="rec-kind">{KIND[kind]}</span>
        <span className="rec-ref mono">{item.ref}</span>
        <span className={`badge ${tone}`}>{item.waiting ? `${item.status} · waiting on CMA CGM` : item.status}</span>
      </div>
      {facts.length > 0 && (
        <dl className="rec-facts">
          {facts.map(([k, v, code]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd className={code ? "mono" : undefined}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {item.reason && <p className="problems">{item.reason}</p>}
      {fileIds.length > 0 && (
        <p className="rec-files">
          {fileIds.map((id) => (
            <a key={id} className="linkbtn" href={`/api/shipments/${shipmentId}/portal/files/${id}`} target="_blank" rel="noreferrer">
              {files[id]}
            </a>
          ))}
        </p>
      )}
    </div>
  );
}

// The six DO stages as a timeline, filled from the app and from CMA CGM's answers on
// the local ODeX and RTGS portals (their control panel).
export default function Stages({ shipment, stages, onMove, goTo }) {
  const [portal, setPortal] = useState(null);

  useEffect(() => {
    let on = true;
    const load = () => api.portal(shipment.id).then((d) => on && setPortal(d), () => on && setPortal({ available: false }));
    load();
    // Autoplay on the control panel answers within seconds, so the timeline follows along.
    const timer = setInterval(load, 4000);
    return () => {
      on = false;
      clearInterval(timer);
    };
  }, [shipment.id]);

  const facts = stageFacts(shipment, portal);
  const at = stages.findIndex((s) => s.id === shipment.status);
  const reached = stages.reduce((last, s, i) => (facts[s.id].reached ? i : last), 0);
  const files = (portal && portal.files) || {};
  const built = shipment.folder;

  const note = {
    igm_filed: `Job ${shipment.job_no} opened for MBL ${shipment.values.mbl_no || "not set"}.`,
    invoice_paid: "ODeX releases the DO invoice; Finance pays it by RTGS and CMA CGM sends the DO receipt.",
    deposit_approved: "Finance pays the security deposit; CMA CGM approves it on ODeX and the slip comes out.",
    folder_ready: built
      ? built.stale
        ? "The folder is out of date. Build it again."
        : `Folder built ${fmtMoment(built.built_at)}.`
      : `${shipment.docs_ok} of ${shipment.docs_total} documents ready.`,
    do_requested: "The DO request goes to CMA CGM on ODeX with the folder.",
    edo_received: "CMA CGM issues the eDO on ODeX.",
  };

  return (
    <section>
      <h3 className="section-title">Stages</h3>

      {reached > at && (
        <div className="banner warn compact stage-hint">
          <p>
            <strong>CMA CGM shows {stages[reached].name}.</strong> The job is still at {stages[at].name}.
          </p>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => onMove(stages[reached].id)}>
            Move to {stages[reached].name}
          </button>
        </div>
      )}

      <ol className="timeline">
        {stages.map((stage, i) => {
          const f = facts[stage.id];
          const waiting = f.records.some(([, r]) => r.waiting);
          // Stage names are events ("Invoice paid"), so the job's own stage counts as done.
          const state = i <= at || f.reached ? "done" : waiting ? "waiting" : "next";
          return (
            <li key={stage.id} className={`t-stage ${state}${i === at ? " here" : ""}`}>
              <span className="t-dot" aria-hidden="true">
                {state === "done" ? <CheckIcon size={13} /> : i + 1}
              </span>
              <div className="t-body">
                <div className="t-head">
                  <h4>{stage.name}</h4>
                  {i === at && <span className="badge current">Job is here</span>}
                  {waiting && <span className="badge missing">Waiting on CMA CGM</span>}
                </div>
                <p className="t-note">{note[stage.id]}</p>
                {stage.id === "folder_ready" && !built && (
                  <button type="button" className="linkbtn" onClick={() => goTo("review")}>
                    Review and build
                  </button>
                )}
                {f.records.map(([kind, item]) => (
                  <Record key={`${kind}-${item.ref}`} kind={kind} item={item} files={files} shipmentId={shipment.id} />
                ))}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
