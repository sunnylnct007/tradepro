import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

/**
 * Global OMS mode indicator. Polls /api/oms/mode every 30s so the
 * operator always knows whether strategy intents are auto-approving
 * or holding for manual review. Click navigates to /oms where the
 * toggle lives.
 *
 * Red-tinted in auto because that's the higher-risk state — orders
 * flow without human gating.
 */
export function OmsModeBadge() {
  const [mode, setMode] = useState<"auto" | "manual" | "unknown">("unknown");

  useEffect(() => {
    let live = true;
    const refresh = () => {
      api
        .omsMode()
        .then((r) => {
          if (!live) return;
          setMode(r.mode === "auto" ? "auto" : "manual");
        })
        .catch(() => {
          if (live) setMode("unknown");
        });
    };
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  if (mode === "unknown") return null;

  const isAuto = mode === "auto";
  return (
    <Link
      to="/oms"
      style={{
        padding: "4px 10px",
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        borderRadius: 999,
        textDecoration: "none",
        border: `1px solid ${isAuto ? "#ef4444" : "#4f8cff"}`,
        background: isAuto ? "rgba(239,68,68,0.10)" : "rgba(79,140,255,0.08)",
        color: isAuto ? "#ef4444" : "#4f8cff",
      }}
      title={
        isAuto
          ? "OMS mode = AUTO. Strategy intents auto-approve and place at broker. Click to manage."
          : "OMS mode = MANUAL. Strategy intents wait in PENDING_APPROVAL on /oms. Click to manage."
      }
    >
      OMS {mode}
    </Link>
  );
}
