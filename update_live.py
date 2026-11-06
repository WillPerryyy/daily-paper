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
import re
import sys
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "live.json")
# Sessions are defined in New York, not UTC. Hardcoding a UTC hour is wrong
# for the four months the city is on standard time.
NY = ZoneInfo("America/New_York")

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


# Every network read goes through _CACHE so build_payload() can warm the whole
# edition in parallel first. Serially this file made about thirty requests one
# after another - tolerable in a scheduled job nobody waits for, far too slow
# behind a page load.
_CACHE = {}


def fetch_bytes(url, timeout=25):
    if url in _CACHE:
        return _CACHE[url]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def prefetch(urls, timeout=25, workers=12):
    """Warm _CACHE in parallel. A URL that fails is simply left out: the
    ordinary serial caller then retries it on its own and handles the failure
    exactly as it did before, so a dead feed degrades rather than throws."""
    def one(u):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return u, r.read()
        except Exception:  # noqa: BLE001
            return u, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, b in ex.map(one, list(dict.fromkeys(urls))):
            if b is not None:
                _CACHE[u] = b


def get(url, timeout=45):
    return json.loads(fetch_bytes(url, timeout))


def session_over(res, day):
    """Has the regular session for `day` (a New York date) finished?

    This used to be `when.hour < 20`, a hardcoded UTC close. 16:00 in New York
    is 20:00 UTC only on daylight time; from the first Sunday in November it is
    21:00 UTC, so for four months of the year the test cleared an intraday
    quote as a settled close for the whole final hour of trading. Observed
    doing exactly that on 6 Nov 2026 at 15:58 ET.

    Yahoo's own currentTradingPeriod.regular.end is preferred because it is
    also right on the half days - 13:00 ET the Friday after Thanksgiving, and
    Christmas Eve - where any fixed hour is wrong. 16:00 New York is the
    fallback when the metadata is missing or describes another date.
    """
    now = datetime.now(timezone.utc).astimezone(NY)
    today = now.strftime("%Y-%m-%d")
    if today != day:
        return today > day                      # a later session has begun
    end = (((res.get("meta") or {}).get("currentTradingPeriod") or {})
           .get("regular") or {}).get("end")
    if end:
        e = datetime.fromtimestamp(end, timezone.utc).astimezone(NY)
        if e.strftime("%Y-%m-%d") == day:
            return now >= e
    return now.hour >= 16


def drop_unsettled_tail(res, rows):
    """Discard a trailing bar for a session that has not closed yet.

    Yahoo publishes the CURRENT day as a daily bar carrying the live price, so
    any read during market hours would otherwise compute the gate from an
    intraday quote and label it a close - a reading that flips through the
    session. The gate is defined on closes only.
    """
    if not rows:
        return rows
    if not session_over(res, rows[-1][0]):
        return rows[:-1]
    return rows


def settled_meta_row(res, last_date):
    """Recover a session Yahoo has settled but serves as a null daily bar.

    Seen 2026-08-18: every symbol returned close=None for 2026-08-17 while
    meta.regularMarketTime/regularMarketPrice still carried Monday's 20:00Z
    close. The `c is not None` filter then silently deleted a real trading day,
    which shortens the rolling windows and freezes the gate on a stale session.

    Only taken once the session is genuinely over, by the same New York test
    drop_unsettled_tail uses, so an in-progress quote can never be written in
    as a close.
    """
    meta = res.get("meta") or {}
    t, px = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if not t or px is None:
        return None
    day = datetime.fromtimestamp(t, timezone.utc).astimezone(NY).strftime("%Y-%m-%d")
    if last_date and day <= last_date:
        return None
    if not session_over(res, day):
        return None
    return (day, float(px))


def chart_url(symbol, rng="3y"):
    return ("https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.request.quote(symbol)}?range={rng}&interval=1d")


