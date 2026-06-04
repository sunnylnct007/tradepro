/**
 * useSort — reusable client-side table sorting. One hook for every tabular
 * surface so sorting behaves identically everywhere (click header to sort,
 * click again to flip). Pass accessors keyed by column id; cells can be
 * derived (e.g. OMS net, P&L%) not just raw fields.
 *
 *   const { sorted, sortKey, dir, toggle } = useSort(rows, {
 *     ticker: r => r.ticker, pnl: r => r.unrealisedAbs,
 *   });
 */
import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";
type Accessor<T> = (row: T) => number | string | null | undefined;

export function useSort<T>(
  rows: T[],
  accessors: Record<string, Accessor<T>>,
  initial?: { key: string; dir: SortDir },
) {
  const [state, setState] = useState<{ key: string; dir: SortDir } | null>(initial ?? null);

  const sorted = useMemo(() => {
    if (!state) return rows;
    const get = accessors[state.key];
    if (!get) return rows;
    const out = [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      let c: number;
      if (av == null && bv == null) c = 0;
      else if (av == null) c = 1;           // nulls sort last
      else if (bv == null) c = -1;
      else if (typeof av === "string" || typeof bv === "string") c = String(av).localeCompare(String(bv));
      else c = av < bv ? -1 : av > bv ? 1 : 0;
      return state.dir === "asc" ? c : -c;
    });
    return out;
  }, [rows, state, accessors]);

  const toggle = (key: string) =>
    setState((s) => (s && s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  return { sorted, sortKey: state?.key ?? null, dir: state?.dir ?? "asc" as SortDir, toggle };
}
