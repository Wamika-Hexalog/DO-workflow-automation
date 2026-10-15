import { useEffect } from "react";
import mascot from "../assets/mascot-ready.webp";
import { CloseIcon } from "./Icons.jsx";

const CONTAINERS = [
  [70, 78, 1], [108, 78, 2], [146, 78, 3], [184, 78, 4],
  [89, 56, 5], [127, 56, 6], [165, 56, 1],
];
const CONFETTI = ["#834DFB", "#FFD728", "#B2BC85", "#A783FC", "#907862", "#FFD728", "#834DFB", "#B2BC85"];

// Shown when the DO folder PDF starts to download: the Hexanaut sails off with the folder.
export default function Celebrate({ mbl, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    const timer = setTimeout(onClose, 9000);
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div className="celebrate" role="dialog" aria-modal="true" aria-labelledby="celebrate-title" onClick={onClose}>
      <div className="celebrate-card" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="celebrate-close" aria-label="Close" onClick={onClose}>
          <CloseIcon size={16} />
        </button>
        <div className="celebrate-scene" aria-hidden="true">
          {CONFETTI.map((c, i) => (
            <span key={i} className="confetti" style={{ "--c": c, "--i": i }} />
          ))}
          <div className="celebrate-ship">
            <img src={mascot} alt="" width="290" height="480" />
            <svg viewBox="0 0 320 150" width="320" height="150">
              {CONTAINERS.map(([x, y, s]) => (
                <g key={`${x}-${y}`}>
                  <rect x={x} y={y} width="36" height="21" rx="2" fill={`var(--stage-${s})`} />
                  <path d={`M${x + 9} ${y + 4}v13M${x + 18} ${y + 4}v13M${x + 27} ${y + 4}v13`} stroke="rgba(255,255,255,.35)" strokeWidth="1.5" />
                </g>
              ))}
              <rect x="236" y="54" width="40" height="45" rx="3" fill="#FFFFFF" stroke="#EDEAF7" />
              <rect x="243" y="62" width="8" height="7" rx="1" fill="#26124D" />
              <rect x="256" y="62" width="8" height="7" rx="1" fill="#26124D" />
              <rect x="250" y="36" width="12" height="18" rx="2" fill="#834DFB" />
              <path d="M8 99h304l-30 43H40z" fill="#26124D" />
              <path d="M14 108h292l-4 6H18z" fill="#834DFB" />
            </svg>
          </div>
          <svg className="celebrate-sea" viewBox="0 0 640 40" preserveAspectRatio="none">
            <path d="M0 14q40-14 80 0t80 0 80 0 80 0 80 0 80 0 80 0 80 0V40H0z" fill="#CFC2FB" />
            <path d="M0 22q40-12 80 0t80 0 80 0 80 0 80 0 80 0 80 0 80 0V40H0z" fill="#A783FC" />
          </svg>
        </div>
        <h2 id="celebrate-title">Yay, you filed the DO!</h2>
        <p>
          The DO folder{mbl ? ` for MBL ${mbl}` : ""} is downloading. The Hexanaut is shipping it off.
        </p>
        <button type="button" className="btn btn-primary" onClick={onClose}>
          Back to work
        </button>
      </div>
    </div>
  );
}
