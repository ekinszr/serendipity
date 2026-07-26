#!/usr/bin/env python3
"""
Serendipity Digest — akademik ve dusunce-kurulusu kaynaklarindan
periyodik, kesif-odakli okuma listesi ureten arac.

Kullanim:
    python3 digest.py                  -> varsayilan 7 maddelik liste uretir
    python3 digest.py --count 10       -> 10 madde
    python3 digest.py --reset          -> "daha once gorduklerim" hafizasini sifirlar
    python3 digest.py --category "Karmasik Sistemler ve Bilim"  -> sadece o kategoriden
"""
import argparse
import hashlib
import json
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import feedparser

BASE_DIR = Path(__file__).parent
FEEDS_PATH = BASE_DIR / "feeds.json"
STATE_PATH = BASE_DIR / "state.json"
OUTPUT_DIR = BASE_DIR / "output"

USER_AGENT = "Mozilla/5.0 (compatible; SerendipityDigest/1.0; personal reading tool)"


# --------------------------------------------------------------------------
# Yardimci fonksiyonlar
# --------------------------------------------------------------------------

def strip_html(raw: str) -> str:
    """Feed ozetlerinden HTML etiketlerini temizler."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def article_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def fetch_url(url: str, max_hops: int = 5) -> bytes:
    """Feed'i ceker; 301/302/307/308 yonlendirmelerini elle takip eder
    (Python'un urllib'i bazi surumlerde 308'i izlemez)."""
    import urllib.error
    import urllib.parse
    for _ in range(max_hops):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(req, timeout=15).read()
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not loc:
                raise
            url = urllib.parse.urljoin(url, loc)
    raise RuntimeError("cok fazla yonlendirme")


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_state():
    return {
        "seen_ids": [],           # daha once digest'e girmis makale id'leri
        "category_last_used": {}, # kategori -> son kullanilma zamani (epoch)
        "history": []             # gecmis digest kayitlari (tarih + basliklar)
    }


# --------------------------------------------------------------------------
# 1. Feed'leri cek
# --------------------------------------------------------------------------

# OpenAlex: bir kurumdan (Princeton, MIT, PoliMi...) cikan EN YENI hakemli
# calismalari dogrudan ceker. "En iyi kurumlardan en yeni fikirler" hedefinin
# asil motoru budur -- RSS'in aksine kaynaga gore filtreler ve abstract verir.
OPENALEX_API = "https://api.openalex.org"
# OpenAlex "polite pool": istek basina bir iletisim adresi bekler ve karsiliginda
# daha yuksek hiz siniri verir. Adres repoda durmasin diye ortamdan okunur
# (yerelde export, Actions icinde secret). Yoksa istek yine calisir, sadece
# nazik havuzun disinda kalir.
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "").strip()


def _openalex_get(path: str) -> dict:
    import json as _json
    url = f"{OPENALEX_API}/{path}"
    if OPENALEX_MAILTO:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}mailto={OPENALEX_MAILTO}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _json.loads(urllib.request.urlopen(req, timeout=25).read())


def _decode_abstract(inv_index: dict) -> str:
    """OpenAlex abstract'i 'inverted index' olarak verir; duz metne cevirir."""
    if not inv_index:
        return ""
    positions = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def fetch_openalex_institution(name: str, inst_id: str, days: int = 21,
                               limit: int = 15) -> list:
    """Bir kurumun son `days` gununde yayimlanan hakemli makalelerini ceker."""
    from datetime import date, timedelta
    frm = (date.today() - timedelta(days=days)).isoformat()
    to = date.today().isoformat()
    filt = (f"authorships.institutions.id:{inst_id},"
            f"from_publication_date:{frm},to_publication_date:{to},"
            f"type:article,is_paratext:false,has_abstract:true")
    data = _openalex_get(f"works?filter={filt}&sort=publication_date:desc"
                         f"&per_page={limit}")
    out = []
    for it in data.get("results", []):
        title = (it.get("title") or "").strip()
        if not title or len(title) < 12:
            continue
        # kod deposu / veri seti gurultusunu ele
        low = title.lower()
        if low.startswith(("code ", "data for", "dataset", "supplementary")):
            continue
        link = it.get("doi") or it.get("id") or ""
        summary = strip_html(_decode_abstract(it.get("abstract_inverted_index")))
        out.append({
            "id": article_id(it.get("id") or link),
            "title": title,
            "link": link,
            "summary": summary[:400],
            "source": name,
            "category": None,       # cagiran doldurur
            "published": it.get("publication_date", ""),
        })
    return out


