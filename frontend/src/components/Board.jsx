import { useEffect, useState } from "react";
import Card from "./Card.jsx";
import { CheckIcon } from "./Icons.jsx";

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
    invoice_paid: "Jobs move here once Finance pays the DO invoice and deposit.",
    deposit_approved: "Jobs move here once ODeX shows the deposit approved.",
    folder_ready: "Jobs move here when their DO folder is built.",
    do_requested: "Jobs move here once the DO request is raised on ODeX.",
    edo_received: "Jobs move here when the eDO arrives.",
  };
  const matches = matcher(query);

  return (
    <main className={`board${dragId ? " dragging" : ""}`} aria-label="Shipments by DO stage">
      {stages.map((stage, step) => {
        const cards = shipments.filter((s) => s.status === stage.id && matches(s));
        return (
          <section key={stage.id} id={`col-${stage.id}`} className="col" aria-label={stage.name}>
            <h2 className="col-head">
              <span className="col-step">{String(step + 1).padStart(2, "0")}</span>
              <span className="col-name">{stage.name}</span>
              <span className="col-count" aria-label={`${cards.length} jobs`}>{cards.length}</span>
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
              {cards.map((s, i) => (
                <Card
                  key={s.id}
                  index={i}
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
                <div className="col-empty">
                  <CheckIcon size={18} />
                  <strong>{query.trim() ? "No matches" : "Nothing here yet"}</strong>
                  <span>{query.trim() ? "No job in this stage matches the search." : emptyText[stage.id]}</span>
                </div>
              )}
            </div>
          </section>
        );
      })}
    </main>
  );
}
