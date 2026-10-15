// Indexes the DO folder definition from GET /api/folder for quick lookups in the UI.
export function indexFolder(raw) {
  const fields = {};
  raw.profile_fields.forEach((f) => { fields[f.key] = f; });
  raw.shipment_groups.forEach((g) => g.fields.forEach((f) => { fields[f.key] = f; }));
  // How many generated documents print each field, shown beside the field.
  const useCount = {};
  raw.documents.forEach((d) => d.uses.forEach((key) => { useCount[key] = (useCount[key] || 0) + 1; }));
  return {
    ...raw,
    fields,
    useCount,
    shipmentFields: raw.shipment_groups.flatMap((g) => g.fields),
    stageNames: Object.fromEntries(raw.stages.map((s) => [s.id, s.name])),
  };
}

export const SECTIONS = ["shipment", "fixed", "portal", "documents", "review", "process", "history"];

export function sectionTitle(id) {
  return {
    shipment: "Shipment details",
    fixed: "Fixed details",
    documents: "DO folder",
    review: "Review and build",
    portal: "Stages",
    process: "Process",
    history: "History",
  }[id];
}

export function slotName(slot) {
  return slot.hbl ? `${slot.title} · ${slot.hbl}` : slot.title;
}

export function requiredFieldsDone(folder, shipment) {
  const required = folder.shipmentFields.filter((f) => !f.optional);
  return { done: required.filter((f) => shipment.field_status[f.key] === "ok").length, total: required.length };
}

export function profileDone(folder, shipment) {
  return folder.profile_fields.every((f) => f.optional || shipment.field_status[f.key] === "ok");
}

export function openFlags(shipment) {
  return shipment.slots.filter((s) => s.status === "flagged").length;
}