def fetch_category_articles(category: str, sources: list) -> list:
    """Bir kategorideki tum kaynaklardan makaleleri ceker (RSS veya OpenAlex)."""
    articles = []
    for src in sources:
        name = src["name"]

        # OpenAlex kurum kaynagi
        if src.get("type") == "openalex":
            try:
                items = fetch_openalex_institution(
                    name, src["institution_id"],
                    days=src.get("days", 21), limit=src.get("limit", 15))
            except Exception as e:
                print(f"  [atlandi] {name}: OpenAlex hatasi ({e})")
                continue
            for it in items:
                it["category"] = category
            articles.extend(items)
            print(f"  [ok] {name}: {len(items)} calisma bulundu (OpenAlex)")
            continue

        # RSS kaynagi
        url = src["url"]
        try:
            raw = fetch_url(url)
            parsed = feedparser.parse(raw)
        except Exception as e:
            print(f"  [atlandi] {name}: erisim hatasi ({e})")
            continue

        if not parsed.entries:
            print(f"  [bos] {name}: feed'de icerik bulunamadi")
            continue

        for entry in parsed.entries[:20]:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title:
                continue
            summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
            articles.append({
                "id": article_id(link),
                "title": title,
                "link": link,
                "summary": summary[:400],
                "source": name,
                "category": category,
                "published": entry.get("published", ""),
            })
        print(f"  [ok] {name}: {len(parsed.entries)} madde bulundu")
    return articles


def fetch_all(feeds_config: dict, only_category: str = None) -> dict:
    """Kategori -> makale listesi seklinde tum havuzu doner."""
    pool = {}
    for category, cfg in feeds_config.items():
        if category.startswith("_"):
            continue
        if only_category and category != only_category:
            continue
        print(f"\n{category} taraniyor...")
        pool[category] = fetch_category_articles(category, cfg["sources"])
    return pool


# --------------------------------------------------------------------------
# 2. Serendipity secim algoritmasi
# --------------------------------------------------------------------------

def select_serendipitous(pool: dict, feeds_config: dict, state: dict, count: int) -> list:
    """
    Kategoriler arasi cesitliligi koruyarak, uzun suredir secilmemis
    kategorilere agirlik vererek, kategori icinde TAMAMEN rastgele secim yapar.
    Boylece hem akademik/guvenilir kaynak havuzundan, hem de tahmin
    edilemez (serendipity) bir liste cikar.
    """
    seen_ids = set(state["seen_ids"])
    now = time.time()

    # Her kategori icin: gorulmemis makaleler + "ne kadar zamandir secilmedi" agirligi
    candidates = {}
    for category, articles in pool.items():
        unseen = [a for a in articles if a["id"] not in seen_ids]
        if not unseen:
            continue
        last_used = state["category_last_used"].get(category, 0)
        days_since = max((now - last_used) / 86400, 0.1)
        base_weight = feeds_config.get(category, {}).get("weight", 1.0)
        # log-benzeri buyume: uzun sure secilmeyen kategori daha agirlikli olur
        recency_boost = min(days_since, 30)
        candidates[category] = {
            "articles": unseen,
            "score": base_weight * (1 + recency_boost),
            # fise yazilacak GERCEK uzaklik sinyali: bu alan en son ne zaman geldi.
            # (v2'de embedding mesafesi gelene kadar uydurma degil, olcum kullanilir)
            "gap_days": days_since,
            "first_time": not last_used,
        }

    selected = []
    used_sources_this_run = set()
    cats = list(candidates.keys())

    while len(selected) < count and cats:
        weights = [candidates[c]["score"] for c in cats]
        chosen_cat = random.choices(cats, weights=weights, k=1)[0]

        # kategori icinde TAMAMEN rastgele sec (serendipity'nin kalbi burasi)
        pool_articles = candidates[chosen_cat]["articles"]
        # ayni kaynaktan ust uste gelmeyi hafifce azalt
        preferred = [a for a in pool_articles if a["source"] not in used_sources_this_run]
        article = random.choice(preferred if preferred else pool_articles)

        article["field_gap_days"] = candidates[chosen_cat]["gap_days"]
        article["field_first_time"] = candidates[chosen_cat]["first_time"]
        selected.append(article)
        used_sources_this_run.add(article["source"])
        candidates[chosen_cat]["articles"].remove(article)
        state["category_last_used"][chosen_cat] = now
        # bu calistirma icinde ayni kategori tekrar secilirse agirligini
        # dusur ki tek bir kategori listeye hakim olmasin (cesitlilik icin)
        candidates[chosen_cat]["score"] *= 0.35

        if not candidates[chosen_cat]["articles"]:
            cats.remove(chosen_cat)

    random.shuffle(selected)  # kategori siralamasi da ongorulemez olsun
    return selected


# --------------------------------------------------------------------------
# 3. HTML cikti
# --------------------------------------------------------------------------

