"""Fetch everything deterministic for the paper and write data/live.json.

Runs server-side in a GitHub Action, for the same reason the gate app does:
a browser cannot fetch Yahoo (no CORS header) and the free tiers of the APIs
that do send CORS are too short for a 250-day volatility window. CORS is a
browser rule and does not apply here, so this reads Yahoo directly and commits
the result, which the page then reads same-origin — no API key, no rate limit.

This file owns ONLY the deterministic half: the TQQQ gate, the markets table
and NYC weather. It must never touch data/research.json, which is written by
Claude and holds the jobs, cyber, news and sport sections. Two owners, two
files, so neither can clobber the other.
"""
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "live.json")

# gate parameters — identical to tqqq-strategy/spec.py
P = dict(rv_win=20, rv_thr=0.35, vr_fast=20, vr_slow=250,
         vr_thr=1.10, dd_win=250, dd_exit=0.20, dd_reenter=0.10, persist=2)

TICKERS = [
    ("^NDX", "Nasdaq 100", "idx"), ("^GSPC", "S&P 500", "idx"),
    ("QQQ", "QQQ", "idx"), ("TQQQ", "TQQQ", "idx"),
    ("^VIX", "VIX", "lvl"), ("^IRX", "13w T-bill", "pct"),
    ("^TNX", "10y Treasury", "pct"), ("GBPUSD=X", "GBP/USD", "fx"),
    ("BZ=F", "Brent crude", "usd"), ("BTC-USD", "Bitcoin", "usd"),
]

WMO = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
       45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
       55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       66: "Freezing rain", 67: "Freezing rain", 71: "Light snow", 73: "Snow",
       75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
       82: "Violent showers", 85: "Snow showers", 86: "Snow showers",
       95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}


def get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chart(symbol, rng="3y"):
    d = get("https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.request.quote(symbol)}?range={rng}&interval=1d")
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    rows = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(ts, close) if c is not None]
    return rows


def sd(a):
    m = sum(a) / len(a)
    return math.sqrt(sum((v - m) ** 2 for v in a) / (len(a) - 1))


def gate(dates, closes):
    """The three gates, computed exactly as spec.py does."""
    n = len(closes)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    for i, r in enumerate(rets):
        if abs(r) > 0.25:
            return {"ok": False,
                    "error": f"a {r*100:.0f}% move on {dates[i+1]} looks like a "
                             f"split, not a market move"}
    if n < P["vr_slow"] + 2:
        return {"ok": False, "error": f"only {n} sessions, need {P['vr_slow']+2}"}

    g3prev, comb, last = 1, [], None
    for i in range(P["vr_slow"], n):
        v20 = sd(rets[i - P["rv_win"]:i]) * math.sqrt(252)
        v250 = sd(rets[i - P["vr_slow"]:i]) * math.sqrt(252)
        g1 = 1 if v20 < P["rv_thr"] else 0
        ratio = v20 / v250
        g2 = 1 if ratio < P["vr_thr"] else 0
        hi = max(closes[i - P["dd_win"] + 1:i + 1])
        dd = closes[i] / hi - 1
        g3 = 0 if dd < -P["dd_exit"] else (1 if dd > -P["dd_reenter"] else g3prev)
        g3prev = g3
        comb.append(min(g1, g2, g3))
        last = dict(date=dates[i], v20=v20, ratio=ratio, dd=dd,
                    g1=g1, g2=g2, g3=g3, sd20=sd(rets[i - P["rv_win"]:i]))

    sig, run = 0, 1
    for i in range(1, len(comb)):
        run = run + 1 if comb[i] == comb[i - 1] else 1
        if run >= P["persist"]:
            sig = comb[i]
    held = 1
    for i in range(len(comb) - 2, -1, -1):
        if comb[i] == comb[-1]:
            held += 1
        else:
            break

    blockers = [n for n, k in [("absolute vol", "g1"), ("vol shock", "g2"),
                               ("drawdown", "g3")] if last[k] == 0]
    last.update(ok=True, signal=sig, held=held, blockers=blockers)
    return last


