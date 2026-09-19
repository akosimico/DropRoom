import type { JoinResponse, RoomCreated, RoomView, FileView, UploadSessionCreated, UploadSessionStatus } from "./types";

export const CSRF_COOKIE = "dr_csrf";

export function wsBase(): string {
  return process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = new RegExp(`(?:^|; )${name.replace(/[.$*?^(){}[\]\\]/g, "\\$&")}=([^;]*)`).exec(
    document.cookie
  );
  if (!m) return null;
  return decodeURIComponent(m[1]).replace(/^"|"$/g, "");
}

async function csrfHeaderValue(): Promise<string> {
  let token = readCookie(CSRF_COOKIE);
  if (!token) {
    await fetch("/api/v1/csrf", { credentials: "same-origin" });
    token = readCookie(CSRF_COOKIE) ?? "";
  }
  return token;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  needsCsrf = false
): Promise<T> {
  const headers = new Headers(init.headers);
  if (needsCsrf) headers.set("x-csrf-token", await csrfHeaderValue());
  if (init.body && typeof init.body === "string" && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    let code = "HTTP_ERROR";
    try {
      const body = (await res.json()) as { detail?: string; code?: string };
      if (body.detail) detail = body.detail;
      if (body.code) code = body.code;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(code, res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(code: string, status: number, detail: string) {
    super(detail);
    this.code = code;
    this.status = status;
  }
}

export const api = {
  createRoom(body: {
    name?: string | null;
    password?: string | null;
    guestUploadEnabled?: boolean;
    guestDownloadEnabled?: boolean;
    lifetimeSeconds?: number | null;
  }): Promise<RoomCreated> {
    return request("/api/v1/rooms", {
      method: "POST",
      body: JSON.stringify(body),
    }, true);
  },

  joinRoom(token: string, body: { displayName?: string | null; password?: string | null; ownerToken?: string | null } = {}): Promise<JoinResponse> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/join`, {
      method: "POST",
      body: JSON.stringify(body),
    }, true);
  },

  joinByCode(roomCode: string, body: { displayName?: string | null; password?: string | null } = {}): Promise<JoinResponse> {
    return request("/api/v1/rooms/join-by-code", {
      method: "POST",
      body: JSON.stringify({ roomCode: roomCode.toUpperCase().trim(), ...body }),
    }, true);
  },

  getRoom(token: string): Promise<RoomView> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}`);
  },

  patchRoom(token: string, body: { name?: string | null; guestUploadEnabled?: boolean; guestDownloadEnabled?: boolean; password?: string | null }): Promise<RoomView> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }, true);
  },

  deleteRoom(token: string): Promise<{ ok: boolean }> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}`, {
      method: "DELETE",
    }, true);
  },

  uploadFile(token: string, file: File): Promise<FileView> {
    const form = new FormData();
    form.append("file", file, file.name);
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/files`, {
      method: "POST",
      body: form,
    }, true);
  },

  createUploadSession(token: string, body: { filename: string; contentType: string | null; totalSizeBytes: number; totalChunks: number }): Promise<UploadSessionCreated> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/uploads`, {
      method: "POST",
      body: JSON.stringify(body),
    }, true);
  },

  uploadChunk(token: string, uploadId: number, index: number, chunk: Blob): Promise<UploadSessionStatus> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/uploads/${uploadId}/chunks/${index}`, {
      method: "PUT",
      body: chunk,
    }, false);
  },

  completeUploadSession(token: string, uploadId: number, totalChunks: number): Promise<FileView> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/uploads/${uploadId}/complete`, {
      method: "POST",
      body: JSON.stringify({ totalChunks }),
    }, true);
  },

  abortUploadSession(token: string, uploadId: number): Promise<{ ok: boolean }> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/uploads/${uploadId}`, {
      method: "DELETE",
    }, true);
  },

  downloadUrl(token: string, fileId: number): string {
    return `/api/v1/rooms/${encodeURIComponent(token)}/files/${fileId}/download`;
  },

  deleteFile(token: string, fileId: number): Promise<FileView> {
    return request(`/api/v1/rooms/${encodeURIComponent(token)}/files/${fileId}`, {
      method: "DELETE",
    }, true);
  },
};

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${d > 0 ? `${d}d ` : ""}${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function getParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

export function setParam(name: string, value: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set(name, value);
  window.history.replaceState(null, "", url.toString());
}

export function clearParam(name: string): void {
  const url = new URL(window.location.href);
  url.searchParams.delete(name);
  window.history.replaceState(null, "", url.toString());
}
