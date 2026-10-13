export class ApiError extends Error {
  constructor(status, message, fields = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function request(method, path, body) {
  const isForm = body instanceof FormData;
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined || isForm ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the API. Check that the backend is running.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && data.detail;
    if (typeof detail === "string") throw new ApiError(response.status, detail);
    if (detail && !Array.isArray(detail)) {
      const fields = detail.fields || {};
      const first = Object.entries(fields)[0];
      const message = first ? `${detail.message} ${first[0]} ${first[1]}.` : detail.message;
      throw new ApiError(response.status, message, fields);
    }
    throw new ApiError(response.status, `The API returned an error (${response.status}).`);
  }
  return data;
}

const slotPath = (id, slotId) => `/shipments/${id}/slots/${encodeURIComponent(slotId)}`;

export const api = {
  folder: () => request("GET", "/folder"),
  profile: () => request("GET", "/profile"),
  saveProfile: (values) => request("PATCH", "/profile", { values }),
  shipments: () => request("GET", "/shipments"),
  createShipment: () => request("POST", "/shipments", {}),
  saveValues: (id, values) => request("PATCH", `/shipments/${id}/values`, { values }),
  move: (id, to) => request("POST", `/shipments/${id}/move`, { to }),
  attach: (id, slotId, file) => {
    const form = new FormData();
    form.append("file", file);
    return request("POST", `${slotPath(id, slotId)}/file`, form);
  },
  remove: (id, slotId) => request("DELETE", `${slotPath(id, slotId)}/file`),
  checkByEye: (id, slotId) => request("POST", `${slotPath(id, slotId)}/check`),
  build: (id) => request("POST", `/shipments/${id}/build`, { confirmed: true }),
  events: (id) => request("GET", `/shipments/${id}/events`),
  resetDemo: () => request("POST", "/demo/reset"),
};