TR_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
            "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def tr_date(dt) -> str:
    """Fiste tarih Turkce yazilir (locale'e guvenmeden)."""
    return f"{dt.day} {TR_AYLAR[dt.month - 1]} {dt.year}"


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Tasarim: "siyanotip arsivi"
# Prusya mavisi zemin + tebesir beyazi + siyanotip mavisi; tek sicak sinyal
# rengi (pas) yalnizca "uzak/ilk kez" isaretinde kullanilir.
# Bodoni Moda (levha basligi) + Spectral (yem metni) + IBM Plex Mono (etiket).
# --------------------------------------------------------------------------

FONTS_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600"
    "&family=Spectral:ital,wght@0,300;0,400;0,500;1,400"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap"
)

CSS = """
  /* --- jetonlar: varsayilan = siyanotip baskisi (koyu) --- */
  :root {
    --ground: #071a28;
    --plate: #0c2436;
    --chalk: #e4edf2;
    --body: #c3d4de;
    --muted: #93aab8;
    --faint: #7793a6;
    --cyan: #4e9bd1;
    --rust: #d4834f;
    --rule: rgba(138, 163, 180, 0.22);
    --hairline: rgba(138, 163, 180, 0.12);
    --glow: rgba(78, 155, 209, 0.14);
  }
  @media (prefers-color-scheme: light) {
    :root {
      --ground: #e9eff2; --plate: #f4f8fa; --chalk: #0b2233; --body: #22414f;
      --muted: #3f5c6d; --faint: #55707f; --cyan: #1f5c8c; --rust: #9c5227;
      --rule: rgba(11, 34, 51, 0.18); --hairline: rgba(11, 34, 51, 0.09);
      --glow: rgba(31, 92, 140, 0.08);
    }
  }
  :root[data-theme="light"] {
    --ground: #e9eff2; --plate: #f4f8fa; --chalk: #0b2233; --body: #22414f;
    --muted: #3f5c6d; --faint: #55707f; --cyan: #1f5c8c; --rust: #9c5227;
    --rule: rgba(11, 34, 51, 0.18); --hairline: rgba(11, 34, 51, 0.09);
    --glow: rgba(31, 92, 140, 0.08);
  }
  :root[data-theme="dark"] {
    --ground: #071a28; --plate: #0c2436; --chalk: #e4edf2; --body: #c3d4de;
    --muted: #93aab8; --faint: #7793a6; --cyan: #4e9bd1; --rust: #d4834f;
    --rule: rgba(138, 163, 180, 0.22); --hairline: rgba(138, 163, 180, 0.12);
    --glow: rgba(78, 155, 209, 0.14);
  }

  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0;
    background:
      radial-gradient(140% 90% at 82% -10%, var(--glow) 0%, transparent 60%),
      var(--ground);
    color: var(--body);
    font-family: Spectral, Georgia, "Times New Roman", serif;
    font-weight: 400;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; }
  :focus-visible { outline: 2px solid var(--cyan); outline-offset: 3px; border-radius: 2px; }

  .sheet {
    max-width: 880px;
    margin: 0 auto;
    padding: clamp(26px, 5vw, 64px) clamp(18px, 5vw, 44px) 88px;
  }

  /* --- kunye: katalog karti --- */
  .plate-head { border-bottom: 1px solid var(--rule); padding-bottom: clamp(20px, 4vw, 32px); }
  .stamp {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 11px; letter-spacing: 0.26em; text-transform: uppercase;
    color: var(--cyan);
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px;
    padding-bottom: clamp(18px, 4vw, 30px);
  }
  h1 {
    font-family: "Bodoni Moda", Didot, "Bodoni MT", Georgia, serif;
    font-optical-sizing: auto;
    font-size: clamp(44px, 10vw, 88px);
    line-height: 1.04; margin: 0; font-weight: 400;
    color: var(--chalk); letter-spacing: -0.015em;
    text-wrap: balance;
  }
  h1 .thin { display: block; font-style: italic; font-weight: 400; color: var(--cyan); }
  .standfirst {
    margin: clamp(18px, 3vw, 26px) 0 0;
    max-width: 54ch; font-size: 17px; line-height: 1.62; color: var(--muted);
  }
  .fields {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
    gap: 2px 18px; margin-top: clamp(22px, 4vw, 34px);
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
  }
  .fields dt {
    font-size: 10px; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--faint); margin-bottom: 5px;
  }
  .fields dd { margin: 0 0 6px; font-size: 15px; color: var(--chalk); font-weight: 500; }

  /* --- kayitlar --- */
  .records { display: flex; flex-direction: column; }
  .rec {
    display: grid; grid-template-columns: 92px 1fr;
    gap: clamp(14px, 3vw, 28px);
    padding: clamp(28px, 4vw, 40px) 0;
    border-bottom: 1px solid var(--hairline);
  }
  .rail {
    display: flex; flex-direction: column; gap: 10px;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    padding-top: 4px;
  }
  /* kategori ikonu: renk tek basina ayirt edici olmasin diye yaninda
     her zaman alan adi yazisi durur (marka kitabi kurali) */
  .cat-icon { width: 26px; height: 26px; color: var(--cyan); flex: none; }
  .rec.far .cat-icon { color: var(--rust); }
  .acc {
    font-size: 13px; letter-spacing: 0.08em; color: var(--chalk);
    font-variant-numeric: tabular-nums; font-weight: 500;
  }
  .acc span { color: var(--faint); }
  .meter { display: flex; gap: 3px; }
  .tick { width: 14px; height: 3px; background: var(--hairline); border-radius: 1px; }
  .tick.on { background: var(--cyan); }
  .rec.far .tick.on { background: var(--rust); }
  .dist {
    font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--faint);
  }
  .rec.far .dist { color: var(--rust); }

  .field-label {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 10.5px; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--cyan); margin-bottom: 12px;
  }
  .rec-title {
    font-family: "Bodoni Moda", Didot, "Bodoni MT", Georgia, serif;
    font-optical-sizing: auto;
    margin: 0 0 14px; font-weight: 400;
    font-size: clamp(24px, 4.2vw, 34px); line-height: 1.16;
    letter-spacing: -0.005em; color: var(--chalk); text-wrap: balance;
  }
  .rec-title a {
    text-decoration: none;
    background: linear-gradient(var(--cyan), var(--cyan)) no-repeat;
    background-size: 0% 1px; background-position: 0 92%;
    transition: background-size 0.4s cubic-bezier(0.2, 0.7, 0.2, 1), color 0.2s ease;
  }
  .rec:hover .rec-title a { background-size: 100% 1px; }
  .rec-title a:hover { color: var(--cyan); }
  .hook { margin: 0 0 20px; font-size: 17.5px; line-height: 1.68; max-width: 62ch; }

  .rec-foot {
    display: flex; justify-content: space-between; align-items: center;
    gap: 12px 20px; flex-wrap: wrap;
    font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 11.5px;
  }
  .src { letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
  .src .lure { color: var(--cyan); margin-left: 6px; }
  .acts { display: flex; align-items: center; gap: 6px; }
  .vote {
    font: inherit; font-size: 11px; letter-spacing: 0.1em;
    background: none; border: 1px solid var(--hairline); color: var(--faint);
    padding: 5px 10px; border-radius: 2px; cursor: pointer;
    transition: color 0.18s ease, border-color 0.18s ease, background 0.18s ease;
  }
  .vote:hover { color: var(--chalk); border-color: var(--rule); }
  .vote[aria-pressed="true"] { color: var(--ground); background: var(--cyan); border-color: var(--cyan); }
  .vote.no[aria-pressed="true"] { background: var(--rust); border-color: var(--rust); }
  .go {
    text-decoration: none; color: var(--cyan); font-weight: 500;
    letter-spacing: 0.08em; white-space: nowrap; margin-left: 8px;
  }
  .go:hover { text-decoration: underline; text-underline-offset: 3px; }

  /* --- alt bilgi --- */
  .colophon {
    margin-top: clamp(34px, 5vw, 52px);
    display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 11px; line-height: 1.7; color: var(--faint);
  }
  .colophon p { margin: 0; max-width: 46ch; }
  .colophon a { color: var(--cyan); }

  @media (max-width: 560px) {
    .rec { grid-template-columns: 1fr; gap: 12px; }
    .rail { flex-direction: row; align-items: center; gap: 12px; }
    .rec-foot { align-items: flex-start; flex-direction: column; }
    .go { margin-left: 0; }
  }

  /* --- hareket: yalnizca yuklenirken tek bir sirali aciliş --- */
  @media (prefers-reduced-motion: no-preference) {
    .rec { animation: rise 0.5s cubic-bezier(0.2, 0.7, 0.2, 1) both; animation-delay: calc(var(--i) * 60ms); }
    @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  }
"""

