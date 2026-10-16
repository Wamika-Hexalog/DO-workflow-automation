import { useEffect, useRef, useState } from "react";
import { slotName } from "../lib/folder.js";
import { fmtMoment, plural } from "../lib/format.js";
import mascot from "../assets/mascot-ready.webp";
import { AlertIcon, ChevronLeftIcon, ChevronRightIcon, ExternalIcon } from "./Icons.jsx";

// A document counts as seen until its content changes: a new file, or for a
// letter the app writes, a change to the values it prints. The bonds never change.
function seenKey(slot, shipment) {
  if (slot.file) return `${slot.id}:${slot.file.id}`;
  if (slot.source === "fixed") return slot.id;
  return `${slot.id}:${JSON.stringify(shipment.values)}`;
}

let pdfjs;
// pdf.js is large, so it loads the first time a page is drawn.
function loadPdfjs() {
  pdfjs ??= Promise.all([import("pdfjs-dist"), import("pdfjs-dist/build/pdf.worker.min.mjs?url")]).then(
    ([lib, worker]) => {
      lib.GlobalWorkerOptions.workerSrc = worker.default;
      return lib;
    },
  );
  return pdfjs;
}

// One document's stamped pages, drawn when they come near the screen.
function DocPages({ url, title }) {
  const ref = useRef(null);
  const [state, setState] = useState("idle");

  useEffect(() => {
    const box = ref.current;
    let cancelled = false;
    let task;
    const io = new IntersectionObserver(
      async ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        setState("loading");
        try {
          const lib = await loadPdfjs();
          task = lib.getDocument(url);
          const pdf = await task.promise;
          const width = Math.min(box.clientWidth, 900);
          const ratio = window.devicePixelRatio || 1;
          for (let n = 1; n <= pdf.numPages; n++) {
            const page = await pdf.getPage(n);
            const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width });
            const canvas = document.createElement("canvas");
            canvas.className = "doc-page";
            canvas.width = Math.floor(viewport.width * ratio);
            canvas.height = Math.floor(viewport.height * ratio);
            canvas.style.width = `${viewport.width}px`;
            canvas.setAttribute("role", "img");
            canvas.setAttribute("aria-label", `${title}, page ${n} of ${pdf.numPages}`);
            await page.render({
              canvasContext: canvas.getContext("2d"),
              viewport,
              transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
            }).promise;
            if (cancelled) return;
            box.appendChild(canvas);
          }
          setState("done");
        } catch {
          if (!cancelled) setState("error");
        }
      },
      { rootMargin: "1200px 0px" },
    );
    io.observe(box);
    return () => {
      cancelled = true;
      io.disconnect();
      task?.destroy();
      box.replaceChildren();
    };
  }, [url, title]);

  return (
    <>
      <div ref={ref} className={`doc-pages ${state}`} />
      {state === "loading" && <p className="doc-msg">Loading the stamped pages…</p>}
      {state === "error" && (
        <p className="doc-msg">
          The preview can't be drawn here.{" "}
          <a className="linkbtn" href={url} target="_blank" rel="noreferrer">
            Open the PDF
          </a>
        </p>
      )}
    </>
  );
}

