import { useCallback, useEffect, useRef } from "react";
import { api } from "../api.js";
import { useAutosave } from "../hooks/useAutosave.js";
import { sectionTitle } from "../lib/doSet.js";
import { fmtMoment, fmtShort, ISO_DATE } from "../lib/format.js";
import PageStrip from "./PageStrip.jsx";
import { FixedForm, PageForm, ShipmentForm } from "./Sections.jsx";

function Check() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path
        d="M3 8.5l3.2 3.2L13 5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function NavItem({ num, label, active, done, state, onClick }) {
  return (
    <button
      type="button"
      className={`nav-item${active ? " active" : ""}`}
      aria-current={active ? "true" : undefined}
      onClick={onClick}
    >
      {num != null && <span className="num">{num}</span>}
      <span className="label">{label}</span>
      <span className={`state${done ? " done" : ""}`}>
        {done ? (
          <>
            <Check />
            <span className="sr">Complete</span>
          </>
        ) : (
          state
        )}
      </span>
    </button>
  );
}

function SaveState({ statuses }) {
  const error = statuses.find((s) => s.state === "error");
  if (error) {
    return (
      <span className="save-state error" role="status">
        Not saved. {error.message}
      </span>
    );
  }
  if (statuses.some((s) => s.state === "pending" || s.state === "saving")) {
    return (
      <span className="save-state" role="status">
        Saving…
      </span>
    );
  }
  if (statuses.some((s) => s.state === "saved")) {
    return (
      <span className="save-state" role="status">
        All changes saved
      </span>
    );
  }
  return null;
}