def chart(symbol, rng="3y"):
    d = get(chart_url(symbol, rng))
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    rows = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(ts, close) if c is not None]
    rows = drop_unsettled_tail(res, rows)
    extra = settled_meta_row(res, rows[-1][0] if rows else None)
    if extra:
        rows.append(extra)
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

    # trail keeps the last fortnight of per-gate verdicts. The panel used to
    # show only today, which cannot answer the one question the persistence
    # rule makes you ask - did it block yesterday too? - so a single blocked
    # day looked like an imminent flip.
    g3prev, comb, last = 1, [], None
    trail = deque(maxlen=14)
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
        trail.append(dict(date=dates[i], g1=g1, g2=g2, g3=g3,
                          all=min(g1, g2, g3)))

    # Track the PERSISTED SIGNAL, not the raw combined gates. The signal only
    # flips after `persist` agreeing days, so it is far steadier: counting the
    # raw-gate run reported 9 sessions where the real signal had held 44.
    sig, run, sig_series = 0, 1, []
    for i in range(1, len(comb)):
        run = run + 1 if comb[i] == comb[i - 1] else 1
        if run >= P["persist"]:
            sig = comb[i]
        sig_series.append(sig)

    held = 1
    for i in range(len(sig_series) - 2, -1, -1):
        if sig_series[i] == sig_series[-1]:
            held += 1
        else:
            break
    # sig_series[k] corresponds to dates[P["vr_slow"] + 1 + k]
    since = dates[P["vr_slow"] + 1 + (len(sig_series) - held)]

    blockers = [n for n, k in [("absolute vol", "g1"), ("vol shock", "g2"),
                               ("drawdown", "g3")] if last[k] == 0]

    # Five years of price and signal so the page can shade the periods the rule
    # was invested. sig is a 0/1 string rather than an array - it is a quarter
    # the size in JSON and this file is fetched on every page open, which is why
    # a 5x longer window only costs about 25KB.
    span = 1260
    idx0 = P["vr_slow"] + 1
    hist_dates = dates[idx0:]
    hist_close = closes[idx0:]
    hist = {
        "dates": hist_dates[-span:],
        "closes": [round(c, 2) for c in hist_close[-span:]],
        "sig": "".join(str(int(x)) for x in sig_series[-span:]),
    }
    # Closes immediately BEFORE the window, so the page can compute a 200-day
    # average that is already valid on day one of the chart. 199 extra numbers
    # is about 1.6KB; shipping three pre-computed SMA arrays instead would have
    # been nearer 30KB on a file fetched every time the page opens.
    pre = hist_close[:-span][-199:] if len(hist_close) > span else []
    hist["pre"] = [round(c, 2) for c in pre]

    # SPY on the same dates, for the faint comparison line. Aligned by date
    # rather than by position: the two series do not always have identical
    # session lists, and zipping them blind would silently shear the overlay.
    try:
        spy = dict(chart("SPY", "10y"))
        hist["spy"] = [round(spy[d], 2) if d in spy else None
                       for d in hist["dates"]]
        miss = sum(1 for v in hist["spy"] if v is None)
        if miss:
            print(f"  spy: {miss} of {len(hist['spy'])} dates missing", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  spy fetch failed ({type(e).__name__}); overlay omitted", file=sys.stderr)
        hist["spy"] = []
    # One entry per contiguous in-market run, carrying what TQQQ actually did
    # over it. Computed here rather than in the page because the page only has
    # QQQ closes, and 3x is not 3x the index return over a multi-week stretch -
    # daily reset and financing make the difference large enough to mislead.
    hist["runs"] = green_runs(hist["dates"], hist["sig"])
    # Attach the persisted signal to each trailing day, so the strip can show
    # where the verdict actually changed rather than only where a gate blocked.
    trail = list(trail)
    for k, row in enumerate(trail):
        j = len(comb) - len(trail) + k          # index into comb
        row["sig"] = sig_series[j - 1] if j >= 1 else 0

    last.update(ok=True, signal=sig, held=held, since=since,
                blockers=blockers, trail=trail, history=hist)
    return last



def green_runs(dates, sig):
    """Contiguous in-market spells, with the real TQQQ return over each.

    TQQQ prices are fetched separately; where a date is missing on either end
    the run still reports its span and length, with a null return, rather than
    being silently dropped or filled with a guess.
    """
    try:
        tq = dict(chart("TQQQ", "10y"))
    except Exception as e:  # noqa: BLE001
        print(f"  TQQQ fetch failed ({type(e).__name__}); runs carry no returns",
              file=sys.stderr)
        tq = {}
    out, i, n = [], 0, len(sig)
    while i < n:
        if sig[i] != "1":
            i += 1
            continue
        j = i
        while j + 1 < n and sig[j + 1] == "1":
            j += 1
        a, b = dates[i], dates[j]
        pa, pb = tq.get(a), tq.get(b)
        out.append({"a": a, "b": b, "i0": i, "i1": j, "n": j - i + 1,
                    "tq": None if not (pa and pb) else round((pb / pa - 1) * 100, 1)})
        i = j + 1
    return out


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


# Feeds are chosen on measured freshness, not on name. The first version of
# this used a CNBC feed whose median item was 179 HOURS old (7.5 days) and a
# Yahoo feed carrying no pubDate at all, which is how week-old headlines reached
# the page. Measured medians at selection: CNBC top 6.7h, CNBC economy 7.8h,
# MarketWatch 4.7h, BBC World 6.8h, Al Jazeera 4.5h.
FEEDS = {
    "markets": [
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml"
                 "?partnerId=wrss01&id=100003114"),
        ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml"
                 "?partnerId=wrss01&id=10000664"),
    ],
    "world": [
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
    # These five used to live in research.json, which nothing ever updated -
    # it sat at 2026-08-11 for a fortnight while the page presented it as
    # current. Every feed below was measured before being wired in: newest
    # item and median age checked, and anything without a parseable pubDate or
    # older than 48h at probe time was rejected outright (Krebs newest 268h,
    # CBS Sports NBA 89h, BleepingComputer/ESPN/NBA.com unreachable).
    "cyber": [
        ("Insurance Journal", "https://www.insurancejournal.com/feed/"),
        ("Reinsurance News", "https://www.reinsurancene.ws/feed/"),
        ("The Record", "https://therecord.media/feed"),
        ("Artemis", "https://www.artemis.bm/feed/"),
        ("Cybersecurity Dive", "https://www.cybersecuritydive.com/feeds/news/"),
    ],
    "uk": [
        ("BBC", "https://feeds.bbci.co.uk/news/uk/rss.xml"),
        ("Guardian", "https://www.theguardian.com/uk-news/rss"),
        ("Sky News", "https://feeds.skynews.com/feeds/rss/uk.xml"),
        ("BBC Politics", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    ],
    "premier_league": [
        ("BBC Sport", "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml"),
        ("Sky Sports", "https://www.skysports.com/rss/11661"),
        ("Guardian", "https://www.theguardian.com/football/premierleague/rss"),
    ],
    "f1": [
        ("Autosport", "https://www.autosport.com/rss/f1/news/"),
        ("Motorsport", "https://www.motorsport.com/rss/f1/news/"),
        ("Sky Sports", "https://www.skysports.com/rss/12433"),
        ("BBC Sport", "https://feeds.bbci.co.uk/sport/formula1/rss.xml"),
    ],
    "nba": [
        ("Yahoo Sports", "https://sports.yahoo.com/nba/rss.xml"),
        ("RealGM", "https://basketball.realgm.com/rss/wiretap/0/0.xml"),
    ],
}

MAX_AGE_H = 48      # anything older is not news

# Sport is lumpier than news - a league can go quiet for days, and the NBA is
# out of season for a third of the year - so those sections get a longer window
# rather than rendering empty.
SECTION_MAX_AGE_H = {"premier_league": 96, "f1": 96, "nba": 120, "cyber": 72}

# The insurance and reinsurance feeds carry all insurance news, not just cyber,
# so the cyber section is keyword-filtered. Applied to every cyber feed, not
# just the general ones: an item from a security title that matches none of
# these is not what this section is for either.
CYBER_RE = re.compile(
    r"cyber|ransom|breach|hack|malware|phish|extort|zero[- ]day|vulnerab|"
    r"data protection|privacy|infosec|exfiltrat|ddos|ciso|threat actor",
    re.I)

SECTION_FILTER = {"cyber": CYBER_RE}


def headlines(limit=8):
    """Market and geopolitical headlines from RSS, hard-filtered on age.

    Two defences, because picking good feeds once is not enough: an item with
    no parseable pubDate is dropped rather than trusted, and anything older
    than MAX_AGE_H is dropped whatever feed it came from. Sorted newest first,
    and each item carries its age so the page can show it and you can see decay
    rather than infer it.
    """
    import email.utils
    import xml.etree.ElementTree as ET
    now = datetime.now(timezone.utc)
    out = {}
    for section, feeds in FEEDS.items():
        max_age = SECTION_MAX_AGE_H.get(section, MAX_AGE_H)
        keep = SECTION_FILTER.get(section)
        items, seen = [], set()
        for source, url in feeds:
            try:
                root = ET.fromstring(fetch_bytes(url, 25))
                for it in root.findall(".//item")[:15]:
                    title = (it.findtext("title") or "").strip()
                    pd = it.findtext("pubDate")
                    if not title or not pd:
                        continue
                    try:
                        dt = email.utils.parsedate_to_datetime(pd)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:  # noqa: BLE001
                        continue
                    age = (now - dt).total_seconds() / 3600
                    # Sky Sports stamps some items a little in the future.
                    # Rejecting age < 0 silently dropped its freshest headlines,
                    # so allow a small skew and clamp rather than discard.
                    if age < -6 or age > max_age:
                        continue
                    age = max(age, 0.0)
                    if keep is not None and not keep.search(title):
                        continue
                    key = title.lower()[:60]
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"headline": title, "source": source,
                                  "url": (it.findtext("link") or "").strip(),
                                  "age_h": round(age, 1)})
            except Exception as e:  # noqa: BLE001
                print(f"  feed {source} ({section}) failed: {type(e).__name__}",
                      file=sys.stderr)
        items.sort(key=lambda x: x["age_h"])
        out[section] = items[:limit]
        if items:
            print(f"  {section}: {len(out[section])} items, "
                  f"newest {items[0]['age_h']:.1f}h, oldest kept "
                  f"{out[section][-1]['age_h']:.1f}h")
        else:
            print(f"  {section}: NO items inside {max_age}h", file=sys.stderr)
    return out