JS = """
  // Geri bildirim yerelde tutulur (Kademe 2'de sunucuya baglanacak).
  // Amaci konu ogrenmek degil, kalibrasyon: hangi mesafe iyi geldi.
  (function () {
    var KEY = "serendipity-oy";
    var store = {};
    try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }

    function paint() {
      document.querySelectorAll(".vote").forEach(function (b) {
        b.setAttribute("aria-pressed", String(store[b.dataset.acc] === b.dataset.vote));
      });
      var n = Object.keys(store).length;
      var tally = document.getElementById("tally");
      if (tally) tally.textContent = n ? n + " kayda not düşüldü" : "Henüz not düşülmedi";
    }

    document.querySelectorAll(".vote").forEach(function (b) {
      b.addEventListener("click", function () {
        var acc = b.dataset.acc;
        if (store[acc] === b.dataset.vote) { delete store[acc]; } else { store[acc] = b.dataset.vote; }
        try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {}
        paint();
      });
    });
    paint();
  })();
"""


# --------------------------------------------------------------------------
# Kategori ikonlari (kesif-fisi-brand-book.html'deki illustrasyon sistemi)
# --------------------------------------------------------------------------
# Marka kitabi kurali: kategoriyi renk TEK BASINA anlatmasin -- ikon + yazi
# birlikte tasisin. Kitaptaki 5 ikon aynen korundu; motorun geri kalan 3
# kategorisi (seckin kurumlar, genel bilim, hakemli) ayni cizgi diliyle
# (32x32, stroke-width 1.6, dolgusuz) tamamlandi.

