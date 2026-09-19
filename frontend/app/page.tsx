"use client";

import { FormEvent, useState } from "react";
import {
  ArrowRight,
  Check,
  Clock3,
  Flame,
  Heart,
  KeyRound,
  LockKeyhole,
  Loader2,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  UsersRound,
} from "lucide-react";

// lucide-react no longer ships trademarked brand icons (Github, Twitter, etc.)
// in current releases, so this is a small inline mark instead of an import.
function GithubMark({ size = 17 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .5C5.73.5.98 5.24.98 11.52c0 5.02 3.26 9.27 7.77 10.77.57.1.78-.25.78-.55 0-.27-.01-1.17-.02-2.12-3.16.69-3.83-1.34-3.83-1.34-.52-1.32-1.26-1.67-1.26-1.67-1.03-.7.08-.69.08-.69 1.14.08 1.74 1.17 1.74 1.17 1.01 1.73 2.65 1.23 3.3.94.1-.74.4-1.23.72-1.51-2.52-.29-5.17-1.26-5.17-5.6 0-1.24.44-2.25 1.17-3.04-.12-.29-.51-1.45.11-3.02 0 0 .96-.31 3.14 1.16a10.9 10.9 0 0 1 5.72 0c2.18-1.47 3.14-1.16 3.14-1.16.62 1.57.23 2.73.11 3.02.73.79 1.17 1.8 1.17 3.04 0 4.35-2.65 5.31-5.18 5.59.41.35.77 1.04.77 2.1 0 1.52-.01 2.74-.01 3.11 0 .3.2.66.79.55A10.53 10.53 0 0 0 23.02 11.5C23.02 5.24 18.27.5 12 .5Z" />
    </svg>
  );
}
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { RoomCreated } from "@/lib/types";
import { ThemeToggle } from "@/lib/theme";