WEATHER_URL = ("https://api.open-meteo.com/v1/forecast?latitude=40.7128"
               "&longitude=-74.0060&daily=temperature_2m_max,temperature_2m_min,"
               "precipitation_probability_max,weathercode,sunset"
               "&current=temperature_2m,weathercode,wind_speed_10m"
               "&timezone=America/New_York&forecast_days=2"
               "&temperature_unit=fahrenheit")


def weather():
    try:
        d = get(WEATHER_URL)
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


def build_payload(prev_date=None):
    """Assemble one complete edition. Returns (payload, error).

    Shared by the committed-file path in main() and the on-demand endpoint in
    api/live.py, so the gate has exactly one implementation. It is verified
    against tqqq-strategy/spec.py, and a second copy would be free to drift
    from it silently.
    """
    # Clear first. On Vercel the module stays warm between requests, so a
    # surviving _CACHE would serve yesterday's bytes for any source that failed
    # this time round - a stale edition presented as a live one, which is the
    # exact failure this endpoint exists to end.
    _CACHE.clear()
    prefetch([chart_url("QQQ", "10y"), chart_url("SPY", "10y"), WEATHER_URL]
             + [chart_url(sym, "3mo") for sym, _, _ in TICKERS]
             + [u for feeds in FEEDS.values() for _, u in feeds])

    # 250 sessions warm up the gates and another 200 warm up the 200-day moving
    # average, so five full years of chart need 1260 + 250 + 200 + slack. At
    # 1560 the average only became valid 151 sessions into the window.
    qqq = chart("QQQ", "10y")[-1760:]
    if len(qqq) < 1700:
        return None, f"only {len(qqq)} QQQ sessions"
    g = gate([d for d, _ in qqq], [round(float(c), 4) for _, c in qqq])

    # Never serve a series that has gone backwards. A vendor gap that drops the
    # newest session must leave the last good edition standing, not roll it.
    if prev_date and str(g.get("date") or "") < prev_date:
        return None, (f"fetched close {g.get('date')} is older than the "
                      f"published {prev_date}; refusing to publish a regression")

    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": g,
        "markets": markets(),
        "headlines": headlines(),
        "weather": weather(),
        "params": {"rv_thr": P["rv_thr"], "vr_thr": P["vr_thr"],
                   "dd_exit": P["dd_exit"], "dd_reenter": P["dd_reenter"]},
    }, None


def main():
    prev = None
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                prev = (json.load(f).get("gate") or {}).get("date")
        except Exception:
            prev = None

    payload, err = build_payload(prev)
    if err:
        print(f"FAIL: {err}", file=sys.stderr)
        return 1
    g = payload["gate"]
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
