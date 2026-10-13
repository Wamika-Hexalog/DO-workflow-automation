import { useEffect, useState } from "react";
import Card from "./Card.jsx";

function matcher(query) {
  const q = query.trim().toLowerCase().replace(/\s+/g, "");
  if (!q) return () => true;
  return (s) =>
    [s.job_no, s.values.mbl_no, s.values.hbl_nos, s.values.vessel, s.values.containers].some((v) =>
      String(v || "").toLowerCase().replace(/\s+/g, "").includes(q),
    );
}

export default function Board({ stages, shipments, query, focusId, flashId, onOpen, onMove }) {
  const [dragId, setDragId] = useState(null);
  const [over, setOver] = useState(null);

  useEffect(() => {
    if (!focusId) return;
    const el = document.getElementById(`card-${focusId}`);
    if (el) {
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
      el.focus({ preventScroll: true });
    }
  }, [focusId]);

  const emptyText = {
    igm_filed: "New jobs start here once CMA CGM files the IGM.",
    invoice_paid: "Drag a job here once Finance pays the DO invoice and deposit.",
    deposit_approved: "Drag a job here once ODeX shows the deposit approved.",
    folder_ready: "Jobs land here when their DO folder is built.",
    do_requested: "Drag a job here once the DO request is raised on ODeX.",
    edo_received: "Drag a job here when the eDO arrives.",
  };
  const matches = matcher(query);

  return (
    <main className="board" aria-label="Shipments by DO stage">
      {stages.map((stage) => {
        const cards = shipments.filter((s) => s.status === stage.id && matches(s));
        return (
          <section key={stage.id} className="col" aria-label={stage.name}>
            <div className="route" aria-hidden="true">
              <span className="stop" />
            </div>
            <h2 className="col-head">
              <span>{stage.name}</span>
              <span className="col-count">{cards.length}</span>
            </h2>
            <div
              className={`list${over === stage.id ? " drop" : ""}`}
              onDragOver={(e) => {
                if (!dragId) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (over !== stage.id) setOver(stage.id);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget)) setOver((o) => (o === stage.id ? null : o));
              }}
              onDrop={(e) => {
                e.preventDefault();
                const id = e.dataTransfer.getData("text/plain") || dragId;
                setOver(null);
                setDragId(null);
                if (id) onMove(id, stage.id);
              }}
            >
              {cards.map((s) => (
                <Card
                  key={s.id}
                  shipment={s}
                  flash={flashId === s.id}
                  dragging={dragId === s.id}
                  onOpen={onOpen}
                  onDragStart={(e, id) => {
                    e.dataTransfer.setData("text/plain", id);
                    e.dataTransfer.effectAllowed = "move";
                    setDragId(id);
                  }}
                  onDragEnd={() => {
                    setDragId(null);
                    setOver(null);
                  }}
                />
              ))}
              {cards.length === 0 && (
                <p className="col-empty">{query.trim() ? "No matches in this stage." : emptyText[stage.id]}</p>
              )}
            </div>
          </section>
        );
      })}
    </main>
  );
}
