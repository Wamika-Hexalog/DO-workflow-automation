import { useEffect, useState } from "react";
import { slotName } from "../lib/folder.js";
import { fmtMoment, plural } from "../lib/format.js";
import mascot from "../assets/mascot-ready.webp";
import { AlertIcon, ChevronLeftIcon, ChevronRightIcon, ExternalIcon } from "./Icons.jsx";

// A document counts as seen until its content changes: a new file, or for a
// letter the app writes, a change to the values it prints. The bonds never change.
function seenKey(slot, shipment) {
  if (slot.file) return `${slot.id}:${slot.file.id}`;
  if (slot.source === "fixed") return slot.id;
  return `${slot.id}:${JSON.stringify(shipment.values)}`;
}

// After the build: the DO request on ODeX, waiting for ops, running, failed or done.
function DoRequest({ shipment, automated, onApprove, approving }) {
  const task = shipment.tasks.find((t) => t.key === "odex_do_request");
  if (!task || task.status === "pending") return null;
  const requested = shipment.status === "do_requested" || shipment.status === "edo_received";
  if (task.status === "awaiting_approval") {
    return (
      <div className="notice">
        <strong>DO request on ODeX.</strong> {task.note}
        <div className="slot-actions">
          <button type="button" className="btn btn-primary" disabled={approving} onClick={onApprove}>
            {approving ? "Starting…" : automated ? "Approve and raise it on ODeX" : "I've raised it on ODeX"}
          </button>
        </div>
      </div>
    );
  }
  if (task.status === "failed") {
    return (
      <div className="notice warn">
        <strong>The DO request didn't go through.</strong> {task.note}
        <div className="slot-actions">
          <button type="button" className="btn" disabled={approving} onClick={onApprove}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!requested) {
    return (
      <div className="notice">
        <strong>{automated ? "Raising the DO request on ODeX…" : "Recording the DO request…"}</strong>
      </div>
    );
  }
  return (
    <div className="notice">
      <strong>DO request raised.</strong> {task.note}
    </div>
  );
}

// `seen` lives in the shipment view, so stepping out to fix a field keeps the progress.
export default function Review({ shipment, seen, setSeen, onBuild, goTo, building, automated, onApprove, approving }) {
  const [current, setCurrent] = useState(0);
  const [confirmed, setConfirmed] = useState(false);

  const slots = shipment.slots;
  const slot = slots[Math.min(current, slots.length - 1)];
  const currentKey = seenKey(slot, shipment);

  // Whatever is on screen counts as looked at.
  useEffect(() => {
    if (slot.preview_url) setSeen((prev) => (prev.has(currentKey) ? prev : new Set(prev).add(currentKey)));
  }, [currentKey, slot.preview_url]);
  const isSeen = (s) => seen.has(seenKey(s, shipment));
  const unseen = slots.filter((s) => !isSeen(s)).length;
  const open = slots.filter((s) => s.status !== "ok").length;
  const folder = shipment.folder;

  const src = slot.preview_url && `${slot.preview_url}?v=${encodeURIComponent(shipment.updated_at)}`;

  return (
    <section className="review-page">
      <header className="review-head">
        <h3 className="section-title">Review and build</h3>
        {open > 0 ? (
          <div className="banner warn compact">
            <AlertIcon size={16} />
            <p>
              <strong>{plural(open, "document")} need attention</strong> before the folder can be built.
            </p>
            <button type="button" className="btn btn-sm" onClick={() => goTo("documents")}>
              View documents
            </button>
          </div>
        ) : (
          <div className="banner ready compact">
            <img src={mascot} alt="" width="290" height="480" />
            <p>
              <strong>All {slots.length} documents are ready.</strong>{" "}
              {unseen > 0
                ? `Look through the ${plural(unseen, "document")} not seen yet, then build the folder.`
                : "Every document has been looked at. Confirm and build the folder."}
            </p>
          </div>
        )}
      </header>

      {folder && (
        <div className={`notice${folder.stale ? " warn" : ""}`}>
          {folder.stale ? (
            <>
              <strong>The folder is out of date.</strong> Something changed after it was built on{" "}
              {fmtMoment(folder.built_at)}. Review and build it again before uploading it.
            </>
          ) : (
            <>
              <strong>Folder built {fmtMoment(folder.built_at)}.</strong>{" "}
              <a className="linkbtn" href={folder.url} download={folder.filename}>
                Download the PDF
              </a>
            </>
          )}
        </div>
      )}

      {shipment.building && (
        <div className="notice">
          <strong>Building the DO folder…</strong> The PDF downloads when it's ready.
        </div>
      )}

      {folder && !folder.stale && (
        <DoRequest shipment={shipment} automated={automated} onApprove={onApprove} approving={approving} />
      )}

      <div className="review">
        <div className="review-side">
          <ol className="review-list">
            {slots.map((s, i) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={`review-item${i === current ? " active" : ""}`}
                  aria-current={i === current ? "true" : undefined}
                  onClick={() => setCurrent(i)}
                >
                  <span className="num">{i + 1}</span>
                  <span className="label" title={slotName(s)}>
                    {slotName(s)}
                  </span>
                  <span className={`tick ${s.status !== "ok" ? s.status : isSeen(s) ? "seen" : ""}`}>
                    {s.status === "missing" ? "Missing" : s.status === "flagged" ? "Check" : isSeen(s) ? "Seen" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="build">
            <label className="check">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={unseen > 0}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>
                I have looked at all {slots.length} documents and they belong to job {shipment.job_no}, MBL{" "}
                {shipment.values.mbl_no || "not set"}.
              </span>
            </label>
            <p className="hint">
              {unseen > 0 ? `${plural(unseen, "document")} not looked at yet.` : "Every document has been looked at."}
            </p>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!confirmed || unseen > 0 || open > 0 || building || shipment.building}
              onClick={async () => {
                if (await onBuild()) setConfirmed(false);
              }}
            >
              {building ? "Building…" : folder ? "Build the DO folder again" : "Build the DO folder"}
            </button>
          </div>
        </div>
        <div className="viewer">
          <div className="viewer-bar">
            <button
              type="button"
              className="btn btn-sm btn-icon"
              aria-label="Previous document"
              disabled={current === 0}
              onClick={() => setCurrent(current - 1)}
            >
              <ChevronLeftIcon />
            </button>
            <button
              type="button"
              className="btn btn-sm btn-icon"
              aria-label="Next document"
              disabled={current === slots.length - 1}
              onClick={() => setCurrent(current + 1)}
            >
              <ChevronRightIcon />
            </button>
            <span className="viewer-title">{slotName(slot)}</span>
            <span className="viewer-count">
              {current + 1} of {slots.length}
            </span>
            {src && (
              <a className="btn btn-sm" href={src} target="_blank" rel="noreferrer">
                <ExternalIcon size={14} />
                Open
              </a>
            )}
          </div>
          {src ? (
            // The browser's own PDF toolbar is hidden to give the page its height; Open has zoom and print.
            <iframe
              key={currentKey}
              className="viewer-frame"
              title={`Preview of ${slotName(slot)}`}
              src={`${src}#toolbar=0&navpanes=0&view=FitH`}
            />
          ) : (
            <div className="viewer-empty">
              <p>{slot.flags[0] ? slot.flags[0].message : "This document isn't ready yet."}</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