const LIFETIMES = [
  { value: 1800, label: "30 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 21600, label: "6 hours" },
  { value: 43200, label: "12 hours" },
  { value: 86400, label: "24 hours" },
];

const field = "field mt-2 w-full rounded-xl px-3.5 py-3 text-sm shadow-sm outline-none transition";

export default function Home() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [joinBusy, setJoinBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [guestUpload, setGuestUpload] = useState(false);
  const [guestDownload, setGuestDownload] = useState(true);
  const [lifetime, setLifetime] = useState(3600);
  const [joinCode, setJoinCode] = useState("");
  const [joinName, setJoinName] = useState("");
  const [joinPassword, setJoinPassword] = useState("");

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
      const owner = /[?&]owner=([^&]+)/.exec(room.ownerUrl)?.[1] || "";
      router.push("/r/" + room.accessToken + (owner ? "?owner=" + encodeURIComponent(owner) : ""));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create your room. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function onJoin(e: FormEvent) {
    e.preventDefault();
    if (!joinCode.trim()) return;
    setJoinBusy(true);
    setError(null);
    try {
      const room = await api.joinByCode(joinCode, {
        displayName: joinName.trim() || null,
        password: joinPassword.trim() || null,
      });
      router.push("/r/" + room.accessToken);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not join that room.");
    } finally {
      setJoinBusy(false);
    }
  }

  return (
    <main className="app-shell overflow-hidden">
      {/* ---------- nav ---------- */}
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-5 py-6 sm:px-8">
        <div className="flex items-center gap-0 font-display text-lg font-semibold tracking-tight">
  <img
    src="/logo.png"
    alt="DropRoom"
    className="h-12 w-12 object-contain"
  />
  <span className="-ml-2">DropRoom</span>
</div>
        <div className="flex items-center gap-3">
          <a
            href="#about"
            className="hidden text-sm font-medium text-[var(--text-muted)] transition hover:text-[var(--text)] sm:inline"
          >
            About
          </a>
          <span className="hidden items-center gap-2 text-sm text-[var(--text-muted)] sm:flex">
            <ShieldCheck size={17} /> Private by default
          </span>
          <ThemeToggle />
        </div>
      </nav>

      {/* ---------- hero ---------- */}
      <section className="relative mx-auto grid max-w-6xl items-center gap-12 px-5 pb-16 pt-10 sm:px-8 lg:grid-cols-[1.05fr_.95fr] lg:pb-24 lg:pt-20">

        <div className="reveal relative">
          <p className="chip mb-5 inline-flex items-center gap-2 rounded-full px-4 py-2 text-xs font-semibold tracking-wide">
            <Clock3 size={15} /> Built to disappear
          </p>
          <h1 className="font-display max-w-xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Share the file.
            <br />
            <span style={{ color: "var(--accent)" }}>Skip the clutter.</span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-[var(--text-muted)]">
            Create a private, temporary space for your files. Send a link, collaborate in real
            time, and let the room clean itself up behind you.
          </p>
          <div className="mt-8 flex flex-wrap gap-x-5 gap-y-3 text-sm font-medium text-[var(--text-muted)]">
            <span className="flex items-center gap-2">
              <Check size={17} style={{ color: "var(--accent)" }} /> No account needed
            </span>
            <span className="flex items-center gap-2">
              <Check size={17} style={{ color: "var(--accent)" }} /> Room-level access controls
            </span>
          </div>
        </div>

        <div className="surface reveal-delay relative rounded-[2rem] p-5 sm:p-7">
          <div className="mb-6 flex items-center gap-3">
            <span className="chip grid h-10 w-10 place-items-center rounded-xl">
              <UploadCloud size={20} />
            </span>
            <div>
              <h2 className="font-display font-semibold">Start a DropRoom</h2>
              <p className="text-sm text-[var(--text-muted)]">Your files, your rules, your timer.</p>
            </div>
          </div>

          {error && <div className="banner-danger mb-4 rounded-xl px-4 py-3 text-sm">{error}</div>}

          <form onSubmit={onCreate} className="space-y-4">
            <label className="block text-sm font-semibold">
              Room name <span className="font-normal text-[var(--text-muted)]">(optional)</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                className={field}
                placeholder="e.g. Weekend photos"
              />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-semibold">
                Room password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  maxLength={128}
                  className={field}
                  placeholder="Optional"
                />
              </label>
              <label className="block text-sm font-semibold">
                Expires after
                <select value={lifetime} onChange={(e) => setLifetime(Number(e.target.value))} className={field}>
                  {LIFETIMES.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Toggle label="Guests can upload" checked={guestUpload} onChange={setGuestUpload} />
              <Toggle label="Guests can download" checked={guestDownload} onChange={setGuestDownload} />
            </div>
            <button
  disabled={busy}
  className="btn-primary lift flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3.5 font-semibold transition"
>
  {busy ? (
    <>
      <Loader2 size={18} className="animate-spin" />
      Creating room…
    </>
  ) : (
    <>
      Create room <ArrowRight size={18} />
    </>
  )}
</button>
          </form>
        </div>
      </section>

      {/* ---------- join ---------- */}
      <section className="mx-auto grid max-w-6xl gap-6 px-5 pb-20 sm:px-8 lg:grid-cols-[.8fr_1.2fr]">
        <div className="surface-solid reveal rounded-3xl p-7" style={{ background: "var(--accent)", color: "var(--accent-contrast)", borderColor: "transparent" }}>
          <KeyRound size={25} />
          <h2 className="font-display mt-5 text-2xl font-semibold tracking-tight">Got an invite?</h2>
          <p className="mt-2 max-w-sm text-sm leading-6 opacity-85">
            Enter the room code your host sent. A password may be required.
          </p>
        </div>
        <form onSubmit={onJoin} className="surface reveal-delay rounded-3xl p-6 sm:p-7">
          <div className="grid gap-4 sm:grid-cols-[1.1fr_1fr_1fr_auto] sm:items-end">
            <label className="block text-sm font-semibold">
              Room code
              <input
                required
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value.toUpperCase().slice(0, 12))}
                className={field + " font-mono uppercase tracking-[.2em]"}
                placeholder="ABC123"
              />
            </label>
            <label className="block text-sm font-semibold">
              Your name
              <input
                value={joinName}
                onChange={(e) => setJoinName(e.target.value)}
                className={field}
                placeholder="Optional"
              />
            </label>
            <label className="block text-sm font-semibold">
              Password
              <input
                type="password"
                value={joinPassword}
                onChange={(e) => setJoinPassword(e.target.value)}
                className={field}
                placeholder="If required"
              />
            </label>
            <button
              disabled={joinBusy}
              className="btn-outline lift flex h-[46px] items-center justify-center gap-2 rounded-xl px-5 font-semibold transition"
            >
              {joinBusy ? "Joining…" : (
                <>
                  Join <ArrowRight size={17} />
                </>
              )}
            </button>
          </div>
        </form>
      </section>

      {/* ---------- features ---------- */}
      <section className="mx-auto grid max-w-6xl gap-4 px-5 pb-16 sm:grid-cols-3 sm:px-8">
        <Feature icon={<LockKeyhole size={19} />} title="Private rooms" text="Share only with people you invite." />
        <Feature icon={<UsersRound size={19} />} title="Built for groups" text="See who is in the room, live." />
        <Feature icon={<KeyRound size={19} />} title="One clean link" text="Simple to share, simple to join." />
      </section>

      {/* ---------- about ---------- */}
      <section id="about" className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <div className="surface reveal grid gap-8 rounded-3xl p-7 sm:p-10 lg:grid-cols-[1fr_1fr]">
          <div>
            <p className="chip mb-4 inline-flex items-center gap-2 rounded-full px-4 py-2 text-xs font-semibold tracking-wide">
               About DropRoom
            </p>
            <h2 className="font-display text-3xl font-semibold tracking-tight">
              A room that cleans up after itself.
            </h2>
            <p className="mt-4 leading-7 text-[var(--text-muted)]">
              DropRoom exists for the files you don&apos;t want living forever in a chat thread
              or an inbox. Spin up a room, set an expiry, share the code — and every file, message,
              and member disappears the moment the timer runs out. No account, no archive, no trace.
            </p>
          </div>
          <ul className="grid gap-4 self-center text-sm">
            <AboutPoint text="Rooms auto-expire on a timer you choose, from 30 minutes to 24 hours." />
            <AboutPoint text="Owners control who can upload or download, at any time." />
            <AboutPoint text="Live member list and chat, powered by a real-time connection." />
            <AboutPoint text="Deleting a room wipes its files and messages immediately." />
          </ul>
        </div>
      </section>

      {/* ---------- footer ---------- */}
      <footer className="border-t px-5 py-8 sm:px-8" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 text-sm text-[var(--text-muted)] sm:flex-row">
         <div className="flex items-center gap-0 font-display font-semibold text-[var(--text)]">
  <img
    src="/logo.png"
    alt="DropRoom"
    className="h-10 w-10 object-contain"
  />
  <span className="-ml-2">DropRoom</span>
</div>
          <p className="flex flex-wrap items-center justify-center gap-1.5 text-center">
            Built with by
            <a
              href="https://akosimico.space"
              className="font-medium text-[var(--text)] underline decoration-[var(--accent)]/40 underline-offset-4 transition hover:text-[var(--accent)]"
            >
              Mico Helis
            </a>
          </p>
          <div className="flex items-center gap-4">
            <a href="https://github.com/akosimico" aria-label="GitHub" className="btn-ghost rounded-lg p-2 transition">
              <GithubMark size={17} />
            </a>
            <span>© 2026 DropRoom</span>
          </div>
        </div>
      </footer>
    </main>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="btn-outline lift flex items-center justify-between rounded-xl px-3.5 py-3 text-left text-sm font-medium transition"
    >
      <span>{label}</span>
      <span
  className="relative h-6 w-11 rounded-full transition"
  style={{ background: checked ? "var(--accent)" : "var(--border)" }}
>
  <span
    className="absolute top-1 h-4 w-4 rounded-full shadow transition"
    style={{
      left: checked ? "1.5rem" : "0.25rem",
      background: checked ? "var(--accent-contrast)" : "#ffffff",
    }}
  />
</span>
    </button>
  );
}

function Feature({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return (
    <article className="surface lift reveal-delay-2 rounded-2xl p-5">
      <span style={{ color: "var(--accent)" }}>{icon}</span>
      <h3 className="font-display mt-3 font-semibold">{title}</h3>
      <p className="mt-1 text-sm text-[var(--text-muted)]">{text}</p>
    </article>
  );
}

function AboutPoint({ text }: { text: string }) {
  return (
    <li className="flex items-start gap-3">
      <span className="chip mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full">
        <Check size={13} />
      </span>
      <span className="text-[var(--text-muted)]">{text}</span>
    </li>
  );
}