const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function toDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d, 12);
}

export function fmtShort(iso) {
  const d = toDate(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export function fmtLong(iso) {
  return `${fmtShort(iso)} ${toDate(iso).getFullYear()}`;
}

export function fmtMoment(isoDateTime) {
  const d = new Date(isoDateTime);
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${time}`;
}

export function daysFromToday(iso) {
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  return Math.round((toDate(iso) - today) / 86400000);
}

export function relDay(iso) {
  const n = daysFromToday(iso);
  if (n === 0) return "today";
  if (n === -1) return "yesterday";
  if (n === 1) return "tomorrow";
  return fmtShort(iso);
}

export function initials(name) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0].toUpperCase()).join("");
}

export function splitContainers(value) {
  return String(value || "")
    .split(/[\n,;]+/)
    .map((part) => part.replace(/\s+/g, "").toUpperCase())
    .filter(Boolean);
}

export function containerSummary(values) {
  const n = splitContainers(values.containers).length;
  if (!n) return "No containers yet";
  if (values.container_size) return `${n} × ${values.container_size}`;
  return n === 1 ? "1 container" : `${n} containers`;
}

export function etaInfo(shipment) {
  const eta = shipment.values.eta;
  if (!eta || !ISO_DATE.test(eta)) return { text: "ETA not set", urgent: false };
  const n = daysFromToday(eta);
  const urgent = shipment.status === "pending" && shipment.pages_done < shipment.pages_total && n <= 3;
  let text;
  if (n === 0) text = "ETA today";
  else if (n === 1) text = "ETA tomorrow";
  else if (n === -1) text = "Arrived yesterday";
  else if (n < -1) text = `Arrived ${fmtShort(eta)}`;
  else if (urgent) text = `ETA in ${n} days`;
  else text = `ETA ${fmtShort(eta)}`;
  return { text, urgent };
}

export function stageNote(shipment) {
  if (shipment.status === "requested" && shipment.requested_on) return `Requested ${relDay(shipment.requested_on)}`;
  if (shipment.status === "received") {
    if (shipment.edo_valid_till) return `eDO valid till ${fmtShort(shipment.edo_valid_till)}`;
    if (shipment.edo_received_on) return `eDO received ${relDay(shipment.edo_received_on)}`;
  }
  if (shipment.status === "closed" && shipment.closed_on) return `Closed ${relDay(shipment.closed_on)}`;
  return null;
}

export function displayValue(field, value) {
  if (field.type === "checkbox") return value ? "Yes" : "No";
  if (field.type === "date") return ISO_DATE.test(value) ? fmtLong(value) : value;
  if (field.type === "containers") return splitContainers(value).join(", ");
  if (field.type === "number") {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString("en-IN") : value;
  }
  return value;
}
