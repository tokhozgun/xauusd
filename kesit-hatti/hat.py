#!/usr/bin/env python3
"""Açık Açık kesit hattı.

Bir yayın kaydından, iş dosyasındaki (isler/is.json) talimata göre:
  - mod "kareler": kaynaktan belirli saniyelerde kare çıkarır (kırpma kutularını belirlemek için)
  - mod "kesit":   her kesit için
        <id>_<ad>_yatay.mp4   orijinal yatay kesit
        <id>_<ad>_reels.mp4   1080x1920: üstte konuk, ortada beyaz bant + siyah altyazı, altta sunucu
        <id>_<ad>.srt         kesitin altyazısı
üretir. Çıktılar cikti/ klasörüne yazılır.

Kullanım: python hat.py isler/is.json [--kaynak yerel.mp4] [--cikti cikti]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

W, H = 1080, 1920
UST_H = 880       # konuk paneli
BANT_H = 160      # beyaz altyazı bandı
ALT_H = H - UST_H - BANT_H  # sunucu paneli (880)
SATIR_KARAKTER = 34
ENFAZLA_SATIR = 2
ENFAZLA_SURE = 3.2


def sh(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def sure(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def boyut(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


# ---------------------------------------------------------------- kaynak

def kaynak_al(is_, hedef: Path, yerel: str | None) -> Path:
    if yerel:
        return Path(yerel)
    if hedef.exists() and hedef.stat().st_size > 0:
        return hedef
    k = is_.get("kaynak", {})
    hatalar = []
    if k.get("youtube"):
        try:
            sh(["yt-dlp", "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
                "--merge-output-format", "mp4", "-o", str(hedef), k["youtube"]])
            return hedef
        except subprocess.CalledProcessError as e:
            hatalar.append(f"yt-dlp: {e}")
    if k.get("yedek_url"):
        try:
            sh(["curl", "-fL", "--retry", "3", "-o", str(hedef), k["yedek_url"]])
            return hedef
        except subprocess.CalledProcessError as e:
            hatalar.append(f"yedek_url: {e}")
    sys.exit("Kaynak indirilemedi: " + "; ".join(hatalar))


# ---------------------------------------------------------------- kareler

def kareler(is_, src: Path, cikti: Path):
    d = cikti / "kareler"
    d.mkdir(parents=True, exist_ok=True)
    w, h = boyut(src)
    (d / "kaynak.txt").write_text(f"genislik={w}\nyukseklik={h}\nsure={sure(src):.1f}\n")
    for t in is_.get("kareler", [30, 60, 120, 300, 600]):
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", t, "-i", src, "-frames:v", "1",
            "-vf", "scale=960:-2", "-q:v", "3", d / f"kare_{int(t):05d}.jpg"])


# ---------------------------------------------------------------- altyazı

def kelimeler_whisper(wav: Path, model_adi: str, dil: str):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_adi, device="cpu", compute_type="int8")
    import wave
    import numpy as np
    with wave.open(str(wav), "rb") as wf:
        ses = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs, _ = model.transcribe(ses, language=dil, word_timestamps=True, vad_filter=True,
                               initial_prompt="Dr. İbrahim Can, Güzem Yılmaz Ertem, Açık Açık, halka arz, konkordato, SPK.")
    out = []
    for s in segs:
        for wd in s.words or []:
            t = wd.word.strip()
            if t:
                out.append((wd.start, wd.end, t))
    return out


def srt_oku(path: Path, ofset: float = 0.0):
    """SRT'yi kelime düzeyine yayar (zamanlar kelimelere orantılı bölünür)."""
    blok = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    out = []
    for b in blok:
        satir = b.strip().splitlines()
        if len(satir) < 3:
            continue
        m = re.match(r"(\S+)\s*-->\s*(\S+)", satir[1])
        if not m:
            continue
        a, z = zaman_coz(m.group(1)) - ofset, zaman_coz(m.group(2)) - ofset
        kel = " ".join(satir[2:]).split()
        if not kel:
            continue
        adim = (z - a) / len(kel)
        for i, k in enumerate(kel):
            out.append((a + i * adim, a + (i + 1) * adim, k))
    return out


def zaman_coz(s):
    h, m, rest = s.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parcala(kelimeler):
    """Kelimeleri 2 satırlık, ~34 karakterlik altyazı bloklarına böler."""
    bloklar, cur = [], []

    def metin(ws):
        return " ".join(w[2] for w in ws)

    def sarili(ws):
        satirlar, s = [], ""
        for w in ws:
            aday = (s + " " + w[2]).strip()
            if len(aday) > SATIR_KARAKTER and s:
                satirlar.append(s)
                s = w[2]
            else:
                s = aday
        if s:
            satirlar.append(s)
        return satirlar

    for w in kelimeler:
        deneme = cur + [w]
        if cur and (len(sarili(deneme)) > ENFAZLA_SATIR or w[1] - cur[0][0] > ENFAZLA_SURE):
            bloklar.append(cur)
            cur = [w]
        else:
            cur = deneme
        if cur and re.search(r"[.?!]$", w[2]) and w[1] - cur[0][0] > 1.2:
            bloklar.append(cur)
            cur = []
    if cur:
        bloklar.append(cur)
    out = []
    for i, b in enumerate(bloklar):
        bas = b[0][0]
        son = b[-1][1]
        if i + 1 < len(bloklar):
            son = min(max(son, bas + 0.8), bloklar[i + 1][0][0])
        out.append((max(0.0, bas), son, sarili(b), metin(b)))
    return out


