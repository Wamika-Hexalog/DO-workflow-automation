import { useEffect } from "react";

const SHEETS = 4;

// The clip is gitignored (third-party audio): without it, play() rejects and the printer stays silent.
function printerSound() {
  const audio = new Audio("/printer.mp3");
  audio.loop = true;
  audio.play().catch(() => {});
  return () => audio.pause();
}

// Shown while the build workflow makes the DO folder: the documents go in, one PDF comes out.
export default function Printer() {
  useEffect(() => printerSound(), []);

  return (
    <div className="celebrate" role="status" aria-live="polite">
      <div className="celebrate-card">
        <div className="celebrate-scene" aria-hidden="true">
          {Array.from({ length: SHEETS }, (_, i) => (
            <span key={i} className="sheet" style={{ "--i": i }} />
          ))}
          <div className="printer">
            <span className="printer-light" />
          </div>
          <div className="printer-tray">
            <span className="printer-out">PDF</span>
          </div>
        </div>
        <h2>Building the DO folder…</h2>
        <p>Putting every document in order and stamping it into one PDF.</p>
      </div>
    </div>
  );
}
