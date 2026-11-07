# ============================================================
# data.py
# ============================================================
"""Data fetching: Yahoo Finance + Wikipedia index scraping."""

from __future__ import annotations

from datetime import datetime
from io import StringIO

import pandas as pd
import requests


class DataFetcher:
    """Encapsulates all market-data IO."""

    USER_AGENT = "Mozilla/5.0"
    SP_INDEX_URLS = (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    )

    def __init__(self, start_date: str, end_date: str) -> None:
        self.start = start_date
        self.end = end_date
        self._p1 = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        self._p2 = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())

    def fetch(self, ticker: str) -> pd.DataFrame | None:
        """Fetch daily adjusted OHLCV. Returns None on failure."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        try:
            r = requests.get(
                url,
                params={"period1": self._p1, "period2": self._p2, "interval": "1d"},
                headers={"User-Agent": self.USER_AGENT},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            res = r.json().get("chart", {}).get("result", [None])[0]
            if not res:
                return None
            q = res["indicators"]["quote"][0]
            adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
            return pd.DataFrame({
                "Open": q["open"], "High": q["high"], "Low": q["low"],
                "Close": adj, "Volume": q["volume"],
            }, index=pd.to_datetime(res["timestamp"], unit="s").normalize()).dropna()
        except Exception:
            return None

    def fetch_index_tickers(self) -> list[str]:
        """Scrape S&P 500 + 400 ticker lists from Wikipedia."""
        tickers = []
        for url in self.SP_INDEX_URLS:
            html = requests.get(url, headers={"User-Agent": self.USER_AGENT}, timeout=20).text
            df = pd.read_html(StringIO(html))[0]
            tickers.extend(
                df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
            )
        return sorted(set(tickers))

    def fetch_many(
        self, tickers: list[str], min_rows: int = 300, progress_every: int = 200,
    ) -> dict[str, pd.DataFrame]:
        """Bulk-fetch tickers, skipping failures and short histories."""
        data: dict[str, pd.DataFrame] = {}
        for i, t in enumerate(tickers):
            if i and i % progress_every == 0:
                print(f"  Fetched {i}/{len(tickers)}...")
            df = self.fetch(t)
            if df is not None and len(df) >= min_rows:
                data[t] = df
        return data
