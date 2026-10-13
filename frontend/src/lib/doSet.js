// Indexes the DO set from GET /api/do-set for quick lookups in the UI.
export function indexDoSet(raw) {
  const fields = {};
  raw.client_fields.forEach((f) => { fields[f.key] = f; });
  raw.shipment_groups.forEach((g) => g.fields.forEach((f) => { fields[f.key] = f; }));
  raw.pages.forEach((p) => p.fields.forEach((f) => { fields[f.key] = f; }));
  return {
    ...raw,
    fields,
    shipmentFields: raw.shipment_groups.flatMap((g) => g.fields),
    sections: ["fixed", "shipment", ...raw.pages.map((p) => p.id)],
    pageTitles: Object.fromEntries(raw.pages.map((p) => [p.id, p.title])),
  };
}

export function sectionTitle(doSet, id, clientName) {
  if (id === "fixed") return `Fixed for ${clientName}`;
  if (id === "shipment") return "Shipment details";
  return doSet.pageTitles[id];
}
