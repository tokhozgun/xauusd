#!/usr/bin/env python3
"""Whisper kelime listesine düzeltme sözlüğü uygular, kesit başına SRT üretir.

Kullanım: python duzelt.py kelimeler.json duzeltmeler.json kesitler.json cikti_klasoru
  duzeltmeler.json: [["yanlış kelime dizisi", "doğru yazım"], ...]  (boşluklarla ayrılmış kelime dizileri)
  kesitler.json:    [{"id": "01", "ad": "...", "bas": 29.2, "son": 78.7}, ...]
SRT zamanları mutlaktır (yayının başına göre).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hat import parcala, srt_yaz  # noqa: E402


def norm(t):
    return t.strip()


def uygula(kelimeler, duzeltmeler):
    kurallar = sorted(((k.split(), v.split()) for k, v in duzeltmeler), key=lambda x: -len(x[0]))
    out, i = [], 0
    while i < len(kelimeler):
        for yanlis, dogru in kurallar:
            n = len(yanlis)
            if [norm(w[2]) for w in kelimeler[i:i + n]] == yanlis:
                a, z = kelimeler[i][0], kelimeler[i + n - 1][1]
                adim = (z - a) / max(1, len(dogru))
                for j, t in enumerate(dogru):
                    out.append((a + j * adim, a + (j + 1) * adim, t))
                i += n
                break
        else:
            out.append(tuple(kelimeler[i]))
            i += 1
    return out


def main():
    kel = [tuple(x) for x in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))]
    duz = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    kesitler = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    cik = Path(sys.argv[4])
    cik.mkdir(parents=True, exist_ok=True)
    kel = uygula(kel, duz)
    for k in kesitler:
        parca = [w for w in kel if w[0] >= k["bas"] - 0.05 and w[1] <= k["son"] + 0.15]
        bloklar = parcala(parca)
        yol = cik / f"{k['id']}_{k['ad']}.srt"
        srt_yaz(bloklar, yol)
        print(f"--- {yol.name}")
        for a, z, satirlar, _ in bloklar:
            print(f"  {a:7.2f}  " + " / ".join(satirlar))


if __name__ == "__main__":
    main()
