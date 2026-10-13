import Field from "./Field.jsx";

export function ShipmentForm({ folder, values, setValue, issues, pendingKeys }) {
  return (
    <section>
      <h3 className="section-title">Shipment details</h3>
      <p className="section-sub">
        Entered once for this MBL. The letters in the DO folder print these values, so a change here updates
        every letter.
      </p>
      {folder.shipment_groups.map((group) => (
        <div key={group.title}>
          <h4 className="group-title">{group.title}</h4>
          <div className="grid">
            {group.fields.map((f) => (
              <Field
                key={f.key}
                field={f}
                value={values[f.key]}
                uses={folder.useCount[f.key]}
                issues={issues[f.key]}
                pending={pendingKeys.has(f.key)}
                onChange={(value) => setValue(f.key, value)}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

export function FixedForm({ folder, values, setValue }) {
  return (
    <section>
      <h3 className="section-title">Fixed details</h3>
      <p className="section-sub">
        The same for every job: who signs, who collects the DO, and where the line's office is. A change here
        updates the letters of every shipment, and folders already built will need building again.
      </p>
      <div className="grid grid-top">
        {folder.profile_fields.map((f) => (
          <Field
            key={f.key}
            field={f}
            value={values[f.key]}
            uses={folder.useCount[f.key]}
            onChange={(value) => setValue(f.key, value)}
          />
        ))}
      </div>
    </section>
  );
}
