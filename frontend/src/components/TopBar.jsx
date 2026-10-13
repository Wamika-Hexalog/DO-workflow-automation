export default function TopBar({ clientName, query, setQuery, showSearch, onNew, onReset }) {
  return (
    <header className="topbar">
      <div className="brand">
        <h1>DO Workflow</h1>
        {clientName && <span>{clientName} sea imports</span>}
      </div>
      {showSearch && (
        <div className="search">
          <input
            type="search"
            className="input"
            placeholder="Search BL, container, job or vessel"
            aria-label="Search shipments"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      )}
      <div className="actions">
        <button type="button" className="btn btn-quiet" onClick={onReset}>
          Reset demo data
        </button>
        <button type="button" className="btn btn-primary" onClick={onNew}>
          New shipment
        </button>
      </div>
    </header>
  );
}
