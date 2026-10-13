export class ApiError extends Error {
  constructor(status, message, fields = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
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

export const api = {
  doSet: () => request("GET", "/do-set"),
  client: () => request("GET", "/client"),
  saveClient: (values) => request("PATCH", "/client", { values }),
  shipments: () => request("GET", "/shipments"),
  createShipment: () => request("POST", "/shipments", {}),
  saveValues: (id, values) => request("PATCH", `/shipments/${id}/values`, { values }),
  move: (id, to) => request("POST", `/shipments/${id}/move`, { to }),
  generate: (id) => request("POST", `/shipments/${id}/generate`),
  resetDemo: () => request("POST", "/demo/reset"),
};