_ICON_BODY = {
    # kurum: alinlik + sutunlar
    "kurum": '<path d="M4 12 16 5l12 7"/><path d="M8.5 13v10M16 13v10M23.5 13v10"/>'
             '<path d="M5 23h22M3.5 27h25"/>',
    # tasarim: tuy kalem (marka kitabindan)
    "kalem": '<path d="M24 6c-8 0-15.5 6-18 20 6-2 10.5-6.5 12.5-11"/><path d="M8 26 22 8"/>'
             '<path d="M12.5 21.5 16 18"/><path d="M16.5 17 19.5 14"/>',
    # karmasik sistemler: yorungeler (marka kitabindan)
    "yorunge": '<ellipse cx="16" cy="16" rx="13" ry="5.5"/>'
               '<ellipse cx="16" cy="16" rx="13" ry="5.5" transform="rotate(60 16 16)"/>'
               '<ellipse cx="16" cy="16" rx="13" ry="5.5" transform="rotate(120 16 16)"/>'
               '<circle cx="16" cy="16" r="1.8" fill="currentColor" stroke="none"/>',
    # genel bilim: acik dergi
    "dergi": '<path d="M16 9.5C13 7 9 6.5 4.5 7v17c4.5-.5 8.5 0 11.5 2.5 3-2.5 7-3 11.5-2.5V7C23 6.5 19 7 16 9.5z"/>'
             '<path d="M16 9.5v17"/>',
    # teknoloji ve toplum: dugum agi
    "ag": '<circle cx="16" cy="6.5" r="2.6"/><circle cx="6.5" cy="23" r="2.6"/>'
          '<circle cx="25.5" cy="23" r="2.6"/><path d="M14.2 8.8 8.3 20.7M17.8 8.8l5.9 11.9M9.1 23h13.8"/>',
    # politika ve ekonomi: terazi (marka kitabindan)
    "terazi": '<path d="M16 4v23M7 9h18"/><path d="M4.5 17a6 6 0 0 0 11 0L10 9z"/>'
              '<path d="M16.5 17a6 6 0 0 0 11 0L22 9z"/><path d="M11 27h10"/>',
    # hakemli: onaylanmis sayfa
    "onay": '<path d="M7 4h13l5 5v19H7z"/><path d="M20 4v5h5"/>'
            '<path d="m11.5 18.5 3 3 6.5-7"/>',
    # on-baski: deney sisesi (marka kitabindan)
    "sise": '<path d="M13 4h6M14 4v6.5L7 22a3 3 0 0 0 3 4h12a3 3 0 0 0 3-4l-7-11.5V4"/>'
            '<path d="M10.5 19h11"/>',
}

# anahtar kelime -> ikon (feeds.json adlari degisse de tutar)
_ICON_RULES = [
    ("seckin", "kurum"), ("kurum", "kurum"),
    ("tasarim", "kalem"), ("yaratici", "kalem"),
    ("karmasik", "yorunge"), ("sistem", "yorunge"),
    ("on-baski", "sise"), ("arxiv", "sise"), ("preprint", "sise"),
    ("hakemli", "onay"),
    ("genel bilim", "dergi"), ("bilim", "dergi"),
    ("teknoloji", "ag"), ("toplum", "ag"),
    ("politika", "terazi"), ("ekonomi", "terazi"),
]

_TR_SADE = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


# e-postadaki PNG ikonlarin rengi (kagit zemin uzerinde okunan siyanotip mavisi)
EMAIL_ICON_COLOR = "#1f5c8c"


def _icon_key(category: str) -> str:
    """Kategori adindan ikon anahtari. Eslesme yoksa notr bir isaret."""
    key = (category or "").translate(_TR_SADE).lower()
    for needle, icon in _ICON_RULES:
        if needle in key:
            return icon
    return "yorunge"


