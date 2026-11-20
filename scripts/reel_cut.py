"""冒頭の1コマ目から「続きが気になる形」を見せるリール。

投稿データの "reel_style" が list / split / myth で、"reel" に "open" があるときだけ使う（無い投稿は今までの描画のまま）。

  list  : 数を先に言い、中身を「？？？」で伏せた一覧を1コマ目から出す。一覧は出したまま、場面ごとに1つずつ開く
  split : 上下2分割。上に「よくある形」、下に「こうする形」（下は「？？？」で伏せておき、途中で開く）
  myth  : 思い込みの一言を大きく出し、「実際は ？？？」を伏せておく。×を付けてから、実際を開く

どの型も、背景は動く実写（"reel.bg" に動画か写真のパスを並べる。場面ごとに順に切り替える）、
画面の字は「beats」（読み上げ1文を短い句に割ったもの）を1〜2秒ごとに入れ替える。

  "reel_style": "list",
  "reel": {
    "bg": ["assets/videos/pexels-v-123.mp4", "assets/videos/pexels-v-456.mp4"],
    "open": {"title": "診察券の裏に\n*入れる3つ*", "items": ["予約のQRコード", "休診日のページ", "電話番号"],
             "beats": ["診察券の裏、", "*白いまま*ですか"]},
    "scenes": [
      {"type": "item", "n": 1, "beats": ["1つ目は", "*予約のQRコード*"]},     # list: n 番目を開く（"n": [2, 3] で順に2つ）
      {"type": "beats", "beats": [...]},                                       # 型の絵を出したまま、句だけ替える
      {"type": "half", "side": "bottom", "reveal": true, "beats": [...]},      # split: 上か下を明るくする。reveal で下を開く
      {"type": "fact", "beats": [...]},                                        # myth: 「実際は」を開く
      {"type": "full", "label": "手順", "beats": [...]},                       # 全画面の実写の上に句だけ
      {"type": "card", "label": "文例", "lines": ["…", "…"], "beats": [...]},  # 白い札に文例を1行ずつ
      {"type": "end", "lines": ["…", "*…*"], "say": "…"}]}
  split の open: {"title": "HPの持ち物欄", "top": {"label": "古い書き方", "text": "…"}, "bottom": {"label": "いまの書き方", "text": "…"}, "beats": [...]}
  myth  の open: {"label": "よくある思い込み", "myth": "「19時に\n着けばいい」", "fact_label": "実際は", "fact": "受付は18時半まで", "beats": [...]}

読み上げ（narration）は、冒頭（open）＋ scenes の順に1つずつ。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

import note_style as ns
import reel_screen as rs
import voice
from common import ROOT, log
from media import ffmpeg_path
from reel_anim import FPS, H, W, Writer, ease_back, ease_out, pulse, solid_in, underline

STYLES = ("list", "split", "myth")
DARK = (14, 15, 20)
GRAY = (58, 61, 70)
RED = (226, 60, 66)
VIDEO_EXT = (".mp4", ".mov", ".webm", ".m4v")
MAX_BEAT = 1.8  # 1つの句がこれより長く出るときは、途中で強調の色を入れ替える（同じ字のまま2秒止めない）


def uses(post: dict) -> bool:
    return post.get("reel_style") in STYLES and bool((post.get("reel") or {}).get("open"))


def schedule(beats: list[str], dur: float) -> list[tuple[float, float]]:
    """句ごとの（出る時刻, 出ている長さ）。読む字数に比例して割る。"""
    w = [max(3, len(voice.speakable(ns.strip_rich(b)).replace("、", ""))) for b in beats] or [1]
    tot, t, out = sum(w), 0.0, []
    for x in w:
        out.append((t, dur * x / tot))
        t += dur * x / tot
    return out


class Bg:
    """背景。動画は ffmpeg で1コマずつ読み（くり返し）、写真はゆっくり寄りながら2秒ほどで切り替える。"""

    def __init__(self, w: int, h: int, dim: float = 0.66):
        self.w, self.h, self.dim = w, h, dim
        self.p = None
        self.still: Image.Image | None = None
        self.src = None
        self.k = 0
        self.last = Image.new("RGB", (w, h), DARK)
        self.lut = None  # 明るい素材をさらに暗くする表（最初の1コマの明るさで決める）

    def use(self, path: str | None) -> None:
        if path == self.src:
            return
        self.close()
        self.src, self.k, self.still, self.lut = path, 0, None, None
        if not path:
            return
        pth = Path(path)
        pth = pth if pth.is_absolute() else ROOT / pth
        if not pth.exists():
            log(f"背景が見つからないため、暗い地で描きます: {path}")
            return
        if pth.suffix.lower() in VIDEO_EXT:
            vf = (f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},fps={FPS},"
                  f"lutrgb=r=val*{self.dim}:g=val*{self.dim}:b=val*{self.dim}")
            self.p = subprocess.Popen([ffmpeg_path(), "-loglevel", "error", "-stream_loop", "-1", "-i", str(pth), "-an",
                                       "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                      stdout=subprocess.PIPE, bufsize=self.w * self.h * 3)
        else:
            im = ns._photo_fill(str(pth), int(self.w * 1.2), int(self.h * 1.2), 0.4)
            self.still = Image.blend(im, Image.new("RGB", im.size, DARK), 1 - self.dim)

    def frame(self) -> Image.Image:
        self.k += 1
        if self.p is not None:
            n = self.w * self.h * 3
            buf = self.p.stdout.read(n)
            if len(buf) == n:
                self.last = self._tone(Image.frombytes("RGB", (self.w, self.h), buf))
            else:  # 読めなくなったら、最後の絵のまま続ける（描画は止めない）
                self.close()
            return self.last
        if self.still is not None:
            seg, q = divmod(self.k, 62)  # 2秒ほどで寄り方を切り替える
            p = q / 62
            s = (1.2 - 0.1 * p) if seg % 2 == 0 else (1.06 + 0.1 * p)
            cw, ch = int(self.w * s / 1.2 * 1.0), int(self.h * s / 1.2 * 1.0)
            x = int((self.still.width - cw) * (0.5 + (0.3 if seg % 3 == 1 else -0.3 if seg % 3 == 2 else 0.0) * 1.0))
            x = max(0, min(self.still.width - cw, x))
            y = int((self.still.height - ch) * (0.35 if seg % 2 == 0 else 0.6))
            self.last = self._tone(self.still.crop((x, y, x + cw, y + ch)).resize((self.w, self.h), Image.BILINEAR))
        return self.last

    def _tone(self, im: Image.Image) -> Image.Image:
        if self.lut is None:
            px = list(im.resize((24, 24), Image.BILINEAR).convert("L").getdata())
            mean = sum(px) / len(px)
            f = min(1.0, self.target / max(1.0, mean))
            self.lut = [int(v * f) for v in range(256)] * 3 if f < 0.93 else False
        return im.point(self.lut) if self.lut else im

    target = 88.0  # 背景の明るさの上限（0〜255 の平均）。白い字と黄色い字が読める暗さ

    def close(self) -> None:
        if self.p is not None:
            try:
                self.p.kill()
                self.p.stdout.close()
                self.p.wait(timeout=10)
            except Exception:
                pass
            self.p = None


class CutReel:
    def __init__(self, cfg: dict, post: dict, out: Path):
        self.cfg, self.post, self.out = cfg, post, out
        self.x = rs.Ctx(cfg, post)
        self.n = self.x.n
        self.spec = post.get("reel") or {}
        self.style = post.get("reel_style")
        self.open = self.spec.get("open") or {}
        self.bgs = [b for b in (self.spec.get("bg") or []) if b]
        n = self.n
        self.yellow = n.marker if ns.luminance(n.marker) > 0.6 else (255, 236, 90)
        self.nar = voice.Narration(cfg, post, out.parent / f"{out.stem}_voice")
        self.bg = Bg(W, H)
        self.half = [Bg(W, H // 2), Bg(W, H // 2)]
        self.opened: set[int] = set()   # list: 開いた行
        self.struck = False             # myth: ×を付けたか
        self.fact = False               # myth: 実際を開いたか
        self.s_open = False             # split: 下を開いたか
        self.beat_log: list[dict] = []
        self._probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        self._crops: dict = {}
        getattr(self, f"_setup_{self.style}")()

    # ---------------------------------------------------------------- 部品
    def comp(self, fr: Image.Image, ly: Image.Image) -> None:
        """全画面の透明な層を重ねる。字のある範囲だけを切り出して重ねる（毎コマ全画面を合成しない）。"""
        c = self._crops.get(id(ly))
        if c is None or c[0] is not ly:
            box = ly.getbbox()
            c = (ly, box, ly.crop(box) if box else None)
            if len(self._crops) > 400:
                self._crops.clear()
            self._crops[id(ly)] = c
        if c[2] is not None:
            fr.alpha_composite(c[2], (c[1][0], c[1][1]))

    def cap(self, text: str, y: int, size: float, box: bool = False) -> Image.Image:
        """縁取りの大きな字。改行を指定した行が画面の幅を超えるときは、収まるまで小さくする。"""
        lines = ns.strip_rich(text).split("\n")
        for _ in range(14):
            f = self.n.f(int(W * size))
            if max(self._probe.textlength(ln, font=f) for ln in lines) + f.size * 0.3 <= W * 0.9 or size < 0.05:
                break
            size *= 0.95
        return self.x.caption(text, y=y, size=size, box=box)

    def fit(self, text: str, size: float, max_w: int, floor: float = 0.04):
        f = self.n.f(int(W * size))
        while self._probe.textlength(text, font=f) > max_w and f.size > W * floor:
            f = self.n.f(int(f.size * 0.95))
        return f

    def pill(self, text: str, y: int, fill, fg=(255, 255, 255), size: float = 0.042) -> Image.Image:
        ly = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if not text:
            return ly
        d = ImageDraw.Draw(ly)
        f = self.fit(text, size, int(W * 0.8))
        tw = d.textlength(text, font=f)
        hh = int(f.size * 1.9)
        d.rounded_rectangle([(W - tw) / 2 - 30, y, (W + tw) / 2 + 30, y + hh], radius=hh // 2, fill=tuple(fill) + (255,))
        d.text((W / 2, y + hh / 2), text, font=f, fill=tuple(fg) + (255,), anchor="mm")
        return ly

    def bar(self, tag: str, text: str, opened: bool, active: bool, rh: int, number: bool = True) -> Image.Image:
        """一覧の1行（伏せた形／開いた形）。左に番号か札、右に中身。"""
        n = self.n
        rw = int(W * 0.86)
        im = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        fill = (255, 255, 255, 255) if opened else (20, 22, 28, 238)
        line = self.yellow + (255,) if active else ((255, 255, 255, 110) if not opened else (255, 255, 255, 255))
        d.rounded_rectangle([0, 0, rw - 1, rh - 1], radius=int(rh * 0.28), fill=fill, outline=line, width=8 if active else 3)
        pad = int(rh * 0.18)
        if number:
            r = (rh - pad * 2) // 2
            d.ellipse([pad, pad, pad + r * 2, pad + r * 2], fill=n.primary + (255,))
            d.text((pad + r, pad + r), tag, font=n.f(int(r * 1.15)), fill=n.on_primary + (255,), anchor="mm")
            x0 = pad + r * 2 + int(W * 0.03)
        else:
            ft = n.f(int(rh * 0.26))
            tw = int(d.textlength(tag, font=ft)) + 36
            d.rounded_rectangle([pad, pad, pad + tw, rh - pad], radius=int(rh * 0.2), fill=n.primary + (255,))
            d.text((pad + tw / 2, rh / 2), tag, font=ft, fill=n.on_primary + (255,), anchor="mm")
            x0 = pad + tw + int(W * 0.03)
        if opened:
            f = self.fit(text, rh * 0.46 / W, rw - x0 - pad, floor=0.034)
            d.text((x0, rh / 2), text, font=f, fill=ns.INK + (255,), anchor="lm")
        else:
            d.text((x0, rh / 2), "？？？", font=n.f(int(rh * 0.46)), fill=self.yellow + (255,), anchor="lm")
        return im

    def flip(self, fr: Image.Image, closed: Image.Image, opened: Image.Image, y: int, t: float) -> None:
        """伏せた行が縦に閉じて、開いた行がはじけるように出る。"""
        if t < 0.14:
            im, s = closed, 1 - t / 0.14
        else:
            im, s = opened, (ease_back(min(1.0, (t - 0.14) / 0.24)) if t < 0.38 else 1.0)
        if abs(s - 1) > 0.004:
            im = im.resize((im.width, max(2, int(im.height * s))), Image.BILINEAR)
        fr.alpha_composite(im, ((W - im.width) // 2, max(0, y + (closed.height - im.height) // 2)))

    # ---------------------------------------------------------------- list
    def _setup_list(self) -> None:
        o = self.open
        self.items = list(o.get("items") or [])
        self.l_title = self.cap(o.get("title", ""), int(H * 0.155), 0.098)
        tb = self.l_title.getbbox() or (0, 0, W, int(H * 0.2))
        self.l_rh = int(W * (0.14 if len(self.items) <= 3 else 0.122 if len(self.items) == 4 else 0.104))
        gap = int(W * 0.02)
        y = tb[3] + int(W * 0.05)
        self.l_y = [y + i * (self.l_rh + gap) for i in range(len(self.items))]
        self.board_bottom = y + len(self.items) * (self.l_rh + gap)
        self._rows: dict = {}

    def _row(self, i: int, opened: bool, active: bool) -> Image.Image:
        key = (i, opened, active)
        if key not in self._rows:
            self._rows[key] = self.bar(str(i + 1), ns.strip_rich(self.items[i]), opened, active, self.l_rh)
        return self._rows[key]

    @staticmethod
    def _ns(sc: dict) -> list[int]:
        n = sc.get("n", 0)
        return [int(x) - 1 for x in (n if isinstance(n, list) else [n])]

    def board_list(self, fr: Image.Image, t: float, dur: float, sc: dict) -> None:
        self.comp(fr, self.l_title)
        cur = self._ns(sc) if sc.get("type") == "item" else []
        first = self.open.get("first") if sc.get("type") == "open" else None  # 冒頭の半ばで開く行（答えを早く1つ見せる）
        t_first = dur * float(self.open.get("first_at", 0.5))
        scan = int(t / 0.5) % max(1, len(self.items)) if sc.get("type") == "open" else None  # 冒頭は伏せた行を順に光らせる
        for i, y in enumerate(self.l_y):
            if first is not None and i == int(first) - 1 and t >= t_first:
                self.flip(fr, self._row(i, False, True), self._row(i, True, True), y, t - t_first)
            elif i in cur and i not in self.opened:  # この場面で開く行（もう開いている行は開いたまま）。2つ以上あるときは、場面の長さを等分した時刻に順に開く
                ti = t - cur.index(i) * dur / len(cur)
                if ti < 0:
                    im = self._row(i, False, False)
                    fr.alpha_composite(im, ((W - im.width) // 2, y))
                else:
                    last = cur.index(i) == len(cur) - 1 or t < (cur.index(i) + 1) * dur / len(cur)
                    self.flip(fr, self._row(i, False, True), self._row(i, True, last), y, ti)
            else:
                op = i in self.opened
                im = self._row(i, op, (not op) and i == scan)
                fr.alpha_composite(im, ((W - im.width) // 2, y))

    # ---------------------------------------------------------------- myth
    def _setup_myth(self) -> None:
        o = self.open
        self.m_pill = self.pill(o.get("label") or "よくある思い込み", int(H * 0.15), GRAY, size=0.046)
        self.m_text = self.cap(o.get("myth", ""), int(H * 0.21), 0.108)
        mb = self.m_text.getbbox() or (0, 0, W, int(H * 0.3))
        self.m_box = mb
        dim = self.m_text.copy()
        dim.putalpha(dim.getchannel("A").point(lambda v: int(v * 0.42)))
        self.m_dim = dim
        self.m_rh = int(W * 0.17)
        self.m_y = mb[3] + int(W * 0.07)
        tag = o.get("fact_label") or "実際は"
        self.m_closed = self.bar(tag, "", False, False, self.m_rh, number=False)
        self.m_closed_on = self.bar(tag, "", False, True, self.m_rh, number=False)
        self.m_opened = self.bar(tag, ns.strip_rich(o.get("fact", "")), True, True, self.m_rh, number=False)
        self.board_bottom = self.m_y + self.m_rh

    def board_myth(self, fr: Image.Image, t: float, dur: float, sc: dict) -> None:
        typ = sc.get("type")
        self.comp(fr, self.m_pill)
        self.comp(fr, self.m_dim if self.fact else self.m_text)  # 「実際は」を開いたあとの場面では、思い込みの字を薄くする
        ts = t - dur * 0.3 if typ == "open" else (99.0 if self.struck else -1.0)  # ×の印は冒頭の早いうちに付ける
        if self.open.get("strike") is False:  # 思い込みではなく「よくある場面」を出すときは×を付けない
            ts = -1.0
        if ts > 0:  # 字には重ねず、右上に丸い×の印を押す
            x0, y0, x1, y1 = self.m_box
            r = int(W * 0.05 * (ease_back(min(1.0, ts / 0.25)) if ts < 0.25 else 1.0))
            cx, cy = min(W - int(W * 0.07), x1 + int(W * 0.02)), y0 - int(W * 0.005)
            d = ImageDraw.Draw(fr)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED + (255,), outline=(255, 255, 255, 255), width=5)
            k = int(r * 0.42)
            d.line([(cx - k, cy - k), (cx + k, cy + k)], fill=(255, 255, 255, 255), width=max(6, r // 5))
            d.line([(cx - k, cy + k), (cx + k, cy - k)], fill=(255, 255, 255, 255), width=max(6, r // 5))
        fa = self.open.get("fact_at")
        t_fact = t if typ == "fact" and not self.fact else (t - dur * float(fa) if typ == "open" and fa is not None else None)
        if t_fact is not None and t_fact >= 0:
            self.flip(fr, self.m_closed_on, self.m_opened, self.m_y, t_fact)
        else:
            blink = typ == "open" and int(t / 0.5) % 2 == 1
            im = self.m_opened if self.fact else (self.m_closed_on if blink else self.m_closed)
            fr.alpha_composite(im, ((W - im.width) // 2, self.m_y))

    # ---------------------------------------------------------------- split
    def _setup_split(self) -> None:
        o = self.open
        top, bot = o.get("top") or {}, o.get("bottom") or {}
        n = self.n
        y_top, y_bot = int(H * 0.115), H // 2 + int(H * 0.05)
        mark = [top.get("mark", "×"), bot.get("mark", "○")]  # 良し悪しではない2つを並べるときは mark: "" で印を外す
        self.s_lab = [self.pill((mark[0] + " " if mark[0] else "") + top.get("label", "よくある形"), y_top, GRAY, size=0.05),
                      self.pill((mark[1] + " " if mark[1] else "") + bot.get("label", "こうする形"), y_bot, n.primary, n.on_primary, size=0.05)]
        self.s_txt = [self.cap(top.get("text", ""), y_top + int(H * 0.058), 0.094),
                      self.cap(bot.get("text", ""), y_bot + int(H * 0.058), 0.094)]
        self.s_hidden = self.cap("*？？？*", y_bot + int(H * 0.062), 0.13)
        self.s_beat_y = [(ly.getbbox() or (0, 0, 0, y + int(H * 0.15)))[3] + int(H * 0.022)
                         for ly, y in zip(self.s_txt, (y_top, y_bot))]
        self.s_title = self.pill(o.get("title", ""), H // 2 - int(W * 0.05), (255, 255, 255), ns.INK, size=0.05)
        self.s_shade = Image.new("RGBA", (W, H // 2), DARK + (150,))
        self.s_tint = Image.new("RGBA", (W, H // 2), n.primary + (54,))
        self.board_bottom = int(H * 0.5)

    def board_split(self, fr: Image.Image, t: float, dur: float, sc: dict) -> None:
        typ = sc.get("type")
        side = {"top": 0, "bottom": 1}.get(sc.get("side", "top"), 0) if typ in ("half", "open") else None
        if typ == "open":
            side = None
        fr.alpha_composite(self.s_tint, (0, H // 2))
        if side is not None:  # 話していない側を暗くする
            fr.alpha_composite(self.s_shade, (0, (1 - side) * (H // 2)))
        ImageDraw.Draw(fr).rectangle([0, H // 2 - 4, W, H // 2 + 4], fill=(255, 255, 255, 255))
        self.comp(fr, self.s_lab[0])
        self.comp(fr, self.s_lab[1])
        self.comp(fr, self.s_txt[0])
        ra = self.open.get("reveal_at")
        if typ == "open" and ra is not None and t >= dur * float(ra):
            self.comp(fr, rs._pop(self.s_txt[1], t - dur * float(ra) + 0.03, 0.4))
        elif typ == "half" and sc.get("reveal") and not self.s_open:
            self.comp(fr, rs._pop(self.s_txt[1], t + 0.03, 0.4) if t > 0.12 else self.s_hidden)
        elif self.s_open:
            self.comp(fr, self.s_txt[1])
        else:
            self.comp(fr, pulse(self.s_hidden, (t % 1.0) - 0.3, 0.4, 0.08))
        self.comp(fr, self.s_title)

    # ---------------------------------------------------------------- 場面
    def backdrop(self, sc: dict, t: float, dur: float) -> Image.Image:
        if self.style == "split" and sc.get("type") in ("open", "half", "beats"):
            fr = Image.new("RGB", (W, H))
            fr.paste(self.half[0].frame(), (0, 0))
            fr.paste(self.half[1].frame(), (0, H // 2))
            return fr.convert("RGBA")
        im = self.bg.frame()
        if dur > 3.2 and t > dur / 2:  # 長い場面は、途中で寄りを切り替える
            w2, h2 = int(W / 1.14), int(H / 1.14)
            x0, y0 = (W - w2) // 2, int((H - h2) * 0.45)
            im = im.crop((x0, y0, x0 + w2, y0 + h2)).resize((W, H), Image.BILINEAR)
        return im.convert("RGBA")

    def scene(self, wr: Writer, idx: int, sc: dict, dur: float) -> None:
        typ = sc.get("type")
        first = idx == 0
        board = None
        beat_y, beat_size = int(H * 0.37), 0.12
        header = None
        card = None
        if typ in ("open", "item", "fact", "half", "beats"):
            board = getattr(self, f"board_{self.style}")
            if self.style == "split":
                side = 0 if typ == "open" else {"top": 0, "bottom": 1}.get(sc.get("side", "top"), 0)
                beat_y, beat_size = self.s_beat_y[side], 0.074
            else:
                beat_y, beat_size = self.board_bottom + int(H * 0.05), 0.112
        elif typ == "card":
            card = self._card(sc)
            beat_y, beat_size = card[2] + int(H * 0.04), 0.088
            header = self.pill(self.open.get("header") or self.n.label, int(H * 0.118), self.n.primary, self.n.on_primary)
        else:  # full
            header = self.pill(self.open.get("header") or self.n.label, int(H * 0.118), self.n.primary, self.n.on_primary)
            if sc.get("label"):
                kick = self.cap(sc["label"], int(H * 0.27), 0.062)
                beat_y = int(H * 0.35)
            else:
                kick = None
        beats = list(sc.get("beats") or [])
        sched = schedule(beats, dur)
        boxed = board is not None and self.style == "split"  # 2分割の中の字幕は帯付き（段の大きな字と続けて読めないように）
        layers = [self.cap(b, beat_y, beat_size, boxed) for b in beats]
        alts = {}
        for j, (b, (_, bd)) in enumerate(zip(beats, sched)):
            if bd > MAX_BEAT:  # 半ばで色を入れ替えた版（強調なし→全部強調、強調あり→強調を外す）
                plain = ns.strip_rich(b)
                alts[j] = self.cap(plain if "*" in b else "\n".join(f"*{x}*" for x in plain.split("\n")), beat_y, beat_size, boxed)
        steps = max(1, int(dur * FPS))
        t0 = wr.n / FPS
        for j, (b, (st, bd)) in enumerate(zip(beats, sched)):
            self.beat_log.append({"scene": idx, "type": typ, "at": round(t0 + st, 2), "sec": round(bd, 2), "text": ns.strip_rich(b)})
        for k in range(steps):
            t = k / FPS
            fr = self.backdrop(sc, t, dur)
            if board:
                board(fr, t, dur, sc)
            if header is not None:
                self.comp(fr, header)
            if typ == "full" and sc.get("label") and kick is not None:
                self.comp(fr, kick)
            if card:
                self._card_draw(fr, card, t, dur)
            if layers:
                j = max(i for i, (st, _) in enumerate(sched) if st <= t + 1e-6)
                st, bd = sched[j]
                tb = t - st
                ly = solid_in(layers[j], tb) if (first and j == 0) else rs._pop(layers[j], tb + 0.07, 0.26)
                if j in alts and tb >= bd / 2:  # 長く出る句は、半ばで色を入れ替えて下線を引く
                    ly = rs._pop(alts[j], tb - bd / 2 + 0.07, 0.26)
                    underline(fr, alts[j], tb - bd / 2, self.yellow)
                self.comp(fr, ly)
            wr.put(fr)
        # 場面が終わった時点の状態を残す
        if typ == "item":
            self.opened.update(self._ns(sc))
        if typ == "open" and self.style == "list" and self.open.get("first"):
            self.opened.add(int(self.open["first"]) - 1)
        if typ == "open" and self.style == "myth":
            self.struck = True
        if typ == "fact" or (typ == "open" and self.style == "myth" and self.open.get("fact_at") is not None):
            self.fact = True
        if (typ == "half" and sc.get("reveal")) or (typ == "open" and self.style == "split" and self.open.get("reveal_at") is not None):
            self.s_open = True

    def _card(self, sc: dict):
        n = self.n
        lines = [ns.strip_rich(x) for x in sc.get("lines") or []]
        cw, P = int(W * 0.86), int(W * 0.06)
        f = n.f(int(W * 0.066))
        while lines and max(self._probe.textlength(x, font=f) for x in lines) > cw - P * 2 - 30 and f.size > W * 0.042:
            f = n.f(int(f.size * 0.95))
        fl = n.f(int(W * 0.04))
        lh = int(f.size * 1.55)
        lab_h = int(fl.size * 2.2)
        ch = P + lab_h + len(lines) * lh + P - int(lh * 0.2)
        y0 = int(H * 0.19)
        base = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(base)
        d.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=int(W * 0.04), fill=(255, 255, 255, 255))
        lab = sc.get("label") or "文例"
        lw = int(d.textlength(lab, font=fl)) + 44
        d.rounded_rectangle([P, P - 8, P + lw, P - 8 + int(fl.size * 1.7)], radius=int(fl.size * 0.85), fill=n.primary + (255,))
        d.text((P + lw / 2, P - 8 + fl.size * 0.85), lab, font=fl, fill=n.on_primary + (255,), anchor="mm")
        rows = []
        for i, ln in enumerate(lines):
            ly = Image.new("RGBA", (cw, lh), (0, 0, 0, 0))
            dl = ImageDraw.Draw(ly)
            dl.rectangle([P, int(lh * 0.12), P + 8, int(lh * 0.88)], fill=n.primary + (255,))
            dl.text((P + 30, lh / 2), ln, font=f, fill=ns.INK + (255,), anchor="lm")
            rows.append((ly, P + lab_h + i * lh))
        return base, rows, y0 + ch, y0

    def _card_draw(self, fr: Image.Image, card, t: float, dur: float) -> None:
        base, rows, _, y0 = card
        x0 = (W - base.width) // 2
        fr.alpha_composite(base, (x0, y0))
        gap = min(1.3, dur * 0.62 / max(1, len(rows)))
        for i, (ly, yy) in enumerate(rows):
            ti = t - i * gap
            if ti < 0:
                break
            e = ease_out(min(1.0, ti / 0.25)) if i else 1.0  # 1行目は入った時点で出ている
            piece = ly
            if e < 1:
                piece = ly.copy()
                piece.putalpha(piece.getchannel("A").point(lambda v, e=e: int(v * e)))
            fr.alpha_composite(piece, (x0 + int((1 - e) * 50), y0 + yy))

    def end(self, wr: Writer, sc: dict, dur: float) -> None:
        n = self.n
        style = sc.get("style") or self.post.get("end_style") or "save"
        veil = Image.new("RGBA", (W, H), n.primary + (176,))
        lines = [self.cap(ln, int(H * 0.3) + i * int(W * 0.15), 0.11) for i, ln in enumerate(sc.get("lines", []))]
        cap = self.cap(sc.get("say", ""), int(H * 0.55), 0.072)
        cb = cap.getbbox()
        mark_y = (cb[3] if cb else int(H * 0.6)) + 50
        steps = max(1, int(max(dur, float(sc.get("sec", 2.6))) * FPS))
        total = steps / FPS
        t_line = min(0.8, total * 0.18)
        t_cap = max(t_line + 0.3, total * 0.4)
        t_mark = max(t_cap + 0.3, total * 0.62)
        for k in range(steps):
            t = k / FPS
            fr = self.bg.frame().convert("RGBA")
            fr.alpha_composite(veil)
            for i, ly in enumerate(lines):
                if i == 0:
                    self.comp(fr, ly)
                elif t > t_line + (i - 1) * 0.15:
                    fr.alpha_composite(rs._pop(ly, t - t_line - (i - 1) * 0.15 + 0.05, 0.36))
            if t > t_cap:
                fr.alpha_composite(rs._pop(cap, t - t_cap + 0.05))
            if t > t_mark and style == "save":
                bounce = abs(((t - t_mark) * 2.2) % 2 - 1)
                ns._bookmark(ImageDraw.Draw(fr), W // 2 - 43, mark_y + 30 - int(30 * bounce), 120, (255, 255, 255))
            elif t > t_mark:
                underline(fr, cap, t - t_mark, (255, 255, 255))
            wr.put(fr)

    # ---------------------------------------------------------------- 全体
    def render(self) -> Path:
        wr = Writer(self.out, 60)
        scenes = [dict(self.open, type="open")] + list(self.spec.get("scenes", []))
        bgs = self.bgs or [None]
        full = bgs
        if self.style == "split":  # 上と下に1本ずつ。全画面の場面は残りを使う
            self.half[0].use(bgs[0])
            self.half[1].use(bgs[1 % len(bgs)])
            full = bgs[2:] or bgs
        try:
            nfull = 0
            for i, sc in enumerate(scenes):
                self.nar.mark(i, wr.n / FPS)
                beats = sc.get("beats") or []
                dur = max(self.nar.need(i), float(sc.get("sec", 0)),
                          0.0 if self.nar.need(i) else 0.9 + 0.16 * sum(len(ns.strip_rich(b)) for b in beats))
                is_split_board = self.style == "split" and sc.get("type") in ("open", "half", "beats")
                if not is_split_board:
                    self.bg.use(full[nfull % len(full)])
                    nfull += 1
                if sc.get("type") == "end":
                    self.end(wr, sc, dur)
                else:
                    self.scene(wr, i, sc, dur)
        finally:
            self.bg.close()
            for b in self.half:
                b.close()
        sec = wr.close()
        self.nar.mux(self.out, sec)
        if os.environ.get("REEL_BEAT_LOG"):  # 確かめ用: どの句が何秒出ていたか
            Path(os.environ["REEL_BEAT_LOG"]).write_text(json.dumps(self.beat_log, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"リール動画を生成しました（{self.style} の型）: {self.out} ({sec:.1f}秒)")
        return self.out


def render_cut_reel(cfg: dict, post: dict, out_path: Path) -> Path:
    return CutReel(cfg, post, out_path).render()
