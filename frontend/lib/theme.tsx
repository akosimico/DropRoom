"use client";

import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "droproom-theme";

/**
 * Self-contained theme hook. No context/provider required — every
 * instance reads the same source of truth (localStorage + the
 * `dark` class on <html>), so the toggle stays in sync across pages
 * without wrapping the app in a provider you don't have yet.
 *
 * Tip: to avoid a flash of the wrong theme on first paint, add this
 * inline script in the <head> of app/layout.tsx, before your content:
 *
 *   <script
 *     dangerouslySetInnerHTML={{
 *       __html: `(function(){try{var t=localStorage.getItem('droproom-theme')||(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');document.documentElement.classList.toggle('dark',t==='dark');}catch(e){}})();`,
 *     }}
 *   />
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("dark");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as Theme | null;
    const initial: Theme =
      stored ?? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.classList.toggle("dark", initial === "dark");
    setThemeState(initial);
    setReady(true);
  }, []);

  const toggle = useCallback(() => {
    setThemeState((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      window.localStorage.setItem(STORAGE_KEY, next);
      document.documentElement.classList.toggle("dark", next === "dark");
      return next;
    });
  }, []);

  return { theme, toggle, ready };
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, toggle, ready } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className={
        "btn-outline lift grid h-10 w-10 shrink-0 place-items-center rounded-xl transition " +
        (ready ? "opacity-100" : "opacity-0") +
        " " +
        className
      }
    >
      {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}