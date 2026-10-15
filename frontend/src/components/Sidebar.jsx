import { BoardIcon } from "./Icons.jsx";

export default function Sidebar({ stages, shipments, onStage, onReset }) {
  return (
    <aside className="sidebar" aria-label="Workspace">
      <div className="side-brand">
        {/* A plain tile until the brand's cube mark is added: the brand never redraws its logo. */}
        <span className="side-mark" aria-hidden="true" />
        <div>
          <strong>DO Workflow</strong>
          <span>Hexalog customs</span>
        </div>
      </div>
      <nav className="side-nav" aria-label="Main">
        <a className="side-link active" href="#board" aria-current="page" onClick={(e) => e.preventDefault()}>
          <BoardIcon />
          Board
        </a>
      </nav>
      <p className="side-label">Pipeline</p>
      <ol className="pipeline">
        {stages.map((stage, i) => {
          const n = shipments.filter((s) => s.status === stage.id).length;
          return (
            <li key={stage.id}>
              <button type="button" className={`pipe-item${n ? "" : " zero"}`} onClick={() => onStage(stage.id)}>
                <span className="pipe-step">{String(i + 1).padStart(2, "0")}</span>
                <span className="pipe-name">{stage.name}</span>
                <span className="pipe-count">{n}</span>
              </button>
            </li>
          );
        })}
      </ol>
      <details className="side-user">
        <summary>
          <span className="avatar" aria-hidden="true">CO</span>
          <span className="who">
            <strong>Customs ops</strong>
            <span>Hexalog Global</span>
          </span>
        </summary>
        <div className="side-menu">
          <button type="button" onClick={onReset}>
            Reset demo data
          </button>
        </div>
      </details>
    </aside>
  );
}
