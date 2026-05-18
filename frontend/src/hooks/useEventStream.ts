import { useEffect, useRef, useState } from "react";
import { config } from "../config";
import { getIdToken } from "../firebase";

/**
 * Subscribes to the API's Server-Sent Events stream at /api/events/stream.
 *
 * We can't use the browser's EventSource — it doesn't allow custom
 * headers, and our API auth is bearer-token. Streaming fetch with a
 * manual line parser handles SSE just as well and lets us attach the
 * Authorization header.
 *
 * Reconnect semantics: on any network error or stream end, the hook
 * waits 2 s and re-opens, passing the last-seen `seq` as `since=` so
 * the server catches up missed events. This is the same pattern as
 * EventSource's automatic reconnect with Last-Event-ID, just hand-
 * rolled because we control both sides.
 *
 * Pass an `onEvent` callback to receive events; the hook itself
 * returns `{ connected, lastSeq }` so a parent can show a small
 * "live" pip in the UI if it wants. Filter to a specific event_type
 * via the `type` option to avoid every component re-rendering on
 * every event in the system.
 */
export type DomainEvent = {
  seq: number;
  eventType: string;
  aggregateId: string | null;
  payload: Record<string, unknown>;
  occurredAt: string;
};

export interface UseEventStreamOptions {
  /** Filter the stream to one event_type (e.g. "order_emitted"). */
  type?: string;
  /** Called for every event received. The most recent seq is also
   *  available via the return value. */
  onEvent?: (ev: DomainEvent) => void;
  /** Disable the hook entirely. Useful while a parent decides whether
   *  it actually needs live updates. */
  enabled?: boolean;
}

interface UseEventStreamResult {
  connected: boolean;
  lastSeq: number | null;
  /** Increments whenever an event arrives — components can depend on
   *  this in a useEffect to refetch their own data without keeping a
   *  full event-handler reference. */
  pulse: number;
}

export function useEventStream(opts: UseEventStreamOptions = {}): UseEventStreamResult {
  const [connected, setConnected] = useState(false);
  const [lastSeq, setLastSeq] = useState<number | null>(null);
  const [pulse, setPulse] = useState(0);

  // Keep callback in a ref so the effect doesn't restart whenever
  // the parent passes a new closure.
  const onEventRef = useRef(opts.onEvent);
  useEffect(() => { onEventRef.current = opts.onEvent; }, [opts.onEvent]);

  const enabled = opts.enabled !== false;
  const type = opts.type;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let abort = new AbortController();
    // Survives reconnects so we don't re-stream events we already saw.
    let sinceLocal: number | null = null;
    const seqRef = { current: null as number | null };

    async function loop(): Promise<void> {
      while (!cancelled) {
        try {
          const token = await getIdToken();
          const url = new URL("/api/events/stream", config.apiBaseUrl);
          if (sinceLocal != null) url.searchParams.set("since", String(sinceLocal));
          if (type) url.searchParams.set("type", type);
          abort = new AbortController();
          const resp = await fetch(url, {
            headers: {
              accept: "text/event-stream",
              ...(token ? { authorization: `Bearer ${token}` } : {}),
            },
            signal: abort.signal,
          });
          if (!resp.ok || !resp.body) {
            // Likely auth or transient — pause and retry.
            setConnected(false);
            await sleep(2000);
            continue;
          }
          setConnected(true);
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          while (!cancelled) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            // SSE frames are separated by a blank line. Split, keep
            // the trailing partial in the buffer.
            const frames = buf.split("\n\n");
            buf = frames.pop() ?? "";
            for (const frame of frames) {
              const trimmed = frame.trim();
              if (!trimmed || trimmed.startsWith(":")) continue; // keepalive / comment
              const ev = parseFrame(trimmed);
              if (!ev) continue;
              sinceLocal = ev.seq;
              seqRef.current = ev.seq;
              setLastSeq(ev.seq);
              setPulse((p) => p + 1);
              try { onEventRef.current?.(ev); } catch { /* swallow user-handler errors */ }
            }
          }
        } catch (e) {
          // AbortError is normal — happens on unmount.
          if ((e as Error)?.name === "AbortError") return;
        }
        setConnected(false);
        if (cancelled) return;
        await sleep(2000); // gentle backoff before reconnect
      }
    }
    void loop();

    return () => {
      cancelled = true;
      abort.abort();
    };
  }, [enabled, type]);

  return { connected, lastSeq, pulse };
}

function parseFrame(frame: string): DomainEvent | null {
  // Frame example:
  //   id: 42
  //   event: order_emitted
  //   data: {"seq":42,...}
  let dataLine: string | null = null;
  for (const raw of frame.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("data:")) {
      // SSE allows multi-line data; the spec says concatenate with \n.
      // We don't emit multi-line in our server but support it for safety.
      dataLine = dataLine == null ? line.slice(5).trimStart() : dataLine + "\n" + line.slice(5).trimStart();
    }
  }
  if (!dataLine) return null;
  try {
    const obj = JSON.parse(dataLine);
    // Normalise PascalCase from the C# serializer to camelCase the
    // rest of the codebase uses.
    const seq = Number(obj.seq ?? obj.Seq);
    const eventType = String(obj.eventType ?? obj.EventType ?? "");
    const aggregateId = (obj.aggregateId ?? obj.AggregateId) ?? null;
    const payload = obj.payload ?? obj.Payload ?? {};
    const occurredAt = String(obj.occurredAt ?? obj.OccurredAt ?? "");
    if (!Number.isFinite(seq) || !eventType) return null;
    return { seq, eventType, aggregateId, payload, occurredAt };
  } catch {
    return null;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