def _cat_icon(category: str) -> str:
    """Fis icin satir ici SVG ikon (tema rengini currentColor ile alir)."""
    body = _ICON_BODY[_icon_key(category)]
    return (f'<svg class="cat-icon" viewBox="0 0 32 32" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true" focusable="false">{body}</svg>')


def _distance(art: dict):
    """Fise yazilan uzaklik: OLCUM, tahmin degil.
    Su an olcu = bu kesif alani en son ne zaman fise girdi.
    (v2'de embedding mesafesi bunun yerini alacak.)"""
    if art.get("field_first_time"):
        return 3, "uzak", "Bu alan fişe ilk kez giriyor"
    gap = art.get("field_gap_days")
    if gap is None:
        return 0, "&mdash;", "Bu alan için geçmiş kaydı yok"
    days = int(round(gap))
    if gap < 7:
        return 1, "yakın", f"Bu alan {days} gün önce de gelmişti"
    if gap < 21:
        return 2, "orta", f"Bu alan {days} gündür gelmemişti"
    return 3, "uzak", f"Bu alan {days} gündür gelmemişti"


def render_html(selected: list, run_date: str, accession_start: int) -> str:
    fields = []
    for a in selected:
        if a.get("category") and a["category"] not in fields:
            fields.append(a["category"])

    far_count = 0
    entries = []
    for i, art in enumerate(selected):
        acc = f"{accession_start + i + 1:04d}"
        ticks, word, explain = _distance(art)
        if ticks == 3:
            far_count += 1
        meter = "".join(
            f'<span class="tick{" on" if t < ticks else ""}"></span>' for t in range(3)
        )
        lure = '<span class="lure" title="merak açıcı yem">&#10022;</span>' if art.get("enriched") else ""
        entries.append(f"""
        <article class="rec{' far' if ticks == 3 else ''}" style="--i:{i}">
          <div class="rail">
            {_cat_icon(art.get("category", ""))}
            <div class="acc"><span>No.</span> {acc}</div>
            <div class="meter" role="img" aria-label="{_esc(explain)}" title="{_esc(explain)}">{meter}</div>
            <div class="dist">{word}</div>
          </div>
          <div class="rec-body">
            <div class="field-label">{_esc(art.get("category", ""))}</div>
            <h2 class="rec-title"><a href="{art.get("link", "#")}" target="_blank" rel="noopener">{_esc(art.get("title", ""))}</a></h2>
            <p class="hook">{_esc(art.get("summary") or "Özet yok &mdash; kaynağa gidin.")}</p>
            <div class="rec-foot">
              <span class="src">{_esc(art.get("source", ""))}{lure}</span>
              <div class="acts">
                <button class="vote" type="button" data-acc="{acc}" data-vote="up" aria-pressed="false">&#10022; vay</button>
                <button class="vote no" type="button" data-acc="{acc}" data-vote="down" aria-pressed="false">&#10005; alakasız</button>
                <a class="go" href="{art.get("link", "#")}" target="_blank" rel="noopener">Kaynağa git &rarr;</a>
              </div>
            </div>
          </div>
        </article>""")

    issue = f"{(accession_start // 100) + 1:03d}"
    entries_html = "\n".join(entries)

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Keşif Fişi &middot; {run_date}</title>
<meta name="description" content="Haftalık keşif fişi: güvenilir kaynaklardan ilgili ama beklenmedik okumalar.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{FONTS_HREF}" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
  <main class="sheet">
    <header class="plate-head">
      <div class="stamp"><span>Serendipity &middot; Keşif Fişi</span><span>Levha {issue}</span></div>
      <h1>Keşif<span class="thin">Fişi</span></h1>
      <p class="standfirst">Seçkin kurumlardan, hakemli dergilerden ve fikir yazılarından &mdash;
      kontrollü rastgelelikle seçilmiş, ilgili ama beklenmedik okumalar.</p>
      <dl class="fields">
        <div><dt>Tarih</dt><dd>{run_date}</dd></div>
        <div><dt>Kayıt</dt><dd>{len(selected)}</dd></div>
        <div><dt>Alan</dt><dd>{len(fields)}</dd></div>
        <div><dt>Uzak</dt><dd>{far_count}</dd></div>
      </dl>
    </header>
    <div class="records">{entries_html}
    </div>
    <footer class="colophon">
      <p>Görülmemiş kaynaklar arasından ağırlıklı-rastgele seçildi. Soldaki çentikler
      uzaklığı gösterir: o keşif alanı ne kadar zamandır fişe girmemiş.</p>
      <p id="tally">Henüz not düşülmedi</p>
    </footer>
  </main>
