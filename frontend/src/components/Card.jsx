import { containerSummary, etaInfo, initials, stageNote } from "../lib/format.js";
import PageStrip from "./PageStrip.jsx";

export default function Card({ shipment, flash, dragging, onOpen, onDragStart, onDragEnd }) {
  const v = shipment.values;
  const eta = etaInfo(shipment);
  const note = stageNote(shipment);
  const classes = ["card", dragging && "dragging", flash && "flash"].filter(Boolean).join(" ");
  return (
    <div
      id={`card-${shipment.id}`}
      className={classes}
      role="button"
      tabIndex={0}
      draggable
      aria-label={`Open ${v.bl_no || shipment.job_no}`}
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
      <div className={`bl${v.bl_no ? "" : " muted"}`}>{v.bl_no || "No BL number yet"}</div>
      <div className="vessel">
        {v.vessel || "Vessel not set"}
        {v.voyage && <span> {v.voyage}</span>}
      </div>
      <div className="facts">
        <span>{containerSummary(v)}</span>
        <span className={`eta${eta.urgent ? " urgent" : ""}`}>{eta.text}</span>
      </div>
      {note && <div className="note">{note}</div>}
      <div className="strip-row">
        <PageStrip pages={shipment.pages} />
        <span className="count">
          {shipment.pages_done}/{shipment.pages_total}
        </span>
      </div>
    </div>
  );
}
