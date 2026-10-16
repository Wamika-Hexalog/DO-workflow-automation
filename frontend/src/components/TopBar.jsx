import mascot from "../assets/mascot-search.webp";
import { PlusIcon, SearchIcon } from "./Icons.jsx";
import { FleetRoute } from "./Route.jsx";

export default function TopBar({ stages, query, setQuery, shipments, onNew, onStage }) {
  const toCheck = shipments.filter((s) => s.slots.some((slot) => slot.status === "flagged")).length;
  const ready = shipments.filter((s) => s.status === "folder_ready").length;
  return (
    <header className="hero">
      <div className="hero-text">
        <h1>Delivery orders</h1>
        <p className="page-sub">
          {shipments.length} active · <span className={toCheck ? "attn" : undefined}>{toCheck} to check</span> · {ready}{" "}
          folder ready
        </p>
        <div className="hero-actions">
          <label className="search">
            <SearchIcon />
            <input
              type="search"
              placeholder="Find a shipment by MBL, HBL or container"
              aria-label="Search shipments"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button type="button" className="btn btn-primary" onClick={onNew}>
            <PlusIcon />
            New job
          </button>
        </div>
      </div>
      <FleetRoute stages={stages} shipments={shipments} onStage={onStage} />
      <img className="hero-mascot" src={mascot} alt="" width="267" height="480" />
    </header>
  );
}
