import { splitContainers } from "../lib/format.js";

function ContainerHint({ value, issues, pending }) {
  const count = splitContainers(value).length;
  if (!count) return <span className="hint">One per line. Check digits are verified automatically.</span>;
  if (pending) return <span className="hint">Checking the check digits…</span>;
  if (issues && issues.length) {
    return (
      <ul className="problems">
        {issues.map((message, i) => (
          <li key={i}>{message}</li>
        ))}
      </ul>
    );
  }
  return <span className="hint ok">{count === 1 ? "1 container" : `${count} containers`}, check digits verified.</span>;
}

export default function Field({ field, value, onChange, uses, issues, pending }) {
  const id = `f-${field.key}`;
  if (field.type === "checkbox") {
    return (
      <div className="field wide">
        <label className="check" htmlFor={id}>
          <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
          <span>{field.label}</span>
        </label>
      </div>
    );
  }

  const className = `input${field.code ? " code" : ""}`;
  const wide = field.type === "textarea" || field.type === "containers";
  let control;
  if (field.type === "select") {
    control = (
      <select id={id} className={className} value={value || ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose</option>
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (wide) {
    control = (
      <textarea
        id={id}
        className={className}
        rows={3}
        value={value || ""}
        spellCheck={field.code ? false : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  } else {
    const type = field.type === "date" ? "date" : field.type === "number" ? "number" : "text";
    control = (
      <input
        id={id}
        className={className}
        type={type}
        value={value ?? ""}
        autoComplete="off"
        inputMode={field.type === "number" ? "decimal" : undefined}
        spellCheck={field.code ? false : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  return (
    <div className={`field${wide ? " wide" : ""}`}>
      <label htmlFor={id}>
        <span>{field.label}</span>
        {uses ? <span className="uses">{uses === 1 ? "on 1 page" : `on ${uses} pages`}</span> : null}
      </label>
      {control}
      {field.type === "containers" && <ContainerHint value={value} issues={issues} pending={pending} />}
    </div>
  );
}
