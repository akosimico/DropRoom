"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  api,
  ApiError,
  formatBytes,
  formatCountdown,
  getParam,
  wsBase,
} from "@/lib/api";
import type { FileView, JoinResponse, MessageView, RoomState, RoomView, WsEvent } from "@/lib/types";

type Phase = "loading" | "gate" | "room" | "expired" | "notfound";

const OWNER_KEY_PREFIX = "droproom-owner-";

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

  const [state, setState] = useState<RoomState | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number>(0);

  const wsRef = useRef<WebSocket | null>(null);
  const secondsRef = useRef(0);
  const leavingRef = useRef(false);
  const [chatDraft, setChatDraft] = useState("");
  const [fileInputKey, setFileInputKey] = useState(0);
  const [uploading, setUploading] = useState(false);

  const [editName, setEditName] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [removePassword, setRemovePassword] = useState(false);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
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
      if (phase === "room") scheduleReconnect(ws);
    };
  }

  function scheduleReconnect(dead: WebSocket) {
    window.setTimeout(() => {
      if (leavingRef.current) return;
      if (wsRef.current !== dead || phase !== "room") return;
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
        await api.uploadFile(token, f);
      }
    } catch (err) {
      setActionError((err as ApiError).message ?? "Upload failed");
    } finally {
      setUploading(false);
      setFileInputKey((k) => k + 1);
    }
  }

  async function onDeleteFile(f: FileView) {
    setActionError(null);
    try {
      await api.deleteFile(token, f.id);
    } catch (err) {
      setActionError((err as ApiError).message ?? "Delete failed");
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

  async function onDeleteRoom() {
    if (!window.confirm("Delete this room and all files now? This cannot be undone.")) return;
    try {
      await api.deleteRoom(token);
      setPhase("expired");
    } catch (err) {
      setActionError((err as ApiError).message ?? "Delete failed");
    }
  }

  function onLeaveRoom() {
    leavingRef.current = true;
    wsRef.current?.close();
    router.push("/");
  }

  async function copyShareLink() {
    const url = `${window.location.origin}/r/${token}`;
    await navigator.clipboard.writeText(url);
    setActionError(null);
  }

  async function copyOwnerLink() {
    const owner =
      window.sessionStorage.getItem(ownerStorageKey(token)) ?? ownerToken;
    if (!owner) return;
    const url = `${window.location.origin}/r/${token}?owner=${owner}`;
    await navigator.clipboard.writeText(url);
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
      <main className="mx-auto flex w-full max-w-lg flex-1 flex-col items-center justify-center px-6 py-16">
        <p className="text-sm uppercase tracking-widest text-zinc-400">Private room</p>
        <h1 className="mt-2 text-3xl font-bold">{view.name ?? `Code ${view.roomCode}`}</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">Room code: {view.roomCode}</p>
        {joinError && <p className="mt-4 text-sm text-red-600">{joinError}</p>}
        <form onSubmit={onJoinGate} className="mt-6 w-full space-y-3">
          <label className="block">
            <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">Your name (optional)</span>
            <input
              value={joinName}
              onChange={(e) => setJoinName(e.target.value)}
              maxLength={60}
              className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          {view.passwordRequired && (
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">Room password</span>
              <input
                type="password"
                value={joinPassword}
                onChange={(e) => setJoinPassword(e.target.value)}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>
          )}
          <button
            type="submit"
            disabled={joinBusy}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {joinBusy ? "Joining…" : "Enter room"}
          </button>
        </form>
      </main>
    );
  }
  if (phase !== "room" || !state) {
    return <LoadingScreen label="Connecting…" />;
  }

  const isOwner = role === "OWNER";
  const canUpload = isOwner || state.room.guestUploadEnabled;
  const canDownload = isOwner || state.room.guestDownloadEnabled;

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-zinc-400">
            {isOwner ? "You're the owner" : "Guest"} · code {state.room.roomCode}
          </p>
          <h1 className="mt-1 text-3xl font-bold">{state.room.name ?? "Untitled room"}</h1>
        </div>
        <div className="text-right">
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Expires in <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">{formatCountdown(secondsLeft)}</span>
          </p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => void copyShareLink()}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Copy invite link
            </button>
            {isOwner && (
              <button
                onClick={() => void copyOwnerLink()}
                className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                Copy owner link
              </button>
            )}
          </div>
        </div>
      </div>

      {secondsLeft < 300 && phase === "room" && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          This room expires in {formatCountdown(secondsLeft)}. Download anything you need soon.
        </div>
      )}
      {actionError && (
        <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {actionError}
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_320px]">
        <section className="space-y-4">
          <div className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">
                Files{" "}
                <span className="text-sm font-normal text-zinc-400">
                  {state.room.fileCount} · {formatBytes(state.room.storageUsedBytes)}
                </span>
              </h2>
              {canUpload && (
                <label className="cursor-pointer rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {uploading ? "Uploading…" : "Upload files"}
                  <input key={fileInputKey} type="file" multiple className="hidden" onChange={(e) => void onUpload(e)} disabled={uploading} />
                </label>
              )}
            </div>
            {!canUpload && (
              <p className="mt-2 text-sm text-zinc-500">The owner has disabled guest uploads.</p>
            )}
            {state.files.length === 0 ? (
              <p className="mt-4 py-8 text-center text-sm text-zinc-400">No files yet.</p>
            ) : (
              <ul className="mt-4 divide-y divide-zinc-100 dark:divide-zinc-800">
                {state.files.map((f) => (
                  <li key={f.id} className="flex items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{f.originalFilename}</p>
                      <p className="text-xs text-zinc-400">
                        {formatBytes(f.sizeBytes)} · by {f.uploadedByName ?? "someone"} ·{" "}
                        {f.downloadCount} download{f.downloadCount === 1 ? "" : "s"}
                      </p>
                    </div>
                    {canDownload ? (
                      <a
                        href={api.downloadUrl(token, f.id)}
                        className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                      >
                        Download
                      </a>
                    ) : (
                      <span className="text-xs text-zinc-400">downloads off</span>
                    )}
                    {isOwner && (
                      <button
                        onClick={() => void onDeleteFile(f)}
                        className="rounded-lg border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
                      >
                        Delete
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {view?.passwordRequired !== undefined && (
            <div className="rounded-2xl border border-zinc-200 p-4 text-sm text-zinc-500 dark:border-zinc-800">
              Guests join with the room code{view.passwordRequired ? " and the room password" : ""} —
              share it however you like. Files disappear when the room expires.
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <section className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
            <h2 className="font-semibold">People</h2>
            <ul className="mt-2 space-y-1.5 text-sm">
              {state.members.map((m) => (
                <li key={m.sessionId} className="flex items-center justify-between">
                  <span className="truncate">{m.displayName ?? "Anonymous"}</span>
                  {m.role === "OWNER" && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-900 dark:text-amber-200">owner</span>}
                </li>
              ))}
            </ul>
          </section>

          {isOwner && (
            <section className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
              <h2 className="font-semibold">Owner settings</h2>
              <div className="mt-3 space-y-2 text-sm">
                <label className="flex items-center justify-between">
                  <span>Guests can upload</span>
                  <input
                    type="checkbox"
                    disabled={editing}
                    checked={state.room.guestUploadEnabled}
                    onChange={(e) => {
                      void api.patchRoom(token, {
                        guestUploadEnabled: e.target.checked,
                        guestDownloadEnabled: state.room.guestDownloadEnabled,
                      });
                    }}
                  />
                </label>
                <label className="flex items-center justify-between">
                  <span>Guests can download</span>
                  <input
                    type="checkbox"
                    disabled={editing}
                    checked={state.room.guestDownloadEnabled}
                    onChange={(e) => {
                      void api.patchRoom(token, {
                        guestUploadEnabled: state.room.guestUploadEnabled,
                        guestDownloadEnabled: e.target.checked,
                      });
                    }}
                  />
                </label>
                {editing ? (
                  <form onSubmit={onSaveSettings} className="space-y-2 pt-1">
                    <input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      maxLength={120}
                      className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                      placeholder="Room name"
                    />
                    <input
                      type="password"
                      value={editPassword}
                      onChange={(e) => setEditPassword(e.target.value)}
                      maxLength={128}
                      className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
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
                      <button type="submit" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-white hover:bg-indigo-700">
                        Save
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditPassword("");
                          setRemovePassword(false);
                          setEditing(false);
                        }}
                        className="rounded-lg border border-zinc-300 px-3 py-1.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
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
                    className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-1.5 text-center hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                  >
                    Edit room details
                  </button>
                )}
                <button
                  onClick={() => void onDeleteRoom()}
                  className="mt-2 w-full rounded-lg border border-red-200 px-3 py-1.5 text-center text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
                >
                  Delete room now
                </button>
              </div>
            </section>
          )}

          {!isOwner && (
            <button
              onClick={onLeaveRoom}
              className="w-full rounded-lg border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Leave room
            </button>
          )}

          <ChatPanel
            messages={state.messages}
            canDelete={isOwner}
            onSend={(body) => wsSend({ type: "CHAT_SEND", payload: { body } })}
            onDelete={(id) => wsSend({ type: "CHAT_DELETE", payload: { messageId: id } })}
            draft={chatDraft}
            setDraft={setChatDraft}
          />
        </aside>
      </div>
    </main>
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
    <section className="rounded-2xl border border-zinc-200 p-4 dark:border-zinc-800">
      <h2 className="font-semibold">Chat</h2>
      <div ref={boxRef} className="mt-2 max-h-64 space-y-2 overflow-y-auto pr-1 text-sm">
        {messages.length === 0 && <p className="text-zinc-400">No messages yet.</p>}
        {messages.map((m) => (
          <div key={m.id} className="group">
            <p className="text-xs text-zinc-400">{m.displayName ?? "Anonymous"}</p>
            <p className="break-words">{m.body}</p>
            {canDelete && (
              <button
                onClick={() => onDelete(m.id)}
                className="text-xs text-zinc-400 opacity-0 transition group-hover:opacity-100 hover:text-red-500"
              >
                delete
              </button>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={submit} className="mt-2 flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={1000}
          className="flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          placeholder="Type a message…"
        />
        <button type="submit" className="rounded-lg bg-zinc-800 px-3 py-2 text-sm text-white hover:bg-zinc-900 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-white">
          Send
        </button>
      </form>
    </section>
  );
}

function LoadingScreen({ label }: { label: string }) {
  return (
    <main className="flex flex-1 items-center justify-center text-sm text-zinc-400">{label}</main>
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
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-6 text-center">
      <h1 className="text-3xl font-bold">{title}</h1>
      <p className="mt-3 text-zinc-500 dark:text-zinc-400">{body}</p>
      {withHome && (
        <Link href="/" className="mt-6 rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700">
          Create your own room
        </Link>
      )}
    </main>
  );
}