#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GoldDR -> Telegram alerts   (free, no TradingView plan needed, standard library only)

What it does
  Re-implements your GoldDR Pine script (KD S/R zones, Daily + 4H bias, entry candles on S/R,
  SL / TP1 / TP plan) and sends a Telegram message to your phone when a NEW entry candle closes
  on the 15m and/or 30m chart.

SETUP (5 minutes)
  1. Install Python 3.9 or newer (python.org). Nothing else to install.
     Windows only, if you see a timezone error:   pip install tzdata
  2. In Telegram open @BotFather -> /newbot -> copy the TOKEN it gives you.
  3. Open your new bot in Telegram and press Start (send it any message).
  4. Put the token in the environment (or in TELEGRAM_BOT_TOKEN below) and run
         python gold_alerts.py --find-chat-id          (prints your chat id)
     Put the chat id in TELEGRAM_CHAT_ID below (or the environment variable of the same name).
  5. Test the phone message:      python gold_alerts.py --test-telegram
  6. Compare with TradingView:    python gold_alerts.py --check
  7. Run it (keep the computer / server on):   python gold_alerts.py

Environment variables (alternative to editing the file):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_API_KEY

IMPORTANT
  * This is a re-implementation, NOT the same code that runs on TradingView. Data comes from a
    different feed (if you switch to Twelve Data instead of TradingView), so a candle can differ by a few
    cents and a zone or a signal can differ slightly. Use  --check  to compare with your chart.
  * Settings below are copied from the screenshots of your Pine inputs. Check each one against your
    indicator and edit it here if you changed anything.
  * Signals appear when the entry candle CLOSES (same as the triangle on TradingView).
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import os
import random
import sys
import time
from urllib import error as urlerr
from urllib import parse as urlparse
from urllib import request as urlreq
import ssl

try:  # use certifi's certificates when available (fixes "CERTIFICATE_VERIFY_FAILED" on macOS)
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())        # also used by the TradingView websocket library
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:  # noqa
    SSL_CTX = None

# =============================================================================
# SETTINGS  (mirror your Pine inputs)
# =============================================================================
TELEGRAM_BOT_TOKEN = ""            # or env TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = ""              # or env TELEGRAM_CHAT_ID

DATA_SOURCE = os.environ.get("DATA_SOURCE", "tradingview")  # "tradingview" = the same candles as your chart (free, no account needed)
                                   # "twelvedata"  = backup option (free key from twelvedata.com)
TV_SYMBOL = "XAUUSD"               # TradingView symbol ...
TV_EXCHANGE = "OANDA"              # ... and broker feed (use the same one your chart shows)
TV_USERNAME = ""                   # optional: your free TradingView login (more stable). or env TV_USERNAME
TV_PASSWORD = ""                   # optional. or env TV_PASSWORD
YAHOO_SYMBOL = "XAUUSD=X"          # spot gold on Yahoo. (GC=F is the futures price and is NOT the same)
TWELVE_SYMBOL = "XAU/USD"
TWELVE_API_KEY = ""                # or env TWELVE_DATA_API_KEY

TRACK_TRADES = True                # after an entry alert, also message when SL or each TP step is hit
TP_STEP_PIPS = 100.0               # one message every this many pips of profit (TP1, TP2, TP3 ... up to the full TP)
WAIT_CANDLE_SEC = 45               # at a candle close, wait up to this long for the provider to publish the new candle
OPEN_NOTICE = True                 # send a message when the market opens after the weekend (also proves the program is running)
OPEN_GAP_HRS = 1.0                 # a gap this long between candles = the market was closed (daily break, weekend or holiday)
OPEN_WINDOW_HRS = 3.0              # only announce an opening that happened in the last N hours
TIMEFRAMES = [15, 30, 60, 120, 240]   # chart timeframes to watch (minutes): 15, 30, 60 (1H), 120 (2H), 240 (4H)
CHECK_DELAY_SEC = 25               # wait this long after a candle closes before reading the data
DISPLAY_UTC_OFFSET_HOURS = 3       # times shown in the messages (UTC+3 = Addis Ababa)
MAX_CATCHUP_BARS = int(os.environ.get("MAX_CATCHUP_BARS", "3"))               # after downtime: still alert for signals of the last N closed candles

# ---- 1) Bias (Daily + 4H)
DAILY_SWING = 6
H4_SWING = 4
USE_BIAS = True                    # False = no bias filter, long AND short allowed
BIAS_RULE = "Daily + 4H agree"     # "Daily + 4H agree" | "Daily only" | "4H only"

# ---- 2) Zones (KD logic)
PIP_SIZE = 0.1                     # XAU on OANDA: 1 pip = $0.10  (100 pips = $10)
WICK_ZONES = True                  # False = body rejection on S/R (KD), True = weak / wick rejection
LEFT_BARS = 3
RIGHT_BARS = 3
MIN_TOUCHES = 1
SPACING_HRS = 4.0
WINDOW_DAYS = 180
MAX_PIPS_HTF = 100.0
MAX_PIPS_MID = 50.0                # used for 15m and 30m charts
MAX_PIPS_LOW = 15.0
MERGE_PIPS = 150.0

# ---- 3) Entry candles on S/R
WICK_PCT = 50.0
INC_ENGULF = False                 # "Also count engulfing candles as a rejection"
TOL_PIPS = 30.0
SWEEP_PIPS = 300.0
MIN_GAP = 1
MAX_GAP = 40
MUST_LEAVE = True
CHAIN_REJ = False
EN_SINGLE = False
EN_DOUBLE = True
EN_ENGULF = False
EN_DOJI = False
EN_HAMMER = False
EN_STAR = False
EN_INV_HAM = False             # Inverted hammer: BUY, long upper wick after a bearish candle
EN_HAM_AUTO = True           # Hammer / Shooting star: the bias picks the candle (works also when USE_BIAS is False)

# ---- 4) Trade
TEST_DAYS = 120
SL_PIPS = 100.0
TP1_PIPS = 100.0
BE_PIPS = 100.0
BE_MOVE = False
TP_PIPS = 300.0                    # minimum TP on 15m / 30m (1:3 with SL 100)
SL_1H, SL_2H, SL_4H = 150.0, 200.0, 200.0      # stop loss on the 1H / 2H / 4H charts
TP_1H, TP_2H, TP_4H = 450.0, 600.0, 600.0      # minimum take profit on the 1H / 2H / 4H charts
# ---- Setup grade (points -> A+ ... C). Letters: A zone 2 touches, B zone 3+ touches, C one entry candle,
#      D two or more entry candles together, E no major zone inside the minimum TP distance
PTS_A, PTS_B, PTS_C, PTS_D, PTS_E = 1, 2, 1, 2, 1
THR_APLUS, THR_A, THR_BPLUS, THR_B, THR_CPLUS = 5, 4, 3, 2, 1
EXTEND_TP = False
TP_MAX_PIPS = 1000.0
TP_BUF_PIPS = 0.0
BLOCK_SKIP = False
ONE_AT_A_TIME = True
MAX_TRADE_BARS = 200

# ---- Any setting above can be changed WITHOUT editing this file: set an environment variable with the same name
# (on GitHub: repo Settings > Secrets and variables > Actions > Variables tab > New repository variable).
_TUNABLE = ["DAILY_SWING", "H4_SWING", "USE_BIAS", "BIAS_RULE", "WICK_ZONES", "LEFT_BARS", "RIGHT_BARS",
            "MIN_TOUCHES", "SPACING_HRS", "WINDOW_DAYS", "MAX_PIPS_MID", "MERGE_PIPS", "WICK_PCT", "INC_ENGULF",
            "TOL_PIPS", "SWEEP_PIPS", "MIN_GAP", "MAX_GAP", "MUST_LEAVE", "CHAIN_REJ", "EN_SINGLE", "EN_DOUBLE",
            "EN_ENGULF", "EN_DOJI", "EN_HAMMER", "EN_STAR", "EN_HAM_AUTO", "EN_INV_HAM", "SL_1H", "SL_2H", "SL_4H", "TP_1H", "TP_2H", "TP_4H", "PTS_A", "PTS_B", "PTS_C", "PTS_D", "PTS_E", "THR_APLUS", "THR_A", "THR_BPLUS", "THR_B", "THR_CPLUS", "SL_PIPS", "TP1_PIPS", "BE_PIPS", "BE_MOVE", "TP_PIPS",
            "EXTEND_TP", "TP_MAX_PIPS", "OPEN_NOTICE", "TRACK_TRADES", "TP_STEP_PIPS", "WAIT_CANDLE_SEC", "TP_BUF_PIPS", "BLOCK_SKIP", "ONE_AT_A_TIME", "MAX_TRADE_BARS"]
