"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { RoomCreated } from "@/lib/types";

const LIFETIMES = [
  { value: 1800, label: "30 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 21600, label: "6 hours" },
  { value: 43200, label: "12 hours" },
  { value: 86400, label: "24 hours" },
];

export default function Home() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [guestUpload, setGuestUpload] = useState(false);
  const [guestDownload, setGuestDownload] = useState(true);
  const [lifetime, setLifetime] = useState(3600);

  const [joinCode, setJoinCode] = useState("");
  const [joinName, setJoinName] = useState("");
  const [joinPassword, setJoinPassword] = useState("");
  const [joinBusy, setJoinBusy] = useState(false);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const room: RoomCreated = await api.createRoom({
        name: name.trim() || null,
        password: password.trim() || null,
        guestUploadEnabled: guestUpload,
        guestDownloadEnabled: guestDownload,
        lifetimeSeconds: lifetime,
      });
      const q = new URLSearchParams();
      const ownerMatch = /[?&]owner=([^&]+)/.exec(room.ownerUrl);
      if (ownerMatch) q.set("owner", ownerMatch[1]);
      router.push(`/r/${room.accessToken}${q.size ? `?${q.toString()}` : ""}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  async function onJoinByCode(e: FormEvent) {
    e.preventDefault();
    if (!joinCode.trim()) return;
    setJoinBusy(true);
    setError(null);
    try {
      const res = await api.joinByCode(joinCode, {
        displayName: joinName.trim() || null,
        password: joinPassword.trim() || null,
      });
      router.push(`/r/${res.accessToken}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setJoinBusy(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-6 py-16">
      <header className="mb-12 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-600 text-2xl font-bold text-white">
          DR
        </div>
        <h1 className="text-4xl font-bold tracking-tight">DropRoom</h1>
        <p className="mt-3 text-zinc-500 dark:text-zinc-400">
          Send files through a temporary room that disappears. No accounts, no
          tracking — everything is wiped when the link expires.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <section className="rounded-2xl border border-zinc-200 p-6 dark:border-zinc-800">
          <h2 className="mb-4 text-lg font-semibold">Create a room</h2>
          <form onSubmit={onCreate} className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Room name (optional)
              </span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                placeholder="Birthday photos"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Password (optional)
              </span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                maxLength={128}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                placeholder="Require a password to join"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Expires after
              </span>
              <select
                value={lifetime}
                onChange={(e) => setLifetime(Number(e.target.value))}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                {LIFETIMES.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="space-y-2 pt-1">
              <label className="flex items-center justify-between rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
                <span>Guests can upload</span>
                <input
                  type="checkbox"
                  checked={guestUpload}
                  onChange={(e) => setGuestUpload(e.target.checked)}
                />
              </label>
              <label className="flex items-center justify-between rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
                <span>Guests can download</span>
                <input
                  type="checkbox"
                  checked={guestDownload}
                  onChange={(e) => setGuestDownload(e.target.checked)}
                />
              </label>
            </div>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy ? "Creating…" : "Create room"}
            </button>
          </form>
        </section>

        <section className="rounded-2xl border border-zinc-200 p-6 dark:border-zinc-800">
          <h2 className="mb-4 text-lg font-semibold">Join with code</h2>
          <form onSubmit={onJoinByCode} className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Room code
              </span>
              <input
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value.toUpperCase().slice(0, 12))}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm uppercase tracking-widest dark:border-zinc-700 dark:bg-zinc-900"
                placeholder="ABC123"
                required
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Your name (optional)
              </span>
              <input
                value={joinName}
                onChange={(e) => setJoinName(e.target.value)}
                maxLength={60}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                placeholder="Mario"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-500 dark:text-zinc-400">
                Password (if set by the owner)
              </span>
              <input
                type="password"
                value={joinPassword}
                onChange={(e) => setJoinPassword(e.target.value)}
                maxLength={128}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              />
            </label>
            <button
              type="submit"
              disabled={joinBusy || !joinCode.trim()}
              className="w-full rounded-lg bg-zinc-800 px-4 py-2 font-medium text-white transition hover:bg-zinc-900 disabled:opacity-50 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-white"
            >
              {joinBusy ? "Joining…" : "Join room"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}