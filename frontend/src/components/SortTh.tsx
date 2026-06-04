/**
 * SortTh — a clickable, sortable table header cell. Pairs with useSort.
 * Shows ▲/▼ on the active column. Reusable across every table.
 */
import type { SortDir } from "../util/useSort";

export function SortTh({
  label, col, sortKey, dir, onSort, style,
}: {
  label: string;
  col: string;
  sortKey: string | null;
  dir: SortDir;
  onSort: (col: string) => void;
  style?: React.CSSProperties;
}) {
  const active = sortKey === col;
  return (
    <th
      onClick={() => onSort(col)}
      title="Sort"
      style={{ ...style, cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
    >
      {label}
      <span style={{ opacity: active ? 1 : 0.25, marginLeft: 3 }}>
        {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
      </span>
    </th>
  );
}
