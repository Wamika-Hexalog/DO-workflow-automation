// Documents ready, as text and a bar; the text alone carries the meaning.
export default function Progress({ ok, total }) {
  return (
    <div className={`progress${total && ok === total ? " done" : ""}`}>
      <div className="progress-head">
        <span>Documents</span>
        <span className="progress-count">
          {ok} of {total} ready
        </span>
      </div>
      <div className="bar" aria-hidden="true">
        <span style={{ width: `${total ? (100 * ok) / total : 0}%` }} />
      </div>
    </div>
  );
}
