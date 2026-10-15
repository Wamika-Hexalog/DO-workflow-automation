import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtMoment, plural, SOURCE_LABEL } from "../lib/format.js";
import { MoreIcon } from "./Icons.jsx";

const STATUS = { ok: "Ready", flagged: "Needs a look", missing: "Missing" };

const closeMenu = (e) => e.currentTarget.closest("details").removeAttribute("open");

function SlotRow({ index, slot, shipment, folder, highlight, onShipment, say, goTo }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const generated = slot.defined_source === "generate";
  const replaced = generated && slot.source === "upload";
  // A per-HBL document can't take a file until its house bill number is entered.
  const perHbl = folder.documents.find((d) => d.key === slot.doc_key).per_hbl;
  const needsHbl = perHbl && !slot.hbl && slot.defined_source !== "fixed";
  const canAttach = slot.defined_source !== "fixed" && !needsHbl;

  // The Missing badge already says it; the flag would only repeat it.
  const flags = slot.flags.filter((f) => f.kind !== "missing");

  const run = async (label, action) => {
    setBusy(label);
    try {
      onShipment(await action());
    } catch (err) {
      say(err.message);
    } finally {
      setBusy(false);
    }
  };

  const attach = (file) => {
    if (!file) return;
    run("Uploading…", () => api.attach(shipment.id, slot.id, file));
  };

  return (
    <li
      id={`slot-${slot.id}`}
      className={`slot ${slot.status}${over ? " drop" : ""}${highlight ? " flash" : ""}`}
      onDragOver={(e) => {
        if (!canAttach || !e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        setOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setOver(false);
      }}
      onDrop={(e) => {
        if (!canAttach) return;
        e.preventDefault();
        setOver(false);
        attach(e.dataTransfer.files[0]);
      }}
    >
      <span className="slot-num">{String(index + 1).padStart(2, "0")}</span>
      <div className="slot-body">
        <h4>
          {slot.title}
          {slot.hbl && <span className="slot-hbl">{slot.hbl}</span>}
        </h4>
        <p className="slot-meta">
          {replaced ? "Signed copy uploaded in place of the app's letter" : SOURCE_LABEL[slot.defined_source]}
          {slot.stamp && !replaced && <> · stamped {folder.stamps[slot.stamp]}</>}
          {slot.file && (
            <>
              {" "}· {slot.file.name} · {plural(slot.file.pages, "page")} · added {fmtMoment(slot.file.uploaded_at)}
              {slot.file.checked_by_eye && " · checked by eye"}
            </>
          )}
        </p>
      </div>
      <span className={`badge ${slot.status}`}>{busy || STATUS[slot.status]}</span>
      <div className="slot-actions">
        {canAttach && (
          <input
            ref={input}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.webp,application/pdf,image/*"
            hidden
            onChange={(e) => {
              attach(e.target.files[0]);
              e.target.value = "";
            }}
          />
        )}
        {canAttach && !slot.file && slot.defined_source === "upload" && (
          <button type="button" className="btn btn-sm" disabled={!!busy} onClick={() => input.current.click()}>
            Attach file
          </button>
        )}
        {slot.preview_url && (
          <a className="btn btn-sm" href={`${slot.preview_url}?v=${shipment.updated_at}`} target="_blank" rel="noreferrer">
            Preview
          </a>
        )}
        {slot.file && (
          <a className="btn btn-sm" href={slot.file.original_url}>
            Original
          </a>
        )}
        {(slot.file || (canAttach && generated)) && (
          <details className="menu">
            <summary className="btn btn-sm btn-icon" aria-label={`More actions for ${slot.title}`} title="More actions">
              <MoreIcon />
            </summary>
            <div className="menu-pop" role="menu">
              {canAttach && (
                <button
                  type="button"
                  role="menuitem"
                  disabled={!!busy}
                  onClick={(e) => {
                    closeMenu(e);
                    input.current.click();
                  }}
                >
                  {slot.file ? "Replace document" : "Upload a signed copy instead"}
                </button>
              )}
              {slot.file && (
                <button
                  type="button"
                  role="menuitem"
                  className="danger"
                  disabled={!!busy}
                  onClick={(e) => {
                    closeMenu(e);
                    run("Removing…", () => api.remove(shipment.id, slot.id));
                  }}
                >
                  {replaced ? "Use the app's letter" : "Remove"}
                </button>
              )}
            </div>
          </details>
        )}
      </div>
      {(flags.length > 0 || needsHbl) && (
        <ul className="flags">
          {needsHbl && (
            <li className="flag fields">
              Add the house B/L no. first: this document is attached once for each HBL.
              <button type="button" className="linkbtn" onClick={() => goTo("shipment", "hbl_nos")}>
                Add HBL no.
              </button>
            </li>
          )}
          {flags.map((flag, i) => (
            <li key={i} className={`flag ${flag.kind}`}>
              {flag.message}
              {flag.can_check && (
                <button
                  type="button"
                  className="linkbtn"
                  disabled={!!busy}
                  onClick={() => run("Saving…", () => api.checkByEye(shipment.id, slot.id))}
                >
                  I've checked it, it's right
                </button>
              )}
              {flag.kind === "fields" && (
                <button type="button" className="linkbtn" onClick={() => goTo("shipment")}>
                  Fill in shipment details
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function FolderList({ folder, shipment, focusSlot, setFocusSlot, onShipment, say, goTo }) {
  const [flash, setFlash] = useState(null);

  useEffect(() => {
    if (!focusSlot) return undefined;
    const el = document.getElementById(`slot-${focusSlot}`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlash(focusSlot);
    setFocusSlot(null);
    const timer = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(timer);
  }, [focusSlot, setFocusSlot]);

  const missing = shipment.slots.filter((s) => s.status === "missing").length;
  const flagged = shipment.slots.filter((s) => s.status === "flagged").length;
  return (
    <section>
      <h3 className="section-title">DO folder</h3>
      <p className="section-sub">
        {shipment.docs_total} documents, in the order they go into the PDF for CMA CGM.{" "}
        {missing + flagged === 0
          ? "Everything is in place. Review and build the folder next."
          : [missing && `${missing} still missing`, flagged && `${flagged} to check`].filter(Boolean).join(", ") + "."}
      </p>
      <ol className="slots">
        {shipment.slots.map((slot, i) => (
          <SlotRow
            key={slot.id}
            index={i}
            slot={slot}
            shipment={shipment}
            folder={folder}
            highlight={flash === slot.id}
            onShipment={onShipment}
            say={say}
            goTo={goTo}
          />
        ))}
      </ol>
    </section>
  );
}
