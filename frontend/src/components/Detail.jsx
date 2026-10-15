import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAutosave } from "../hooks/useAutosave.js";
import { profileDone, requiredFieldsDone, SECTIONS, sectionTitle } from "../lib/folder.js";
import { fmtMoment, initials, plural, splitLines } from "../lib/format.js";
import { ArrowLeftIcon, CheckIcon, FileIcon, ShipIcon } from "./Icons.jsx";
import Progress from "./Progress.jsx";
import FolderList from "./FolderList.jsx";
import { FixedForm, ShipmentForm } from "./Forms.jsx";
import History from "./History.jsx";
import Review from "./Review.jsx";
import { Route } from "./Route.jsx";
import Stages from "./Stages.jsx";

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
            <CheckIcon size={14} />
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

  const details = requiredFieldsDone(folder, shipment);
  const hbls = splitLines(values.hbl_nos);
  const built = shipment.folder;
  const vessel = [values.vessel, values.voyage].filter(Boolean).join(" ");
  const open = shipment.slots.filter((s) => s.status !== "ok").length;
  const stageAt = Math.max(0, folder.stages.findIndex((s) => s.id === shipment.status));

  let content;
  if (section === "fixed") {
    content = (
      <FixedForm folder={folder} values={profileValues} setValue={profileEdits.setValue} status={shipment.field_status} />
    );
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
  } else if (section === "portal") {
    content = (
      <Stages
        shipment={shipment}
        stages={folder.stages}
        goTo={goTo}
        onMove={async (to) => {
          if (await saveAll()) onMove(shipment.id, to);
        }}
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
        status={shipment.field_status}
      />
    );
  }

  return (
    <div className="detail" ref={detailRef}>
      <header className={`d-head${section === "review" ? " slim" : ""}`}>
        <div className="d-bar">
          <button type="button" className="btn btn-quiet back" onClick={leave}>
            <ArrowLeftIcon />
            Delivery orders
          </button>
          <SaveState statuses={[shipmentEdits.status, profileEdits.status]} />
        </div>
        <div className="d-row">
          <div className="d-id">
            <div className="d-name">
              <h2 className={`d-title${values.mbl_no ? "" : " muted"}`}>{values.mbl_no || "New job"}</h2>
              {shipment.ready ? (
                <span className="badge ready">Ready to build</span>
              ) : (
                <span className="badge missing">{plural(open, "document")} to go</span>
              )}
            </div>
            <div className="d-chips">
              <span className="chip mono">{shipment.job_no}</span>
              <span className="chip">
                <ShipIcon size={14} />
                {vessel || "Vessel not set"}
              </span>
              <span className={`chip${hbls.length ? " mono" : ""}`}>
                <FileIcon size={14} />
                {hbls.length ? `HBL ${hbls.join(" · ")}` : "HBL not set"}
              </span>
              {shipment.owner && (
                <span className="chip">
                  <span className="avatar xs" aria-hidden="true">
                    {initials(shipment.owner)}
                  </span>
                  {shipment.owner}
                </span>
              )}
            </div>
          </div>
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
          {/* Purple only when the folder can be built; until then it is one way in, not the next step. */}
          <button type="button" className={`btn${shipment.ready ? " btn-primary" : ""}`} onClick={() => goTo("review")}>
            Review and build
          </button>
        </div>
        <div className="d-progress">
          <Progress ok={shipment.docs_ok} total={shipment.docs_total} />
          <button type="button" className="route-link" title="Open the stages" onClick={() => goTo("portal")}>
            <Route stages={folder.stages} at={stageAt} />
          </button>
          <p>
            {built && !built.stale && (
              <>
                Folder built {fmtMoment(built.built_at)}.{" "}
                <a className="linkbtn" href={built.url} download={built.filename}>
                  Download PDF
                </a>
              </>
            )}
            {built && built.stale && <span className="attn">The built folder is out of date.</span>}
            {!built && (shipment.ready ? "Ready to review and build." : "The folder can be built once all are ready.")}
          </p>
        </div>
      </header>
      <div className="d-body">
        <nav className="nav" aria-label="Sections">
          <p className="nav-label first">Job</p>
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
          <NavItem
            label="Stages"
            active={section === "portal"}
            state={`${stageAt + 1}/${folder.stages.length}`}
            onClick={() => goTo("portal")}
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
        <div className={`main${section === "review" ? " tight" : ""}`} ref={mainRef}>
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