def ts_srt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ts_ass(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def srt_yaz(bloklar, path: Path):
    with path.open("w", encoding="utf-8") as f:
        for i, (a, z, satirlar, _) in enumerate(bloklar, 1):
            f.write(f"{i}\n{ts_srt(a)} --> {ts_srt(z)}\n" + "\n".join(satirlar) + "\n\n")


def ass_yaz(bloklar, path: Path, font: str, punto: int):
    cy = UST_H + BANT_H // 2
    with path.open("w", encoding="utf-8") as f:
        f.write(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bant,{font},{punto},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""")
        for a, z, satirlar, _ in bloklar:
            txt = "\\N".join(s.replace("{", "(").replace("}", ")") for s in satirlar)
            f.write(f"Dialogue: 0,{ts_ass(a)},{ts_ass(z)},Bant,,0,0,0,,{{\\pos({W // 2},{cy})}}{txt}\n")


# ---------------------------------------------------------------- kesit

def kirp_filtre(k, etiket, hedef_h):
    return (f"crop={k['w']}:{k['h']}:{k['x']}:{k['y']},"
            f"scale={W}:{hedef_h}:force_original_aspect_ratio=increase,crop={W}:{hedef_h},setsar=1[{etiket}]")


def varsayilan_kirpma(w, h):
    # Kırpma belirtilmemişse: konuk sağ yarı, sunucu sol yarı (kareler moduyla doğrulanmalı)
    return {"ust": {"x": w // 2, "y": 0, "w": w // 2, "h": h},
            "alt": {"x": 0, "y": 0, "w": w // 2, "h": h}}


def kesit(is_, src: Path, cikti: Path):
    cikti.mkdir(parents=True, exist_ok=True)
    w, h = boyut(src)
    kirpma = is_.get("kirpma") or varsayilan_kirpma(w, h)
    ay = is_.get("altyazi", {})
    font = ay.get("font", "Inter SemiBold")
    punto = int(ay.get("punto", 52))
    fontdir = ay.get("fontdir", "")
    tmp = cikti / "_gecici"
    tmp.mkdir(exist_ok=True)

    for k in is_["kesitler"]:
        ad = f"{k['id']}_{k['ad']}"
        bas, son = float(k["bas"]), float(k["son"])
        dur = son - bas
        print(f"\n=== {ad}: {bas:.1f}–{son:.1f} ({dur:.0f} sn)", flush=True)

        # 1) yatay kesit
        yatay = cikti / f"{ad}_yatay.mp4"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", bas, "-i", src, "-t", dur,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-movflags", "+faststart", yatay])

        # 2) altyazı
        if k.get("srt") or ay.get("srt"):
            kel = srt_oku(Path(k.get("srt") or ay["srt"]), ofset=bas if ay.get("srt_mutlak", True) else 0)
            kel = [x for x in kel if 0 <= x[0] < dur]
        else:
            wav = tmp / f"{ad}.wav"
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", yatay, "-vn", "-ac", "1", "-ar", "16000", wav])
            kel = kelimeler_whisper(wav, ay.get("model", "small"), ay.get("dil", "tr"))
        bloklar = parcala(kel)
        srt_yaz(bloklar, cikti / f"{ad}.srt")
        ass = tmp / f"{ad}.ass"
        ass_yaz(bloklar, ass, font, punto)

        # 3) dikey reels: üst konuk, beyaz bant, alt sunucu
        ass_arg = f"ass='{ass}'" + (f":fontsdir='{fontdir}'" if fontdir else "")
        fc = ";".join([
            "[0:v]split=2[a][b]",
            "[a]" + kirp_filtre(kirpma["ust"], "ust", UST_H),
            "[b]" + kirp_filtre(kirpma["alt"], "alt", ALT_H),
            f"color=white:s={W}x{BANT_H}:r=30[bant]",
            "[ust][bant][alt]vstack=inputs=3:shortest=1,fps=30,format=yuv420p," + ass_arg + "[v]",
        ])
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", yatay, "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", dur,
            cikti / f"{ad}_reels.mp4"])

        # 4) önizleme karesi
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", min(5, dur / 2), "-i", cikti / f"{ad}_reels.mp4",
            "-frames:v", "1", "-vf", "scale=540:-2", cikti / f"{ad}_onizleme.jpg"])

    shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("is_dosyasi")
    ap.add_argument("--kaynak", help="Yerel kaynak video (indirmeyi atlar)")
    ap.add_argument("--cikti", default="cikti")
    a = ap.parse_args()
    is_ = json.loads(Path(a.is_dosyasi).read_text(encoding="utf-8"))
    cikti = Path(a.cikti)
    cikti.mkdir(parents=True, exist_ok=True)
    src = kaynak_al(is_, Path(os.environ.get("KAYNAK_YOLU", "kaynak.mp4")), a.kaynak)
    if is_.get("mod", "kesit") == "kareler":
        kareler(is_, src, cikti)
    else:
        kesit(is_, src, cikti)


if __name__ == "__main__":
    main()
