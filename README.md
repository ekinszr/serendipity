# Serendipity Digest

Akademik kurumlar ve düşünce kuruluşlarından beslenen, **rastgele ama dengeli**
bir okuma listesi üreten küçük bir araç. Amaç: her çalıştırdığınızda daha önce
görmediğiniz, farklı disiplinlerden 5-10 makale/makale-özeti sunmak.

## Nasıl çalışır

1. `feeds.json` içindeki kategorilere ve kaynaklara bakar (her biri gerçek bir RSS feed'i)
2. Her kaynaktan güncel makaleleri çeker
3. Daha önce size gösterilmemiş makaleler arasından, **kategoriler arası çeşitliliği
   koruyacak ve uzun süredir seçilmemiş kategorilere öncelik verecek şekilde**
   ağırlıklı-rastgele seçim yapar
4. Seçilenleri "kaç görüldü" listesine ekler (bir daha aynı makale çıkmaz)
5. Sonucu `output/` klasörüne şık bir HTML sayfası olarak yazar

Rastgelelik kasıtlı: sistem "en popüler" veya "en yeni" makaleyi değil,
kategori içinde **gerçekten rastgele** bir makale seçiyor. Serendipity'nin
kalbi burası — algoritmik bir öneri motoru değil, sınırlı ama güvenilir bir
kaynak havuzunda şans faktörünü işe koşan bir sistem.

## Kurulum

```bash
pip install feedparser
```

Python 3.8+ yeterli, başka bağımlılık yok.

## Kullanım

```bash
# Varsayılan: 7 madde
python3 digest.py

# 10 madde iste
python3 digest.py --count 10

# Sadece belirli bir kategoriden
python3 digest.py --category "Karmasik Sistemler ve Bilim"

# "Daha önce gördüklerim" hafızasını sıfırla (baştan başlamak isterseniz)
python3 digest.py --reset
```

Her çalıştırmada `output/digest_TARIH_SAAT.html` dosyası oluşur — tarayıcıda açın.

## Kaynakları özelleştirme (`feeds.json`)

Dosya kategori → kaynak listesi şeklinde. Yeni bir akademik kaynak eklemek için
o kategorinin `sources` listesine `{"name": "...", "url": "RSS-linki"}` eklemeniz
yeterli. Bozuk/erişilemeyen bir feed script'i durdurmaz, sadece atlanır ve
terminale yazılır — bu yüzden cesurca kaynak ekleyip deneyebilirsiniz.

Yeni kategori eklemek isterseniz aynı yapıda bir blok daha ekleyin:

```json
"Yeni Kategori Adi": {
  "weight": 1.0,
  "sources": [
    {"name": "Kaynak Adi", "url": "https://ornek.edu/feed"}
  ]
}
```

`weight` değeri o kategorinin ne sıklıkla seçileceğini etkiler (1.0 varsayılan;
0.5 daha seyrek, 1.5 daha sık demektir).

### RSS linki bulma ipucu
Bir sitenin RSS feed'i genelde `site.com/feed`, `site.com/rss`, ya da
`site.com/feed.rss` gibi adreslerde olur. Bulamazsanız Google'da
`site:ornek.edu rss` aratabilir, ya da sitenin altbilgisinde/"Subscribe"
bölümünde arayabilirsiniz.

## Otomatik çalıştırma (asıl "otomatik sistem" kısmı)

Script kendi başına periyodik çalışmaz — bir zamanlayıcıya ihtiyacı var.
İki seçenek:

### Seçenek A — Kendi bilgisayarınızda (basit, ama bilgisayar açık olmalı)

**Mac/Linux (cron):**
```bash
crontab -e
# Her Pazartesi sabah 08:00'de çalıştır:
0 8 * * MON cd /tam/yol/serendipity_digest && /usr/bin/python3 digest.py
```

**Windows (Task Scheduler):**
Task Scheduler'da yeni görev oluşturun, tetikleyici olarak "Weekly / Monday
08:00", eylem olarak `python.exe` programını `digest.py` argümanıyla
çalıştıracak şekilde ayarlayın.

### Seçenek B — Bulutta, bilgisayarınız kapalıyken bile (GitHub Actions)

Bu depoyu (bu klasörü) bir GitHub reposuna koyup `.github/workflows/digest.yml`
adında şu dosyayı eklerseniz, GitHub'ın sunucuları haftalık olarak sizin için
çalıştırır ve sonucu depoya commit'ler — siz sadece GitHub'daki dosyayı
açarsınız:

```yaml
name: Serendipity Digest
on:
  schedule:
    - cron: '0 6 * * MON'   # her Pazartesi 06:00 UTC
  workflow_dispatch: {}       # manuel de tetikleyebilirsiniz
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install feedparser
      - run: python digest.py --count 7
      - run: |
          git config user.name "digest-bot"
          git config user.email "bot@example.com"
          git add state.json output/
          git commit -m "Yeni kesif fisi" || echo "degisiklik yok"
          git push
```

Bu sayede sistem gerçekten "otomatik" olur: siz hiçbir şey yapmadan her
hafta yeni bir `output/digest_*.html` dosyası deponuza düşer.

## Sınırlamalar / dürüst notlar

- Bazı kurumların (ör. Stanford d.school) düzenli RSS feed'i yok; onlar için
  sayfayı elle ziyaret etmeniz gerekir. `feeds.json` içinde bu tür girişleri
  görürseniz bilerek bıraktım, çünkü kurum güvenilir ama feed'i yok.
- RSS'ler zaman zaman format değiştirir; bir kaynak sürekli "[atlandi]"
  yazıyorsa URL'sinin güncelliğini kontrol edin.
- Bu sistem sizi belirli bir "algoritma balonuna" hapsetmemek için tasarlandı;
  ama nihayetinde `feeds.json`'a hangi kaynakları koyduğunuz sizin seçiminiz —
  gerçek çeşitlilik için zaman zaman yeni, alışılmadık kaynaklar eklemeyi
  düşünün.