for _n in _TUNABLE:
    _v = os.environ.get(_n, "").strip()
    if _v == "":
        continue
    _d = globals()[_n]
    try:
        if isinstance(_d, bool):
            globals()[_n] = _v.lower() in ("1", "true", "yes", "on")
        elif isinstance(_d, int):
            globals()[_n] = int(float(_v))
        elif isinstance(_d, float):
            globals()[_n] = float(_v)
        else:
            globals()[_n] = _v
    except ValueError:
        print("WARNING: ignoring bad value for %s: %r" % (_n, _v), flush=True)

EQ_TOL = 0.05                      # price tolerance when matching a pivot price to a candle (two feeds differ by cents)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_alerts_state.json")

# =============================================================================
# Time helpers (TradingView / OANDA gold: the trading day starts 17:00 New York)
# =============================================================================
def _get_ny():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        return None


NY = _get_ny()
if NY is None:
    print("WARNING: timezone database not found (pip install tzdata). Using a fixed UTC-5 offset for "
          "the New York day boundary; the Daily / 4H candles can be one hour off in summer.", flush=True)


def shifted_local(ts):
    if NY is not None:
        d = dt.datetime.fromtimestamp(ts, tz=NY)
    else:
        d = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) - dt.timedelta(hours=5)
    return d - dt.timedelta(hours=17)


def key_4h(ts):
    s = shifted_local(ts)
    return (s.date().toordinal(), s.hour // 4)


def key_d(ts):
    s = shifted_local(ts)
    return (s.date().toordinal(), 0)


def key_w(ts):
    s = shifted_local(ts)
    d = s.date() - dt.timedelta(days=(s.weekday() + 1) % 7)      # weeks start on Sunday evening
    return (d.toordinal(), 0)


BUCKET_LEN = {"4h": 4 * 3600, "d": 86400, "w": 7 * 86400}


def bucket_start_ts(kind, key):
    d = dt.date.fromordinal(key[0])
    hours = 17 + (4 * key[1] if kind == "4h" else 0)
    naive = dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=hours)
    if NY is not None:
        return naive.replace(tzinfo=NY).timestamp()
    return (naive + dt.timedelta(hours=5)).replace(tzinfo=dt.timezone.utc).timestamp()


def fmt_time(ts):
    z = dt.timezone(dt.timedelta(hours=DISPLAY_UTC_OFFSET_HOURS))
    return dt.datetime.fromtimestamp(ts, tz=z).strftime("%Y-%m-%d %H:%M")


def log(msg):
    print("[%s] %s" % (fmt_time(time.time()), msg), flush=True)


def market_closed(now):
    d = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
    wd, hr = d.weekday(), d.hour
    return wd == 5 or (wd == 4 and hr >= 22) or (wd == 6 and hr < 22)


# =============================================================================
# Data
# =============================================================================
def http_get(url, headers=None, timeout=30):
    req = urlreq.Request(url, headers=headers or {})
    with urlreq.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read().decode("utf-8")


def parse_yahoo(data):
    res = data["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((int(t), float(o), float(h), float(l), float(c)))
    return out


def fetch_yahoo(symbol, interval, rng):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=false"
           % (urlparse.quote(symbol, safe="="), interval, rng))
    raw = http_get(url, {"User-Agent": "Mozilla/5.0"})
    return parse_yahoo(json.loads(raw))


