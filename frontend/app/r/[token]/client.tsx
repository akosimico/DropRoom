"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Clock3,
  Copy,
  Crown,
  Download,
  FileUp,
  Flame,
  LogOut,
  MessageCircle,
  Settings2,
  Trash2,
  UsersRound,
} from "lucide-react";
import {
  api,
  ApiError,
  formatBytes,
  formatCountdown,
  getParam,
  wsBase,
} from "@/lib/api";
import type { FileView, JoinResponse, MessageView, RoomState, RoomView, WsEvent } from "@/lib/types";
import { ThemeToggle } from "@/lib/theme";

type Phase = "loading" | "gate" | "room" | "expired" | "notfound";
type UploadProgress = { filename: string; receivedBytes: number; totalSizeBytes: number };

const OWNER_KEY_PREFIX = "droproom-owner-";
const field = "field w-full rounded-xl px-3 py-2 text-sm outline-none transition";

function ownerStorageKey(token: string): string {
  return `${OWNER_KEY_PREFIX}${token}`;
}

export default function RoomClient({ token, ownerToken }: { token: string; ownerToken: string | null }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("loading");
  const [view, setView] = useState<RoomView | null>(null);
  const [joinError, setJoinError] = useState<string | null>(null);
  const [joinBusy, setJoinBusy] = useState(false);
  const [joinPassword, setJoinPassword] = useState("");
  const [joinName, setJoinName] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [state, setState] = useState<RoomState | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number>(0);

  const wsRef = useRef<WebSocket | null>(null);
  const secondsRef = useRef(0);
  const leavingRef = useRef(false);
  const phaseRef = useRef<Phase>("loading");
  const startedRef = useRef(false);
  const [chatDraft, setChatDraft] = useState("");
  const [fileInputKey, setFileInputKey] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);

  const [editName, setEditName] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [removePassword, setRemovePassword] = useState(false);
  const [editing, setEditing] = useState(false);
  const [updatingPermissions, setUpdatingPermissions] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
