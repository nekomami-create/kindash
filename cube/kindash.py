# -*- coding: utf-8 -*-
"""Kindle 3 に出す天気と電力の1画面（600x800、8bit グレー PNG）を、Cube J1 の上で描いて配る。

Cube の Python 2.7 は標準ライブラリだけで、ssl も zlib も画像ライブラリもない。
- HTTPS は /system/bin/curl に任せる（CA は同じフォルダの cacert.pem）
- 文字と天気マークは PC で作った kindash_assets.json / .bin を貼るだけ
- PNG は無圧縮の deflate で書く（Kindle の eips は 8bit しか受け付けない）

  GET /screen.png   いちばん新しい画面（古ければ描き直してから返す）
  GET /render       今すぐ描き直す
  GET /garbage      今日のごみの日（Kindle が鳴らす音声の名前を1行ずつ）
  GET /healthz      "ok"

PC では KINDASH_SAMPLES=<フォルダ> を付けると、ネットワークの代わりに
samples/ の JSON で1枚だけ描く（python kindash.py --once out.png）。
"""
from __future__ import division, print_function, unicode_literals

import calendar
import io
import json
import math
import os
import select
import socket
import struct
import subprocess
import sys
import time
import binascii

try:
    import httplib
except ImportError:  # PC の Python 3 で試すとき
    import http.client as httplib

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("KINDASH_STATE", HERE)
CACERT = os.path.join(HERE, "cacert.pem")
SAMPLES = os.environ.get("KINDASH_SAMPLES")
PORT = int(os.environ.get("KINDASH_PORT", "8090"))
METER_HUB = ("127.0.0.1", 8080)

# 地点。既定は東京駅で、実際の場所は同じフォルダの kindash_config.json に書く
# （リポジトリには入れない）。最寄りのアメダスは緯度経度から自動で選ぶ。
#   {"lat": 35.68, "lon": 139.77, "jma_office": "130000", "jma_area": "130010",
#    "jma_temp_station": "44132"}
CONFIG = {"lat": 35.6812, "lon": 139.7671,
          "jma_office": "130000", "jma_area": "130010", "jma_temp_station": "44132"}
try:  # KINDASH_CONFIG で別のファイルを指せる（PC で既定の東京駅のまま描くときは存在しないパスを渡す）
    with io.open(os.environ.get("KINDASH_CONFIG", os.path.join(HERE, "kindash_config.json")),
                 "r", encoding="utf-8") as _f:
        CONFIG.update(json.load(_f))
except (IOError, OSError):
    pass
# ごみの日も同じファイルに書く（無ければ出さない）。曜日は「月」、第n曜日は「第3金」:
#   "garbage": [["可燃ごみ", ["火", "金"], "burnable"], ["不燃ごみ", ["第1水", "第3水"], "nonburnable"]],
#   "garbage_skip": ["2026-12-31"]
# 3つ目は Kindle が鳴らす音声ファイルの名前（kindle/voice/<名前>.wav）
GARBAGE = CONFIG.get("garbage", [])
GARBAGE_SKIP = set(CONFIG.get("garbage_skip", []))
GARBAGE_HOURS = (4, 12)  # 見出しに帯を出す時間（収集日の朝）
LAT, LON = float(CONFIG["lat"]), float(CONFIG["lon"])
JMA_OFFICE, JMA_AREA, JMA_TEMP_STATION = CONFIG["jma_office"], CONFIG["jma_area"], CONFIG["jma_temp_station"]
JMA = "https://www.jma.go.jp/bosai"
OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
              "&hourly=temperature_2m,precipitation,weather_code"
              "&timezone=Asia%%2FTokyo&timeformat=unixtime&past_days=1&forecast_days=2"
              "&models=jma_seamless,best_match") % (LAT, LON)

W, H = 600, 800
BLACK, DARK, MID, LIGHT, PAPER = 0, 68, 136, 187, 255
JST = 9 * 3600
RENDER_EVERY = 15 * 60
AVG_WINDOW = 5 * 60  # 「いまの電力」は直近5分の平均
STALE_AFTER = 10 * 60

# 段の高さ（合計 800）。1時間予報は12時間ずつ2段で、残りが電力
H_HEAD, H_BANNER, H_NOW, H_HOURLY, H_PRECIP, H_WEEK = 34, 34, 118, 216, 124, 138
H_POWER = H - (H_HEAD + H_BANNER + H_NOW + H_HOURLY + H_PRECIP + H_WEEK)

# tools/build_assets.py がこの2つを読んで素材を作る
FONT_SPECS = {
    "r11": (11, 0), "r12": (12, 0), "r13": (13, 0), "r14": (14, 0), "r15": (15, 0),
    "r24": (24, 0), "b17": (17, 1), "b18": (18, 1), "b19": (19, 1), "b20": (20, 1),
    "b64": (64, 1),
}
ICON_SIZES = (19, 25)

WEEKDAYS = "月火水木金土日"
WIND_DIRS = ["静穏", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南",
             "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西", "北"]
