import { openFlags } from "../lib/folder.js";
import { initials, shipmentFacts, stageNote } from "../lib/format.js";
import Progress from "./Progress.jsx";

function cardStatus(shipment, flags) {
  if (shipment.folder && shipment.folder.stale) return { kind: "error", label: "Folder out of date" };
  if (flags > 0) return { kind: "attn", label: `${flags} to check` };
  if (shipment.docs_total > 0 && shipment.docs_ok === shipment.docs_total) return { kind: "ready", label: "Ready" };
  return null;
}

export default function Card({ shipment, index, flash, dragging, onOpen, onDragStart, onDragEnd }) {
  const v = shipment.values;
  const note = stageNote(shipment);
  const status = cardStatus(shipment, openFlags(shipment));
  const classes = ["card", dragging && "dragging", flash && "flash"].filter(Boolean).join(" ");
  return (
    <div
      id={`card-${shipment.id}`}
      className={classes}
      style={{ "--i": index }}
      role="button"
      tabIndex={0}
      draggable
      aria-label={`Open ${v.mbl_no || shipment.job_no}`}
      onClick={() => onOpen(shipment.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(shipment.id);
        }
      }}
      onDragStart={(e) => onDragStart(e, shipment.id)}
      onDragEnd={onDragEnd}
    >
      <span className="job">{shipment.job_no}</span>
      <div className={`bl${v.mbl_no ? "" : " muted"}`}>{v.mbl_no || "No MBL number yet"}</div>
      <div className="vessel">{[v.vessel || "Vessel not set", v.voyage].filter(Boolean).join(" · ")}</div>
      <div className="facts">{shipmentFacts(v).join(" · ")}</div>
      <Progress ok={shipment.docs_ok} total={shipment.docs_total} />
      {note && <div className="note">{note}</div>}
      {(status || shipment.owner) && (
        <div className="card-foot">
          {shipment.owner ? (
            <span className="avatar" title={shipment.owner}>
              {initials(shipment.owner)}
            </span>
          ) : (
            <span />
          )}
          {status && <span className={`badge ${status.kind}`}>{status.label}</span>}
        </div>
      )}
    </div>
  );
}