<script>{JS}</script>
</body>
</html>"""


# --------------------------------------------------------------------------
# 4. E-posta tetigi (Kademe 2)
# --------------------------------------------------------------------------
# E-posta haberci, fis degil: sadece basliklar (yem YOK -- merak fise tiklatsin),
# tek buyuk buton, tablo-tabanli HTML, web font yok (Georgia). Istemcilerin
# CSS'i kirdigi varsayilir; her sey inline stil.

PAGE_URL = "https://ekinszr.github.io/serendipity/"


def send_via_resend(subject: str, html: str) -> str:
    """Fis haberini e-postayla yollar. Anahtar/alici yoksa sessizce atlanir --
    e-posta bir ek, fisin uretilmesi ona bagli degil.

    Ortam degiskenleri (Actions'ta secret):
      RESEND_API_KEY  -> resend.com hesabindan alinan anahtar
      EMAIL_TO        -> alici adres (repoda durmasin diye burada degil)
      EMAIL_FROM      -> gonderici; varsayilan Resend'in test adresi
    """
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    to = os.environ.get("EMAIL_TO", "").strip()
    if not api_key or not to:
        return "e-posta atlandi (RESEND_API_KEY veya EMAIL_TO yok)"

    sender = os.environ.get("EMAIL_FROM", "").strip() or \
        "Kesif Fisi <onboarding@resend.dev>"
    payload = json.dumps({
        "from": sender, "to": [to], "subject": subject, "html": html,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 # Cloudflare, Python-urllib'in varsayilan imzasini 403/1010
                 # ile eliyor; istegi normal bir istemci gibi tanit.
                 "User-Agent": USER_AGENT,
                 "Accept": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return f"e-posta gonderildi -> {to} (id {resp.get('id', '?')})"
    except Exception as e:
        detail = ""
        body = getattr(e, "file", None)
        if body is not None:
            try:
                detail = f" — {body.read().decode('utf-8', 'replace')[:200]}"
            except Exception:
                pass
        return f"e-posta GONDERILEMEDI: {e}{detail}"


def render_email(selected: list, run_date: str, page_url: str = PAGE_URL) -> tuple:
    """(konu, html) doner. Konu her hafta degisir ki gelen kutusunda korlesmesin."""
    fields = []
    for a in selected:
        if a.get("category") and a["category"] not in fields:
            fields.append(a["category"])
    far = [a for a in selected if _distance(a)[0] == 3]

    # konu satiri: o haftanin kendi verisinden dogar, sablon degil
    if far:
        subject = f"Keşif Fişi · {len(far)} tanesi uzaktan geldi"
    elif len(fields) >= 5:
        subject = f"Keşif Fişi · {len(fields)} ayrı alandan {len(selected)} okuma"
    else:
        subject = f"Keşif Fişi · {run_date}"

    # istah acici satir: kategori adlarini tekrarlamak yerine fisin sekli
    appetizer = f"{len(fields)} ayrı alandan {len(selected)} okuma"
    if far:
        appetizer += f", {len(far)} tanesi uzun süredir uğramadığın bir alandan"

    rows = []
    for a in selected:
        # ikon PNG olarak, mutlak adresten: istemciler satir ici SVG'yi de
        # data: URI'yi de atar. Gorseller engellenirse alan adi yazisi zaten
        # yerinde duruyor, o yuzden ikon dekoratif (alt="").
        icon_url = f"{page_url.rstrip('/')}/icons/{_icon_key(a.get('category',''))}.png"
        rows.append(f"""
              <tr><td style="padding:0 0 18px;border-bottom:1px solid #d3dee4;">
                <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.16em;text-transform:uppercase;color:#1f5c8c;padding-bottom:6px;"><img src="{icon_url}" width="18" height="18" alt="" style="vertical-align:-4px;margin-right:7px;border:0;">{_esc(a.get("category",""))}</div>
                <a href="{a.get("link","#")}" style="font:400 20px/1.3 Georgia,'Times New Roman',serif;color:#0b2233;text-decoration:none;">{_esc(a.get("title",""))}</a>
                <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.12em;text-transform:uppercase;color:#6d8798;padding-top:7px;">{_esc(a.get("source",""))}</div>
              </td></tr>
              <tr><td style="height:18px;line-height:18px;">&nbsp;</td></tr>""")

    html = f"""<!DOCTYPE html>
<html lang="tr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#e9eff2;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_esc(appetizer)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#e9eff2;">
    <tr><td align="center" style="padding:28px 16px 48px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;background:#f6f9fb;">

        <tr><td style="background:#071a28;padding:26px 28px;">
          <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.26em;text-transform:uppercase;color:#4e9bd1;">Serendipity · Keşif Fişi</div>
          <div style="font:400 34px/1.1 Georgia,'Times New Roman',serif;color:#e4edf2;padding-top:10px;">Bu haftanın fişi hazır</div>
          <div style="font:italic 15px/1.5 Georgia,serif;color:#8aa3b4;padding-top:10px;">{_esc(appetizer)}</div>
        </td></tr>

        <tr><td style="padding:28px 28px 6px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            {"".join(rows)}
          </table>
        </td></tr>

        <tr><td align="center" style="padding:10px 28px 34px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td align="center" bgcolor="#1f5c8c" style="border-radius:2px;">
              <a href="{page_url}" style="display:inline-block;padding:15px 34px;font:600 13px/1 Menlo,Consolas,monospace;letter-spacing:.18em;text-transform:uppercase;color:#f6f9fb;text-decoration:none;">Fişi aç &rarr;</a>
            </td>
          </tr></table>
          <div style="font:12px/1.6 Menlo,Consolas,monospace;color:#7d97a6;padding-top:16px;">
            Yem özetler ve uzaklık çentikleri fişte.
          </div>
        </td></tr>

        <tr><td style="padding:0 28px 26px;border-top:1px solid #d3dee4;">
          <div style="font:11px/1.7 Menlo,Consolas,monospace;color:#7d97a6;padding-top:16px;">
            {run_date} · {len(selected)} kayıt · {len(fields)} alan · görülmemiş kaynaklardan ağırlıklı-rastgele seçildi.
          </div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""
    return subject, html


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Serendipity Digest uretici")
    parser.add_argument("--count", type=int, default=7, help="Kac madde secilsin (varsayilan 7)")
    parser.add_argument("--category", type=str, default=None, help="Sadece belirli bir kategoriden tara")
    parser.add_argument("--reset", action="store_true", help="Gorulmus makale hafizasini sifirla")
    parser.add_argument("--no-llm", action="store_true", help="LLM yem katmanini atla (sadece ham feed ozeti)")
    parser.add_argument("--no-email", action="store_true", help="Haberci e-postayi gonderme")
    args = parser.parse_args()

    feeds_config = load_json(FEEDS_PATH, {})
    state = load_json(STATE_PATH, default_state())

    if args.reset:
        state = default_state()
        print("Hafiza sifirlandi: tum makaleler tekrar 'gorulmemis' sayilacak.\n")

    pool = fetch_all(feeds_config, only_category=args.category)
    total_found = sum(len(v) for v in pool.values())
    print(f"\nToplam {total_found} makale tarandi.")

    selected = select_serendipitous(pool, feeds_config, state, args.count)

    if not selected:
        print("Uyari: secilecek yeni makale bulunamadi. --reset ile hafizayi sifirlayip tekrar deneyin.")
        return

    # Secilen maddeleri merak-acici "yem"lere cevir (Claude ile; opsiyonel).
    # API anahtari/paket yoksa sessizce ham ozet kullanilir.
    if not args.no_llm:
        try:
            from enrich import enrich_hooks
            enrich_hooks(selected)
        except Exception as e:
            print(f"  [yem atlandi] {e}")

    # state guncelle
    state["seen_ids"].extend([a["id"] for a in selected])
    state["seen_ids"] = list(set(state["seen_ids"]))[-2000:]  # sinirsiz buyumesin
    run_date = tr_date(datetime.now(timezone.utc).astimezone())
    state["history"].append({
        "date": run_date,
        "titles": [a["title"] for a in selected],
    })
    state["history"] = state["history"][-50:]
    save_json(STATE_PATH, state)

    accession_start = len(state["history"]) * 100  # her calistirmada numaralar ilerlesin

    OUTPUT_DIR.mkdir(exist_ok=True)
    html = render_html(selected, run_date, accession_start)
    out_file = OUTPUT_DIR / f"digest_{datetime.now().strftime('%Y-%m-%d_%H%M')}.html"
    out_file.write_text(html, encoding="utf-8")
    # GitHub Pages'in kok adresi hep EN GUNCEL fisi gostersin diye
    # ayni cikti index.html olarak da yazilir ("Fisi Ac" butonu sabit adrese gider).
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")

    # Haberci e-posta: fis yayinlandiktan sonra "geldi" demek icin.
    # Anahtar yoksa sessizce atlanir, fis yine yerinde durur.
    subject, email_html = render_email(selected, run_date)
    (OUTPUT_DIR / "eposta-son.html").write_text(email_html, encoding="utf-8")
    if not args.no_email:
        print(f"\n{send_via_resend(subject, email_html)}")

    print(f"\n{len(selected)} madde secildi:")
    for a in selected:
        print(f"  - [{a['category']}] {a['title']}  ({a['source']})")
    print(f"\nCikti dosyasi: {out_file}")


if __name__ == "__main__":
    main()
