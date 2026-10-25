"""Kindle 3（600x800・16階調）向けの1画面案を、ダミーの値で PNG に描く。"""
import math
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 600, 800
BLACK, DARK, MID, LIGHT, PAPER = 0, 68, 136, 187, 255
FONT_R = "C:/Windows/Fonts/BIZ-UDGothicR.ttc"
FONT_B = "C:/Windows/Fonts/BIZ-UDGothicB.ttc"

# 段の高さ（合計 800）
H_HEAD, H_BANNER, H_NOW, H_HOURLY, H_PRECIP, H_WEEK = 34, 34, 124, 134, 132, 148
H_POWER = H - (H_HEAD + H_BANNER + H_NOW + H_HOURLY + H_PRECIP + H_WEEK)


def font(size, bold=False):
    # index=1 は BIZ UDPゴシック（プロポーショナル）
    return ImageFont.truetype(FONT_B if bold else FONT_R, size, index=1)


img = Image.new("L", (W, H), PAPER)
d = ImageDraw.Draw(img)


def text(xy, s, size, bold=False, fill=BLACK, anchor="la"):
    d.text(xy, s, font=font(size, bold), fill=fill, anchor=anchor)


def hline(y, fill=MID):
    d.line([(0, y), (W, y)], fill=fill, width=1)


# --- 天気マーク（白黒で描ける単純な形）---
def sun(cx, cy, r):
    d.ellipse([cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45], outline=BLACK, width=2)
    for k in range(8):
        a = k * math.pi / 4
        d.line([(cx + math.cos(a) * r * 0.65, cy + math.sin(a) * r * 0.65),
                (cx + math.cos(a) * r * 0.95, cy + math.sin(a) * r * 0.95)], fill=BLACK, width=2)


def cloud(cx, cy, r, fill=PAPER):
    parts = [(-0.45, 0.1, 0.38), (0.0, -0.15, 0.5), (0.45, 0.1, 0.38)]
    for dx, dy, rr in parts:
        d.ellipse([cx + (dx - rr) * r, cy + (dy - rr) * r, cx + (dx + rr) * r, cy + (dy + rr) * r],
                  fill=fill, outline=BLACK, width=2)
    for dx, dy, rr in parts:
        d.ellipse([cx + (dx - rr) * r + 2, cy + (dy - rr) * r + 2, cx + (dx + rr) * r - 2, cy + (dy + rr) * r - 2],
                  fill=fill)
    d.rectangle([cx - 0.45 * r, cy + 0.05 * r, cx + 0.45 * r, cy + 0.48 * r], fill=fill)
    d.line([(cx - 0.5 * r, cy + 0.48 * r), (cx + 0.5 * r, cy + 0.48 * r)], fill=BLACK, width=2)


def rain(cx, cy, r):
    cloud(cx, cy - r * 0.2, r * 0.85, fill=LIGHT)
    for dx in (-0.35, 0.0, 0.35):
        x = cx + dx * r
        d.line([(x, cy + r * 0.35), (x - r * 0.12, cy + r * 0.7)], fill=BLACK, width=2)


def fog(cx, cy, r):
    for k, w in enumerate((0.8, 0.6, 0.8)):
        y = cy - r * 0.35 + k * r * 0.35
        d.line([(cx - w * r, y), (cx + w * r, y)], fill=BLACK, width=3)


ICON = {"s": sun, "c": cloud, "r": rain, "p": fog}

# --- 見出し ---
y = 0
text((12, H_HEAD / 2), "東京", 20, bold=True, anchor="lm")
text((W - 12, H_HEAD / 2), "10/25(日) 17:00 更新", 15, fill=DARK, anchor="rm")
y += H_HEAD
hline(y)

# --- 雨の警告帯（反転）---
d.rectangle([0, y, W, y + H_BANNER], fill=BLACK)
text((12, y + H_BANNER / 2), "☂ 19時ごろから雨　最大 2mm/h（降水確率 70%）", 17, bold=True, fill=PAPER, anchor="lm")
y += H_BANNER

