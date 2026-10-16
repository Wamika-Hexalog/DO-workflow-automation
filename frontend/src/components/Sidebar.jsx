import mark from "../assets/hexalog-mark.svg";
import { BoardIcon, HelpIcon, WalletIcon } from "./Icons.jsx";
import { stageTone } from "./Route.jsx";

export default function Sidebar({ page, onPage, stages, shipments, onStage, onReset, user, onSignOut }) {
  return (
    <aside className="sidebar" aria-label="Workspace">
      <div className="side-brand">
        <img className="side-mark" src={mark} alt="Hexalog" width="28" height="32" />
        <div>
          <strong>DO Workflow</strong>
          <span>Hexalog customs</span>
        </div>
      </div>
      <nav className="side-nav" aria-label="Main">
        {[
          ["board", "Board", BoardIcon],
          ["finance", "Finance", WalletIcon],
          ["faq", "FAQ", HelpIcon],
        ].map(([id, label, LinkIcon]) => (
          <button
            key={id}
            type="button"
            className={`side-link${page === id ? " active" : ""}`}
            aria-current={page === id ? "page" : undefined}
            onClick={() => onPage(id)}
          >
            <LinkIcon />
            {label}
          </button>
        ))}
      </nav>
      <p className="side-label">Pipeline</p>
      <ol className="pipeline">
        {stages.map((stage, i) => {
          const n = shipments.filter((s) => s.status === stage.id).length;
          return (
            <li key={stage.id} style={stageTone(i)}>
              <button type="button" className={`pipe-item${n ? "" : " zero"}`} onClick={() => onStage(stage.id)}>
                <span className="pipe-dot" aria-hidden="true" />
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
          <span className="avatar" aria-hidden="true">{user.slice(0, 2).toUpperCase()}</span>
          <span className="who">
            <strong>{user}</strong>
            <span>Hexalog Global</span>
          </span>
        </summary>
        <div className="side-menu">
          <button type="button" onClick={onReset}>
            Reset demo data
          </button>
          <button type="button" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </details>
    </aside>
  );
}
