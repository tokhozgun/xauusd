#!/usr/bin/env python3
"""Film birleşim noktalarını kontrol eder.
Kullanım: python gecis_kontrol.py film_reels.mp4 film_gecisler.json cikti_klasoru
Her geçiş için: ses enerjisi profili (±1.2 sn), kararma süresi, birleşimden önceki/sonraki kareler (pafta)."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def ses_db(src, t0, sure, sr=16000, p=0.02):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{sure:.3f}", "-i", src,
                        "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768
    n = int(sr * p)
    k = len(x) // n
    x = x[:k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-6)


def parlaklik(src, t0, sure, fps=30):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t0):.3f}", "-t", f"{sure:.3f}", "-i", src,
                        "-vf", f"fps={fps},scale=64:36,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    k = len(r.stdout) // (64 * 36)
    return np.frombuffer(r.stdout[:k * 64 * 36], dtype=np.uint8).reshape(k, -1).mean(axis=1)


def kare(src, t, w=200):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", src, "-frames:v", "1",
                          "-vf", f"scale={w}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, check=True).stdout
    from io import BytesIO
    return Image.open(BytesIO(out)).convert("RGB")


def main():
    src, gecis_yol, cik = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    cik.mkdir(parents=True, exist_ok=True)
    gec = json.loads(Path(gecis_yol).read_text())
    rapor = []
    satirlar = []
    for g in gec[:-1]:
        t = g["film_son"]
        db = ses_db(src, t - 1.2, 2.4)
        p = 0.02
        orta = int(1.2 / p)
        once, sonra = db[:orta], db[orta:]
        # önceki parçanın sonundaki sessizlik (son kelimeden sonra ne kadar boşluk var)
        taban = float(np.percentile(db, 10))
        esik = max(-40.0, taban + 10)  # oda gürültüsünün 10 dB üstü = konuşma
        son_ses = max([i for i in range(len(once)) if once[i] > esik], default=-1)
        kuyruk = (len(once) - 1 - son_ses) * p if son_ses >= 0 else None
        ilk_ses = min([i for i in range(len(sonra)) if sonra[i] > esik], default=None)
        on_bosluk = ilk_ses * p if ilk_ses is not None else None
        br = parlaklik(src, t - 0.6, 1.2)
        kararma = float((br < br.max() * 0.35).sum()) / 30
        r = {"gecis": f"{g['parca']}→{g['parca'] + 1}", "film_sn": round(t, 2),
             "onceki_kuyruk_sn": round(kuyruk, 2) if kuyruk is not None else None,
             "sonraki_on_bosluk_sn": round(on_bosluk, 2) if on_bosluk is not None else None,
             "kararma_sn": round(kararma, 2),
             "uyari": []}
        if kuyruk is not None and kuyruk < 0.15:
            r["uyari"].append("önceki parça son heceye çok yakın kesiliyor")
        if on_bosluk is not None and on_bosluk < 0.08:
            r["uyari"].append("sonraki parçanın ilk hecesi kırpılmış olabilir")
        if kuyruk is not None and on_bosluk is not None and kuyruk + on_bosluk > 1.4:
            r["uyari"].append("geçişte uzun sessizlik")
        rapor.append(r)
        satirlar.append([kare(src, t - 0.45), kare(src, t - 0.08), kare(src, t + 0.08), kare(src, t + 0.45)])
    (cik / "gecis_raporu.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=1))
    if satirlar:
        w, h = satirlar[0][0].size
        paf = Image.new("RGB", (w * 4 + 30, (h + 20) * len(satirlar)), "white")
        d = ImageDraw.Draw(paf)
        for i, s in enumerate(satirlar):
            d.text((4, i * (h + 20) + 4), f"geçiş {rapor[i]['gecis']} @ {rapor[i]['film_sn']} sn  (-0.45 / -0.08 / +0.08 / +0.45)", fill="black")
            for j, im in enumerate(s):
                paf.paste(im, (j * (w + 10), i * (h + 20) + 20))
        paf.save(cik / "gecis_pafta.jpg", quality=85)
    print(json.dumps(rapor, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
