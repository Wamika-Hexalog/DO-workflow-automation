import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAutosave } from "../hooks/useAutosave.js";
import { profileDone, requiredFieldsDone, SECTIONS, sectionTitle } from "../lib/folder.js";
import { fmtMoment, plural, splitLines } from "../lib/format.js";
import DocStrip from "./DocStrip.jsx";
import FolderList from "./FolderList.jsx";
import { FixedForm, ShipmentForm } from "./Forms.jsx";
import History from "./History.jsx";
import Review from "./Review.jsx";

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

function NavItem({ label, active, done, state, warn, onClick }) {
  return (
    <button
      type="button"
      className={`nav-item${active ? " active" : ""}`}
      aria-current={active ? "true" : undefined}
      onClick={onClick}
    >
      <span className="label">{label}</span>
      <span className={`state${done ? " done" : ""}${warn ? " warn" : ""}`}>
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
  folder,
  profile,
  shipment,
  section,
  setSection,
  focusKey,
  setFocusKey,
  focusSlot,
  setFocusSlot,
  onBack,
  onMove,
  onBuild,
  building,
  onApproveDoRequest,
  approving,
  onShipmentSaved,
  onProfileSaved,
  say,
}) {
  const detailRef = useRef(null);
  const [reviewSeen, setReviewSeen] = useState(() => new Set());
  const mainRef = useRef(null);
  const shipmentEdits = useAutosave(async (changes) => onShipmentSaved(await api.saveValues(shipment.id, changes)));
  const profileEdits = useAutosave(async (changes) => onProfileSaved(await api.saveProfile(changes)));
  const { flush: flushShipment } = shipmentEdits;
  const { flush: flushProfile } = profileEdits;

  const values = { ...shipment.values, ...shipmentEdits.edits };
  const profileValues = { ...profile.values, ...profileEdits.edits };

  const saveAll = useCallback(async () => {
    const results = await Promise.all([flushShipment(), flushProfile()]);
    if (results.every(Boolean)) return true;
    say("Some changes are not saved yet. Check that the API is running, then try again.");
    return false;
  }, [flushShipment, flushProfile, say]);

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

  // Pending edits change the letters, so they are saved before leaving the forms.
  const goTo = async (target, key) => {
    if (target !== "shipment" && target !== "fixed") await saveAll();
    setSection(target);
    if (key) setFocusKey(key);
  };

  const pickSlot = (slotId) => {
    goTo("documents");
    setFocusSlot(slotId);
  };

  const details = requiredFieldsDone(folder, shipment);
  const hbls = splitLines(values.hbl_nos);
  const built = shipment.folder;
  const vessel = [values.vessel, values.voyage].filter(Boolean).join(" ");

  let content;
  if (section === "fixed") {
    content = <FixedForm folder={folder} values={profileValues} setValue={profileEdits.setValue} />;
  } else if (section === "documents") {
    content = (
      <FolderList
        folder={folder}
        shipment={shipment}
        focusSlot={focusSlot}
        setFocusSlot={setFocusSlot}
        onShipment={onShipmentSaved}
        say={say}
        goTo={goTo}
      />
    );
  } else if (section === "review") {
    content = (
      <Review
        shipment={shipment}
        seen={reviewSeen}
        setSeen={setReviewSeen}
        onBuild={() => onBuild(shipment.id)}
        building={building}
        automated={Boolean(folder.odex && folder.odex.automated)}
        onApprove={() => onApproveDoRequest(shipment.id)}
        approving={approving}
        goTo={goTo}
      />
    );
  } else if (section === "history") {
    content = <History shipment={shipment} stageNames={folder.stageNames} />;
  } else {
    content = (
      <ShipmentForm
        folder={folder}
        values={values}
        setValue={shipmentEdits.setValue}
        issues={shipment.issues}
        pendingKeys={new Set(Object.keys(shipmentEdits.edits))}
      />
    );
  }

  return (
    <div className="detail" ref={detailRef}>
      <header className="d-head">
        <div className="d-bar">
          <button type="button" className="btn btn-quiet back" onClick={leave}>
            Back to board
          </button>
          <SaveState statuses={[shipmentEdits.status, profileEdits.status]} />
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
              {folder.stages.map((stage) => (
                <option key={stage.id} value={stage.id}>
                  {stage.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn btn-primary" onClick={() => goTo("review")}>
            Review and build
          </button>
        </div>
        <p className="eyebrow">
          {shipment.job_no} · {folder.stageNames[shipment.status]}
        </p>
        <h2 className={`d-title${values.mbl_no ? "" : " muted"}`}>{values.mbl_no || "New job"}</h2>
        <div className="d-meta">
          <span><em>Vessel</em>{vessel || "Not set"}</span>
          <span><em>HBL</em><span className={hbls.length ? "mono" : undefined}>{hbls.length ? hbls.join(", ") : "Not set"}</span></span>
          {shipment.owner && <span><em>Owner</em>{shipment.owner}</span>}
        </div>
        <div className="d-progress">
          <DocStrip large slots={shipment.slots} onPick={pickSlot} />
          <p>
            <strong>
              {shipment.docs_ok} of {shipment.docs_total} documents ready.
            </strong>{" "}
            {built && !built.stale && (
              <>
                Folder built {fmtMoment(built.built_at)}.{" "}
                <a className="linkbtn" href={built.url} download={built.filename}>
                  Download PDF
                </a>
              </>
            )}
            {built && built.stale && "The built folder is out of date."}
            {!built && (shipment.ready ? "Ready to review and build." : "The folder can be built once all are ready.")}
          </p>
        </div>
      </header>
      <div className="d-body">
        <nav className="nav" aria-label="Sections">
          <NavItem
            label="Shipment details"
            active={section === "shipment"}
            done={details.done === details.total}
            state={`${details.done}/${details.total}`}
            onClick={() => goTo("shipment")}
          />
          <NavItem
            label="Fixed details"
            active={section === "fixed"}
            done={profileDone(folder, shipment)}
            state="Check"
            onClick={() => goTo("fixed")}
          />
          <p className="nav-label">DO folder</p>
          <NavItem
            label="Documents"
            active={section === "documents"}
            done={shipment.ready}
            state={`${shipment.docs_ok}/${shipment.docs_total}`}
            onClick={() => goTo("documents")}
          />
          <NavItem
            label="Review and build"
            active={section === "review"}
            done={built && !built.stale}
            warn={built && built.stale}
            state={built ? "Out of date" : ""}
            onClick={() => goTo("review")}
          />
          <NavItem label="History" active={section === "history"} state="" onClick={() => goTo("history")} />
          <p className="nav-note">
            {plural(shipment.docs_total, "document")} for MBL {values.mbl_no || "not set"}
            {hbls.length > 1 ? `, with HBL documents once for each of ${hbls.length} house bills.` : "."}
          </p>
        </nav>
        <div className="main" ref={mainRef}>
          <div className={`main-inner${section === "review" ? " wide" : ""}`}>
            <select
              className="input mobile-nav"
              aria-label="Section"
              value={section}
              onChange={(e) => goTo(e.target.value)}
            >
              {SECTIONS.map((id) => (
                <option key={id} value={id}>
                  {sectionTitle(id)}
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
