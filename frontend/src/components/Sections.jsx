import { sectionTitle } from "../lib/doSet.js";
import { displayValue } from "../lib/format.js";
import Field from "./Field.jsx";

export function Pager({ doSet, clientName, section, goTo }) {
  const i = doSet.sections.indexOf(section);
  const prev = i > 0 ? doSet.sections[i - 1] : null;
  const next = i < doSet.sections.length - 1 ? doSet.sections[i + 1] : null;
  return (
    <div className="pager">
      {prev && (
        <button type="button" className="btn" onClick={() => goTo(prev)}>
          Previous: {sectionTitle(doSet, prev, clientName)}
        </button>
      )}
      {next && (
        <button type="button" className="btn next" onClick={() => goTo(next)}>
          Next: {sectionTitle(doSet, next, clientName)}
        </button>
      )}
    </div>
  );
}

export function FixedForm({ doSet, clientName, values, setValue, goTo }) {
  return (
    <section>
      <h3 className="section-title">Fixed for {clientName}</h3>
      <p className="section-sub">
        Set once for every {clientName} shipment. A change here updates every DO set.
      </p>
      <div className="grid grid-top">
        {doSet.client_fields.map((f) => (
          <Field
            key={f.key}
            field={f}
            value={values[f.key]}
            uses={doSet.use_count[f.key]}
            onChange={(value) => setValue(f.key, value)}
          />
        ))}
      </div>
      <Pager doSet={doSet} clientName={clientName} section="fixed" goTo={goTo} />
    </section>
  );
}

export function ShipmentForm({ doSet, clientName, values, setValue, issues, pendingKeys, goTo }) {
  return (
    <section>
      <h3 className="section-title">Shipment details</h3>
      <p className="section-sub">
        Enter these once for this shipment. The note beside each field shows how many of the{" "}
        {doSet.pages.length} pages it fills.
      </p>
      {doSet.shipment_groups.map((group) => (
        <div key={group.title}>
          <h4 className="group-title">{group.title}</h4>
          <div className="grid">
            {group.fields.map((f) => (
              <Field
                key={f.key}
                field={f}
                value={values[f.key]}
                uses={doSet.use_count[f.key]}
                issues={issues[f.key]}
                pending={pendingKeys.has(f.key)}
                onChange={(value) => setValue(f.key, value)}
              />
            ))}
          </div>
        </div>
      ))}
      <Pager doSet={doSet} clientName={clientName} section="shipment" goTo={goTo} />
    </section>
  );
}

export function PageForm({ doSet, clientName, page, index, values, clientValues, progress, fieldStatus, setValue, goTo }) {
  const rows = [
    ...page.uses.filter((key) => doSet.fields[key].scope === "shipment"),
    ...page.uses.filter((key) => doSet.fields[key].scope === "client"),
  ];
  return (
    <section>
      <h3 className="section-title">{page.title}</h3>
      <p className="section-sub">
        Page {index + 1} of {doSet.pages.length} in the DO set.{" "}
        {progress.status === "done" ? "Complete." : `${progress.filled} of ${progress.total} fields filled.`}
      </p>
      <h4 className="group-title">Filled automatically</h4>
      <p className="group-note">
        From shipment details and the {clientName} profile. Change a value there and every page that uses it updates.
      </p>
      <div className="auto">
        {rows.map((key) => {
          const field = doSet.fields[key];
          const status = fieldStatus[key];
          const value = field.scope === "client" ? clientValues[key] : values[key];
          return (
            <div key={key} className="auto-row">
              <span className="k">{field.label}</span>
              <span className={`v${field.code ? " code" : ""}${status === "ok" ? "" : " missing"}`}>
                {status === "ok" ? displayValue(field, value) : status === "invalid" ? "Needs fixing" : "Missing"}
              </span>
              {status === "ok" ? (
                <span />
              ) : (
                <button
                  type="button"
                  className="linkbtn"
                  onClick={() => goTo(field.scope === "client" ? "fixed" : "shipment", key)}
                >
                  {status === "invalid" ? "Fix" : "Add"}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <h4 className="group-title">This page only</h4>
      <div className="grid">
        {page.fields.map((f) => (
          <Field key={f.key} field={f} value={values[f.key]} onChange={(value) => setValue(f.key, value)} />
        ))}
      </div>
      <Pager doSet={doSet} clientName={clientName} section={page.id} goTo={goTo} />
    </section>
  );
}
