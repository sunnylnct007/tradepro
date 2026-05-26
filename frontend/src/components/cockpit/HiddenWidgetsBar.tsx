/**
 * HiddenWidgetsBar — small toolbar shown above the panel grid when
 * the trader has hidden one or more widgets. Each hidden widget is
 * a pill labelled with its title; click restores. "Show all" wipes
 * the hidden set in one tap.
 *
 * Rendered conditionally — empty hidden set means zero visual weight.
 */
import type { WidgetMeta } from "./useHiddenWidgets";

export function HiddenWidgetsBar({
  widgets,
  hidden,
  onShow,
  onShowAll,
}: {
  /** Full catalog of available widgets on this screen. */
  widgets: WidgetMeta[];
  /** Subset currently hidden. */
  hidden: Set<string>;
  onShow: (id: string) => void;
  onShowAll: () => void;
}) {
  const hiddenList = widgets.filter((w) => hidden.has(w.id));
  if (hiddenList.length === 0) return null;
  return (
    <div
      style={{
        display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center",
        padding: "6px 10px",
        marginBottom: 10,
        border: "1px dashed var(--border)",
        borderRadius: 8,
        background: "rgba(255,255,255,0.02)",
        fontSize: 11,
      }}
    >
      <span style={{
        fontSize: 9, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        Hidden ({hiddenList.length})
      </span>
      {hiddenList.map((w) => (
        <button
          key={w.id}
          onClick={() => onShow(w.id)}
          title={`Restore "${w.title}"`}
          style={{
            padding: "2px 9px", fontSize: 11, borderRadius: 999,
            border: "1px solid var(--border)",
            background: "transparent",
            color: "var(--text-dim)",
            cursor: "pointer",
            display: "inline-flex", gap: 4, alignItems: "center",
          }}
        >
          <span style={{ color: "var(--text-muted)", fontSize: 9 }}>+</span>
          {w.title}
        </button>
      ))}
      <button
        onClick={onShowAll}
        style={{
          marginLeft: "auto",
          padding: "2px 8px", fontSize: 10,
          background: "transparent", border: "1px solid var(--border)",
          borderRadius: 4, color: "var(--text-muted)", cursor: "pointer",
        }}
      >
        Show all
      </button>
    </div>
  );
}
