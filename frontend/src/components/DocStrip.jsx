import { slotName } from "../lib/folder.js";

const STATE = { ok: "ready", flagged: "needs attention", missing: "missing" };

// One segment per document of the DO folder, in PDF order.
export default function DocStrip({ slots, large = false, onPick }) {
  const ready = slots.filter((s) => s.status === "ok").length;
  if (!large) {
    return (
      <div className="strip" role="img" aria-label={`${ready} of ${slots.length} documents ready`}>
        {slots.map((s) => (
          <span key={s.id} className={`seg ${s.status}`} />
        ))}
      </div>
    );
  }
  return (
    <div className="strip lg" role="group" aria-label="Documents in the DO folder">
      {slots.map((s, i) => (
        <button
          key={s.id}
          type="button"
          className={`seg ${s.status}`}
          title={`${i + 1}. ${slotName(s)}: ${STATE[s.status]}`}
          aria-label={`Document ${i + 1}, ${slotName(s)}, ${STATE[s.status]}`}
          onClick={() => onPick(s.id)}
        >
          {i + 1}
        </button>
      ))}
    </div>
  );
}
