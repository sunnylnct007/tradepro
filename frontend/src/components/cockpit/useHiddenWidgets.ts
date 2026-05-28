/**
 * useHiddenWidgets — manages the trader's "hide this widget" state for
 * a given cockpit screen. State is a Set<string> of widget IDs; the
 * cockpit shell skips rendering any CockpitCard whose id is in the set.
 *
 * Persists per-screen in localStorage so the layout the trader sets
 * up (hide noisy panels, keep just signals + positions) survives
 * reloads.
 *
 * Why a hook rather than a component: the cockpit shell needs to
 * conditionally render each CockpitCard outside, but the toolbar that
 * shows hidden widgets needs to call .show(). One hook + one
 * <HiddenWidgetsBar /> consumer keeps everything in sync.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

export type WidgetMeta = {
  id: string;
  /** Short label rendered on the restore-pill. Keep it the same as
   *  the CockpitCard `title` so the trader recognises the widget. */
  title: string;
};

export function useHiddenWidgets(
  storageKey: string,
  /** Default-hidden widget IDs applied only on first visit (when no
   *  localStorage entry exists). Lets the cockpit ship trader-first:
   *  IT-analyst surfaces (lifecycle Gantt, system health) hidden until
   *  the trader explicitly restores via the HiddenWidgetsBar. */
  defaultHidden: string[] = [],
) {
  const [hidden, setHidden] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set(defaultHidden);
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) return new Set<string>(JSON.parse(raw));
      return new Set(defaultHidden);
    } catch {
      return new Set(defaultHidden);
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(Array.from(hidden)));
    } catch { /* noop */ }
  }, [hidden, storageKey]);

  const hide = useCallback((id: string) => {
    setHidden((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  const show = useCallback((id: string) => {
    setHidden((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const showAll = useCallback(() => setHidden(new Set()), []);

  return useMemo(
    () => ({ hidden, hide, show, showAll, isHidden: (id: string) => hidden.has(id) }),
    [hidden, hide, show, showAll],
  );
}
