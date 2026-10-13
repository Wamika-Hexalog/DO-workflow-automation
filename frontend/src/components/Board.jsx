import { useEffect, useState } from "react";
import Card from "./Card.jsx";

// Soonest ETA first; shipments without an ETA (usually new ones) on top.
function byEta(a, b) {
  const x = a.values.eta || "";
  const y = b.values.eta || "";
  if (x === y) return 0;
  if (!x) return -1;
  if (!y) return 1;
  return x < y ? -1 : 1;
}

function matcher(query) {
  const q = query.trim().toLowerCase().replace(/\s+/g, "");
  if (!q) return () => true;
  return (s) =>
    [s.job_no, s.values.bl_no, s.values.vessel, s.values.containers].some((v) =>
      String(v || "").toLowerCase().replace(/\s+/g, "").includes(q),
    );
}

export default function Board({ stages, shipments, clientName, query, focusId, flashId, onOpen, onMove }) {
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
    pending: `New ${clientName} shipments start here.`,
    ready: "Shipments move here once their DO set is generated.",
    requested: "Drag a shipment here when its DO request goes to the line.",
    received: "Drag a shipment here when the line issues the eDO.",
    closed: "Drag a shipment here when the job is closed.",
  };
  const matches = matcher(query);

  return (
    <main className="board" aria-label="Shipments by DO stage">
      {stages.map((stage) => {
        const cards = shipments.filter((s) => s.status === stage.id && matches(s)).sort(byEta);
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