// After the build: the DO request on ODeX, waiting for ops, running, failed or done.
function DoRequest({ shipment, automated, onApprove, approving }) {
  const task = shipment.tasks.find((t) => t.key === "odex_do_request");
  if (!task || task.status === "pending") return null;
  const requested = shipment.status === "do_requested" || shipment.status === "edo_received";
  if (task.status === "awaiting_approval") {
    return (
      <div className="notice">
        <strong>DO request on ODeX.</strong> {task.note}
        <div className="slot-actions">
          <button type="button" className="btn btn-primary" disabled={approving} onClick={onApprove}>
            {approving ? "Starting…" : automated ? "Approve and raise it on ODeX" : "I've raised it on ODeX"}
          </button>
        </div>
      </div>
    );
  }
  if (task.status === "failed") {
    return (
      <div className="notice warn">
        <strong>The DO request didn't go through.</strong> {task.note}
        <div className="slot-actions">
          <button type="button" className="btn" disabled={approving} onClick={onApprove}>
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (!requested) {
    return (
      <div className="notice">
        <strong>{automated ? "Raising the DO request on ODeX…" : "Recording the DO request…"}</strong>
      </div>
    );
  }
  return (
    <div className="notice">
      <strong>DO request raised.</strong> {task.note}
    </div>
  );
}

// `seen` lives in the shipment view, so stepping out to fix a field keeps the progress.
export default function Review({ shipment, seen, setSeen, onBuild, onDownload, goTo, building, automated, onApprove, approving }) {
  const [current, setCurrent] = useState(0);
  const [confirmed, setConfirmed] = useState(false);

  const slots = shipment.slots;
  const slot = slots[Math.min(current, slots.length - 1)];
  const currentKey = seenKey(slot, shipment);

  // A document counts as looked at once it stays on screen, not when the scroll passes it.
  useEffect(() => {
    if (!slot.preview_url) return undefined;
    const timer = setTimeout(
      () => setSeen((prev) => (prev.has(currentKey) ? prev : new Set(prev).add(currentKey))),
      800,
    );
    return () => clearTimeout(timer);
  }, [currentKey, slot.preview_url]);
  const isSeen = (s) => seen.has(seenKey(s, shipment));
  const unseen = slots.filter((s) => !isSeen(s)).length;
  const open = slots.filter((s) => s.status !== "ok").length;
  const folder = shipment.folder;

  const srcOf = (s) => s.preview_url && `${s.preview_url}?v=${encodeURIComponent(shipment.updated_at)}`;
  const src = srcOf(slot);

  // Every document in one scroll; the one at the top of the view is the current one.
  const scroller = useRef(null);
  const show = (i) => {
    setCurrent(i);
    // Instant: a smooth jump draws every document on the way, and their growing pages move the target.
    scroller.current?.querySelector(`[data-doc="${i}"]`)?.scrollIntoView({ block: "start", behavior: "instant" });
  };
  const onScroll = (e) => {
    const box = e.currentTarget;
    const docs = [...box.querySelectorAll("[data-doc]")];
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    const top = box.getBoundingClientRect().top + 48;
    const i = atEnd ? docs.length - 1 : docs.filter((d) => d.getBoundingClientRect().top <= top).length - 1;
    if (i >= 0 && i !== current) setCurrent(i);
  };

  return (
    <section className="review-page">
      <header className="review-head">
        <h3 className="section-title">Review and build</h3>
        {open > 0 ? (
          <div className="banner warn compact">
            <AlertIcon size={16} />
            <p>
              <strong>{plural(open, "document")} need attention</strong> before the folder can be built.
            </p>
            <button type="button" className="btn btn-sm" onClick={() => goTo("documents")}>
              View documents
            </button>
          </div>
        ) : (
          <div className="banner ready compact">
            <img src={mascot} alt="" width="290" height="480" />
            <p>
              <strong>All {slots.length} documents are ready.</strong>{" "}
              {unseen > 0
                ? `Look through the ${plural(unseen, "document")} not seen yet, then build the folder.`
                : "Every document has been looked at. Confirm and build the folder."}
            </p>
          </div>
        )}
      </header>

      {folder && (
        <div className={`notice${folder.stale ? " warn" : ""}`}>
          {folder.stale ? (
            <>
              <strong>The folder is out of date.</strong> Something changed after it was built on{" "}
              {fmtMoment(folder.built_at)}. Review and build it again before uploading it.
            </>
          ) : (
            <>
              <strong>Folder built {fmtMoment(folder.built_at)}.</strong>{" "}
              <a className="linkbtn" href={folder.url} download={folder.filename} onClick={onDownload}>
                Download the PDF
              </a>
            </>
          )}
        </div>
      )}

      {shipment.building && (
        <div className="notice">
          <strong>Building the DO folder…</strong> The PDF downloads when it's ready.
        </div>
      )}

      {folder && !folder.stale && (
        <DoRequest shipment={shipment} automated={automated} onApprove={onApprove} approving={approving} />
      )}

      <div className="review">
        <div className="review-side">
          <ol className="review-list">
            {slots.map((s, i) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={`review-item${i === current ? " active" : ""}`}
                  aria-current={i === current ? "true" : undefined}
                  onClick={() => show(i)}
                >
                  <span className="num">{i + 1}</span>
                  <span className="label" title={slotName(s)}>
                    {slotName(s)}
                  </span>
                  <span className={`tick ${s.status !== "ok" ? s.status : isSeen(s) ? "seen" : ""}`}>
                    {s.status === "missing" ? "Missing" : s.status === "flagged" ? "Check" : isSeen(s) ? "Seen" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="build">
            <label className="check">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={unseen > 0}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>
                I have looked at all {slots.length} documents and they belong to job {shipment.job_no}, MBL{" "}
                {shipment.values.mbl_no || "not set"}.
              </span>
            </label>
            <p className="hint">
              {unseen > 0 ? `${plural(unseen, "document")} not looked at yet.` : "Every document has been looked at."}
            </p>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!confirmed || unseen > 0 || open > 0 || building || shipment.building}
              onClick={async () => {
                if (await onBuild()) setConfirmed(false);
              }}
            >
              {building ? "Building…" : folder ? "Build the DO folder again" : "Build the DO folder"}
            </button>
          </div>
        </div>
        <div className="viewer">
          <div className="viewer-bar">
            <button
              type="button"
              className="btn btn-sm btn-icon"
              aria-label="Previous document"
              disabled={current === 0}
              onClick={() => show(current - 1)}
            >
              <ChevronLeftIcon />
            </button>
            <button
              type="button"
              className="btn btn-sm btn-icon"
              aria-label="Next document"
              disabled={current === slots.length - 1}
              onClick={() => show(current + 1)}
            >
              <ChevronRightIcon />
            </button>
            <span className="viewer-title">{slotName(slot)}</span>
            <span className="viewer-count">
              {current + 1} of {slots.length}
            </span>
            {src && (
              <a className="btn btn-sm" href={src} target="_blank" rel="noreferrer">
                <ExternalIcon size={14} />
                Open
              </a>
            )}
          </div>
          <div className="viewer-scroll" ref={scroller} onScroll={onScroll}>
            {slots.map((s, i) => (
              <article key={s.id} className="doc" data-doc={i} aria-label={slotName(s)}>
                <h4 className="doc-head">
                  <span className="num">{i + 1}</span>
                  <span className="label">{slotName(s)}</span>
                  {s.status !== "ok" && (
                    <span className={`badge ${s.status === "missing" ? "missing" : "error"}`}>
                      {s.status === "missing" ? "Missing" : "Check"}
                    </span>
                  )}
                </h4>
                {s.preview_url ? (
                  <DocPages url={srcOf(s)} title={slotName(s)} />
                ) : (
                  <div className="viewer-empty">
                    <p>{s.flags[0] ? s.flags[0].message : "This document isn't ready yet."}</p>
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