# 画面に出す文言の残り（build_assets.py が字を拾えるようにここに並べておく）
MESSAGES = ["データなし", "取得できません", "3時間以内に雨の予報はありません", "古い値",
            "いま雨", "ごろまで", "ごろから雨", "最大", "降水確率", "観測",
            # ごみの日の帯。分別の名前は設定から来るので、よく使う字をここに並べておく
            "今日は", "の日", "朝8時まで", "可燃ごみ", "不燃ごみ", "資源", "粗大ごみ", "プラスチック",
            "ペットボトル", "びん・缶", "古紙", "容器包装", "燃やすごみ", "燃やさないごみ", "危険物"]
# アメダスの観測所名の字は、build_assets.py が全観測所ぶん r14 に入れる


def log(msg):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + JST)), msg)
    sys.stderr.write(line)
    path = os.path.join(STATE_DIR, "kindash.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 200000:
            os.rename(path, path + ".1")
        with io.open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def jst_tm(ts):
    return time.gmtime(ts + JST)


def jst_ts(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) - JST


def parse_iso_jst(s):
    """'2026-10-25T20:00:00+09:00' -> unix time。JMA は必ず +09:00 で返す。"""
    return jst_ts(int(s[0:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16]), int(s[17:19]))


# ---------------------------------------------------------------- 取得

def curl(url, timeout=25):
    cmd = ["curl", "-sS", "-f", "-m", str(timeout)]
    if os.path.exists(CACERT):
        cmd += ["--cacert", CACERT]
    p = subprocess.Popen(cmd + [url], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode != 0:
        raise IOError("curl %d %s: %s" % (p.returncode, url, err.decode("utf-8", "replace").strip()))
    return out


def sample(name):
    with io.open(os.path.join(SAMPLES, name), "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def get_json(url, sample_name):
    if SAMPLES:
        return sample(sample_name)
    return json.loads(curl(url).decode("utf-8"))


def get_text(url, sample_value):
    if SAMPLES:
        return sample_value
    return curl(url).decode("utf-8").strip()


def meter_hub(path, sample_name):
    if SAMPLES:
        return sample(sample_name)
    conn = httplib.HTTPConnection(METER_HUB[0], METER_HUB[1], timeout=10)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        if res.status != 200:
            raise IOError("meter_hub %s: %d" % (path, res.status))
        return json.loads(res.read().decode("utf-8"))
    finally:
        conn.close()


def write_json(path, obj):
    s = json.dumps(obj, ensure_ascii=False)
    if isinstance(s, bytes):  # Python 2 は ASCII だけだと str を返す
        s = s.decode("utf-8")
    # 途中で落ちても壊れたファイルを残さない
    with io.open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(s)
    os.rename(path + ".tmp", path)


def nearest_station():
    """最寄りのアメダス。表は大きいので、一度決めたら覚えておく（地点を変えたら選び直す）。"""
    path = os.path.join(STATE_DIR, "station.json")
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("for") == [LAT, LON]:
            return cached
    except Exception:
        pass
    table = get_json(JMA + "/amedas/const/amedastable.json", "amedastable.json")
    best = None
    for sid, e in table.items():
        lat = e["lat"][0] + e["lat"][1] / 60.0
        lon = e["lon"][0] + e["lon"][1] / 60.0
        dkm = math.hypot((lat - LAT) * 111.0, (lon - LON) * 111.0 * math.cos(math.radians(LAT)))
        # 気温のない観測所（雨量だけの所）は使わない
        if e.get("elems", "0")[0] != "1":
            continue
        if best is None or dkm < best["km"]:
            best = {"id": sid, "name": e["kjName"], "km": round(dkm, 1), "for": [LAT, LON]}
    if not SAMPLES:
        write_json(path, best)
    return best


def fetch_amedas(station, latest):
    tm = jst_tm(latest)
    key = time.strftime("%Y%m%d%H%M00", tm)
    data = get_json(JMA + "/amedas/data/map/%s.json" % key, "amedas_map.json")
    obs = data.get(station["id"]) or {}

    def val(name):
        v = obs.get(name)
        return v[0] if v and v[1] == 0 else None

    return {"time": latest, "temp": val("temp"), "humidity": val("humidity"),
            "wind": val("wind"), "wind_dir": val("windDirection"),
            "precip1h": val("precipitation1h"), "station": station["name"]}


def fetch_observed_rain(station, latest):
    """過去24時間の1時間降水量（毎正時の precipitation1h）。確定した3時間ぶんは覚えておく。"""
    cache_dir = os.path.join(STATE_DIR, "amedas_cache")
    if not os.path.isdir(cache_dir) and not SAMPLES:
        os.makedirs(cache_dir)
    hour_end = latest - latest % 3600
    hours = [hour_end - 3600 * k for k in range(23, -1, -1)]
    blocks = []
    for t in hours:
        tm = jst_tm(t)
        bid = "%s_%02d" % (time.strftime("%Y%m%d", tm), tm.tm_hour // 3 * 3)
        if bid not in blocks:
            blocks.append(bid)
    out = {}
    for bid in blocks:
        block_start = jst_ts(int(bid[0:4]), int(bid[4:6]), int(bid[6:8]), int(bid[9:11]))
        done = block_start + 3 * 3600 <= latest
        path = os.path.join(cache_dir, "%s_%s.json" % (station["id"], bid))
        recs = None
        if done and os.path.exists(path):
            try:
                with io.open(path, "r", encoding="utf-8") as f:
                    recs = json.load(f)
            except ValueError:
                recs = None  # 壊れていたら取り直す
        if recs is None:
            if SAMPLES:
                recs = sample("amedas_point.json")
            else:
                recs = json.loads(curl(JMA + "/amedas/data/point/%s/%s.json" % (station["id"], bid)).decode("utf-8"))
                if done:
                    write_json(path, recs)
        for k, rec in recs.items():
            if k.endswith("0000"):
                v = rec.get("precipitation1h")
                if v and v[1] == 0:
                    out[parse_iso_jst("%s-%s-%sT%s:00:00" % (k[0:4], k[4:6], k[6:8], k[8:10]))] = v[0]
    if not SAMPLES:  # 3日より古いキャッシュは捨てる
        for name in os.listdir(cache_dir):
            p = os.path.join(cache_dir, name)
            if os.path.getmtime(p) < time.time() - 3 * 86400:
                os.remove(p)
    return out


def fetch_open_meteo():
    """1時間ごとの予報。気象庁のモデルを優先し、欠けた時間だけ best_match で埋める。
    降水確率は取らない（気象庁のモデルは出さず、best_match の値は ECMWF のもので
    天気や降水量と食い違う）。確率は気象庁の府県予報の6時間ごとの値を使う。"""
    h = get_json(OPEN_METEO, "openmeteo.json")["hourly"]

    def pick(field, i):
        v = h.get(field + "_jma_seamless", [None] * (i + 1))[i]
        return v if v is not None else h.get(field + "_best_match", [None] * (i + 1))[i]

    pts = []
    for i, t in enumerate(h["time"]):
        pts.append({"time": t, "temp": pick("temperature_2m", i), "mm": pick("precipitation", i),
                    "code": pick("weather_code", i)})
    return pts


def fetch_forecast(now):
    """気象庁の府県予報と週間予報。週間予報（明日から6日分）と、6時間ごとの降水確率を返す。
    明日・明後日は府県予報（data[0]）で、週間予報（data[1]）の空欄を埋める。"""
    data = get_json(JMA + "/forecast/data/forecast/%s.json" % JMA_OFFICE, "forecast_%s.json" % JMA_OFFICE)
    short, weekly = data[0], data[1]

    def area(ts, code):
        for a in ts["areas"]:
            if a["area"]["code"] == code:
                return a
        return ts["areas"][0]

    def day_of(iso):
        return iso[0:10]

    days = {}
    w0 = weekly["timeSeries"][0]
    wa = area(w0, JMA_AREA)
    for i, iso in enumerate(w0["timeDefines"]):
        days[day_of(iso)] = {"code": wa["weatherCodes"][i], "pop": wa["pops"][i], "min": "", "max": ""}
    w1 = weekly["timeSeries"][1]
    wt = area(w1, JMA_TEMP_STATION)
    for i, iso in enumerate(w1["timeDefines"]):
        d = days.setdefault(day_of(iso), {"code": "", "pop": ""})
        d["min"], d["max"] = wt["tempsMin"][i], wt["tempsMax"][i]

    s0 = short["timeSeries"][0]
    sa = area(s0, JMA_AREA)
    for i, iso in enumerate(s0["timeDefines"]):
        d = days.get(day_of(iso))
        if d is not None and sa["weatherCodes"][i]:
            d["code"] = sa["weatherCodes"][i]
    s1 = short["timeSeries"][1]
    pa = area(s1, JMA_AREA)
    pops = {}
    blocks = []  # [始まりの unix 時間, 降水確率%]。6時間ずつ
    for i, iso in enumerate(s1["timeDefines"]):
        if pa["pops"][i] != "":
            pops.setdefault(day_of(iso), []).append(int(pa["pops"][i]))
            blocks.append([parse_iso_jst(iso), int(pa["pops"][i])])
    s2 = short["timeSeries"][2]
    ta = area(s2, JMA_TEMP_STATION)
    for i, iso in enumerate(s2["timeDefines"]):
        d = days.get(day_of(iso))
        if d is None or ta["temps"][i] == "":
            continue
        if iso[11:13] == "00" and not d.get("min"):
            d["min"] = ta["temps"][i]
        elif iso[11:13] == "09" and not d.get("max"):
            d["max"] = ta["temps"][i]
    for day, ps in pops.items():
        if day in days and days[day]["pop"] == "":
            days[day]["pop"] = str(max(ps))

    out = []
    tm = jst_tm(now)
    base = jst_ts(tm.tm_year, tm.tm_mon, tm.tm_mday)
    for k in range(1, 7):
        t = jst_tm(base + k * 86400 + 3600)
        key = time.strftime("%Y-%m-%d", t)
        d = days.get(key, {})
        out.append({"mday": t.tm_mday, "wday": t.tm_wday, "code": d.get("code", ""),
                    "pop": d.get("pop", ""), "min": d.get("min", ""), "max": d.get("max", "")})
    return {"weekly": out, "pops": blocks}


def fetch_power(now):
    latest = meter_hub("/api", "cube_api.json")
    daily = meter_hub("/api/daily?days=2", "cube_daily.json")
    # 1分ごとの値 [時刻, W, T相A, R相A] から直近5分の平均を出す。無ければ最新値で代える
    hist = meter_hub("/api/history?since=%d" % (now - AVG_WINDOW), "cube_history.json")
    recent = [s for s in hist.get("samples", []) if now - AVG_WINDOW < s[0] <= now + 60 and s[1] is not None]
    if recent:
        w = sum(s[1] for s in recent) / float(len(recent))
        ts, n = max(s[0] for s in recent), len(recent)
    else:
        w, ts, n = latest.get("power_w"), latest.get("ts"), 0
    tm = jst_tm(now)
    today = time.strftime("%Y-%m-%d", tm)
    yday = time.strftime("%Y-%m-%d", jst_tm(now - 86400))
    by_date = dict((d["date"], d) for d in daily.get("days", []))
    return {"w": w, "ts": ts, "n": n,
            "today": (by_date.get(today) or {}).get("use_kwh") or [None] * 48,
            "yday": (by_date.get(yday) or {}).get("use_kwh") or [None] * 48}


def collect(now):
    """パネルごとに独立して取る。どれかが失敗しても、残りは描く。"""
    out = {"now": now}

    def attempt(name, fn):
        try:
            out[name] = fn()
        except Exception as e:  # 1つの失敗で画面全体を落とさない
            out[name] = None
            log("fetch %s failed: %s" % (name, e))

    station = None
    try:
        station = nearest_station()
    except Exception as e:
        log("station failed: %s" % e)
    latest = None
    try:
        latest = parse_iso_jst(get_text(JMA + "/amedas/data/latest_time.txt",
                                        sample("latest_time.json")["latest"] if SAMPLES else None))
    except Exception as e:
        log("latest_time failed: %s" % e)
    if latest and station:
        attempt("amedas", lambda: fetch_amedas(station, latest))
        attempt("observed", lambda: fetch_observed_rain(station, latest))
    attempt("hourly", fetch_open_meteo)
    attempt("forecast", lambda: fetch_forecast(now))
    fc = out.pop("forecast", None)
    out["weekly"] = fc["weekly"] if fc else None
    out["pops"] = fc["pops"] if fc else None
    attempt("power", lambda: fetch_power(now))
    return out


# ---------------------------------------------------------------- 描画

class Assets(object):
    def __init__(self):
        with io.open(os.path.join(HERE, "kindash_assets.json"), "r", encoding="utf-8") as f:
            self.index = json.load(f)
        with io.open(os.path.join(HERE, "kindash_assets.bin"), "rb") as f:
            self.blob = bytearray(f.read())


class Canvas(object):
    def __init__(self, assets):
        self.a = assets
        self.buf = bytearray([PAPER]) * (W * H)

    def rect(self, x0, y0, x1, y1, v):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1)))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytearray([v]) * (x1 - x0)
        b = self.buf
        for y in range(y0, y1):
            o = y * W
            b[o + x0:o + x1] = row

    def hline(self, y, v=MID, x0=0, x1=W):
        self.rect(x0, y, x1, y + 1, v)

    def vline(self, x, y0, y1, v=MID):
        self.rect(x, y0, x + 1, y1, v)

    def dashed_vline(self, x, y0, y1, v=BLACK):
        for y in range(int(y0), int(y1), 6):
            self.rect(x, y, x + 1, min(y + 3, y1), v)

    def line(self, x0, y0, x1, y1, v=BLACK, w=2):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        b = self.buf
        half = w // 2
        for i in range(n + 1):
            x = int(round(x0 + (x1 - x0) * i / float(n))) - half
            y = int(round(y0 + (y1 - y0) * i / float(n))) - half
            for dy in range(w):
                yy = y + dy
                if 0 <= yy < H:
                    o = yy * W
                    for dx in range(w):
                        xx = x + dx
                        if 0 <= xx < W:
                            b[o + xx] = v

    def polyline(self, pts, v=BLACK, w=2):
        for i in range(len(pts) - 1):
            self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], v, w)

    # --- 文字 ---
    def font(self, name):
        return self.a.index["fonts"][name]

    def measure(self, s, fname):
        g = self.font(fname)["glyphs"]
        return sum(g[ch][5] if ch in g else g[" "][5] for ch in s)

    def text(self, x, y, s, fname, v=BLACK, anchor="la"):
        f = self.font(fname)
        g = f["glyphs"]
        width = self.measure(s, fname)
        h, vert = anchor[0], anchor[1]
        if h == "m":
            x -= width / 2.0
        elif h == "r":
            x -= width
        asc, desc = f["ascent"], f["descent"]
        if vert in "at":
            base = y + asc
        elif vert == "m":
            base = y + (asc - desc) / 2.0
        elif vert in "bd":
            base = y - desc
        else:
            base = y
        pen = x
        blob, b = self.a.blob, self.buf
        for ch in s:
            gl = g.get(ch) or g[" "]
            off, gw, gh, gx, gy, adv = gl
            if gw and gh:
                ox, oy = int(round(pen + gx)), int(round(base + gy))
                for row in range(gh):
                    yy = oy + row
                    if not 0 <= yy < H:
                        continue
                    o = yy * W
                    src = off + row * gw
                    for col in range(gw):
                        al = blob[src + col]
                        if al:
                            xx = ox + col
                            if 0 <= xx < W:
                                p = b[o + xx]
                                b[o + xx] = p + (v - p) * al // 255
            pen += adv
        return width

    def icon(self, kind, cx, cy, r):
        off, iw, ih = self.a.index["icons"]["%s%d" % (kind, r)]
        ox, oy = int(round(cx - iw / 2.0)), int(round(cy - ih / 2.0))
        blob, b = self.a.blob, self.buf
        for row in range(ih):
            o = (oy + row) * W + ox
            src = off + row * iw
            for col in range(iw):
                v = blob[src + col]
                if v < b[o + col]:
                    b[o + col] = v

    def png(self):
        raw = bytearray()
        for y in range(H):
            raw.append(0)
            raw += self.buf[y * W:(y + 1) * W]
        z = bytearray(b"\x78\x01")
        n, i = len(raw), 0
        while i < n:
            blk = raw[i:i + 65535]
            z += struct.pack(b"<BHH", 1 if i + 65535 >= n else 0, len(blk), len(blk) ^ 0xFFFF)
            z += blk
            i += 65535
        z += struct.pack(b">I", adler32(raw))

        def chunk(tag, data):
            data = bytes(data)
            return (struct.pack(b">I", len(data)) + tag + data +
                    struct.pack(b">I", binascii.crc32(tag + data) & 0xffffffff))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(b">IIBBBBB", W, H, 8, 0, 0, 0, 0)) +
                chunk(b"IDAT", z) + chunk(b"IEND", b""))


