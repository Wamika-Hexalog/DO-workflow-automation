// One segment per page of the DO set: filled when complete, tinted when started.
export default function PageStrip({ pages, titles, large = false, current, onPick }) {
  const done = pages.filter((p) => p.status === "done").length;
  if (!large) {
    return (
      <div className="strip" role="img" aria-label={`${done} of ${pages.length} pages complete`}>
        {pages.map((p) => (
          <span key={p.id} className={`seg ${p.status}`} />
        ))}
      </div>
    );
  }
  return (
    <div className="strip lg" role="group" aria-label="Pages in the DO set">
      {pages.map((p, i) => {
        const isCurrent = current === p.id;
        const state = p.status === "done" ? "complete" : `${p.filled} of ${p.total} filled`;
        return (
          <button
            key={p.id}
            type="button"
            className={`seg ${p.status}${isCurrent ? " current" : ""}`}
            title={`${i + 1}. ${titles[p.id]}`}
            aria-label={`Page ${i + 1}, ${titles[p.id]}, ${state}`}
            aria-current={isCurrent ? "true" : undefined}
            onClick={() => onPick(p.id)}
          >
            {i + 1}
          </button>
        );
      })}
    </div>
  );
}
