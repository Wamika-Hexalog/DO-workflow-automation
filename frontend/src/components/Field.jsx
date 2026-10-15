import { plural, splitLines } from "../lib/format.js";

function ContainerHint({ value, issues, pending }) {
  const count = splitLines(value).length;
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
  return <span className="hint ok">{plural(count, "container")}, check digits verified.</span>;
}

function ListHint({ value }) {
  const count = splitLines(value).length;
  if (!count) return <span className="hint">One per line. HBL documents are asked for once per house bill.</span>;
  return <span className="hint">{plural(count, "house bill")}.</span>;
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

  const className = `input${field.code ? " code" : ""}${issues && issues.length ? " bad" : ""}`;
  const wide = field.type === "textarea" || field.type === "containers" || field.type === "list";
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
        rows={field.type === "textarea" ? 3 : 2}
        placeholder=" "
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
        placeholder=" "
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
        <span>
          {field.label}
          {field.optional && <span className="optional"> optional</span>}
        </span>
        {uses ? <span className="uses">{uses === 1 ? "in 1 letter" : `in ${uses} letters`}</span> : null}
      </label>
      {control}
      {field.type === "containers" && <ContainerHint value={value} issues={issues} pending={pending} />}
      {field.type === "list" && <ListHint value={value} />}
    </div>
  );
}
