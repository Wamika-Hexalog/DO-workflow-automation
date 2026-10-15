// Each stage's accent, set as CSS variables that its column, stop and dot read.
export const stageTone = (i) => ({ "--stage": `var(--stage-${i + 1})`, "--stage-soft": `var(--stage-${i + 1}-soft)` });

// The DO stages drawn as a route: stops on a line, and a container at the current stop.
export function Route({ stages, at }) {
  const pct = stages.length > 1 ? (100 * at) / (stages.length - 1) : 0;
  return (
    <div className="route" style={{ "--at": `${pct}%` }}>
      <div className="route-track" aria-hidden="true">
        <span className="route-done" />
        {stages.map((s, i) => (
          <span key={s.id} className={`stop${i <= at ? " passed" : ""}`} style={{ left: `${(100 * i) / (stages.length - 1)}%` }} />
        ))}
        <span className="route-mover">
          <span className="box" />
        </span>
      </div>
      <p className="route-label">
        <span>
          Stage {at + 1} of {stages.length}
        </span>
        <strong>{stages[at].name}</strong>
      </p>
    </div>
  );
}

// The whole board on one route: each stop shows how many jobs sit there, as stacked containers.
export function FleetRoute({ stages, shipments, onStage }) {
  return (
    <ol className="fleet" aria-label="Jobs by stage">
      {stages.map((stage, i) => {
        const n = shipments.filter((s) => s.status === stage.id).length;
        return (
          <li key={stage.id} style={stageTone(i)}>
            <button type="button" className={`port${n ? "" : " empty"}`} onClick={() => onStage(stage.id)}>
              <span className="stack" aria-hidden="true">
                {Array.from({ length: Math.min(n, 3) }, (_, i) => (
                  <span key={i} className="box" />
                ))}
              </span>
              <span className="port-dot" aria-hidden="true" />
              <span className="port-count">{n}</span>
              <span className="port-name">{stage.name}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
