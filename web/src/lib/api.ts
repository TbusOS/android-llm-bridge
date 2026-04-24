/**
 * alb REST / WS client helpers.
 *
 * Uses a single base URL — Vite dev proxies /api / /chat etc. to the
 * running alb-api; the production build hits the FastAPI mount on the
 * same origin, so relative URLs work.
 */

export class AlbApiError extends Error {
  status?: number;
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export interface ApiEndpoint {
  method: string;
  path: string;
  description?: string;
}

export interface WsEndpoint {
  path: string;
  description?: string;
  messages?: { type: string; direction?: string; description?: string }[];
}

export interface ApiVersion {
  version: string;
  alb_version: string;
  rest: ApiEndpoint[];
  ws: WsEndpoint[];
  reference?: string;
}

export async function fetchApiVersion(signal?: AbortSignal): Promise<ApiVersion> {
  const r = await fetch("/api/version", { signal });
  if (!r.ok) {
    throw new AlbApiError(
      `GET /api/version returned ${r.status}`,
      r.status,
      "API_VERSION_FAILED",
    );
  }
  return (await r.json()) as ApiVersion;
}