def adler32(data):
    a, b, i, n = 1, 0, 0, len(data)
    while i < n:
        for c in data[i:i + 3800]:
            a += c
            b += a
        a %= 65521
        b %= 65521
        i += 3800
    return (b << 16) | a


def wmo_icon(code):
    if code is None:
        return None
    if code in (0, 1):
        return "sun"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "thunder"
    return "rain"


def jma_icon(code):
    if not code:
        return None
    c = int(code)
    if c in (101, 110, 111, 201, 210, 211):
        return "partly"
    return {1: "sun", 2: "cloud", 3: "rain", 4: "snow"}.get(c // 100, "cloud")


def pop_at(blocks, t):
    """t を含む6時間の降水確率（気象庁の府県予報）。無ければ None。"""
    for start, pop in blocks or []:
        if start <= t < start + 6 * 3600:
            return pop
    return None


def rain_event(hourly, now, lookahead=3 * 3600, horizon=12 * 3600, min_mm=0.5):
    """3時間以内に始まる雨（気象庁モデルで 0.5mm/h 以上）の、ひと続きの時間帯。
    確率は6時間ごとで粗すぎて「何時から」が言えないので、判定は1時間ごとの降水量だけで行う。
    12時間先までで区切り、そこまで続くなら long=True。"""
    pts = [p for p in hourly if now < p["time"] <= now + horizon]
    start = None
    ev = []
    for p in pts:
        wet = (p["mm"] or 0) >= min_mm
        bucket = p["time"] - 3600
        if start is None:
            if wet and bucket <= now + lookahead:
                start = bucket
                ev.append(p)
            elif bucket > now + lookahead:
                break
        elif wet:
            ev.append(p)
        else:
            break
    if not ev:
        return None
    return {"start": start, "end": ev[-1]["time"], "long": ev[-1] is pts[-1],
            "mm": max(p["mm"] or 0 for p in ev)}


def garbage_today(now):
    """今日（日本時間）出せるごみ。設定の順に [名前, 曜日の並び, 音声の名前] を返す。"""
    tm = jst_tm(now)
    if time.strftime("%Y-%m-%d", tm) in GARBAGE_SKIP:
        return []
    wd = WEEKDAYS[tm.tm_wday]
    nth = "第%d%s" % ((tm.tm_mday - 1) // 7 + 1, wd)
    return [g for g in GARBAGE if wd in g[1] or nth in g[1]]


def is_night(ts):
    h = jst_tm(ts).tm_hour
    return h >= 18 or h < 6


def fmt_num(v, digits=1):
    if v is None:
        return "--"
    return ("%%.%df" % digits) % v


def render(data, assets):
    c = Canvas(assets)
    now = data["now"]
    tm = jst_tm(now)

    # --- 見出し。収集日の朝は、左の空いているところにごみの日の帯 ---
    y = 0
    garbage = garbage_today(now) if GARBAGE_HOURS[0] <= tm.tm_hour < GARBAGE_HOURS[1] else []
    if garbage:
        msg = "今日は %s の日（朝8時まで）" % "・".join(g[0] for g in garbage)
        wpx = c.measure(msg, "b17")
        c.rect(6, 4, 6 + wpx + 16, H_HEAD - 4, BLACK)
        c.text(14, H_HEAD / 2.0, msg, "b17", PAPER, anchor="lm")
    c.text(W - 12, H_HEAD / 2.0, "%d/%d(%s) %02d:%02d 更新" % (
        tm.tm_mon, tm.tm_mday, WEEKDAYS[tm.tm_wday], tm.tm_hour, tm.tm_min), "r15", DARK, anchor="rm")
    y += H_HEAD
    c.hline(y)

    # --- 雨の警告帯 ---
    ev = rain_event(data["hourly"], now) if data.get("hourly") else None
    if ev:
        c.rect(0, y, W, y + H_BANNER, BLACK)
        until = "12時間以上続く見込み" if ev["long"] else "%d時ごろまで" % jst_tm(ev["end"]).tm_hour
        if ev["start"] <= now:
            msg = "☂ いま雨　%s　最大 %s mm/h" % (until, fmt_num(ev["mm"]))
        else:
            pop = pop_at(data.get("pops"), ev["start"])
            msg = "☂ %d時ごろから雨　最大 %s mm/h%s" % (
                jst_tm(ev["start"]).tm_hour, fmt_num(ev["mm"]),
                "" if pop is None else "（降水確率 %d%%）" % pop)
        c.text(12, y + H_BANNER / 2.0, msg, "b17", PAPER, anchor="lm")
    else:
        msg = "3時間以内に雨の予報はありません" if data.get("hourly") else "降水の予報を取得できません"
        c.text(12, y + H_BANNER / 2.0, msg, "r15", DARK, anchor="lm")
        c.hline(y + H_BANNER - 1)
    y += H_BANNER

    # --- いま：天気 | 電力 ---
    top = y
    am = data.get("amedas")
    if am:
        obs = jst_tm(am["time"])
        c.text(12, top + 6, "いま（%sアメダス %d:%02d観測）" % (am["station"], obs.tm_hour, obs.tm_min), "r14", DARK)
        s = fmt_num(am["temp"])
        wpx = c.text(12, top + 24, s, "b64")
        c.text(12 + wpx + 6, top + 50, "℃", "r24")
        wind = WIND_DIRS[am["wind_dir"]] if am["wind_dir"] is not None and 0 <= am["wind_dir"] <= 16 else "--"
        c.text(12, top + 95, "湿度 %s%%　%s %sm/s　1h %smm" % (
            fmt_num(am["humidity"], 0), wind, fmt_num(am["wind"]), fmt_num(am["precip1h"])), "r15", DARK)
    else:
        c.text(12, top + 6, "いま（アメダス）", "r14", DARK)
        c.text(12, top + 48, "取得できません", "b18", DARK)
    c.vline(W // 2, top, top + H_NOW)
    pw = data.get("power")
    c.text(W // 2 + 12, top + 6, "電力（直近5分の平均）" if pw and pw["n"] else "いまの電力", "r14", DARK)
    if pw and pw["w"] is not None:
        old = pw["ts"] is None or now - pw["ts"] > 300
        s = "%d" % round(pw["w"])
        wpx = c.text(W // 2 + 12, top + 24, s, "b64", MID if old else BLACK)
        c.text(W // 2 + 12 + wpx + 6, top + 50, "W", "r24")
        kw = pw["w"] / 1000.0
        level = "かなり多め" if kw >= 3 else ("多め" if kw >= 1.2 else "ふつう")
        c.text(W // 2 + 12, top + 95, ("古い値" if old else level) + "（1.2kW 以上で「多め」）", "r15", DARK)
    else:
        c.text(W // 2 + 12, top + 48, "取得できません", "b18", DARK)
    y += H_NOW
    c.hline(y)

    # --- 1時間予報（24時間を12時間ずつ2段）。6時間の区切り（0・6・12・18時の後ろ）に縦線を引き、
    #     区切りの時刻を太字にする。降水確率は気象庁の6時間ごとの値を、まとまりの中央に1つだけ出す ---
    hourly = data.get("hourly")
    pops = data.get("pops")
    if hourly:
        ahead = [p for p in hourly if p["time"] >= now][:24]
        cw = W / 12.0
        row_h = H_HOURLY / 2.0
        for row in range(2):
            ry = y + row * row_h
            if row:
                c.hline(int(ry), LIGHT, 12, W - 12)
            cells = ahead[row * 12:(row + 1) * 12]
            for i, p in enumerate(cells):
                cx = cw * i + cw / 2.0
                h = jst_tm(p["time"]).tm_hour
                edge = h % 6 == 0
                c.text(cx, ry + 5, "%d時" % h, "b17" if edge else "r13", BLACK if edge else DARK, anchor="mt")
                if edge and i < len(cells) - 1:
                    c.vline(int(cw * (i + 1)), int(ry + 3), int(ry + row_h - 3), MID)
                ic = wmo_icon(p["code"])
                if ic in ("sun", "partly") and is_night(p["time"] - 1800):
                    ic = {"sun": "moon", "partly": "mooncloud"}[ic]
                if ic:
                    c.icon(ic, cx, ry + 42, 19)
                c.text(cx, ry + 64, "%s°" % fmt_num(p["temp"], 0), "b18", anchor="mt")
            # 6時間のまとまりごとに、その中央へ確率
            spans = []
            for i, p in enumerate(cells):
                pop = pop_at(pops, p["time"] - 1800)
                key = None if pop is None else (pop, (p["time"] - 1800 + JST) // (6 * 3600))
                if spans and spans[-1][0] == key:
                    spans[-1][2] = i
                else:
                    spans.append([key, i, i])
            ly = ry + 95
            for key, i0, i1 in spans:
                label = "--" if key is None else "降水 %d%%" % key[0]
                if i1 - i0 < 2:  # 狭いまとまりは数字だけ
                    label = "--" if key is None else "%d%%" % key[0]
                c.text(cw * (i0 + i1 + 1) / 2.0, ly, label, "r15", DARK, anchor="mm")
    else:
        c.text(W / 2.0, y + H_HOURLY / 2.0, "1時間予報を取得できません", "b18", DARK, anchor="mm")
    y += H_HOURLY
    c.hline(y)

    # --- 降水量チャート ---
    c.text(12, y + 6, "降水量 mm/h　過去24時間（実測）｜今後12時間（予報）", "r14", DARK)
    x0, base = 34, y + H_PRECIP - 22
    ch = H_PRECIP - 54
    bw = (W - 12 - x0) / 36.0
    hour_now = now - now % 3600
    fc = dict((p["time"], p["mm"]) for p in (hourly or []))
    observed = data.get("observed") or {}
    bars = []
    for k in range(36):
        t = hour_now + (k - 23) * 3600
        if k < 24:
            v = observed.get(t, fc.get(t))
        else:
            v = fc.get(t)
        bars.append(v)
    # 縦軸は 0〜3mm/h を基本に、強い雨のときは区切りのよい値まで広げる
    peak = max([0.0] + [v for v in bars if v])
    step = 1 if peak <= 3 else (2 if peak <= 6 else (5 if peak <= 15 else 10))
    top_mm = max(3, int(math.ceil(peak / step)) * step)
    for v in range(0, top_mm + 1, step):
        gy = base - v / float(top_mm) * ch
        if v:
            c.hline(int(gy), LIGHT, x0, W - 12)
        c.text(x0 - 5, gy, "%d" % v, "r12", DARK, anchor="rm")
    for k, v in enumerate(bars):
        if v and v > 0:
            hpx = max(2, v / float(top_mm) * ch)
            c.rect(x0 + k * bw + 1, base - hpx, x0 + (k + 1) * bw - 1, base, BLACK if k < 24 else MID)
    c.hline(base, MID, x0, W - 12)
    c.vline(x0, base - ch, base + 1, MID)
    c.dashed_vline(x0 + 24 * bw, base - ch, base)
    for lbl, k in (("-24h", 0), ("-12h", 12), ("いま", 24), ("+6h", 30), ("+12h", 36)):
        c.text(x0 + k * bw, base + 4, lbl, "r12", DARK, anchor="mt" if 0 < k < 36 else ("lt" if k == 0 else "rt"))
    y += H_PRECIP
    c.hline(y)

    # --- 週間予報 ---
    weekly = data.get("weekly")
    if weekly:
        cw = W / 6.0
        for i, d in enumerate(weekly):
            cx = cw * i + cw / 2.0
            if i:
                c.vline(int(cw * i), y + 6, y + H_WEEK - 6, LIGHT)
            c.text(cx, y + 6, "%s %d" % (WEEKDAYS[d["wday"]], d["mday"]), "r15", DARK, anchor="mt")
            ic = jma_icon(d["code"])
            if ic:
                c.icon(ic, cx, y + 50, 25)
            c.text(cx, y + 82, "%s° / %s°" % (d["max"] or "--", d["min"] or "--"), "b18", anchor="mt")
            c.text(cx, y + 107, "%s%%" % d["pop"] if d["pop"] else "--", "r14", DARK, anchor="mt")
    else:
        c.text(W / 2.0, y + H_WEEK / 2.0, "週間予報を取得できません", "b18", DARK, anchor="mm")
    y += H_WEEK
    c.hline(y)

    # --- 今日の電力（30分ごと）---
    c.text(12, y + 6, "今日の電力（30分ごと）　棒＝今日　線＝昨日", "r13", DARK)
    base = H - 20
    ph = H_POWER - 44
    bw = (W - 24) / 48.0
    if pw:
        today, yday = pw["today"], pw["yday"]
        done = [i for i, v in enumerate(today) if v is not None]
        last = done[-1] if done else -1
        t_sum = sum(v for v in today if v is not None)
        y_sum = sum(v for v in yday[:last + 1] if v is not None)
        c.text(W - 12, y + 6, "今日 %.1fkWh／昨日同時刻 %.1f" % (t_sum, y_sum), "r13", DARK, anchor="ra")
        top_kwh = max([0.4] + [v for v in today + yday if v is not None]) * 1.1
        for i, v in enumerate(today):
            if v:
                hpx = v / top_kwh * ph
                c.rect(12 + i * bw + 1, base - hpx, 12 + (i + 1) * bw - 1, base, MID)
        pts = [(12 + i * bw + bw / 2.0, base - v / top_kwh * ph) for i, v in enumerate(yday) if v is not None]
        c.polyline(pts, BLACK, 2)
    else:
        c.text(W / 2.0, base - ph / 2.0, "電力を取得できません", "b18", DARK, anchor="mm")
    c.hline(base, MID, 12, W - 12)
    for hh in (0, 6, 12, 18, 24):
        c.text(12 + hh * 2 * bw, base + 3, "%d時" % hh, "r11", DARK,
               anchor="lt" if hh == 0 else ("rt" if hh == 24 else "mt"))
    return c


# ---------------------------------------------------------------- 配信

class Server(object):
    def __init__(self):
        self.assets = Assets()
        self.png = None
        self.rendered_at = 0
        self.path = os.path.join(STATE_DIR, "screen.png")
        try:
            with io.open(self.path, "rb") as f:
                self.png = f.read()
            self.rendered_at = os.path.getmtime(self.path)
        except Exception:
            pass

    def render(self):
        t0 = time.time()
        data = collect(int(t0))
        t1 = time.time()
        png = render(data, self.assets).png()
        tmp = self.path + ".tmp"
        with io.open(tmp, "wb") as f:
            f.write(png)
        os.rename(tmp, self.path)
        self.png, self.rendered_at = png, time.time()
        missing = [k for k in ("amedas", "hourly", "weekly", "power") if data.get(k) is None]
        log("rendered: fetch %.1fs, draw %.1fs, %d bytes%s" % (
            t1 - t0, time.time() - t1, len(png), ", missing " + ",".join(missing) if missing else ""))
        return not missing

    def safe_render(self):
        """全部取れたら True。起動直後はネットワークや meter_hub がまだのことがある。"""
        try:
            return self.render()
        except Exception as e:
            log("render failed: %r" % (e,))
            return False

    def handle(self, conn):
        conn.settimeout(10)
        req = b""
        while b"\r\n\r\n" not in req and len(req) < 8192:
            chunk = conn.recv(1024)
            if not chunk:
                break
            req += chunk
        parts = req.split(b" ")
        path = parts[1].decode("latin-1") if len(parts) > 1 else "/"
        if path == "/healthz":
            self.reply(conn, 200, b"text/plain", b"ok")
        elif path == "/garbage":
            # Kindle が朝に鳴らす音声の名前を1行ずつ。今日が収集日でなければ空
            body = "".join("%s\n" % g[2] for g in garbage_today(time.time()) if len(g) > 2)
            self.reply(conn, 200, b"text/plain", body.encode("utf-8"))
        elif path in ("/screen.png", "/render"):
            if path == "/render" or self.png is None or time.time() - self.rendered_at > STALE_AFTER:
                self.safe_render()
            if self.png is None:
                self.reply(conn, 503, b"text/plain", b"not rendered yet")
            else:
                self.reply(conn, 200, b"image/png", self.png)
        else:
            self.reply(conn, 404, b"text/plain", b"not found")

    def reply(self, conn, code, ctype, body):
        head = ("HTTP/1.0 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n") % (
            code, "OK" if code == 200 else "Error", ctype.decode("ascii"), len(body))
        conn.sendall(head.encode("ascii") + body)

    def serve(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", PORT))
        sock.listen(5)
        log("listening on :%d" % PORT)
        next_at = time.time()
        while True:
            wait = max(0.0, next_at - time.time())
            ready, _, _ = select.select([sock], [], [], wait)
            if ready:
                conn, _ = sock.accept()
                try:
                    self.handle(conn)
                except Exception as e:
                    log("request failed: %r" % (e,))
                finally:
                    conn.close()
            if time.time() >= next_at:
                ok = self.safe_render()
                # 15分の区切り＋60秒（アメダスの毎10分の更新が出そろうのを待つ）。
                # 取れないものがあれば、区切りを待たずに2分後に描き直す
                now = time.time()
                next_at = now - now % RENDER_EVERY + RENDER_EVERY + 60
                if not ok:
                    next_at = min(next_at, now + 120)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--once":
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(STATE_DIR, "screen.png")
        t0 = time.time()
        now = int(os.environ.get("KINDASH_NOW", t0))
        data = collect(now)
        t1 = time.time()
        png = render(data, Assets()).png()
        with io.open(out, "wb") as f:
            f.write(png)
        print("fetch %.2fs draw+png %.2fs -> %s (%d bytes)" % (t1 - t0, time.time() - t1, out, len(png)))
        return
    Server().serve()


if __name__ == "__main__":
    main()
