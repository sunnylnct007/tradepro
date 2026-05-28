"""tradepro-sentiment-score — local-LLM sentiment scorer for the
RiskGate's sentiment veto.

Flow:
  1. Resolve the symbol list (default: currently-held T212 positions
     + the algo's target universe).
  2. For each symbol, pull recent news headlines (last 24h by default)
     via the existing news pipeline.
  3. Call a local LLM (Ollama-style HTTP endpoint by default) with a
     classification prompt; parse the JSON response into a
     (classification, score, rationale) triple.
  4. POST the results to /api/ingest/sentiment so RiskGate +
     RiskMonitorService can use them on the next pre-trade check /
     monitor cycle.

The LLM endpoint + model are configurable so the user can drop in a
finance-tuned local model (the trader mentioned wanting to run one on
the Mac):

  TRADEPRO_LLM_URL    default http://localhost:11434/api/generate (Ollama)
  TRADEPRO_LLM_MODEL  default llama3.1:8b-instruct
  TRADEPRO_LLM_SOURCE default "local-llm" (stored in sentiment_scores.source)

Falls back to MOCK SCORES when the LLM endpoint is unreachable — so
the gate plumbing can be exercised in paper before the user has the
LLM running. Mock scores are deterministic (no random noise) so test
runs are reproducible.

Run modes:
  tradepro-sentiment-score                  # held + universe, push
  tradepro-sentiment-score --dry-run        # compute, no push
  tradepro-sentiment-score AAPL MSFT NVDA   # explicit symbols
  tradepro-sentiment-score --hours 6        # narrower news window
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..secrets import get_secret

log = logging.getLogger("tradepro.cli.sentiment_score")

DEFAULT_LLM_URL = os.environ.get(
    "TRADEPRO_LLM_URL", "http://localhost:11434/api/generate")
DEFAULT_LLM_MODEL = os.environ.get(
    "TRADEPRO_LLM_MODEL", "finbert")
DEFAULT_LLM_SOURCE = os.environ.get(
    "TRADEPRO_LLM_SOURCE", "local-llm")


def _resolve_llm_config(base: str | None, token: str | None) -> dict:
    """Pull llm_url / llm_model / llm_source_tag from /api/settings-kv/.
    Falls back to env-var defaults when the API is unreachable so the
    CLI still works in standalone mode. AWS Secrets Manager isn't
    consulted here — LLM config is runtime-tunable, not credential
    material; credentials would go through get_secret()."""
    if not base or not token:
        return {"url": DEFAULT_LLM_URL, "model": DEFAULT_LLM_MODEL,
                "source": DEFAULT_LLM_SOURCE}
    out: dict[str, str] = {}
    for key, env_default in [
        ("llm_url",         DEFAULT_LLM_URL),
        ("llm_model",       DEFAULT_LLM_MODEL),
        ("llm_source_tag",  DEFAULT_LLM_SOURCE),
    ]:
        try:
            r = requests.get(
                f"{base.rstrip('/')}/api/settings-kv/{key}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if r.ok:
                v = r.json().get("value")
                out[key] = v if isinstance(v, str) else env_default
            else:
                out[key] = env_default
        except requests.RequestException:
            out[key] = env_default
    return {
        "url":    out["llm_url"],
        "model":  out["llm_model"],
        "source": out["llm_source_tag"],
    }

CLASSIFICATIONS = (
    "strongly_negative", "negative", "neutral", "positive", "strongly_positive",
)


# ---------------------------------------------------------------------- #
# Inputs                                                                 #
# ---------------------------------------------------------------------- #


def _resolve_symbols_from_api(base: str, token: str) -> list[str]:
    """Held positions ∪ algo target universe — the names worth
    spending LLM tokens on. Other tickers in S&P 500+400 aren't
    actionable today."""
    syms: set[str] = set()
    # Held T212 demo positions
    try:
        r = requests.get(
            f"{base.rstrip('/')}/api/integrations/trading212/positions?account=demo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.ok:
            body = r.json()
            for p in (body.get("positions") or []):
                t = (p.get("ticker") or "").upper().split("_")[0]
                if t:
                    syms.add(t)
    except requests.RequestException as exc:
        log.warning("positions fetch failed: %s", exc)
    # Algo target universe (latest live-portfolio run)
    try:
        r = requests.get(
            f"{base.rstrip('/')}/api/live-portfolio/ichimoku_equity/latest",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.ok:
            body = r.json()
            for d in (body.get("decisions") or []):
                t = (d.get("symbol") or "").upper().split("_")[0]
                if t:
                    syms.add(t)
    except requests.RequestException as exc:
        log.warning("live-portfolio fetch failed: %s", exc)
    return sorted(syms)


def _fetch_news_for_symbol(symbol: str, hours: int) -> list[dict[str, Any]]:
    """Pull recent news headlines for one symbol. Uses the existing
    news_sentiment / news.py pipeline so the source list is the same
    one the long-term Decide page uses (and so we don't proliferate
    news fetchers across the codebase)."""
    try:
        from ..news import fetch_recent_news_for_symbol
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        items = fetch_recent_news_for_symbol(symbol, since=cutoff) or []
        return [
            {"title": it.get("title") or "",
             "url": it.get("url") or "",
             "published_at": it.get("published_at") or "",
             "summary": (it.get("summary") or "")[:600]}
            for it in items[:8]
        ]
    except ImportError:
        log.debug("news pipeline unavailable; sentiment will fall back to mock")
        return []
    except Exception as exc:  # noqa: BLE001
        log.debug("news fetch for %s failed: %s", symbol, exc)
        return []


# ---------------------------------------------------------------------- #
# LLM call                                                               #
# ---------------------------------------------------------------------- #


_LLM_PROMPT = (
    "You are a finance analyst. Read the following news items about "
    "{ticker} and classify the overall short-term sentiment for the "
    "stock as one of: {classes}.\n\n"
    "Also give a continuous score from -1.0 (strongly negative) to "
    "+1.0 (strongly positive), and a 1-sentence rationale.\n\n"
    "News items:\n{news}\n\n"
    "Respond with STRICT JSON only — no preamble:\n"
    "{{\n"
    '  "classification": "<one of {classes}>",\n'
    '  "score": <number between -1.0 and 1.0>,\n'
    '  "rationale": "<one short sentence>"\n'
    "}}"
)


def _call_llm(symbol: str, news: list[dict[str, Any]],
              url: str, model: str) -> dict[str, Any] | None:
    """POST to Ollama-style /api/generate. Returns the parsed
    {classification, score, rationale} dict; None on any failure
    (caller falls back to mock)."""
    if not news:
        return None
    news_lines = []
    for n in news:
        when = (n.get("published_at") or "")[:16]
        title = n.get("title", "")
        summary = n.get("summary", "")
        news_lines.append(f"- {when}: {title}\n  {summary}")
    prompt = _LLM_PROMPT.format(
        ticker=symbol,
        classes=" / ".join(CLASSIFICATIONS),
        news="\n".join(news_lines),
    )
    try:
        resp = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False,
                  "format": "json"},
            timeout=120,
        )
    except requests.RequestException as exc:
        log.debug("LLM HTTP failed for %s: %s", symbol, exc)
        return None
    if not 200 <= resp.status_code < 300:
        log.debug("LLM HTTP %d for %s", resp.status_code, symbol)
        return None
    try:
        # Ollama returns {"response": "<text>", ...}; the text body
        # should be the JSON we asked for.
        body = resp.json()
        raw = body.get("response") or body.get("text") or ""
        parsed = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.debug("LLM response parse failed for %s: %s", symbol, exc)
        return None
    # Defensive shape check.
    cls = parsed.get("classification")
    score = parsed.get("score")
    if cls not in CLASSIFICATIONS or not isinstance(score, (int, float)):
        log.debug("LLM returned malformed result for %s: %s", symbol, parsed)
        return None
    return {
        "classification": cls,
        "score": float(max(-1.0, min(1.0, score))),
        "rationale": parsed.get("rationale"),
    }


def _mock_score(symbol: str, news: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic placeholder when the LLM endpoint is unreachable.
    Stable across runs so the gate plumbing can be tested without
    introducing noise. Default to neutral so the veto doesn't block
    anything just because the LLM is offline.

    Picks a tone based on whether obvious bearish words appear in the
    titles — crude, but enough to let the gate's path be exercised."""
    titles = " ".join(n.get("title", "").lower() for n in news)
    bearish = any(w in titles for w in (
        "fraud", "lawsuit", "investigation", "downgrade", "miss",
        "guides lower", "warns", "layoff", "bankruptcy", "subpoena",
    ))
    bullish = any(w in titles for w in (
        "beat", "upgrade", "raises guidance", "approval", "wins",
        "record", "all-time high", "exceeds expectations",
    ))
    if bearish and not bullish:
        return {"classification": "negative", "score": -0.4,
                "rationale": "mock: bearish keywords detected (LLM offline)"}
    if bullish and not bearish:
        return {"classification": "positive", "score": 0.4,
                "rationale": "mock: bullish keywords detected (LLM offline)"}
    return {"classification": "neutral", "score": 0.0,
            "rationale": "mock: no clear directional signal (LLM offline)"}


