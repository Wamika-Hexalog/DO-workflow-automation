import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtMoment } from "../lib/format.js";

const ACTION = {
  created: "Opened",
  attached: "Attached",
  replaced: "Replaced",
  removed: "Removed",
  checked: "Checked",
  built: "Built",
  moved: "Stage",
};

export default function History({ shipment, stageNames }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    api
      .events(shipment.id)
      .then((list) => live && setEvents(list))
      .catch((err) => live && setError(err.message));
    return () => {
      live = false;
    };
  }, [shipment.id, shipment.updated_at]);

  // Stage ids in "moved" entries read better as the board's names.
  const readable = (text) => text.replace(/\b[a-z]+_[a-z_]+\b/g, (id) => stageNames[id] || id);

  return (
    <section>
      <h3 className="section-title">History</h3>
      {error && <p className="problems">{error}</p>}
      {events === null && !error && <p className="hint">Loading…</p>}
      {events && (
        <ol className="history">
          {events.map((event, i) => (
            <li key={i}>
              <span className="when">{fmtMoment(event.at)}</span>
              <span className={`what ${event.action}`}>{ACTION[event.action] || event.action}</span>
              <span className="detail-text">{readable(event.detail)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
