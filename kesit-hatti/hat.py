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


def ses_enerjisi(src, t0, sure, sr=16000, pencere=0.02):
    """[t0, t0+sure] aralığının 20 ms'lik pencerelerde dB enerjisi."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{sure:.3f}", "-i", str(src),
                        "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * pencere)
    k = len(x) // n
    if k == 0:
        return np.array([]), pencere
    x = x[:k * n].reshape(k, n)
    db = 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-6)
    return db, pencere


def sessizlige_hizala(src, bas, son):
    """Kesim noktalarını kelime sınırından en yakın nefes/sessizlik anına kaydırır.
    Başlangıç: ilk kelimeden önceki en fazla 0.6 sn içinde, sessiz bölgenin sonuna yakın (kelimeden ~0.1 sn önce).
    Bitiş: son kelimeden sonraki en fazla 0.7 sn içinde, sessizliğin ortası; böylece son hece kırpılmaz."""
    import numpy as np
    # --- başlangıç
    ara0 = 0.6
    db, p = ses_enerjisi(src, bas - ara0, ara0 + 0.05)
    yeni_bas = bas - 0.08
    if len(db):
        esik = min(db.min() + 10, -38)
        sessiz = db < esik
        # kelimeye en yakın sessiz pencereyi bul (sondan geriye)
        idx = [i for i in range(len(db) - 1, -1, -1) if sessiz[i]]
        if idx:
            yeni_bas = (bas - ara0) + idx[0] * p - 0.02
            yeni_bas = min(yeni_bas, bas - 0.10)
    # --- bitiş
    ara1 = 0.7
    db, p = ses_enerjisi(src, son, ara1)
    yeni_son = son + 0.25
    if len(db):
        esik = min(db.min() + 10, -38)
        sessiz = db < esik
        # sondan sonraki ilk 'en az 120 ms' süren sessiz bölge
        uz = max(1, int(0.12 / p))
        for i in range(len(db) - uz):
            if sessiz[i:i + uz].all():
                bolge_son = i
                while bolge_son < len(db) and sessiz[bolge_son]:
                    bolge_son += 1
                orta = (i + min(bolge_son, i + int(0.4 / p))) / 2
                yeni_son = son + orta * p
                break
    return max(0.0, round(yeni_bas, 3)), round(yeni_son, 3)


def sahne_parcalari(sahneler, bas, son, varsayilan="bolunmus"):
    """Mutlak zamanlı sahne listesini [bas, son] aralığına keser; boşlukları varsayılan düzenle doldurur."""
    out, t = [], bas
    for s in sorted(sahneler, key=lambda x: x["bas"]):
        a, z = max(s["bas"], bas), min(s["son"], son)
        if z <= a:
            continue
        if a > t + 0.01:
            out.append((t, a, varsayilan))
        out.append((a, z, s["duzen"]))
        t = z
    if son > t + 0.01:
        out.append((t, son, varsayilan))
    return out


def dikey_parca(src, sb, sd, duzen, hedef, tmp):
    """Bir sahne için 1080x1920 sessiz video parçası. Panel kırpması canlı ya da 'kare' (sabit kare) olabilir."""
    girdiler = ["-ss", f"{sb:.3f}", "-t", f"{sd:.3f}", "-i", str(src)]
    filtreler, n = [], 1
    canli = [p for p in ("ust", "alt") if "kare" not in duzen[p]]
    if len(canli) == 2:
        filtreler.append("[0:v]split=2[c_ust][c_alt]")
    elif len(canli) == 1:
        filtreler.append(f"[0:v]null[c_{canli[0]}]")
    for p, yuk in (("ust", UST_H), ("alt", ALT_H)):
        k = duzen[p]
        if "kare" in k:
            png = tmp / f"kare_{float(k['kare']):.2f}.png"
            if not png.exists():
                sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", k["kare"], "-i", src, "-frames:v", "1", png])
            girdiler += ["-loop", "1", "-framerate", "30", "-t", f"{sd:.3f}", "-i", str(png)]
            filtreler.append(f"[{n}:v]" + kirp_filtre(k, p, yuk))
            n += 1
        else:
            filtreler.append(f"[c_{p}]" + kirp_filtre(k, p, yuk))
    filtreler.append(f"color=white:s={W}x{BANT_H}:r=30:d={sd:.3f}[bant]")
    filtreler.append("[ust][bant][alt]vstack=inputs=3:shortest=1,fps=30,format=yuv420p[v]")
    sh(["ffmpeg", "-y", "-loglevel", "error", *girdiler, "-filter_complex", ";".join(filtreler),
        "-map", "[v]", "-an", "-t", f"{sd:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-r", "30", hedef])


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
        if is_.get("sessizlige_hizala", True):
            kb, ks = bas, son
            bas, son = sessizlige_hizala(src, bas, son)
            print(f"  hizalama: {kb:.2f}->{bas:.2f}  {ks:.2f}->{son:.2f}", flush=True)
        dur = son - bas
        print(f"\n=== {ad}: {bas:.1f}–{son:.1f} ({dur:.0f} sn)", flush=True)

        # 1) yatay kesit (yumuşak ses giriş/çıkışı)
        yatay = cikti / f"{ad}_yatay.mp4"
        af = ("loudnorm=I=-14:TP=-1.5:LRA=11,"
              f"afade=t=in:st=0:d=0.06,afade=t=out:st={max(0, dur - 0.18):.3f}:d=0.18")
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{bas:.3f}", "-i", src, "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-af", af,
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

        # 3) dikey reels: sahne sahne üst konuk + beyaz bant + alt sunucu, sonra altyazı
        ass_arg = f"ass='{ass}'" + (f":fontsdir='{fontdir}'" if fontdir else "")
        duzenler = is_.get("duzenler") or {"bolunmus": kirpma}
        sahneler = sahne_parcalari(is_.get("sahneler", []), bas, son)
        parcalar = []
        for i, (sb, sz, dz) in enumerate(sahneler):
            parca = tmp / f"{ad}_p{i:02d}.mp4"
            dikey_parca(src, sb, sz - sb, duzenler[dz], parca, tmp)
            parcalar.append(parca)
        liste = tmp / f"{ad}_liste.txt"
        liste.write_text("".join(f"file '{p.resolve()}'\n" for p in parcalar))
        birlesik = tmp / f"{ad}_dikey.mp4"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", liste, "-c", "copy", birlesik])
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", birlesik, "-i", yatay,
            "-filter_complex", f"[0:v]{ass_arg}[v]", "-map", "[v]", "-map", "1:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy",
            "-movflags", "+faststart", "-t", dur, cikti / f"{ad}_reels.mp4"])

        # 4) önizleme karesi
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", min(5, dur / 2), "-i", cikti / f"{ad}_reels.mp4",
            "-frames:v", "1", "-vf", "scale=540:-2", cikti / f"{ad}_onizleme.jpg"])

    shutil.rmtree(tmp, ignore_errors=True)


def sahne(is_, src: Path, cikti: Path):
    """Sahne geçişlerini bulur; her geçişten 0.5 sn sonrası için bir kare çıkarır."""
    cikti.mkdir(parents=True, exist_ok=True)
    esik = float(is_.get("sahne_esik", 0.3))
    a0, a1 = is_.get("sahne_aralik", [0, None])
    aralik = ["-ss", str(a0)] + (["-t", str(a1 - a0)] if a1 else [])
    r = subprocess.run(["ffmpeg", "-hide_banner", *aralik, "-i", str(src), "-vf",
                        f"scale=320:-2,select='gt(scene,{esik})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    zamanlar = [float(m) + a0 for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    (cikti / "sahneler.txt").write_text("\n".join(f"{t:.2f}" for t in zamanlar) + "\n")
    d = cikti / "sahne_kareleri"
    d.mkdir(exist_ok=True)
    for t in [0.0] + zamanlar[:80]:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", t + 0.5, "-i", src, "-frames:v", "1",
            "-vf", "scale=960:-2", "-q:v", "4", d / f"s_{t:07.2f}.jpg"])


def transkript(is_, src: Path, cikti: Path):
    """Tüm yayının kelime zamanlı transkripti (mutlak zaman) + düzen taraması için kareler."""
    cikti.mkdir(parents=True, exist_ok=True)
    ay = is_.get("altyazi", {})
    wav = cikti / "_tam.wav"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav])
    kel = kelimeler_whisper(wav, ay.get("model", "large-v3-turbo"), ay.get("dil", "tr"))
    wav.unlink()
    (cikti / "kelimeler.json").write_text(
        json.dumps([[round(a, 2), round(z, 2), t] for a, z, t in kel], ensure_ascii=False), encoding="utf-8")
    srt_yaz(parcala(kel), cikti / "transkript.srt")
    # düzen taraması: her N saniyede küçük kare, tek bir pafta
    adim = int(is_.get("tarama_adim", 10))
    d = cikti / "_tarama"
    d.mkdir(exist_ok=True)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", f"fps=1/{adim},scale=240:-2", d / "t_%04d.jpg"])
    from PIL import Image, ImageDraw
    fs = sorted(d.glob("t_*.jpg"))
    kol = 8
    w, h = Image.open(fs[0]).size
    for p in range(0, len(fs), kol * 8):
        grup = fs[p:p + kol * 8]
        satir = (len(grup) + kol - 1) // kol
        paf = Image.new("RGB", (kol * w, satir * (h + 18)), "white")
        dr = ImageDraw.Draw(paf)
        for i, f in enumerate(grup):
            x, y = (i % kol) * w, (i // kol) * (h + 18)
            paf.paste(Image.open(f), (x, y + 18))
            t = (p + i) * adim
            dr.text((x + 4, y + 3), f"{t // 60}:{t % 60:02d}", fill="black")
        paf.save(cikti / f"pafta_{p // (kol * 8) + 1}.jpg", quality=80)
    shutil.rmtree(d, ignore_errors=True)


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
    adimlar = is_.pop("adimlar", None)
    if adimlar:
        for adim in adimlar:
            alt = {**is_, **adim}
            calistir(alt, src, cikti.parent / alt["ad"])
    else:
        calistir(is_, src, cikti)


def calistir(is_, src, cikti):
    cikti.mkdir(parents=True, exist_ok=True)
    mod = is_.get("mod", "kesit")
    if mod == "kareler":
        kareler(is_, src, cikti)
    elif mod == "transkript":
        transkript(is_, src, cikti)
    elif mod == "sahne":
        sahne(is_, src, cikti)
    else:
        kesit(is_, src, cikti)


if __name__ == "__main__":
    main()
