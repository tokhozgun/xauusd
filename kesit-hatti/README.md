# Kesit hattı

Açık Açık yayınlarından iki çıktı üretir:

- `*_reels.mp4`: 1080x1920. Üstte konuk, ortada beyaz bant üzerinde siyah altyazı, altta sunucu, eş zamanlı.
- `*_yatay.mp4`: orijinal yatay kesit.

## Nasıl çalışır
1. `kesit-hatti/isler/is.json` düzenlenip `kesit-hatti` dalına push edilir.
2. GitHub Actions kaynağı indirir (YouTube, olmazsa vidIQ kopyası), Whisper ile altyazı çıkarır, kesitleri üretir.
3. Çıktılar `kesit-hatti/cikti/<iş adı>/` altına aynı dala yazılır.

`mod: "kareler"` önce kaynaktan kareler çıkarır; kırpma kutuları (`kirpma.ust` = konuk, `kirpma.alt` = sunucu, kaynak piksel cinsinden) bu karelere bakılarak belirlenir.
