import { openFlags } from "../lib/folder.js";
import { initials, shipmentFacts, stageNote } from "../lib/format.js";
import DocStrip from "./DocStrip.jsx";

export default function Card({ shipment, flash, dragging, onOpen, onDragStart, onDragEnd }) {
  const v = shipment.values;
  const note = stageNote(shipment);
  const flags = openFlags(shipment);
  const stale = shipment.folder && shipment.folder.stale;
  const classes = ["card", dragging && "dragging", flash && "flash"].filter(Boolean).join(" ");
  return (
    <div
      id={`card-${shipment.id}`}
      className={classes}
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
      <div className="card-top">
        <span className="job">{shipment.job_no}</span>
        {shipment.owner && (
          <span className="owner" title={shipment.owner}>
            {initials(shipment.owner)}
          </span>
        )}
      </div>
      <div className={`bl${v.mbl_no ? "" : " muted"}`}>{v.mbl_no || "No MBL number yet"}</div>
      <div className="vessel">
        {v.vessel || "Vessel not set"}
        {v.voyage && <span> {v.voyage}</span>}
      </div>
      <div className="facts">
        {shipmentFacts(v).map((fact) => (
          <span key={fact}>{fact}</span>
        ))}
      </div>
      {(flags > 0 || stale) && (
        <div className="badges">
          {flags > 0 && <span className="badge warn">{flags} to check</span>}
          {stale && <span className="badge warn">Out of date</span>}
        </div>
      )}
      {note && <div className="note">{note}</div>}
      <div className="strip-row">
        <DocStrip slots={shipment.slots} />
        <span className="count">
          {shipment.docs_ok}/{shipment.docs_total}
        </span>
      </div>
    </div>
  );
}
