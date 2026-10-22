/** Typed fetch wrapper for the RoomScape API (same-origin via Next rewrites). */

export type ApiErrorBody = {
  code: string;
  message: string;
  fields?: Record<string, string>;
  request_id?: string;
  retryable?: boolean;
};

export class ApiError extends Error {
  status: number;
  code: string;
  fields?: Record<string, string>;
  requestId?: string;
  retryable: boolean;

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message ?? `Request failed (${status})`);
    this.status = status;
    this.code = body.code ?? "HTTP_ERROR";
    this.fields = body.fields;
    this.requestId = body.request_id;
    this.retryable = Boolean(body.retryable);
  }
}

export async function parseError(res: Response): Promise<ApiError> {
  try {
    const data = await res.json();
    return new ApiError(res.status, data?.error ?? {});
  } catch {
    return new ApiError(res.status, { message: res.statusText || "Network error" });
  }
}

type Opts = Omit<RequestInit, "body"> & { body?: unknown; headers?: Record<string, string> };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const { body, headers, ...rest } = opts;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const res = await fetch(path, {
    credentials: "include",
    ...rest,
    headers: {
      "X-Requested-With": "roomscape",
      ...(body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Upload with real byte-level progress (XHR exposes upload progress; fetch does not). */
export function uploadWithProgress<T>(path: string, form: FormData, onProgress: (loaded: number, total: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "roomscape");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, (data as { error?: ApiErrorBody })?.error ?? {}));
    };
    xhr.onerror = () => reject(new ApiError(0, { code: "NETWORK", message: "Upload interrupted. Check your connection and retry." }));
    xhr.send(form);
  });
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h) return `${h}h ${m % 60}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}