def parse_twelve(data):
    if data.get("status") == "error":
        raise RuntimeError("Twelve Data: %s" % data.get("message", "error"))
    out = []
    for v in data.get("values", []):
        t = dt.datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
        out.append((int(t), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
    out.sort()
    return out


def fetch_twelve(symbol, interval, size, key):
    url = ("https://api.twelvedata.com/time_series?symbol=%s&interval=%s&outputsize=%d&order=ASC&timezone=UTC&apikey=%s"
           % (urlparse.quote(symbol, safe=""), interval, size, key))
    return parse_twelve(json.loads(http_get(url)))


_TV = None


def fetch_tradingview(interval_min, n_bars, now):
    """Candles straight from TradingView (free) using the open-source 'tvdatafeed' package."""
    global _TV
    try:
        try:
            from tvDatafeed import TvDatafeed, Interval
        except ImportError:
            from tvdatafeed import TvDatafeed, Interval
    except ImportError as e:
        raise RuntimeError("tvdatafeed import problem (%s). If it is not installed, run:  python3 -m pip install --upgrade "
                           "git+https://github.com/rongardF/tvdatafeed.git" % e)
    if False:
        raise RuntimeError("tvdatafeed is not installed. Run:  pip install --upgrade "
                           "git+https://github.com/rongardF/tvdatafeed.git")
    if _TV is None:
        u = TV_USERNAME or os.environ.get("TV_USERNAME", "")
        pw = TV_PASSWORD or os.environ.get("TV_PASSWORD", "")
        _TV = TvDatafeed(u, pw) if (u and pw) else TvDatafeed()
    iv = Interval.in_15_minute if interval_min == 15 else Interval.in_1_hour
    for attr in ("ws_timeout", "_TvDatafeed__ws_timeout"):   # TradingView can be slow: wait up to 60 s
        try:
            setattr(_TV, attr, 60)
        except Exception:  # noqa
            pass
    df = None
    last = None
    for nb in (n_bars, 3000, 1500):                          # ask for fewer candles if the big request times out
        try:
            df = _TV.get_hist(symbol=TV_SYMBOL, exchange=TV_EXCHANGE, interval=iv, n_bars=nb)
            if df is not None and len(df) > 0:
                break
        except Exception as e:  # noqa
            last = e
        _TV = None
        _TV = TvDatafeed(TV_USERNAME or os.environ.get("TV_USERNAME", ""), TV_PASSWORD or os.environ.get("TV_PASSWORD", "")) \
            if (TV_USERNAME or os.environ.get("TV_USERNAME")) else TvDatafeed()
        try:
            _TV.ws_timeout = 60
        except Exception:  # noqa
            pass
    if df is None and last is not None:
        raise RuntimeError("TradingView request failed: %s" % last)
    if df is None or len(df) == 0:
        _TV = None  # force a fresh connection next try
        raise RuntimeError("TradingView returned no data for %s:%s" % (TV_EXCHANGE, TV_SYMBOL))
    out = []
    for idx, row in df.iterrows():
        # tvdatafeed gives naive datetimes in this computer's local time -> .timestamp() converts back to UTC
        t = int(idx.to_pydatetime().timestamp())
        out.append((t, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    out.sort()
    if out[-1][0] > now + 600:
        raise RuntimeError("TradingView candle times look shifted (newest candle is in the future). "
                           "Tell me and I will adjust the time conversion.")
    return out


def drop_open(bars, period, now):
    """Remove the candle that is still forming."""
    return [b for b in bars if b[0] + period <= now]


def resample(bars, period, now):
    """Aggregate 15m bars into a bigger timeframe aligned to the clock (30m)."""
    out = []
    cur = None
    for (t, o, h, l, c) in bars:
        s = (t // period) * period
        if cur is not None and cur[0] == s:
            cur = (s, cur[1], max(cur[2], h), min(cur[3], l), c)
            out[-1] = cur
        else:
            cur = (s, o, h, l, c)
            out.append(cur)
    return [b for b in out if b[0] + period <= now]


LIVE_BAR = None   # the 15m candle that is still forming (used only to notice SL / TP hits a little earlier)


def fetch_all(now):
    """Returns (closed 15m bars, closed 1h bars)."""
    global LIVE_BAR
    key = TWELVE_API_KEY or os.environ.get("TWELVE_DATA_API_KEY", "")
    last_err = None
    for attempt in range(3):
        try:
            if DATA_SOURCE == "twelvedata":
                if not key:
                    raise RuntimeError("TWELVE_API_KEY is empty")
                b15 = fetch_twelve(TWELVE_SYMBOL, "15min", 5000, key)
                b1h = fetch_twelve(TWELVE_SYMBOL, "1h", 5000, key)
            elif DATA_SOURCE == "tradingview":
                b15 = fetch_tradingview(15, 5000, now)
                b1h = fetch_tradingview(60, 5000, now)
            else:
                b15 = fetch_yahoo(YAHOO_SYMBOL, "15m", "60d")
                b1h = fetch_yahoo(YAHOO_SYMBOL, "1h", "1y")
            LIVE_BAR = b15[-1] if (b15 and b15[-1][0] + 900 > now) else None
            b15 = drop_open(b15, 900, now)
            b1h = drop_open(b1h, 3600, now)
            if len(b15) < 200 or len(b1h) < 200:
                raise RuntimeError("not enough data (15m: %d bars, 1h: %d bars)" % (len(b15), len(b1h)))
            return b15, b1h
        except Exception as e:  # noqa
            last_err = e
            time.sleep(3 + attempt * 5)
    raise RuntimeError("data download failed: %s" % last_err)


# =============================================================================
# Pivots and bias
# =============================================================================
def pivot_high(vals, p, left, right):
    if p - left < 0 or p + right >= len(vals):
        return None
    c = vals[p]
    for k in range(1, left + 1):
        if not (c > vals[p - k]):
            return None
    for k in range(1, right + 1):
        if not (c >= vals[p + k]):
            return None
    return c


def pivot_low(vals, p, left, right):
    if p - left < 0 or p + right >= len(vals):
        return None
    c = vals[p]
    for k in range(1, left + 1):
        if not (c < vals[p - k]):
            return None
    for k in range(1, right + 1):
        if not (c <= vals[p + k]):
            return None
    return c


def htf_bias_series(H, L, C, length):
    """Bias for the bar being formed at index t (uses only completed bars <= t-1). Index 0..n."""
    n = len(C)
    out = [(0, 0)]
    h1 = h2 = l1 = l2 = None
    for t in range(1, n + 1):
        p = t - 1 - length
        if p - length >= 0:
            ph = pivot_high(H, p, length, length)
            if ph is not None:
                h2, h1 = h1, ph
            pl = pivot_low(L, p, length, length)
            if pl is not None:
                l2, l1 = l1, pl
        c = C[t - 1]
        raw = 0
        if None not in (h1, h2, l1, l2):
            if h1 > h2 and l1 > l2:
                raw = 1
            elif h1 < h2 and l1 < l2:
                raw = -1
        ev = 0
        if raw == 1 and c > h1:
            ev = 1
        elif raw == -1 and c < l1:
            ev = -1
        elif raw == 1 and c < l1:
            ev = -2
        elif raw == -1 and c > h1:
            ev = 2
        bias = 0 if ev in (2, -2) else raw
        out.append((bias, ev))
    return out


class Agg:
    pass


def aggregate(h1, keyfn, kind, now):
    """1h bars -> 4H / Daily / Weekly bars (only COMPLETED ones are kept)."""
    a = Agg()
    a.keys, a.o, a.h, a.l, a.c, a.last_t = [], [], [], [], [], []
    for (t, o, h, l, c) in h1:
        k = keyfn(t)
        if a.keys and a.keys[-1] == k:
            a.h[-1] = max(a.h[-1], h)
            a.l[-1] = min(a.l[-1], l)
            a.c[-1] = c
            a.last_t[-1] = t
        else:
            a.keys.append(k)
            a.o.append(o)
            a.h.append(h)
            a.l.append(l)
            a.c.append(c)
            a.last_t.append(t)
    done = 0
    for k in a.keys:
        if now >= bucket_start_ts(kind, k) + BUCKET_LEN[kind]:
            done += 1
        else:
            break
    for name in ("keys", "o", "h", "l", "c", "last_t"):
        setattr(a, name, getattr(a, name)[:done])
    return a


# =============================================================================
# KD zone logic
# =============================================================================
class Levels:
    def __init__(self):
        self.hi, self.lo, self.tc, self.first, self.last = [], [], [], [], []


def dyn_max_pips(tf_min):
    if tf_min < 15:
        return MAX_PIPS_LOW
    if tf_min <= 60:
        return MAX_PIPS_MID
    return MAX_PIPS_HTF


def process_level(Lv, p, ev_time, ev_idx, chart, now, tf_min):
    if p is None:
        return
    if ev_time < now - WINDOW_DAYS * 86400:
        return
    exact = 0
    if ev_idx >= 0:
        exact = ev_idx
        for k in range(0, 501):
            j = ev_idx - k
            if j < 0:
                break
            if WICK_ZONES:
                m = abs(chart.h[j] - p) <= EQ_TOL or abs(chart.l[j] - p) <= EQ_TOL
            else:
                m = abs(chart.c[j] - p) <= EQ_TOL or abs(chart.o[j] - p) <= EQ_TOL
            if m:
                exact = j
                break
    allowed = dyn_max_pips(tf_min) * PIP_SIZE
    for j in range(len(Lv.hi)):
        test_h = max(Lv.hi[j], p)
        test_l = min(Lv.lo[j], p)
        if test_h - test_l <= allowed:
            if ev_time - Lv.last[j] > SPACING_HRS * 3600:
                Lv.tc[j] += 1
                Lv.hi[j] = test_h
                Lv.lo[j] = test_l
                Lv.last[j] = ev_time
            return
    Lv.hi.append(p)
    Lv.lo.append(p)
    Lv.tc.append(1)
    Lv.first.append(exact)
    Lv.last.append(ev_time)


def rebuild_valid(Lv, tf_min):
    """Zones with enough touches, merged / eliminated like the KD indicator (no distance filter)."""
    vt, vb, vc, vs = [], [], [], []
    for i in range(len(Lv.hi)):
        if Lv.tc[i] >= MIN_TOUCHES:
            vt.append(Lv.hi[i])
            vb.append(Lv.lo[i])
            vc.append(Lv.tc[i])
            vs.append(Lv.first[i])
    max_allowed = dyn_max_pips(tf_min) * PIP_SIZE
    merge_dist = MERGE_PIPS * PIP_SIZE
    changed = True
    while changed:
        changed = False
        n = len(vt)
        if n > 1:
            for i in range(0, n - 1):
                for j in range(i + 1, n):
                    mid_i = (vt[i] + vb[i]) / 2
                    mid_j = (vt[j] + vb[j]) / 2
                    if abs(mid_i - mid_j) <= merge_dist:
                        ti, tj = vc[i], vc[j]
                        if abs(ti - tj) > 2:
                            r = j if ti > tj else i
                            for arr in (vt, vb, vc, vs):
                                del arr[r]
                        else:
                            c_top = max(vt[i], vt[j])
                            c_bot = min(vb[i], vb[j])
                            if c_top - c_bot > max_allowed:
                                center = (c_top + c_bot) / 2
                                c_top = center + max_allowed / 2
                                c_bot = center - max_allowed / 2
                            vt[i], vb[i] = c_top, c_bot
                            vc[i] = max(ti, tj)
                            vs[i] = min(vs[i], vs[j])
                            for arr in (vt, vb, vc, vs):
                                del arr[j]
                        changed = True
                        break
                if changed:
                    break
    min_pad = PIP_SIZE * 4
    for i in range(len(vt)):
        if vt[i] - vb[i] < min_pad:
            vt[i] += min_pad / 2
            vb[i] -= min_pad / 2
    return vt, vb, vc, vs


# =============================================================================
# Chart
# =============================================================================
class Chart:
    def __init__(self, bars):
        self.t = [b[0] for b in bars]
        self.o = [b[1] for b in bars]
        self.h = [b[2] for b in bars]
        self.l = [b[3] for b in bars]
        self.c = [b[4] for b in bars]


def candle_flags(chart):
    n = len(chart.t)
    F = {k: [False] * n for k in ("bullRej", "bearRej", "bullEng", "bearEng", "doji", "ham", "star", "inv")}
    for i in range(n):
        o, h, l, c = chart.o[i], chart.h[i], chart.l[i], chart.c[i]
        rng = h - l
        body = abs(c - o)
        lw = min(o, c) - l
        uw = h - max(o, c)
        bull_eng = bear_eng = False
        if i > 0:
            po, pc = chart.o[i - 1], chart.c[i - 1]
            bull_eng = c > o and pc < po and c >= po and o <= pc
            bear_eng = c < o and pc > po and c <= po and o >= pc
        F["bullEng"][i] = bull_eng
        F["bearEng"][i] = bear_eng
        F["bullRej"][i] = (rng > 0 and lw / rng >= WICK_PCT / 100.0 and c >= l + rng * 0.6) or (INC_ENGULF and bull_eng)
        F["bearRej"][i] = (rng > 0 and uw / rng >= WICK_PCT / 100.0 and c <= l + rng * 0.4) or (INC_ENGULF and bear_eng)
        F["doji"][i] = rng > 0 and body <= 0.1 * rng
        F["ham"][i] = rng > 0 and lw >= 2.0 * body and uw <= 0.25 * rng and lw >= 0.5 * rng
        long_up = rng > 0 and uw >= 2.0 * body and lw <= 0.25 * rng and uw >= 0.5 * rng
        prev_bull = i > 0 and chart.c[i - 1] > chart.o[i - 1]
        prev_bear = i > 0 and chart.c[i - 1] < chart.o[i - 1]
        F["star"][i] = long_up and prev_bull      # shooting star: SELL, after a bullish candle
        F["inv"][i] = long_up and prev_bear       # inverted hammer: BUY, after a bearish candle
    return F


# =============================================================================
# Trade plan + simulation
# =============================================================================
def sl_for(tf_min):
    return {60: SL_1H, 120: SL_2H, 240: SL_4H}.get(tf_min, SL_PIPS)


def tp_for(tf_min):
    return {60: TP_1H, 120: TP_2H, 240: TP_4H}.get(tf_min, TP_PIPS)


def tf_name(tf_min):
    return "%dm" % tf_min if tf_min < 60 else "%dH" % (tf_min // 60)


def grade_points(zt, trig, blocked):
    p = PTS_B if zt >= 3 else PTS_A if zt == 2 else 0
    p += PTS_D if len(trig.split(" + ")) >= 2 else PTS_C
    p += 0 if blocked else PTS_E
    return p


def grade_text(p):
    return ("A+" if p >= THR_APLUS else "A" if p >= THR_A else "B+" if p >= THR_BPLUS else
            "B" if p >= THR_B else "C+" if p >= THR_CPLUS else "C")


def grade_letters(zt, trig, blocked):
    out = []
    if zt >= 3:
        if PTS_B > 0:
            out.append("B")
    elif zt == 2:
        if PTS_A > 0:
            out.append("A")
    if len(trig.split(" + ")) >= 2:
        if PTS_D > 0:
            out.append("D")
    elif PTS_C > 0:
        out.append("C")
    if not blocked and PTS_E > 0:
        out.append("E")
    return ",".join(out) if out else "-"


def plan_trade(d, entry, vt, vb, tf_min=15):
    sl_d = sl_for(tf_min) * PIP_SIZE
    tp_min = tp_for(tf_min) * PIP_SIZE
    tp_cap = max(TP_MAX_PIPS, tp_for(tf_min)) * PIP_SIZE
    buf = TP_BUF_PIPS * PIP_SIZE
    near = None
    for i in range(len(vt)):
        edge = vb[i] if d == 1 else vt[i]
        ahead = edge > entry if d == 1 else edge < entry
        if ahead:
            dist = abs(edge - entry)
            if near is None or dist < near:
                near = dist
    blocked = near is not None and near < tp_min
    tp_d = tp_min
    if EXTEND_TP and near is not None and near >= tp_min:
        tp_d = max(tp_min, min(near - buf, tp_cap))
    ok = not (BLOCK_SKIP and blocked)
    return ok, entry - d * sl_d, entry + d * tp_d, tp_d / sl_d, blocked


def simulate(chart, i, rd, entry, sl, tp, tp1, be_trig):
    n = len(chart.t)
    tp1_hit = be_on = be_touch = False
    stat = 0
    exit_i = -1
    max_n = min(MAX_TRADE_BARS, n - 1 - i)
    for k in range(1, max_n + 1):
        kk = i + k
        hk, lk = chart.h[kk], chart.l[kk]
        ent_t = (lk <= entry) if rd == 1 else (hk >= entry)
        if (not BE_MOVE) and tp1_hit and ent_t:
            be_touch = True
        stop_l = entry if (BE_MOVE and be_on) else sl
        sl_h = (lk <= stop_l) if rd == 1 else (hk >= stop_l)
        tp_h = (hk >= tp) if rd == 1 else (lk <= tp)
        t1_r = (hk >= tp1) if rd == 1 else (lk <= tp1)
        be_r = (hk >= be_trig) if rd == 1 else (lk <= be_trig)
        if sl_h:
            if BE_MOVE and be_on:
                stat = 4
                be_touch = True
            else:
                stat = -1
            exit_i = kk
            break
        elif tp_h:
            tp1_hit = True
            stat = 2
            exit_i = kk
            break
        else:
            if t1_r:
                tp1_hit = True
            if BE_MOVE and be_r and not be_on:
                be_on = True
            if k >= MAX_TRADE_BARS:
                stat = 3
                exit_i = kk
                break
    return stat, exit_i, tp1_hit, be_touch


# =============================================================================
# Scan: every entry candle on every zone (same logic as the Pine scan)
# =============================================================================
def scan(chart, zones, allow_l, allow_s, F, now, bias_dir=None, tf_min=15):
    vt, vb, vc, vs = zones
    n = len(chart.t)
    nz = len(vt)
    tol = TOL_PIPS * PIP_SIZE
    sweep = SWEEP_PIPS * PIP_SIZE
    test_start = now - TEST_DAYS * 86400
    a_bar = [-1] * (nz * 2)
    a_left = [False] * (nz * 2)
    trades = []
    st = dict(sig=0, win=0, loss=0, be=0, tp1=0, bet=0, exp=0, sum_r=0.0, sum_pips=0.0,
              pairs=0, blk_bias=0, long=0, short=0, grades={})
    free_from = -1

    for i in range(n):
        c_c, h_i, l_i = chart.c[i], chart.h[i], chart.l[i]
        l_e = min(l_i, chart.l[i - 1]) if i > 0 else l_i
        h_e = max(h_i, chart.h[i - 1]) if i > 0 else h_i

        # (a) update the armed 1st rejections
        if EN_DOUBLE:
            for z in range(nz):
                zt, zb = vt[z], vb[z]
                for k in (0, 1):
                    ix = z * 2 + k
                    ab = a_bar[ix]
                    if ab >= 0:
                        broke = (c_c < zb - tol) if k == 0 else (c_c > zt + tol)
                        left = (c_c > zt) if k == 0 else (c_c < zb)
                        if broke or i - ab > MAX_GAP:
                            a_bar[ix] = -1
                            a_left[ix] = False
                        elif left:
                            a_left[ix] = True

        # (b) entry candles on this bar
        t_sgl_l = EN_SINGLE and F["bullRej"][i]
        t_sgl_s = EN_SINGLE and F["bearRej"][i]
        t_eng_l = EN_ENGULF and F["bullEng"][i]
        t_eng_s = EN_ENGULF and F["bearEng"][i]
        t_doji = EN_DOJI and F["doji"][i]
        t_ham_l = EN_HAMMER and F["ham"][i]
        t_star_s = EN_STAR and F["star"][i]
        bd_i = bias_dir[i] if bias_dir else 0
        t_inv_l = EN_INV_HAM and F["inv"][i]
        t_auto_l = EN_HAM_AUTO and F["ham"][i] and bd_i != -1
        t_auto_s = EN_HAM_AUTO and F["star"][i] and bd_i != 1
        any_trig = (F["bullRej"][i] or F["bearRej"][i] or t_eng_l or t_eng_s or t_doji or t_ham_l or t_star_s
                    or t_inv_l or t_auto_l or t_auto_s)
        if not any_trig:
            continue

        for rk in (0, 1):
            rd = 1 if rk == 0 else -1
            is_rej = F["bullRej"][i] if rk == 0 else F["bearRej"][i]
            w_sgl = t_sgl_l if rk == 0 else t_sgl_s
            w_eng = t_eng_l if rk == 0 else t_eng_s
            w_doj = t_doji
            w_ham = t_ham_l if rk == 0 else False
            w_star = t_star_s if rk == 1 else False
            w_dbl = EN_DOUBLE and is_rej
            w_auto = t_auto_l if rk == 0 else t_auto_s
            w_inv = t_inv_l if rk == 0 else False
            if not (w_sgl or w_eng or w_doj or w_ham or w_star or w_dbl or w_auto or w_inv):
                continue

            best_z, best_tch, best_gap, best_nm = -1, 0, 0, ""
            for z in range(nz):
                if i < vs[z]:
                    continue
                zt, zb = vt[z], vb[z]
                zm = (zt + zb) / 2
                if rk == 0:
                    tap_n = l_i <= zt and l_i >= zb - sweep and c_c >= zm
                    tap_e = l_e <= zt and l_e >= zb - sweep and c_c >= zm
                else:
                    tap_n = h_i >= zb and h_i <= zt + sweep and c_c <= zm
                    tap_e = h_e >= zb and h_e <= zt + sweep and c_c <= zm
                nm = ""
                gap_z = 0
                if w_sgl and tap_n:
                    nm = "Single rejection"
                if w_dbl and tap_n:
                    ix = z * 2 + rk
                    ab = a_bar[ix]
                    away = (c_c > zt) if rk == 0 else (c_c < zb)
                    if ab >= 0:
                        gap_b = i - ab
                        if gap_b >= MIN_GAP:
                            if (not MUST_LEAVE) or a_left[ix]:
                                nm += (" + " if nm else "") + "Double rejection"
                                gap_z = gap_b
                                if CHAIN_REJ:
                                    a_bar[ix] = i
                                    a_left[ix] = away
                                else:
                                    a_bar[ix] = -1
                                    a_left[ix] = False
                            else:
                                a_bar[ix] = i
                                a_left[ix] = away
                    else:
                        a_bar[ix] = i
                        a_left[ix] = away
                if w_eng and tap_e:
                    nm += (" + " if nm else "") + "Engulf"
                if w_doj and tap_n:
                    nm += (" + " if nm else "") + "Doji"
                if w_auto and tap_n and not (w_ham if rk == 0 else w_star):
                    nm += (" + " if nm else "") + ("Hammer" if rk == 0 else "Shooting star")
                if w_inv and tap_n:
                    nm += (" + " if nm else "") + "Inverted hammer"
                if w_ham and tap_n:
                    nm += (" + " if nm else "") + "Hammer"
                if w_star and tap_n:
                    nm += (" + " if nm else "") + "Shooting star"
                if nm:
                    if best_z < 0 or vc[z] > best_tch:
                        best_z, best_tch, best_gap, best_nm = z, vc[z], gap_z, nm

            if best_z < 0:
                continue
            ok_bias = allow_l[i] if rd == 1 else allow_s[i]
            ok_test = chart.t[i] >= test_start
            free_now = (not ONE_AT_A_TIME) or i >= free_from
            if not ok_test:
                continue
            st["pairs"] += 1
            if not ok_bias:
                st["blk_bias"] += 1
                continue
            if not free_now:
                continue
            ok_t, sl_t, tp_t, rr_t, blk_t = plan_trade(rd, c_c, vt, vb, tf_min)
            if not ok_t:
                continue
            gr = grade_text(grade_points(best_tch, best_nm, blk_t))
            gl = grade_letters(best_tch, best_nm, blk_t)
            tp1_t = c_c + rd * min(TP1_PIPS * PIP_SIZE, abs(tp_t - c_c))
            be_trig = c_c + rd * BE_PIPS * PIP_SIZE
            stat, exit_i, tp1_hit, be_touch = simulate(chart, i, rd, c_c, sl_t, tp_t, tp1_t, be_trig)

            st["sig"] += 1
            st["long" if rd == 1 else "short"] += 1
            gs = st["grades"].setdefault(gr, dict(win=0, loss=0, be=0, exp=0, open=0))
            gs[{-1: "loss", 2: "win", 4: "be", 3: "exp", 0: "open"}[stat]] += 1
            if stat == -1:
                st["loss"] += 1
                st["sum_r"] -= 1.0
                st["sum_pips"] -= abs(c_c - sl_t) / PIP_SIZE
            elif stat == 2:
                st["win"] += 1
                st["sum_r"] += rr_t
                st["sum_pips"] += abs(tp_t - c_c) / PIP_SIZE
            elif stat == 4:
                st["be"] += 1
            elif stat == 3:
                st["exp"] += 1
            if stat not in (0, 3):
                if tp1_hit:
                    st["tp1"] += 1
                if be_touch:
                    st["bet"] += 1
            free_from = 10 ** 9 if stat == 0 else exit_i
            trades.append(dict(idx=i, time=chart.t[i], dir=rd, trig=best_nm, entry=c_c, sl=sl_t, tp1=tp1_t, tp=tp_t,
                               rr=rr_t, status=stat, zone_touches=best_tch, grade=gr, letters=gl, gap=best_gap,
                               zone_top=vt[best_z], zone_bot=vb[best_z]))
    return trades, st


# =============================================================================
# Full analysis
# =============================================================================
def analyze(bars, h1, tf_min, now):
    chart = Chart(bars)
    n = len(chart.t)
    tf_sec = tf_min * 60

    # ---- higher timeframes (completed candles only)
    agg = {}
    for kind, fn in (("4h", key_4h), ("d", key_d), ("w", key_w)):
        agg[kind] = aggregate(h1, fn, kind, now)

    # ---- bias for every chart bar
    bias_d = htf_bias_series(agg["d"].h, agg["d"].l, agg["d"].c, DAILY_SWING)
    bias_4 = htf_bias_series(agg["4h"].h, agg["4h"].l, agg["4h"].c, H4_SWING)
    key_funcs = {"4h": key_4h, "d": key_d, "w": key_w}
    last_idx = {"4h": {}, "d": {}, "w": {}}
    d_b, h_b, bias_dir, allow_l, allow_s = [], [], [], [], []
    for i in range(n):
        t = chart.t[i]
        kd, k4 = key_d(t), key_4h(t)
        last_idx["d"][kd] = i
        last_idx["4h"][k4] = i
        last_idx["w"][key_w(t)] = i
        bd = bias_d[bisect.bisect_left(agg["d"].keys, kd)][0]
        b4 = bias_4[bisect.bisect_left(agg["4h"].keys, k4)][0]
        if BIAS_RULE == "Daily + 4H agree":
            bdir = bd if (bd != 0 and bd == b4) else 0
        elif BIAS_RULE == "Daily only":
            bdir = bd
        else:
            bdir = b4
        d_b.append(bd)
        h_b.append(b4)
        bias_dir.append(bdir)
        allow_l.append((not USE_BIAS) or bdir == 1)
        allow_s.append((not USE_BIAS) or bdir == -1)

    # ---- pivot events (KD logic): local chart pivots + 4H / D / W pivots
    if WICK_ZONES:
        src_h, src_l = chart.h, chart.l
    else:
        src_h = [max(o, c) for o, c in zip(chart.o, chart.c)]
        src_l = [min(o, c) for o, c in zip(chart.o, chart.c)]
    events = []
    for p in range(LEFT_BARS, n - RIGHT_BARS):
        ph = pivot_high(src_h, p, LEFT_BARS, RIGHT_BARS)
        pl = pivot_low(src_l, p, LEFT_BARS, RIGHT_BARS)
        di = p + RIGHT_BARS
        if ph is not None:
            events.append((chart.t[di], 0, di, ph))
        if pl is not None:
            events.append((chart.t[di], 1, di, pl))
    for order, kind in ((2, "4h"), (4, "d"), (6, "w")):
        a = agg[kind]
        if WICK_ZONES:
            sh, sl_ = a.h, a.l
        else:
            sh = [max(o, c) for o, c in zip(a.o, a.c)]
            sl_ = [min(o, c) for o, c in zip(a.o, a.c)]
        for t in range(LEFT_BARS + RIGHT_BARS, len(a.keys)):
            p = t - RIGHT_BARS
            ph = pivot_high(sh, p, LEFT_BARS, RIGHT_BARS)
            pl = pivot_low(sl_, p, LEFT_BARS, RIGHT_BARS)
            idx = last_idx[kind].get(a.keys[t], -1)
            ev_time = chart.t[idx] if idx >= 0 else a.last_t[t] + 3600 - tf_sec
            if ph is not None:
                events.append((ev_time, order, idx, ph))
            if pl is not None:
                events.append((ev_time, order + 1, idx, pl))
    events.sort(key=lambda e: (e[0], e[1]))

    Lv = Levels()
    for (ev_time, order, idx, val) in events:
        process_level(Lv, val, ev_time, idx, chart, now, tf_min)
    zones = rebuild_valid(Lv, tf_min)

    F = candle_flags(chart)
    trades, st = scan(chart, zones, allow_l, allow_s, F, now, bias_dir, tf_min)
    return dict(chart=chart, zones=zones, trades=trades, stats=st,
                bias=(d_b[-1], h_b[-1], bias_dir[-1]), n_levels=len(Lv.hi))


# =============================================================================
# Messages
# =============================================================================
def bias_word(b):
    return "Bullish" if b == 1 else "Bearish" if b == -1 else "Range"


def build_message(tf_min, tr, res):
    d = tr["dir"]
    head = ("🟢 BUY" if d == 1 else "🔴 SELL") + " XAUUSD " + tf_name(tf_min)
    pips_sl = abs(tr["entry"] - tr["sl"]) / PIP_SIZE
    pips_tp = abs(tr["tp"] - tr["entry"]) / PIP_SIZE
    pips_t1 = abs(tr["tp1"] - tr["entry"]) / PIP_SIZE
    lines = [head,
             "Setup: %s (%s)" % (tr["grade"], tr["letters"]),
             "Entry candle: %s" % tr["trig"],
             "Entry: %.2f" % tr["entry"],
             "SL: %.2f (%d pips)" % (tr["sl"], round(pips_sl)),
             "TP1: %.2f (%d pips)" % (tr["tp1"], round(pips_t1)),
             "TP: %.2f (%d pips)  R:R 1:%.2f" % (tr["tp"], round(pips_tp), tr["rr"]),
             "Zone: %.2f - %.2f (%d touches)" % (tr["zone_bot"], tr["zone_top"], tr["zone_touches"])]
    if BE_MOVE:
        lines.append("Move SL to entry after +%d pips" % round(BE_PIPS))
    if USE_BIAS:
        lines.append("Bias: Daily %s / 4H %s" % (bias_word(res["bias"][0]), bias_word(res["bias"][1])))
    else:
        lines.append("Bias filter OFF")
    lines.append("Candle closed: %s (UTC+%d)" % (fmt_time(tr["time"] + tf_min * 60), DISPLAY_UTC_OFFSET_HOURS))
    return "\n".join(lines)


def _send_one(token, chat, text):
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urlparse.urlencode({"chat_id": chat, "text": text}).encode("utf-8")
    last = None
    for attempt in range(3):
        try:
            with urlreq.urlopen(urlreq.Request(url, data=data), timeout=30, context=SSL_CTX) as r:
                body = json.loads(r.read().decode("utf-8"))
                if body.get("ok"):
                    return True
                last = body
        except urlerr.HTTPError as e:
            last = "%s %s" % (e.code, e.read().decode("utf-8", "ignore"))
            if e.code in (400, 403):          # wrong id / user blocked the bot: retrying will not help
                break
        except Exception as e:  # noqa
            last = e
        time.sleep(2 + attempt * 3)
    raise RuntimeError("Telegram send failed: %s" % last)


def send_telegram(text):
    """Send to every chat id in TELEGRAM_CHAT_ID (several ids separated by commas). One failing id never blocks the others."""
    token = TELEGRAM_BOT_TOKEN or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    raw = TELEGRAM_CHAT_ID or os.environ.get("TELEGRAM_CHAT_ID", "")
    chats = [c.strip() for c in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",") if c.strip()]
    if not token or not chats:
        raise RuntimeError("Telegram token / chat id missing (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    ok = 0
    errs = []
    for chat in chats:
        try:
            _send_one(token, chat, text)
            ok += 1
        except Exception as e:  # noqa
            errs.append("%s: %s" % (chat, e))
            log("could not send to chat id %s: %s" % (chat, e))
    if ok == 0:
        raise RuntimeError("; ".join(errs))
    return True


def find_chat_id():
    token = TELEGRAM_BOT_TOKEN or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first.")
        return
    data = json.loads(http_get("https://api.telegram.org/bot%s/getUpdates" % token))
    seen = set()
    for u in data.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        ch = m.get("chat") or {}
        if ch.get("id") is not None and ch["id"] not in seen:
            seen.add(ch["id"])
            print("chat id: %s   (%s %s)" % (ch["id"], ch.get("first_name", ""), ch.get("username", "")))
    if not seen:
        print("No messages found. Open your bot in Telegram, press Start, send it a message, then run this again.")


# =============================================================================
# Runner
# =============================================================================
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:  # noqa
        log("could not save state: %s" % e)


def resample_hours(b1h, hours, now):
    """2H / 4H candles from 1h candles, aligned to the 17:00 New York trading day (like TradingView's forex session)."""
    out = []
    cur = None
    cur_key = None
    for (t, o, h, l, c) in b1h:
        s_ = shifted_local(t)
        k = (s_.date().toordinal(), s_.hour // hours)
        if k == cur_key:
            cur = (cur[0], cur[1], max(cur[2], h), min(cur[3], l), c)
            out[-1] = cur
        else:
            cur_key = k
            cur = (t, o, h, l, c)
            out.append(cur)
    # a bucket is complete when the next hour would start a new bucket
    res = []
    for b in out:
        s_ = shifted_local(b[0] + 3600 * 0)
        start_h = (s_.hour // hours) * hours
        end_ts = b[0] + (hours - (s_.hour - start_h)) * 3600
        if end_ts <= now:
            res.append(b)
    return res


def chart_bars_for(tf_min, b15, now, b1h=None):
    if tf_min == 15:
        return b15
    if tf_min < 60:
        return resample(b15, tf_min * 60, now)
    if b1h is None:
        b1h = resample(b15, 3600, now)
    return b1h if tf_min == 60 else resample_hours(b1h, tf_min // 60, now)


def find_market_open(b15):
    """Opening time of the latest session that began after a closed period (daily break, weekend or holiday), or None."""
    gap = OPEN_GAP_HRS * 3600
    for i in range(len(b15) - 1, 0, -1):
        if b15[i][0] - b15[i - 1][0] >= gap:
            return b15[i][0]
    return None


def build_open_message(open_t, res, last_close):
    d, h, tb = res["bias"]
    lines = ["🟢 XAUUSD market is open",
             "First candle: %s (UTC+3)" % fmt_time(open_t),
             "Price: %.2f" % last_close]
    if USE_BIAS:
        lines.append("Bias: Daily %s / 4H %s" % (bias_word(d), bias_word(h)))
        lines.append("Trade bias: %s" % {1: "BUY only", -1: "SELL only", 0: "NO TRADE (wait)"}[tb])
    lines.append("Watching 15m and 30m. I will message you when a possible entry candle closes.")
    return "\n".join(lines)


def expected_last_bar(now):
    """Open time of the 15m candle that should have just closed (None while the market is closed)."""
    d = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
    if market_closed(now) or d.hour == 22:        # weekend, or the daily 22:00-23:00 UTC break
        return None
    return int(now // 900) * 900 - 900


def fetch_fresh(now):
    """fetch_all, but if we are right after a candle close and the new candle is not there yet, wait a little."""
    b15, b1h = fetch_all(now)
    exp = expected_last_bar(now)
    waited = 0
    while exp is not None and b15[-1][0] == exp - 900 and (now - (exp + 900)) < 120 and waited < WAIT_CANDLE_SEC:
        time.sleep(5)
        waited += 5
        now = time.time()
        b15, b1h = fetch_all(now)
    if waited:
        log("waited %ds for the new candle (%s)" % (waited, "arrived" if b15[-1][0] >= exp else "not yet"))
    return now, b15, b1h


# =============================================================================
# Follow open trades: message on SL and on every TP step
# =============================================================================
def trade_levels(tr):
    """TP steps for a trade: every TP_STEP_PIPS of profit, the last one is the full TP."""
    d = tr["dir"]
    tp_pips = abs(tr["tp"] - tr["entry"]) / PIP_SIZE
    levels = []
    k = 1
    while k * TP_STEP_PIPS < tp_pips - 0.5:
        levels.append(round(tr["entry"] + d * k * TP_STEP_PIPS * PIP_SIZE, 2))
        k += 1
    levels.append(round(tr["tp"], 2))
    return levels


def register_trade(state, tf, tr):
    if not TRACK_TRADES:
        return
    lst = state.setdefault("trades", [])
    lst.append(dict(tf=tf, time=tr["time"], dir=tr["dir"], trig=tr["trig"], entry=tr["entry"], sl=tr["sl"],
                    tp=tr["tp"], levels=trade_levels(tr), hit=0, checked=tr["time"] + tf * 60 - 900))


def _side(d):
    return "SELL" if d == -1 else "BUY"


def trade_head(t):
    return "%s XAUUSD %s (%s)\nEntry: %.2f" % (_side(t["dir"]), tf_name(t["tf"]), t["trig"], t["entry"])


def follow_trades(state, b15, now, send=True):
    """Check the open trades against the newest candles. Returns the number of messages sent."""
    trades = state.get("trades") or []
    if not trades:
        return 0
    sent = 0
    keep = []
    live = LIVE_BAR
    for t in trades:
        d = t["dir"]
        start = t["time"] + t["tf"] * 60            # first candle after the entry candle
        bars = [b for b in b15 if b[0] >= start and b[0] > t["checked"]]
        if live is not None and live[0] >= start:
            bars.append(live)
        closed = False
        n_bars_done = int((b15[-1][0] - start) // 900) + 1 if b15 and b15[-1][0] >= start else 0
        for (bt, o, h, l, c) in bars:
            sl_hit = (l <= t["sl"]) if d == 1 else (h >= t["sl"])
            if sl_hit:                                # same rule as the chart: if one candle touches SL it counts as SL
                done = t["hit"]
                pips = abs(t["sl"] - t["entry"]) / PIP_SIZE
                lines = ["❌ SL HIT  -%d pips" % round(pips), trade_head(t), "SL: %.2f" % t["sl"]]
                if done:
                    lines.append("(TP1%s had been hit before)" % ("" if done == 1 else "-TP%d" % done))
                lines.append("Trade closed. Detected: %s (UTC+%d)" % (fmt_time(now), DISPLAY_UTC_OFFSET_HOURS))
                msg = "\n".join(lines)
                log("TRADE UPDATE:\n%s" % msg)
                if send:
                    send_telegram(msg)
                sent += 1
                closed = True
                break
            while t["hit"] < len(t["levels"]):
                lv = t["levels"][t["hit"]]
                if (h >= lv) if d == 1 else (l <= lv):
                    t["hit"] += 1
                    k = t["hit"]
                    last = (k == len(t["levels"]))
                    pips = abs(lv - t["entry"]) / PIP_SIZE
                    if last:
                        lines = ["🏁 FULL TP HIT (TP%d)  +%d pips" % (k, round(pips)), trade_head(t), "TP%d: %.2f" % (k, lv),
                                 "Trade closed."]
                    else:
                        lines = ["✅ TP%d HIT  +%d pips" % (k, round(pips)), trade_head(t), "TP%d: %.2f" % (k, lv),
                                 "Next: TP%d %.2f   |   SL still %.2f" % (k + 1, t["levels"][k], t["sl"])]
                    lines.append("Detected: %s (UTC+%d)" % (fmt_time(now), DISPLAY_UTC_OFFSET_HOURS))
                    msg = "\n".join(lines)
                    log("TRADE UPDATE:\n%s" % msg)
                    if send:
                        send_telegram(msg)
                    sent += 1
                    if last:
                        closed = True
                        break
                else:
                    break
            if closed:
                break
            if live is None or bt != live[0]:
                t["checked"] = max(t["checked"], bt)
        if closed:
            continue
        if n_bars_done >= MAX_TRADE_BARS * max(1, t["tf"] // 15):
            log("trade %s %s %.2f expired, no longer followed" % (_side(d), tf_name(t["tf"]), t["entry"]))
            continue
        keep.append(t)
    state["trades"] = keep
    return sent


def run_cycle(state, send=True):
    now, b15, b1h = fetch_fresh(time.time())
    sent = 0
    res15 = None
    for tf in TIMEFRAMES:
        bars = chart_bars_for(tf, b15, now, b1h)
        res = analyze(bars, b1h, tf, now)
        if tf == 15:
            res15 = res
        last_t = bars[-1][0]
        prev = state.get(str(tf))
        if prev is None:
            state[str(tf)] = last_t
            log("%s: started. Last closed candle %s. Watching for new entries." % (tf_name(tf), fmt_time(last_t)))
            continue
        fresh = [t for t in res["trades"]
                 if t["time"] > prev and t["time"] >= last_t - (MAX_CATCHUP_BARS - 1) * tf * 60]
        for tr in fresh:
            msg = build_message(tf, tr, res)
            log("%s SIGNAL:\n%s" % (tf_name(tf), msg))
            if send:
                send_telegram(msg)
                register_trade(state, tf, tr)
            sent += 1
        if not fresh:
            log("%s: candle %s closed, no entry." % (tf_name(tf), fmt_time(last_t)))
        state[str(tf)] = max(prev, last_t)
    sent += follow_trades(state, b15, now, send)
    if OPEN_NOTICE and res15 is not None:
        open_t = find_market_open(b15)
        if open_t is not None and open_t > state.get("open_notified", 0):
            if now - open_t <= OPEN_WINDOW_HRS * 3600:
                msg = build_open_message(open_t, res15, b15[-1][4])
                log("MARKET OPEN:\n%s" % msg)
                if send:
                    send_telegram(msg)
            state["open_notified"] = open_t
    save_state(state)
    return sent


def seconds_to_next_boundary(now):
    nxt = min((int(now) // (tf * 60) + 1) * tf * 60 for tf in TIMEFRAMES)
    return nxt + CHECK_DELAY_SEC - now


def run_forever():
    state = load_state()
    fails = 0
    last_fail_msg = 0
    log("GoldDR alerts running. Timeframes: %s. Press Ctrl+C to stop." % TIMEFRAMES)
    try:
        send_telegram("✅ GoldDR alerts started (%s). You will get a message when an entry candle closes."
                      % ", ".join("%dm" % t for t in TIMEFRAMES))
    except Exception as e:  # noqa
        log("Telegram not working: %s" % e)
    while True:
        try:
            run_cycle(state)
            fails = 0
        except Exception as e:  # noqa
            fails += 1
            log("cycle failed (%d): %s" % (fails, e))
            if fails >= 3 and time.time() - last_fail_msg > 3600:
                last_fail_msg = time.time()
                try:
                    send_telegram("⚠️ GoldDR alerts: no data for %d cycles (%s)" % (fails, e))
                except Exception:  # noqa
                    pass
        wait = seconds_to_next_boundary(time.time())
        time.sleep(max(5, wait))


def print_check():
    now = time.time()
    b15, b1h = fetch_all(now)
    for tf in TIMEFRAMES:
        bars = chart_bars_for(tf, b15, now, b1h)
        res = analyze(bars, b1h, tf, now)
        print_report(tf, res, bars)


def print_report(tf, res, bars):
    st = res["stats"]
    vt, vb, vc, vs = res["zones"]
    print("\n==================== %s  (data: %d candles, %s -> %s) ====================" %
          (tf_name(tf), len(bars), fmt_time(bars[0][0]), fmt_time(bars[-1][0])))
    print("Bias now: Daily %s | 4H %s | trade bias %s" % (bias_word(res["bias"][0]), bias_word(res["bias"][1]),
                                                          {1: "BUY only", -1: "SELL only", 0: "NO TRADE"}[res["bias"][2]]
                                                          if USE_BIAS else "NOT USED"))
    print("Zones: %d (min touches %d). Closest to price:" % (len(vt), MIN_TOUCHES))
    last = bars[-1][4]
    near = sorted(range(len(vt)), key=lambda i: min(abs(vt[i] - last), abs(vb[i] - last)))[:6]
    for i in sorted(near, key=lambda i: -vt[i]):
        print("   %.2f - %.2f   (%d touches)" % (vb[i], vt[i], vc[i]))
    print("--- trades in the last %d days (compare with the triangles on TradingView) ---" % TEST_DAYS)
    names = {0: "open", 2: "TP", -1: "SL", 4: "BE", 3: "expired"}
    for tr in res["trades"]:
        print("%s  %-5s [%s (%s)] %-22s entry %.2f  SL %.2f  TP %.2f  -> %s" %
              (fmt_time(tr["time"]), "LONG" if tr["dir"] == 1 else "SHORT", tr["grade"], tr["letters"], tr["trig"][:22], tr["entry"], tr["sl"],
               tr["tp"], names[tr["status"]]))
    closed = st["win"] + st["loss"] + st["be"]
    wr = (st["win"] * 100.0 / closed) if closed else 0.0
    print("--- win rate per setup grade ---")
    for g in ("A+", "A", "B+", "B", "C+", "C"):
        x = st["grades"].get(g)
        if x:
            cl = x["win"] + x["loss"] + x["be"]
            print("   %-2s  %d trades | %dW / %dL | win rate %s" %
                  (g, sum(x.values()), x["win"], x["loss"], ("%.0f%%" % (x["win"] * 100.0 / cl)) if cl else "-"))
    print("--- stats ---")
    print("Entries found %d | taken %d (%d long / %d short) | blocked by bias %d" %
          (st["pairs"], st["sig"], st["long"], st["short"], st["blk_bias"]))
    print("WIN RATE %.1f%%  (%dW / %dL%s of %d closed)  | TP1 reached %d | expired %d | net %+.1fR | %+.0f pips" %
          (wr, st["win"], st["loss"], (" / %dBE" % st["be"]) if st["be"] else "", closed, st["tp1"], st["exp"],
           st["sum_r"], st["sum_pips"]))


# =============================================================================
# Self test (synthetic data, no internet)
# =============================================================================
def synthetic_bars(now, days=200, seed=7):
    rnd = random.Random(seed)
    end = (int(now) // 900) * 900
    t = end - days * 86400
    price = 3800.0
    out = []
    while t < end:
        if not market_closed(t):
            o = price
            drift = 0.6 * __import__("math").sin(t / (86400.0 * 3.0))
            c = o + drift + rnd.gauss(0, 2.2)
            h = max(o, c) + abs(rnd.gauss(0, 1.4))
            l = min(o, c) - abs(rnd.gauss(0, 1.4))
            out.append((t, o, h, l, c))
            price = c
        t += 900
    return out


def selftest():
    global MIN_TOUCHES, EN_SINGLE, EN_ENGULF, EN_DOJI, EN_HAMMER, EN_STAR, USE_BIAS
    now = time.time()
    all15 = synthetic_bars(now)
    # 1h bars made from the same 15m data
    b1h = resample(all15, 3600, now)
    b15 = [b for b in all15 if b[0] >= now - 60 * 86400]
    print("synthetic data: %d x 15m, %d x 1h" % (len(b15), len(b1h)))
    for label, setup in (("default settings", {}),
                         ("all entry candles + no bias", dict(EN_SINGLE=True, EN_ENGULF=True, EN_DOJI=True,
                                                              EN_HAMMER=True, EN_STAR=True, USE_BIAS=False))):
        saved = {k: globals()[k] for k in setup}
        globals().update(setup)
        try:
            for tf in (15, 30, 60, 120, 240):
                bars = chart_bars_for(tf, b15, now, b1h)
                t0 = time.time()
                res = analyze(bars, b1h, tf, now)
                print("[%s] %s: %d zones, %d trades, %.1fs" % (label, tf_name(tf), len(res["zones"][0]),
                                                                len(res["trades"]), time.time() - t0))
                if res["trades"]:
                    msg = build_message(tf, res["trades"][-1], res)
                    assert "Entry" in msg
        finally:
            globals().update(saved)
    # parsers
    y = {"chart": {"result": [{"timestamp": [1, 2, 3], "indicators": {"quote": [{
        "open": [1.0, None, 3.0], "high": [2.0, 2.0, 4.0], "low": [0.5, 1.0, 2.0], "close": [1.5, 1.5, 3.5]}]}}]}}
    assert len(parse_yahoo(y)) == 2
    tw = {"values": [{"datetime": "2026-11-19 12:15:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}]}
    assert parse_twelve(tw)[0][1] == 1.0
    print("parsers OK")
    print("SELFTEST PASSED (this checks the code runs; it does not check the market logic against TradingView)")


def main():
    ap = argparse.ArgumentParser(description="GoldDR Telegram alerts")
    ap.add_argument("--check", action="store_true", help="print the signals / win rate found in the history")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--test-telegram", action="store_true", help="send a test message")
    ap.add_argument("--find-chat-id", action="store_true", help="print your Telegram chat id")
    ap.add_argument("--test-open", action="store_true", help="send a sample 'market is open' message now")
    ap.add_argument("--selftest", action="store_true", help="offline test with generated prices")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.find_chat_id:
        find_chat_id()
    elif a.test_open:
        now = time.time()
        b15, b1h = fetch_all(now)
        res = analyze(b15, b1h, 15, now)
        send_telegram("(test) " + build_open_message(find_market_open(b15) or b15[-1][0], res, b15[-1][4]))
        print("sent")
    elif a.test_telegram:
        send_telegram("✅ GoldDR test message. Telegram alerts work.")
        print("sent")
    elif a.check:
        print_check()
    elif a.once:
        run_cycle(load_state())
    else:
        run_forever()


if __name__ == "__main__":
    main()
