import { PlusIcon, SearchIcon } from "./Icons.jsx";

export default function TopBar({ query, setQuery, shipments, onNew }) {
  const toCheck = shipments.filter((s) => s.slots.some((slot) => slot.status === "flagged")).length;
  const ready = shipments.filter((s) => s.status === "folder_ready").length;
  return (
    <header className="page-head">
      <div className="page-row">
        <div className="page-title">
          <p className="eyebrow">Import delivery orders</p>
          <h1>Delivery orders</h1>
          <p className="page-sub">
            {shipments.length} active · <span className={toCheck ? "attn" : undefined}>{toCheck} to check</span> · {ready}{" "}
            folder ready
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={onNew}>
          <PlusIcon />
          New job
        </button>
      </div>
      <label className="search">
        <SearchIcon />
        <input
          type="search"
          placeholder="Search MBL, HBL, container, job or vessel"
          aria-label="Search shipments"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
    </header>
  );
}
