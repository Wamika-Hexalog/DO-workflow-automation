import { useState } from "react";
import mascot from "../assets/mascot-ready.webp";
import { CloseIcon, HelpIcon } from "./Icons.jsx";

// A help box in the corner. A click brings the mascot up to wave and point to the
// FAQ; a second click, or closing the bubble, folds it back below the edge.
export default function Helper({ onOpenFaq }) {
  const [out, setOut] = useState(false);

  return (
    <aside className={`helper${out ? " out" : ""}`} aria-label="Help">
      {out && (
        <div className="helper-bubble" role="status">
          <button type="button" className="helper-close" aria-label="Close" onClick={() => setOut(false)}>
            <CloseIcon size={14} />
          </button>
          <strong>Do you have a question?</strong>
          <p>Look at our FAQ.</p>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={() => {
              setOut(false);
              onOpenFaq();
            }}
          >
            Open the FAQ
          </button>
        </div>
      )}
      <div className="helper-mascot" aria-hidden="true">
        <img src={mascot} alt="" width="290" height="480" />
      </div>
      <button
        type="button"
        className="helper-box"
        aria-label={out ? "Close the help" : "Questions? Open the help"}
        aria-expanded={out}
        onClick={() => setOut(!out)}
      >
        {out ? <CloseIcon size={18} /> : <HelpIcon size={20} />}
      </button>
    </aside>
  );
}