const [deletingRoom, setDeletingRoom] = useState(false);
const [fileToDelete, setFileToDelete] = useState<FileView | null>(null);
const [deletingFile, setDeletingFile] = useState(false);


  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    // React Strict Mode intentionally mounts effects twice in development.
    // A join is stateful (it claims a room slot), so only initiate it once.
    if (startedRef.current) return;
    startedRef.current = true;
    const stored = window.sessionStorage.getItem(ownerStorageKey(token));
    const owner = ownerToken ?? stored;
    if (owner) {
      window.sessionStorage.setItem(ownerStorageKey(token), owner);
    }
    api
      .getRoom(token)
      .then((v) => {
        setView(v);
        if (v.status !== "ACTIVE") return setPhase("expired");
        if (v.passwordRequired && !ownerTokenStoredCurrent()) return setPhase("gate");
        return doJoin(ownerTokenStoredCurrent());
      })
      .catch((err: unknown) => {
        const e = err as ApiError;
        if (e?.status === 410) setPhase("expired");
        else if (e?.status === 404) setPhase("notfound");
        else {
          setJoinError(e?.message ?? "Failed to load room");
          setPhase("gate");
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function ownerTokenStoredCurrent(): string | null {
    return (
      window.sessionStorage.getItem(ownerStorageKey(token)) ?? ownerToken
    );
  }

  async function doJoin(owner?: string | null, extra?: { password?: string }) {
    setJoinBusy(true);
    setJoinError(null);
    try {
      const body: { ownerToken?: string; password?: string; displayName?: string } = {};
      if (owner) body.ownerToken = owner;
      if (extra?.password) body.password = extra.password;
      const nameStored = joinName.trim();
      if (nameStored) body.displayName = nameStored;
      const jr = await api.joinRoom(token, body);
      enterRoom(jr);
      return;
    } catch (err) {
      const e = err as ApiError;
      if (e?.status === 410) return setPhase("expired");
      setJoinError(e?.message ?? "Unable to join this room");
      return setPhase("gate");
    } finally {
      setJoinBusy(false);
    }
  }

  const handleWsEvent = useCallback(
    (msg: WsEvent) => {
      switch (msg.type) {
        case "ROOM_STATE": {
          setState(msg.payload);
          const s = msg.payload.room.remainingSeconds;
          secondsRef.current = s;
          setSecondsLeft(s);
          break;
        }
        case "ROOM_EXPIRING":
          secondsRef.current = msg.payload.remainingSeconds;
          setSecondsLeft(msg.payload.remainingSeconds);
          break;
        case "ROOM_EXPIRED":
          setPhase("expired");
          break;
        case "USER_JOINED":
          setState((prev) =>
            prev
              ? prev.members.some((x) => x.sessionId === msg.payload.sessionId)
                ? prev
                : {
                    ...prev,
                    room: { ...prev.room, userCount: prev.room.userCount + 1 },
                    members: [
                      ...prev.members,
                      {
                        sessionId: msg.payload.sessionId,
                        displayName: msg.payload.displayName,
                        role: msg.payload.role || "GUEST",
                      },
                    ],
                  }
              : prev
          );
          break;
        case "USER_LEFT":
          setState((prev) =>
            prev
              ? {
                  ...prev,
                  room: { ...prev.room, userCount: Math.max(0, prev.room.userCount - 1) },
                  members: prev.members.filter((m) => m.sessionId !== msg.payload.sessionId),
                }
              : prev
          );
          break;
        case "FILE_UPLOAD_COMPLETED": {
          const f = msg.payload.file;
          setUploadProgress((current) =>
            current?.filename === f.originalFilename ? null : current
          );
          setState((prev) =>
            prev
              ? {
                  ...prev,
                  room: {
                    ...prev.room,
                    fileCount: prev.files.length + (prev.files.some((x) => x.id === f.id) ? 0 : 1),
                    storageUsedBytes:
                      prev.files.some((x) => x.id === f.id)
                        ? prev.room.storageUsedBytes
                        : prev.room.storageUsedBytes + Math.max(0, f.sizeBytes),
                  },
                  files: prev.files.some((x) => x.id === f.id)
                    ? prev.files
                    : [...prev.files, f],
                }
              : prev
          );
          break;
        }
        case "FILE_UPLOAD_PROGRESS":
          setUploadProgress({
            filename: msg.payload.filename,
            receivedBytes: msg.payload.receivedBytes,
            totalSizeBytes: msg.payload.totalSizeBytes,
          });
          break;
        case "FILE_DELETED":
          setState((prev) => {
            if (!prev) return prev;
            const gone = prev.files.find((f) => f.id === msg.payload.fileId);
            return {
              ...prev,
              room: {
                ...prev.room,
                fileCount: Math.max(0, prev.room.fileCount - 1),
                storageUsedBytes: Math.max(0, prev.room.storageUsedBytes - (gone ? gone.sizeBytes : 0)),
              },
              files: prev.files.filter((f) => f.id !== msg.payload.fileId),
            };
          });
          break;
        case "FILE_DOWNLOADED":
          setState((prev) =>
            prev
              ? {
                  ...prev,
                  files: prev.files.map((f) =>
                    f.id === msg.payload.fileId ? { ...f, downloadCount: msg.payload.downloadCount } : f
                  ),
                }
              : prev
          );
          break;
        case "ROOM_SETTINGS_CHANGED": {
          const r = msg.payload.room;
          setState((prev) =>
            prev
              ? {
                  ...prev,
                  room: {
                    ...prev.room,
                    name: r.name,
                    guestUploadEnabled: r.guestUploadEnabled,
                    guestDownloadEnabled: r.guestDownloadEnabled,
                  },
                }
              : prev
          );
          setView((v) => (v ? { ...v, ...r } : v));
          break;
        }
        case "CHAT_MESSAGE_SENT":
          setState((prev) =>
            prev
              ? prev.messages.some((m) => m.id === msg.payload.message.id)
                ? prev
                : { ...prev, messages: [...prev.messages, msg.payload.message] }
              : prev
          );
          break;
        case "CHAT_MESSAGE_DELETED":
          setState((prev) =>
            prev
              ? { ...prev, messages: prev.messages.filter((m) => m.id !== msg.payload.messageId) }
              : prev
          );
          break;
        case "ERROR":
          setActionError(msg.payload.detail);
          break;
        case "PONG":
          break;
      }
    },
    []
  );

  function enterRoom(jr: JoinResponse) {
    setRole(jr.role);
    const stored = window.sessionStorage.getItem(ownerStorageKey(token));
    if (!stored && jr.role === "OWNER") {
      const owner = getParam("owner") ?? ownerToken;
      if (owner) {
        window.sessionStorage.setItem(ownerStorageKey(token), owner);
      }
    }
    setPhase("room");
    const base = wsBase().replace(/\/$/, "");
    const ws = new WebSocket(`${base}${jr.wsUrl}`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsEvent;
        handleWsEvent(msg);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (phaseRef.current === "room") scheduleReconnect(ws);
    };
  }

  function scheduleReconnect(dead: WebSocket) {
    window.setTimeout(() => {
      if (leavingRef.current) return;
      if (wsRef.current !== dead || phaseRef.current !== "room") return;
      api
        .joinRoom(token, {})
        .then((jr) => {
          const base = wsBase().replace(/\/$/, "");
          const ws = new WebSocket(`${base}${jr.wsUrl}`);
          wsRef.current = ws;
          ws.onmessage = (ev) => {
            try {
              handleWsEvent(JSON.parse(ev.data as string) as WsEvent);
            } catch {
              /* ignore */
            }
          };
          ws.onclose = () => scheduleReconnect(ws);
        })
        .catch(() => scheduleReconnect(dead));
    }, 2000);
  }

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (secondsRef.current > 0) {
        secondsRef.current -= 1;
        setSecondsLeft(secondsRef.current);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const ping = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "PING" }));
      }
    }, 25000);
    return () => window.clearInterval(ping);
  }, []);

  function wsSend(obj: unknown) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj));
    }
  }

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setUploading(true);
    setActionError(null);
    try {
      for (const f of files) {
        await uploadInChunks(f);
      }
      setNotice(files.length === 1 ? "File uploaded." : `${files.length} files uploaded.`);
    } catch (err) {
      setActionError((err as ApiError).message ?? "Upload failed");
    } finally {
      setUploading(false);
      setUploadProgress(null);
      setFileInputKey((k) => k + 1);
    }
  }

  async function uploadInChunks(file: File) {
    if (file.size === 0) throw new Error("Empty files can’t be uploaded.");
    const assumedChunkSize = 8 * 1024 * 1024;
    const totalChunks = Math.ceil(file.size / assumedChunkSize);
    const session = await api.createUploadSession(token, {
      filename: file.name,
      contentType: file.type || null,
      totalSizeBytes: file.size,
      totalChunks,
    });
    try {
      for (let index = 0; index < session.totalChunks; index += 1) {
        const start = index * session.chunkSizeBytes;
        const status = await api.uploadChunk(token, session.uploadId, index, file.slice(start, start + session.chunkSizeBytes));
        setUploadProgress({ filename: file.name, receivedBytes: status.receivedBytes, totalSizeBytes: file.size });
      }
      await api.completeUploadSession(token, session.uploadId, session.totalChunks);
    } catch (error) {
      void api.abortUploadSession(token, session.uploadId).catch(() => undefined);
      throw error;
    }
  }

  async function onConfirmDeleteFile() {
  if (!fileToDelete) return;

  setDeletingFile(true);
  setActionError(null);
  setNotice(null);

  try {
    await api.deleteFile(token, fileToDelete.id);

    setFileToDelete(null);
    setNotice("File deleted successfully.");
  } catch (err) {
    setActionError((err as ApiError).message ?? "Delete failed");
  } finally {
    setDeletingFile(false);
  }
}

  async function onSaveSettings(e: FormEvent) {
    e.preventDefault();
    setActionError(null);
    try {
      const body: { name?: string | null; guestUploadEnabled?: boolean; guestDownloadEnabled?: boolean; password?: string | null } = {
        name: editName.trim() || null,
        guestUploadEnabled: state?.room.guestUploadEnabled ?? false,
        guestDownloadEnabled: state?.room.guestDownloadEnabled ?? false,
      };
      if (removePassword) body.password = "";
      else if (editPassword.trim()) body.password = editPassword.trim();
      await api.patchRoom(token, body);
      setEditPassword("");
      setRemovePassword(false);
      setEditing(false);
    } catch (err) {
      setActionError((err as ApiError).message ?? "Save failed");
    }
  }

  async function updatePermissions(guestUploadEnabled: boolean, guestDownloadEnabled: boolean) {
    setActionError(null);
    const previous = state?.room;
    setUpdatingPermissions(true);
    setState((current) => current ? {
      ...current,
      room: { ...current.room, guestUploadEnabled, guestDownloadEnabled },
    } : current);
    try {
      await api.patchRoom(token, { guestUploadEnabled, guestDownloadEnabled });
    } catch (err) {
      if (previous) {
        setState((current) => current ? { ...current, room: previous } : current);
      }
      setActionError((err as ApiError).message ?? "Could not update guest permissions");
    } finally {
      setUpdatingPermissions(false);
    }
  }

  async function onDeleteRoom() {
    setDeletingRoom(true);
    try {
      await api.deleteRoom(token);
      setPhase("expired");
    } catch (err) {
      setActionError((err as ApiError).message ?? "Delete failed");
      setDeleteDialogOpen(false);
    } finally {
      setDeletingRoom(false);
    }
  }

  function onLeaveRoom() {
    leavingRef.current = true;
    wsRef.current?.close();
    router.push("/");
  }

  async function copyShareLink() {
    const url = `${window.location.origin}/r/${token}`;
    try {
      await navigator.clipboard.writeText(url);
      setNotice("Invite link copied.");
      setActionError(null);
    } catch {
      setActionError("Couldn’t copy the link. Select it from your browser address bar instead.");
    }
  }

  function onJoinGate(e: FormEvent) {
    e.preventDefault();
    void doJoin(ownerTokenStoredCurrent(), { password: joinPassword });
  }

  // ---- Render ----
  if (phase === "loading") {
    return <LoadingScreen label="Loading room…" />;
  }
  if (phase === "notfound") {
    return <CenteredCard title="Room not found" body="This link doesn't point to a DropRoom." withHome />;
  }
  if (phase === "expired") {
    return (
      <CenteredCard
        title="This room is gone"
        body="The room expired or was deleted. All files inside were wiped automatically."
        withHome
      />
    );
  }
  if (phase === "gate" && view) {
    return (
      <main className="app-shell flex min-h-dvh flex-col">
        <TopBar />
        <div className="mx-auto flex w-full max-w-lg flex-1 flex-col items-center justify-center px-6 py-16">
          <div className="surface reveal w-full rounded-3xl p-7 text-center">
            <span className="chip mx-auto grid h-11 w-11 place-items-center rounded-2xl">
              <Flame size={19} />
            </span>
            <p className="mt-4 text-xs font-semibold uppercase tracking-widest text-[var(--text-muted)]">
              Private room
            </p>
            <h1 className="font-display mt-1 text-2xl font-semibold tracking-tight">
              {view.name ?? `Code ${view.roomCode}`}
            </h1>
            <p className="mt-1 text-sm text-[var(--text-muted)]">Room code: {view.roomCode}</p>
            {joinError && <p className="banner-danger mt-4 rounded-xl px-3 py-2 text-sm">{joinError}</p>}
            <form onSubmit={onJoinGate} className="mt-6 w-full space-y-3 text-left">
              <label className="block">
                <span className="mb-1 block text-sm text-[var(--text-muted)]">Your name (optional)</span>
                <input value={joinName} onChange={(e) => setJoinName(e.target.value)} maxLength={60} className={field} />
              </label>
              {view.passwordRequired && (
                <label className="block">
                  <span className="mb-1 block text-sm text-[var(--text-muted)]">Room password</span>
                  <input
                    type="password"
                    value={joinPassword}
                    onChange={(e) => setJoinPassword(e.target.value)}
                    className={field}
                  />
                </label>
              )}
              <button
                type="submit"
                disabled={joinBusy}
                className="btn-primary lift w-full rounded-xl px-4 py-2.5 font-semibold transition"
              >
                {joinBusy ? "Joining…" : "Enter room"}
              </button>
            </form>
          </div>
        </div>
      </main>
    );
  }
  if (phase !== "room" || !state) {
    return <LoadingScreen label="Connecting…" />;
  }

  const isOwner = role === "OWNER";
  const canUpload = isOwner || state.room.guestUploadEnabled;
  const canDownload = isOwner || state.room.guestDownloadEnabled;
  const expiringSoon = secondsLeft < 300;

  return (
    <main className="app-shell w-full min-w-0 flex-1 overflow-x-hidden px-4 py-7 sm:px-6 sm:py-10">
      <div className="mx-auto min-w-0 max-w-6xl">
        <TopBar compact />
        <div className="surface reveal mt-4 rounded-3xl p-5 sm:p-7">
          <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="chip inline-flex max-w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-bold uppercase tracking-widest">
                <span className="grid h-5 w-5 place-items-center rounded-full">
                  {isOwner ? <Crown size={12} /> : <UsersRound size={12} />}
                </span>
                {isOwner ? "You're the owner" : "Guest"} · code {state.room.roomCode}
              </p>
              <h1 className="font-display mt-2 break-words text-3xl font-semibold tracking-tight">
                {state.room.name ?? "Untitled room"}
              </h1>
            </div>
            <div className="flex min-w-0 flex-col items-start text-left sm:ml-auto sm:items-end sm:text-right">
              <p className="flex flex-wrap items-center gap-1.5 text-sm text-[var(--text-muted)] sm:justify-end">
                <Clock3 size={15} /> Expires in{" "}
                <span className="font-mono font-semibold text-[var(--text)]">{formatCountdown(secondsLeft)}</span>
              </p>
              <div className="mt-2 flex w-full gap-2 sm:w-auto">
                <button
                  onClick={() => void copyShareLink()}
                  className="btn-outline lift flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-medium transition"
                >
                  <Copy size={15} /> Copy invite
                </button>
              </div>
            </div>
          </div>
        </div>

        {expiringSoon && phase === "room" && (
          <div className="banner-warning mt-4 flex items-center gap-2 rounded-lg px-4 py-3 text-sm">
            <AlertTriangle size={16} className="shrink-0" />
            This room expires in {formatCountdown(secondsLeft)}. Download anything you need soon.
          </div>
        )}
        {actionError && (
          <div className="banner-danger mt-4 rounded-lg px-4 py-3 text-sm">{actionError}</div>
        )}
        {notice && (
          <div className="banner-success mt-4 flex items-center justify-between gap-3 rounded-xl px-4 py-3 text-sm shadow-sm">
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} className="font-medium hover:opacity-70" aria-label="Dismiss message">
              ×
            </button>
          </div>
        )}

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section className="space-y-4">
            <div className="surface reveal-delay min-w-0 rounded-3xl p-5 sm:p-6">
              <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
                <h2 className="font-display flex min-w-0 items-center gap-2 font-semibold">
                  <span className="chip grid h-9 w-9 place-items-center rounded-xl">
                    <FileUp size={18} />
                  </span>
                  Files{" "}
                  <span className="text-sm font-normal text-[var(--text-muted)]">
                    {state.room.fileCount} · {formatBytes(state.room.storageUsedBytes)}
                  </span>
                </h2>
                {canUpload && (
                  <label className="btn-primary lift flex w-full cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition sm:w-auto">
                    <FileUp size={16} />
                    {uploading ? "Uploading…" : "Upload files"}
                    <input
                      key={fileInputKey}
                      type="file"
                      multiple
                      className="hidden"
                      onChange={(e) => void onUpload(e)}
                      disabled={uploading}
                    />
                  </label>
                )}
              </div>
              {!canUpload && (
                <p className="mt-2 text-sm text-[var(--text-muted)]">The owner has disabled guest uploads.</p>
              )}
              {uploadProgress && (
                <div className="chip mt-4 rounded-xl p-3">
                  <div className="flex justify-between gap-3 text-xs">
                    <span className="truncate">Uploading {uploadProgress.filename}</span>
                    <span>{Math.round((uploadProgress.receivedBytes / uploadProgress.totalSizeBytes) * 100)}%</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full" style={{ background: "var(--border)" }}>
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(100, (uploadProgress.receivedBytes / uploadProgress.totalSizeBytes) * 100)}%`,
                        background: "var(--accent)",
                      }}
                    />
                  </div>
                </div>
              )}
              {state.files.length === 0 ? (
                <div
                  className="mt-4 rounded-2xl border border-dashed py-10 text-center text-sm text-[var(--text-muted)]"
                  style={{ borderColor: "var(--border)" }}
                >
                  No files yet. {canUpload ? "Upload something to get started." : "Check back when someone adds a file."}
                </div>
              ) : (
                <ul className="mt-4 divide-y" style={{ borderColor: "var(--border)" }}>
                  {state.files.map((f) => (
                    <li key={f.id} className="flex min-w-0 flex-col items-stretch gap-2 py-3 sm:flex-row sm:items-center" style={{ borderColor: "var(--border)" }}>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{f.originalFilename}</p>
                        <p className="text-xs text-[var(--text-muted)]">
                          {formatBytes(f.sizeBytes)} · by {f.uploadedByName ?? "someone"} ·{" "}
                          {f.downloadCount} download{f.downloadCount === 1 ? "" : "s"}
                        </p>
                      </div>
                      {canDownload ? (
                        <a
                          href={api.downloadUrl(token, f.id)}
                          className="btn-outline lift flex w-full items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-sm font-medium transition sm:w-auto"
                        >
                          <Download size={15} /> Download
                        </a>
                      ) : (
                        <span className="text-xs text-[var(--text-muted)]">downloads off</span>
                      )}
                      {isOwner && (
  <button
    onClick={() => setFileToDelete(f)}
    className="banner-danger grid h-9 w-9 place-items-center rounded-xl transition hover:opacity-80"
    aria-label={`Delete ${f.originalFilename}`}
  >
    <Trash2 size={16} />
  </button>
)}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {view?.passwordRequired !== undefined && (
              <div className="rounded-2xl border p-4 text-sm text-[var(--text-muted)]" style={{ borderColor: "var(--border)" }}>
                Guests join with the room code{view.passwordRequired ? " and the room password" : ""} —
                share it however you like. Files disappear when the room expires.
              </div>
            )}
          </section>

          <aside className="space-y-4">
            <section className="surface rounded-3xl p-5">
              <h2 className="font-display flex items-center gap-2 font-semibold">
                <UsersRound size={18} style={{ color: "var(--accent)" }} /> People
              </h2>
              <ul className="mt-2 space-y-1.5 text-sm">
                {state.members.map((m) => (
                  <li key={m.sessionId} className="flex items-center justify-between">
                    <span className="flex items-center gap-2 truncate">
                      <span className="live-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
                      {m.displayName ?? "Anonymous"}
                    </span>
                    {m.role === "OWNER" && (
                      <span className="chip rounded px-1.5 py-0.5 text-xs">owner</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>

            <ChatPanel
              messages={state.messages}
              canDelete={isOwner}
              onSend={(body) => wsSend({ type: "CHAT_SEND", payload: { body } })}
              onDelete={(id) => wsSend({ type: "CHAT_DELETE", payload: { messageId: id } })}
              draft={chatDraft}
              setDraft={setChatDraft}
            />

            {isOwner && (
              <section className="surface rounded-3xl p-5">
                <h2 className="font-display flex items-center gap-2 font-semibold">
                  <Settings2 size={18} style={{ color: "var(--accent)" }} /> Owner settings
                </h2>
                <div className="mt-3 space-y-2 text-sm">
                  <PermissionControl
                    label="Guest uploads"
                    description="Let guests add files"
                    enabled={state.room.guestUploadEnabled}
                    disabled={editing || updatingPermissions}
                    onChange={(enabled) => void updatePermissions(enabled, state.room.guestDownloadEnabled)}
                  />
                  <PermissionControl
                    label="Guest downloads"
                    description="Let guests save files"
                    enabled={state.room.guestDownloadEnabled}
                    disabled={editing || updatingPermissions}
                    onChange={(enabled) => void updatePermissions(state.room.guestUploadEnabled, enabled)}
                  />
                  {editing ? (
                    <form onSubmit={onSaveSettings} className="space-y-2 pt-1">
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        maxLength={120}
                        className={field}
                        placeholder="Room name"
                      />
                      <input
                        type="password"
                        value={editPassword}
                        onChange={(e) => setEditPassword(e.target.value)}
                        maxLength={128}
                        className={field}
                        placeholder={view?.passwordRequired ? "New password (leave blank to keep current)" : "Password (optional)"}
                      />
                      {view?.passwordRequired && (
                        <label className="flex items-center justify-between text-sm">
                          <span>Remove password</span>
                          <input
                            type="checkbox"
                            checked={removePassword}
                            onChange={(e) => setRemovePassword(e.target.checked)}
                          />
                        </label>
                      )}
                      <div className="flex gap-2">
                        <button type="submit" className="btn-primary lift rounded-lg px-3 py-1.5 transition">
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditPassword("");
                            setRemovePassword(false);
                            setEditing(false);
                          }}
                          className="btn-outline lift rounded-lg px-3 py-1.5 transition"
                        >
                          Cancel
                        </button>
                      </div>
                    </form>
                  ) : (
                    <button
                      onClick={() => {
                        setEditName(state.room.name ?? "");
                        setEditPassword("");
                        setRemovePassword(false);
                        setEditing(true);
                      }}
                      className="btn-outline lift mt-1 w-full rounded-lg px-3 py-1.5 text-center transition"
                    >
                      Edit room details
                    </button>
                  )}
                  <button
                    onClick={() => setDeleteDialogOpen(true)}
                    className="banner-danger mt-2 w-full rounded-lg px-3 py-1.5 text-center transition hover:opacity-85"
                  >
                    Delete room now
                  </button>
                </div>
              </section>
            )}

            {!isOwner && (
              <button
                onClick={onLeaveRoom}
                className="btn-outline lift flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium transition"
              >
                <LogOut size={16} /> Leave room
              </button>
            )}
          </aside>
        </div>
      </div>
      <DeleteRoomDialog
  open={deleteDialogOpen}
  busy={deletingRoom}
  onCancel={() => setDeleteDialogOpen(false)}
  onConfirm={() => void onDeleteRoom()}
/>
<DeleteFileDialog
  file={fileToDelete}
  busy={deletingFile}
  onCancel={() => setFileToDelete(null)}
  onConfirm={() => void onConfirmDeleteFile()}
/>
    </main>
  );
}

function TopBar({ compact = false }: { compact?: boolean }) {
  return (
    <div className={"flex items-center justify-between " + (compact ? "" : "px-1 py-2")}>
  <Link
    href="/"
    className="font-display flex items-center gap-0 font-semibold tracking-tight"
  >
    <img
      src="/logo.png"
      alt="DropRoom"
      className="h-12 w-12 object-contain"
    />
    <span className="-ml-2">DropRoom</span>
  </Link>

  <ThemeToggle />
</div>
  );
}


function ChatPanel({
  messages,
  canDelete,
  onSend,
  onDelete,
  draft,
  setDraft,
}: {
  messages: MessageView[];
  canDelete: boolean;
  onSend: (body: string) => void;
  onDelete: (id: number) => void;
  draft: string;
  setDraft: (v: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [messages.length]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const body = draft.trim();
    if (!body) return;
    onSend(body);
    setDraft("");
  }

  return (
    <section className="surface min-w-0 rounded-3xl p-5">
      <h2 className="font-display flex items-center gap-2 font-semibold">
        <MessageCircle size={18} style={{ color: "var(--accent)" }} /> Chat
      </h2>
      <div ref={boxRef} className="mt-2 max-h-64 space-y-2 overflow-y-auto pr-1 text-sm">
        {messages.length === 0 && <p className="text-[var(--text-muted)]">No messages yet.</p>}
        {messages.map((m) => (
          <div key={m.id} className="group">
            <p className="text-xs text-[var(--text-muted)]">{m.displayName ?? "Anonymous"}</p>
            <p className="break-words">{m.body}</p>
            {canDelete && (
              <button
                onClick={() => onDelete(m.id)}
                className="text-xs text-[var(--text-muted)] opacity-0 transition group-hover:opacity-100 hover:text-[var(--accent)]"
              >
                delete
              </button>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={submit} className="mt-2 flex min-w-0 gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={1000}
          className={field + " min-w-0 flex-1 rounded-xl px-3 py-2.5"}
          placeholder="Type a message…"
        />
        <button type="submit" className="btn-primary lift rounded-xl px-3 py-2 text-sm font-semibold transition">
          Send
        </button>
      </form>
    </section>
  );
}

function PermissionControl({
  label,
  description,
  enabled,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  enabled: boolean;
  disabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className="btn-outline lift flex w-full items-center justify-between rounded-2xl px-3.5 py-3 text-left transition disabled:opacity-50"
    >
      <span>
        <span className="block font-semibold">{label}</span>
        <span className="mt-0.5 block text-xs text-[var(--text-muted)]">{description}</span>
      </span>
      <span className="relative h-6 w-11 rounded-full transition" style={{ background: enabled ? "var(--accent)" : "var(--border)" }}>
  <span
    className="absolute top-1 h-4 w-4 rounded-full shadow transition"
    style={{
      left: enabled ? "1.5rem" : "0.25rem",
      background: enabled ? "var(--accent-contrast)" : "#ffffff",
    }}
  />
</span>
    </button>
  );
}

function DeleteRoomDialog({
  open,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4 backdrop-blur-sm"
      style={{ background: "rgba(15, 6, 6, 0.55)" }}
      role="presentation"
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-room-title"
        className="surface-solid reveal w-full max-w-md rounded-3xl p-6 shadow-2xl"
      >
        <div className="banner-danger grid h-11 w-11 place-items-center rounded-2xl">
          <AlertTriangle size={20} />
        </div>
        <h2 id="delete-room-title" className="font-display mt-4 text-xl font-semibold">
          Delete this room?
        </h2>
        <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
          Every file and chat message will be removed now. This action cannot be undone.
        </p>
        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="btn-outline lift rounded-xl px-4 py-2.5 text-sm font-semibold transition disabled:opacity-50"
          >
            Keep room
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="banner-danger lift rounded-xl px-4 py-2.5 text-sm font-semibold transition hover:opacity-85 disabled:opacity-50"
          >
            {busy ? "Deleting…" : "Delete room"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DeleteFileDialog({
  file,
  busy,
  onCancel,
  onConfirm,
}: {
  file: FileView | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!file) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4 backdrop-blur-sm"
      style={{ background: "rgba(15, 6, 6, 0.55)" }}
      role="presentation"
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-file-title"
        className="surface-solid reveal w-full max-w-md rounded-3xl p-6 shadow-2xl"
      >
        <div className="banner-danger grid h-11 w-11 place-items-center rounded-2xl">
          <Trash2 size={20} />
        </div>
        <h2 id="delete-file-title" className="font-display mt-4 text-xl font-semibold">
          Delete this file?
        </h2>
        <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
          <span className="font-medium text-[var(--text)]">{file.originalFilename}</span> will be
          removed for everyone in this room. This action cannot be undone.
        </p>
        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="btn-outline lift rounded-xl px-4 py-2.5 text-sm font-semibold transition disabled:opacity-50"
          >
            Keep file
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="banner-danger lift rounded-xl px-4 py-2.5 text-sm font-semibold transition hover:opacity-85 disabled:opacity-50"
          >
            {busy ? "Deleting…" : "Delete file"}
          </button>
        </div>
      </div>
    </div>
  );
}

function LoadingScreen({ label }: { label: string }) {
  return (
    <main className="app-shell flex flex-1 items-center justify-center text-sm text-[var(--text-muted)]">
      <span className="flex items-center gap-2">
        <Flame size={16} className="live-dot" style={{ color: "var(--accent)" }} />
        {label}
      </span>
    </main>
  );
}

function CenteredCard({
  title,
  body,
  withHome,
}: {
  title: string;
  body: string;
  withHome?: boolean;
}) {
  return (
    <main className="app-shell flex min-h-dvh flex-1 items-center justify-center px-6">
      <div className="surface reveal w-full max-w-md rounded-3xl p-8 text-center">
        <h1 className="font-display text-3xl font-semibold">{title}</h1>
        <p className="mt-3 text-[var(--text-muted)]">{body}</p>
        {withHome && (
          <Link href="/" className="btn-primary lift mt-6 inline-block rounded-xl px-4 py-2 font-medium transition">
            Create your own room
          </Link>
        )}
      </div>
    </main>
  );
}