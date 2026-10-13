import { useEffect, useState } from "react";
import { slotName } from "../lib/folder.js";
import { fmtMoment, plural } from "../lib/format.js";

// A document counts as seen until its content changes: a new file, or for a
// letter the app writes, a change to the values it prints. The bonds never change.
function seenKey(slot, shipment) {
  if (slot.file) return `${slot.id}:${slot.file.id}`;
  if (slot.source === "fixed") return slot.id;
  return `${slot.id}:${JSON.stringify(shipment.values)}`;
}

// `seen` lives in the shipment view, so stepping out to fix a field keeps the progress.
export default function Review({ shipment, seen, setSeen, onBuild, goTo, building }) {
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

  return (
    <section>
      <h3 className="section-title">Review and build</h3>
      <p className="section-sub">
        Look through every document as it will appear in the PDF, stamps included. Building joins them into one
        file for the DO request on ODeX.
      </p>

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

      {open > 0 && (
        <div className="notice warn">
          <strong>{plural(open, "document")} still need attention.</strong> The folder can be built once every
          document is in place and checked.{" "}
          <button type="button" className="linkbtn" onClick={() => goTo("documents")}>
            Go to the DO folder
          </button>
        </div>
      )}

      <div className="review">
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
                <span className="label">{slotName(s)}</span>
                <span className={`tick ${s.status !== "ok" ? s.status : isSeen(s) ? "seen" : ""}`}>
                  {s.status === "missing" ? "Missing" : s.status === "flagged" ? "Check" : isSeen(s) ? "Seen" : ""}
                </span>
              </button>
            </li>
          ))}
        </ol>
        <div className="viewer">
          <div className="viewer-bar">
            <button type="button" className="btn" disabled={current === 0} onClick={() => setCurrent(current - 1)}>
              Previous
            </button>
            <span className="viewer-title">
              {current + 1}. {slotName(slot)}
            </span>
            <button
              type="button"
              className="btn"
              disabled={current === slots.length - 1}
              onClick={() => setCurrent(current + 1)}
            >
              Next
            </button>
          </div>
          {slot.preview_url ? (
            <iframe
              key={currentKey}
              className="viewer-frame"
              title={`Preview of ${slotName(slot)}`}
              src={`${slot.preview_url}?v=${encodeURIComponent(shipment.updated_at)}#view=FitH`}
            />
          ) : (
            <div className="viewer-empty">
              <p>{slot.flags[0] ? slot.flags[0].message : "This document isn't ready yet."}</p>
            </div>
          )}
        </div>
      </div>

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
          disabled={!confirmed || unseen > 0 || open > 0 || building}
          onClick={async () => {
            if (await onBuild()) setConfirmed(false);
          }}
        >
          {building ? "Building…" : folder ? "Build the DO folder again" : "Build the DO folder"}
        </button>
      </div>
    </section>
  );
}