export default function Detail({
  doSet,
  client,
  shipment,
  section,
  setSection,
  focusKey,
  setFocusKey,
  onBack,
  onMove,
  onGenerate,
  onShipmentSaved,
  onClientSaved,
  say,
}) {
  const detailRef = useRef(null);
  const mainRef = useRef(null);
  const shipmentEdits = useAutosave(async (changes) => onShipmentSaved(await api.saveValues(shipment.id, changes)));
  const clientEdits = useAutosave(async (changes) => onClientSaved(await api.saveClient(changes)));
  const { flush: flushShipment } = shipmentEdits;
  const { flush: flushClient } = clientEdits;

  const values = { ...shipment.values, ...shipmentEdits.edits };
  const clientValues = { ...client.values, ...clientEdits.edits };
  const complete = shipment.pages_done === shipment.pages_total;
  const clientName = client.name;

  const saveAll = useCallback(async () => {
    const results = await Promise.all([flushShipment(), flushClient()]);
    if (results.every(Boolean)) return true;
    say("Some changes are not saved yet. Check that the API is running, then try again.");
    return false;
  }, [flushShipment, flushClient, say]);

  const leave = useCallback(async () => {
    if (await saveAll()) onBack();
  }, [saveAll, onBack]);

  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
    if (detailRef.current) detailRef.current.scrollTop = 0;
  }, [section]);

  useEffect(() => {
    if (!focusKey) return;
    const el = document.getElementById(`f-${focusKey}`);
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.focus({ preventScroll: true });
    }
    setFocusKey(null);
  }, [focusKey, section, setFocusKey]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      const tag = e.target && e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      leave();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [leave]);

  const goTo = (target, key) => {
    setSection(target);
    if (key) setFocusKey(key);
  };

  const pendingKeys = new Set(Object.keys(shipmentEdits.edits));
  const detailsFilled = doSet.shipmentFields.filter((f) => shipment.field_status[f.key] === "ok").length;
  const fixedDone = doSet.client_fields.every((f) => shipment.field_status[f.key] === "ok");

  let content;
  if (section === "fixed") {
    content = (
      <FixedForm doSet={doSet} clientName={clientName} values={clientValues} setValue={clientEdits.setValue} goTo={goTo} />
    );
  } else if (section === "shipment") {
    content = (
      <ShipmentForm
        doSet={doSet}
        clientName={clientName}
        values={values}
        setValue={shipmentEdits.setValue}
        issues={shipment.issues}
        pendingKeys={pendingKeys}
        goTo={goTo}
      />
    );
  } else {
    const index = doSet.pages.findIndex((p) => p.id === section);
    content = (
      <PageForm
        key={section}
        doSet={doSet}
        clientName={clientName}
        page={doSet.pages[index]}
        index={index}
        values={values}
        clientValues={clientValues}
        progress={shipment.pages[index]}
        fieldStatus={shipment.field_status}
        setValue={shipmentEdits.setValue}
        goTo={goTo}
      />
    );
  }

  const vessel = [values.vessel, values.voyage].filter(Boolean).join(" ");
  return (
    <div className="detail" ref={detailRef}>
      <header className="d-head">
        <div className="d-bar">
          <button type="button" className="btn btn-quiet back" onClick={leave}>
            Back to board
          </button>
          <SaveState statuses={[shipmentEdits.status, clientEdits.status]} />
          <span className="spacer" />
          <label className="stage-pick">
            <span>Stage</span>
            <select
              className="input"
              value={shipment.status}
              onChange={async (e) => {
                const to = e.target.value;
                if (await saveAll()) onMove(shipment.id, to);
              }}
            >
              {doSet.stages.map((stage) => (
                <option key={stage.id} value={stage.id}>
                  {stage.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!complete}
            onClick={async () => {
              if (await saveAll()) onGenerate(shipment.id);
            }}
          >
            Generate DO set
          </button>
        </div>
        <h2 className={`d-title${values.bl_no ? "" : " muted"}`}>{values.bl_no || "New shipment"}</h2>
        <div className="d-meta">
          <span><em>Job</em>{shipment.job_no}</span>
          <span><em>Line</em>{values.shipping_line || "Not set"}</span>
          <span><em>Vessel</em>{vessel || "Not set"}</span>
          <span><em>ETA</em>{values.eta && ISO_DATE.test(values.eta) ? fmtShort(values.eta) : "Not set"}</span>
          {shipment.owner && <span><em>Owner</em>{shipment.owner}</span>}
        </div>
        <div className="d-progress">
          <PageStrip
            large
            pages={shipment.pages}
            titles={doSet.pageTitles}
            current={section}
            onPick={(id) => goTo(id)}
          />
          <p>
            <strong>
              {shipment.pages_done} of {shipment.pages_total} pages complete.
            </strong>{" "}
            {complete ? "Ready to generate the DO set." : "Generate unlocks when every page is complete."}
            {shipment.document && (
              <>
                {" "}Last generated {fmtMoment(shipment.document.generated_at)}.{" "}
                <a className="linkbtn" href={shipment.document.url} download={shipment.document.filename}>
                  Download
                </a>
              </>
            )}
          </p>
        </div>
      </header>
      <div className="d-body">
        <nav className="nav" aria-label="Sections">
          <NavItem
            label={`Fixed for ${clientName}`}
            active={section === "fixed"}
            done={fixedDone}
            state="Check"
            onClick={() => goTo("fixed")}
          />
          <NavItem
            label="Shipment details"
            active={section === "shipment"}
            done={detailsFilled === doSet.shipmentFields.length}
            state={`${detailsFilled}/${doSet.shipmentFields.length}`}
            onClick={() => goTo("shipment")}
          />
          <p className="nav-label">Pages in the DO set</p>
          <p className="nav-note">Placeholder names and fields until the real set arrives.</p>
          {doSet.pages.map((page, i) => (
            <NavItem
              key={page.id}
              num={i + 1}
              label={page.title}
              active={section === page.id}
              done={shipment.pages[i].status === "done"}
              state={`${shipment.pages[i].filled}/${shipment.pages[i].total}`}
              onClick={() => goTo(page.id)}
            />
          ))}
        </nav>
        <div className="main" ref={mainRef}>
          <div className="main-inner">
            <select
              className="input mobile-nav"
              aria-label="Section"
              value={section}
              onChange={(e) => goTo(e.target.value)}
            >
              {doSet.sections.map((id, i) => (
                <option key={id} value={id}>
                  {i > 1 ? `${i - 1}. ` : ""}
                  {sectionTitle(doSet, id, clientName)}
                </option>
              ))}
            </select>
            {content}
          </div>
        </div>
      </div>
    </div>
  );
}