def markets():
    rows = []
    for sym, label, kind in TICKERS:
        try:
            r = chart(sym, "3mo")
            if len(r) < 7:
                continue
            closes = [c for _, c in r]
            last, prev = closes[-1], closes[-2]
            chg = (last / prev - 1) * 100
            wk = (last / closes[-6] - 1) * 100 if len(closes) >= 6 else None
            if kind == "pct":
                val = f"{last:.2f}%"
            elif kind == "fx":
                val = f"{last:.4f}"
            elif kind == "usd":
                val = f"${last:,.2f}" if last < 1000 else f"${last:,.0f}"
            elif kind == "lvl":
                val = f"{last:.2f}"
            else:
                val = f"{last:,.2f}"
            rows.append({"label": label, "value": val,
                         "chg": round(chg, 2),
                         "wk": round(wk, 2) if wk is not None else None,
                         "date": r[-1][0]})
        except Exception as e:  # noqa: BLE001
            print(f"  markets: {sym} failed ({type(e).__name__})", file=sys.stderr)
    return rows


FEEDS = {
    "markets": [("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml"
                         "?partnerId=wrss01&id=20910258"),
                ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex")],
    "world":   [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
                ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
                ("Guardian", "https://www.theguardian.com/world/rss")],
}


def headlines(limit=9):
    """Market and geopolitical headlines straight from RSS.

    These used to sit in research.json, which meant they were only as fresh as
    the last Claude run. RSS needs no LLM, so the Action can refresh them on its
    own cadence — which is why this file now runs several times a day rather
    than once after the close.
    """
    import xml.etree.ElementTree as ET
    out = {}
    for section, feeds in FEEDS.items():
        items, seen = [], set()
        per = max(2, limit // len(feeds) + 1)
        for source, url in feeds:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=25).read()
                root = ET.fromstring(raw)
                for it in root.findall(".//item")[:per]:
                    title = (it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").strip()
                    if not title:
                        continue
                    key = title.lower()[:60]
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"headline": title, "source": source,
                                  "url": link,
                                  "when": (it.findtext("pubDate") or "")[:16]})
            except Exception as e:  # noqa: BLE001
                print(f"  feed {source} ({section}) failed: {type(e).__name__}",
                      file=sys.stderr)
        out[section] = items[:limit]
    return out


def weather():
    try:
        d = get("https://api.open-meteo.com/v1/forecast?latitude=40.7128"
                "&longitude=-74.0060&daily=temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,weathercode,sunset"
                "&current=temperature_2m,weathercode,wind_speed_10m"
                "&timezone=America/New_York&forecast_days=2"
                "&temperature_unit=fahrenheit")
        dy = d["daily"]
        return {"ok": True,
                "now": round(d["current"]["temperature_2m"]),
                "wind": round(d["current"].get("wind_speed_10m", 0)),
                "desc": WMO.get(dy["weathercode"][0], "—"),
                "high": round(dy["temperature_2m_max"][0]),
                "low": round(dy["temperature_2m_min"][0]),
                "precip": dy["precipitation_probability_max"][0],
                "sunset": dy["sunset"][0][-5:],
                "tmw_high": round(dy["temperature_2m_max"][1]),
                "tmw_desc": WMO.get(dy["weathercode"][1], "—")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}"}


def main():
    qqq = chart("QQQ", "3y")[-420:]
    if len(qqq) < 300:
        print(f"FAIL: only {len(qqq)} QQQ sessions", file=sys.stderr)
        return 1
    g = gate([d for d, _ in qqq], [round(float(c), 4) for _, c in qqq])

    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": g,
        "markets": markets(),
        "headlines": headlines(),
        "weather": weather(),
        "params": {"rv_thr": P["rv_thr"], "vr_thr": P["vr_thr"],
                   "dd_exit": P["dd_exit"], "dd_reenter": P["dd_reenter"]},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    pos = ("TQQQ" if g.get("signal") else "T-BILLS") if g.get("ok") else "ERROR"
    hl = payload["headlines"]
    print(f"wrote live.json  gate={pos}  close={g.get('date')}  "
          f"markets={len(payload['markets'])}  "
          f"news={len(hl.get('markets',[]))}  world={len(hl.get('world',[]))}  "
          f"weather={'ok' if payload['weather']['ok'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