# --- いま：天気 | 電力 ---
top = y
big = font(64, True)
text((12, top + 8), "いま（東京アメダス）", 14, fill=DARK)
text((12, top + 28), "18.4", 64, bold=True)
text((12 + d.textlength("18.4", font=big) + 6, top + 54), "℃", 24)
text((12, top + 100), "湿度 72%　北 2m/s　1h 0.0mm", 15, fill=DARK)
d.line([(W // 2, top), (W // 2, top + H_NOW)], fill=MID)
text((W // 2 + 12, top + 8), "いまの電力", 14, fill=DARK)
text((W // 2 + 12, top + 28), "820", 64, bold=True)
text((W // 2 + 12 + d.textlength("820", font=big) + 6, top + 54), "W", 24)
text((W // 2 + 12, top + 100), "ふつう（1.2kW 以上で「多め」）", 15, fill=DARK)
y += H_NOW
hline(y)

# --- 12時間予報 ---
hours = ["18", "19", "20", "21", "22", "23", "0", "1", "2", "3", "4", "5"]
hicon = "crrrrccccpss"
htemp = [18, 17, 17, 16, 16, 15, 15, 14, 14, 14, 13, 13]
hpop = [40, 70, 70, 60, 40, 30, 20, 10, 10, 10, 0, 0]
cw = W / 12
for i, h in enumerate(hours):
    cx = cw * i + cw / 2
    text((cx, y + 8), h + "時", 14, fill=DARK, anchor="mt")
    ICON[hicon[i]](cx, y + 50, 19)
    text((cx, y + 78), "%d°" % htemp[i], 19, bold=True, anchor="mt")
    text((cx, y + 106), "%d%%" % hpop[i], 14, fill=DARK, anchor="mt")
y += H_HOURLY
hline(y)

# --- 降水量チャート ---
text((12, y + 6), "降水量 mm/h　過去24時間（実測）｜今後12時間（予報）", 14, fill=DARK)
cx0, cy_base = 12, y + H_PRECIP - 22
ch = H_PRECIP - 54
bw = (W - 24) / 36
past = [0] * 6 + [0.5, 1.5, 3, 2, 1] + [0] * 13
fut = [0, 1, 2, 2, 1.5, 0.5, 0, 0, 0, 0, 0, 0]
for i, v in enumerate(past + fut):
    if v <= 0:
        continue
    h = v / 3.0 * ch
    d.rectangle([cx0 + i * bw + 1, cy_base - h, cx0 + (i + 1) * bw - 2, cy_base],
                fill=BLACK if i < 24 else MID)
d.line([(cx0, cy_base), (W - 12, cy_base)], fill=MID)
xnow = cx0 + 24 * bw
for yy in range(int(cy_base - ch), int(cy_base), 6):
    d.line([(xnow, yy), (xnow, yy + 3)], fill=BLACK)
for lbl, k in (("-24h", 0), ("-12h", 12), ("いま", 24), ("+6h", 30), ("+12h", 36)):
    text((cx0 + k * bw, cy_base + 4), lbl, 12, fill=DARK, anchor="mt" if 0 < k < 36 else ("lt" if k == 0 else "rt"))
y += H_PRECIP
hline(y)

# --- 週間予報 ---
days = ["月 26", "火 27", "水 28", "木 29", "金 30", "土 31"]
dicon = "scrscc"
dmax = [21, 19, 16, 20, 22, 21]
dmin = [13, 14, 12, 11, 13, 14]
dpop = [10, 40, 80, 20, 10, 30]
cw = W / 6
for i, dd in enumerate(days):
    cx = cw * i + cw / 2
    if i:
        d.line([(cw * i, y + 6), (cw * i, y + H_WEEK - 6)], fill=LIGHT)
    text((cx, y + 8), dd, 15, fill=DARK, anchor="mt")
    ICON[dicon[i]](cx, y + 54, 25)
    text((cx, y + 88), "%d° / %d°" % (dmax[i], dmin[i]), 18, bold=True, anchor="mt")
    text((cx, y + 116), "%d%%" % dpop[i], 14, fill=DARK, anchor="mt")
y += H_WEEK
hline(y)

# --- 今日の電力（30分ごと）---
text((12, y + 6), "今日の電力（30分ごと）　棒＝今日　線＝昨日", 13, fill=DARK)
text((W - 12, y + 6), "今日 9.8kWh／昨日同時刻 10.6", 13, fill=DARK, anchor="ra")
base = H - 20
ph = H_POWER - 44
bw = (W - 24) / 48


def load(j, k=1.0):
    hr = j / 2.0
    p = 0.15 + (0.35 if 6 < hr < 9 else 0) + (0.45 if 17 < hr < 22 else 0) + (0.2 if 11 < hr < 14 else 0)
    return p * k


scale = ph / 0.85
for j in range(34):
    h = (load(j, 0.92) + 0.04 * math.cos(j)) * scale
    d.rectangle([12 + j * bw + 1, base - h, 12 + (j + 1) * bw - 2, base], fill=MID)
pts = [(12 + j * bw + bw / 2, base - (load(j) + 0.05 * math.sin(j)) * scale) for j in range(48)]
d.line(pts, fill=BLACK, width=2)
d.line([(12, base), (W - 12, base)], fill=MID)
for hh in (0, 6, 12, 18, 24):
    x = 12 + hh * 2 * bw
    text((x, base + 3), "%d時" % hh, 11, fill=DARK, anchor="lt" if hh == 0 else ("rt" if hh == 24 else "mt"))

# Kindle 3 は16階調なので、実機に近づけるため 16 段に丸める
img = img.point(lambda v: (v * 15 + 127) // 255 * 17)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screen_mock.png")
img.save(out)
print(out, "power height", H_POWER)