# ---------------------------------------------------------------------- #
# Push                                                                   #
# ---------------------------------------------------------------------- #


def _push(base: str, token: str, scores: list[dict[str, Any]]) -> bool:
    if not scores:
        return True
    url = f"{base.rstrip('/')}/api/ingest/sentiment"
    try:
        resp = requests.post(
            url, json={"scores": scores},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=60,
        )
    except requests.RequestException as exc:
        log.error("push HTTP failed: %s", exc)
        return False
    if not 200 <= resp.status_code < 300:
        log.error("push HTTP %d: %s", resp.status_code, resp.text[:400])
        return False
    log.info("push ok: %s", resp.text[:300])
    return True


# ---------------------------------------------------------------------- #
# Driver                                                                 #
# ---------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="tradepro-sentiment-score",
        description=(
            "Score recent news per symbol via a local finance-tuned LLM "
            "and POST the results to /api/ingest/sentiment for the "
            "RiskGate's sentiment veto."
        ),
    )
    p.add_argument("symbols", nargs="*",
                   help="Explicit symbols. Empty = held positions + algo universe.")
    p.add_argument("--hours", type=int, default=24,
                   help="News window in hours (default 24).")
    p.add_argument("--llm-url", default=DEFAULT_LLM_URL,
                   help=f"LLM endpoint (default {DEFAULT_LLM_URL}).")
    p.add_argument("--llm-model", default=DEFAULT_LLM_MODEL,
                   help=f"LLM model name (default {DEFAULT_LLM_MODEL}).")
    p.add_argument("--source", default=DEFAULT_LLM_SOURCE,
                   help="`source` tag stored on each score row.")
    p.add_argument("--dry-run", action="store_true", help="Compute, no push.")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    base = get_secret("api-base-url") or get_secret("api-url")
    token = (get_secret("api-token")
             or get_secret("ingest-api-token") or "")
    if not base or not token:
        log.error("missing api-base-url + token credentials")
        return 2

    # Centralised LLM config from app_settings_kv. CLI flags still win
    # for ad-hoc runs (e.g. testing a different model).
    cfg = _resolve_llm_config(base, token)
    llm_url    = args.llm_url   if args.llm_url   != DEFAULT_LLM_URL   else cfg["url"]
    llm_model  = args.llm_model if args.llm_model != DEFAULT_LLM_MODEL else cfg["model"]
    llm_source = args.source    if args.source    != DEFAULT_LLM_SOURCE else cfg["source"]
    log.info("llm endpoint=%s model=%s source=%s", llm_url, llm_model, llm_source)

    symbols = [s.upper() for s in args.symbols] if args.symbols \
        else _resolve_symbols_from_api(base, token)
    if not symbols:
        log.warning("no symbols to score — exiting cleanly")
        return 0
    log.info("scoring %d symbol(s) with %d-hour news window",
             len(symbols), args.hours)

    scored: list[dict[str, Any]] = []
    fell_back = 0
    now = datetime.now(timezone.utc).isoformat()
    for sym in symbols:
        news = _fetch_news_for_symbol(sym, hours=args.hours)
        if news:
            res = _call_llm(sym, news, llm_url, llm_model)
            if res is None:
                res = _mock_score(sym, news)
                fell_back += 1
        else:
            res = {"classification": "neutral", "score": 0.0,
                   "rationale": "no recent news"}
        scored.append({
            "symbol": sym,
            "source": llm_source,
            "score": res["score"],
            "classification": res["classification"],
            "n_articles": len(news),
            "rationale": res.get("rationale"),
            "scored_at_utc": now,
        })

    print("\n=== SENTIMENT SCORES ===")
    print(f"  symbols    : {len(scored)}")
    print(f"  LLM mocked : {fell_back}  (endpoint unreachable or empty news)")
    print()
    for s in scored:
        print(f"  {s['symbol']:8} {s['classification']:18} score {s['score']:+.2f}"
              f"   ({s['n_articles']} articles)  {s.get('rationale') or ''}")

    if args.dry_run:
        log.info("dry-run — not pushing")
        return 0
    ok = _push(base, token, scored)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
