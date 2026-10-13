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

export function splitLines(value) {
  return String(value || "")
    .split(/[\n,;]+/)
    .map((part) => part.replace(/\s+/g, "").toUpperCase())
    .filter(Boolean);
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

export function shipmentFacts(values) {
  const hbls = splitLines(values.hbl_nos).length;
  const boxes = splitLines(values.containers).length;
  return [hbls ? plural(hbls, "HBL") : "No HBL yet", boxes ? plural(boxes, "container") : "No container yet"];
}

export function stageNote(shipment) {
  if (shipment.status === "do_requested" && shipment.requested_on) return `Requested ${relDay(shipment.requested_on)}`;
  if (shipment.status === "edo_received" && shipment.edo_received_on) {
    return `eDO received ${relDay(shipment.edo_received_on)}`;
  }
  return null;
}

export const SOURCE_LABEL = { fixed: "Same every time", upload: "From email", generate: "Written by the app" };
