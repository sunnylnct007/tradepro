/**
 * InlineHint — tiny "ⓘ" glyph next to a metric label whose `title`
 * surfaces the explainer on hover. Lightweight enough to drop on
 * every new KPI without restyling — keeps the user-instruction
 * "every metric needs an in-tool explainer" cheap to honour.
 *
 * Why not a proper tooltip component: the native title attribute
 * already works on every browser and is zero JS — and the cockpit
 * is render-heavy enough that adding a floating-ui dep just for
 * hints would be net-negative. Upgrade later if we need rich
 * markdown / click-to-pin.
 */
export function InlineHint({ text }: { text: string }) {
  return (
    <span
      title={text}
      role="img"
      aria-label={text}
      style={{
        display: "inline-block",
        marginLeft: 4,
        width: 12,
        height: 12,
        lineHeight: "12px",
        textAlign: "center",
        fontSize: 9,
        color: "var(--text-muted)",
        border: "1px solid var(--text-muted)",
        borderRadius: "50%",
        cursor: "help",
        verticalAlign: "middle",
        opacity: 0.55,
        fontFamily: "serif",
        fontWeight: 600,
      }}
    >
      i
    </span>
  );
}
