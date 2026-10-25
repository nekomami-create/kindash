"""Cube で描くための文字と天気マークを、PC 上の Pillow で前もって作る。

Cube の Python 2.7 には画像ライブラリもフォントの描画もないので、
使う文字を使う大きさで1文字ずつ灰色の濃さ（0〜255）の板にしておく。
文字の一覧は cube/kindash.py のソースから拾い、大きさは同じファイルの
FONT_SPECS と ICON_SIZES を読む。kindash.py の文言を変えたら、これを
もう一度走らせる。

出力: cube/kindash_assets.json（目次）と cube/kindash_assets.bin（中身）
"""
import ast
import json
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "cube", "kindash.py")
OUT_JSON = os.path.join(ROOT, "cube", "kindash_assets.json")
OUT_BIN = os.path.join(ROOT, "cube", "kindash_assets.bin")
STATIONS = os.path.join(ROOT, "samples", "amedastable.json")
STATION_FONT = "r14"
FONT_R = "C:/Windows/Fonts/BIZ-UDGothicR.ttc"
FONT_B = "C:/Windows/Fonts/BIZ-UDGothicB.ttc"
# 大きな数字の字体は、数字と符号だけあれば足りる
BIG_CHARS = "0123456789.-−+ "
BIG_SIZE = 40

BLACK, LIGHT, PAPER = 0, 187, 255


def read_specs():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("FONT_SPECS", "ICON_SIZES"):
                found[name] = ast.literal_eval(node.value)
    return found["FONT_SPECS"], found["ICON_SIZES"]


def charset():
    text = open(SRC, encoding="utf-8").read()
    chars = set(chr(c) for c in range(0x20, 0x7F))
    chars.update(ch for ch in text if ord(ch) > 0x7F and ch.isprintable())
    return sorted(chars)


def station_chars():
    """全アメダス観測所の名前の字。地点を設定で変えても名前が欠けないように、r14 にだけ入れる。"""
    table = json.load(open(STATIONS, encoding="utf-8"))
    return set(ch for e in table.values() for ch in e["kjName"])


# --- 天気マーク（mock/render_mock.py と同じ形）---
def draw_icon(kind, r):
    size = int(math.ceil(r * 2.2))
    img = Image.new("L", (size, size), PAPER)
    d = ImageDraw.Draw(img)
    cx = cy = size / 2.0

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
            d.ellipse([cx + (dx - rr) * r + 2, cy + (dy - rr) * r + 2,
                       cx + (dx + rr) * r - 2, cy + (dy + rr) * r - 2], fill=fill)
        d.rectangle([cx - 0.45 * r, cy + 0.05 * r, cx + 0.45 * r, cy + 0.48 * r], fill=fill)
        d.line([(cx - 0.5 * r, cy + 0.48 * r), (cx + 0.5 * r, cy + 0.48 * r)], fill=BLACK, width=2)

    def moon(cx, cy, r):
        # 円から少しずらした円を抜いた三日月の形を作り、その縁だけを黒で描く
        mask = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(mask)
        md.ellipse([cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6], fill=255)
        ox, oy = cx + r * 0.32, cy - r * 0.22
        md.ellipse([ox - r * 0.5, oy - r * 0.5, ox + r * 0.5, oy + r * 0.5], fill=0)
        inner = mask.filter(ImageFilter.MinFilter(5))
        img.paste(PAPER, (0, 0), mask)
        img.paste(BLACK, (0, 0), ImageChops.subtract(mask, inner))

    if kind == "sun":
        sun(cx, cy, r)
    elif kind == "moon":
        moon(cx, cy, r)
    elif kind == "mooncloud":
        moon(cx - r * 0.25, cy - r * 0.25, r * 0.75)
        cloud(cx + r * 0.15, cy + r * 0.15, r * 0.8)
    elif kind == "partly":
        sun(cx - r * 0.3, cy - r * 0.3, r * 0.7)
        cloud(cx + r * 0.15, cy + r * 0.15, r * 0.8)
    elif kind == "cloud":
        cloud(cx, cy, r)
    elif kind == "rain":
        cloud(cx, cy - r * 0.2, r * 0.85, fill=LIGHT)
        for dx in (-0.35, 0.0, 0.35):
            x = cx + dx * r
            d.line([(x, cy + r * 0.35), (x - r * 0.12, cy + r * 0.7)], fill=BLACK, width=2)
    elif kind == "snow":
        cloud(cx, cy - r * 0.2, r * 0.85, fill=LIGHT)
        for dx in (-0.35, 0.0, 0.35):
            x, y = cx + dx * r, cy + r * 0.55
            d.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=BLACK)
    elif kind == "thunder":
        cloud(cx, cy - r * 0.2, r * 0.85, fill=LIGHT)
        d.line([(cx + r * 0.1, cy + r * 0.25), (cx - r * 0.15, cy + r * 0.55),
                (cx + r * 0.1, cy + r * 0.55), (cx - r * 0.15, cy + r * 0.9)], fill=BLACK, width=2)
    elif kind == "fog":
        for k, w in enumerate((0.8, 0.6, 0.8)):
            y = cy - r * 0.35 + k * r * 0.35
            d.line([(cx - w * r, y), (cx + w * r, y)], fill=BLACK, width=3)
    else:
        raise ValueError(kind)
    return img


def main():
    font_specs, icon_sizes = read_specs()
    chars = charset()
    station = sorted(set(chars) | station_chars())
    blob = bytearray()
    index = {"fonts": {}, "icons": {}}

    def put(data):
        off = len(blob)
        blob.extend(data)
        return off

    for name, (size, bold) in sorted(font_specs.items()):
        # index=1 は BIZ UDPゴシック（プロポーショナル）
        font = ImageFont.truetype(FONT_B if bold else FONT_R, size, index=1)
        ascent, descent = font.getmetrics()
        glyphs = {}
        if size >= BIG_SIZE:
            todo = BIG_CHARS
        else:
            todo = station if name == STATION_FONT else chars
        for ch in todo:
            x0, y0, x1, y1 = font.getbbox(ch, anchor="ls")
            adv = font.getlength(ch)
            w, h = max(0, x1 - x0), max(0, y1 - y0)
            off = 0
            if w and h:
                img = Image.new("L", (w, h), 0)
                ImageDraw.Draw(img).text((-x0, -y0), ch, font=font, fill=255, anchor="ls")
                off = put(img.tobytes())
            glyphs[ch] = [off, w, h, x0, y0, round(adv, 2)]
        index["fonts"][name] = {"ascent": ascent, "descent": descent, "glyphs": glyphs}

    for r in icon_sizes:
        for kind in ("sun", "partly", "moon", "mooncloud", "cloud", "rain", "snow", "thunder", "fog"):
            img = draw_icon(kind, r)
            index["icons"]["%s%d" % (kind, r)] = [put(img.tobytes()), img.width, img.height]

    with open(OUT_BIN, "wb") as f:
        f.write(blob)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print("%d chars, %d fonts, %d icons, %d bytes" % (
        len(chars), len(index["fonts"]), len(index["icons"]), len(blob)))


if __name__ == "__main__":
    main()
