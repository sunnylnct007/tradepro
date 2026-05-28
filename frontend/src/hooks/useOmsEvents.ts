/**
 * useOmsEvents — SSE hook for the OMS event stream.
 *
 * Connects to GET /api/events/oms and calls onEvent for each event.
 * Uses EventSource (simple, no auth header required for this endpoint).
 * Reconnects after 3 s on error, passing ?since=<lastSeq> so the
 * server can replay any events missed during the gap.
 *
 * The onEvent callback is held in a ref so callers can inline lambdas
 * without causing the connection to restart on every render.
 */
import { useEffect, useRef } from "react";
import { config } from "../config";

export function useOmsEvents(
  onEvent: (eventType: string, seq: number) => void,
): void {
  const callbackRef = useRef(onEvent);
  useEffect(() => { callbackRef.current = onEvent; }, [onEvent]);

  const lastSeqRef = useRef<number | null>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;

    function connect() {
      if (!alive) return;
      const url = new URL(`${config.apiBaseUrl}/api/events/oms`);
      if (lastSeqRef.current !== null) {
        url.searchParams.set("since", String(lastSeqRef.current));
      }
      es = new EventSource(url.toString());

      es.onmessage = (ev) => {
        try {
          const obj = JSON.parse(ev.data as string) as { seq?: number; Seq?: number; type?: string; Type?: string; eventType?: string; EventType?: string };
          const seq = Number(obj.seq ?? obj.Seq ?? 0);
          const eventType = String(obj.type ?? obj.Type ?? obj.eventType ?? obj.EventType ?? "");
          if (seq) lastSeqRef.current = seq;
          try { callbackRef.current(eventType, seq); } catch { /* swallow */ }
        } catch { /* non-JSON keepalive */ }
      };

      es.onerror = () => {
        es?.close();
        es = null;
        if (alive) {
          retryTimer = setTimeout(connect, 3000);
        }
      };
    }

    connect();

    return () => {
      alive = false;
      if (retryTimer !== null) clearTimeout(retryTimer);
      es?.close();
    };
  }, []);
}
