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
        "history": [],            # gecmis digest kayitlari (tarih + basliklar)
        "feedback": {}            # makale kimligi -> oy (collect_feedback.py yazar)
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
# Tasarim: kesif-fisi-brand-book.html
# Palet, tipografi ve kart dili marka kitabindan bire bir alinmistir.
# Kitapta olmayan tek sey tema varsayilani: kitap yalnizca [data-theme] altinda
# token tanimliyor, burada :root varsayilani + prefers-color-scheme eklendi ki
# isletim sistemi tercihi de calissin.
# --------------------------------------------------------------------------

FONTS_HREF = (
    "https://fonts.googleapis.com/css2"
    "?family=Instrument+Serif:ital@0;1"
    "&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700"
    "&family=JetBrains+Mono:wght@400;500;600"
    "&display=swap"
)

CSS = """
  /* --- jetonlar: marka kitabi --- */
  :root {
    --paper: #eeefe7;
    --paper-raised: #f8f8f2;
    --ink: #1c1e16;
    --ink-soft: #4b4e3f;
    --accent: #b23a2a;
    --accent-2: #4b5e3a;
    --gold: #7a5e24;
    --line: rgba(28,30,22,0.14);
    --line-strong: rgba(28,30,22,0.28);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --paper: #171a13; --paper-raised: #1f2318; --ink: #ece9dd; --ink-soft: #b7b79e;
      --accent: #e2694f; --accent-2: #8fae72; --gold: #d2aa5c;
      --line: rgba(236,233,221,0.14); --line-strong: rgba(236,233,221,0.26);
    }
  }
  :root[data-theme="light"] {
    --paper: #eeefe7; --paper-raised: #f8f8f2; --ink: #1c1e16; --ink-soft: #4b4e3f;
    --accent: #b23a2a; --accent-2: #4b5e3a; --gold: #7a5e24;
    --line: rgba(28,30,22,0.14); --line-strong: rgba(28,30,22,0.28);
  }
  :root[data-theme="dark"] {
    --paper: #171a13; --paper-raised: #1f2318; --ink: #ece9dd; --ink-soft: #b7b79e;
    --accent: #e2694f; --accent-2: #8fae72; --gold: #d2aa5c;
    --line: rgba(236,233,221,0.14); --line-strong: rgba(236,233,221,0.26);
  }

  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: "Source Serif 4", Georgia, serif;
    font-size: 16px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent); }
  ::selection { background: var(--accent); color: var(--paper); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }

  .wrap { max-width: 920px; margin: 0 auto; padding: 48px 24px 100px; }

  /* --- kunye --- */
  header.masthead { border-bottom: 3px solid var(--ink); padding-bottom: 20px; margin-bottom: 40px; }
  .kicker {
    font-family: "JetBrains Mono", monospace; font-size: 11px; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--accent); margin: 0 0 10px;
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px;
  }
  h1.wordmark {
    font-family: "Instrument Serif", Georgia, serif; font-style: italic;
    font-size: clamp(48px, 9vw, 84px); line-height: 0.95; margin: 0;
    letter-spacing: -0.01em; text-wrap: balance;
  }
  .dek { max-width: 56ch; color: var(--ink-soft); font-size: 17px; margin: 16px 0 0; }
  .runbar {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 2px 18px; margin-top: 26px;
    font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums;
  }
  .runbar dt {
    font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ink-soft); margin-bottom: 4px;
  }
  .runbar dd { margin: 0; font-size: 15px; font-weight: 600; }

  /* --- kayitlar: marka kitabindaki "live-card" --- */
  .records { display: flex; flex-direction: column; gap: 20px; }
  .rec {
    background: var(--paper-raised); border: 1px solid var(--line);
    border-radius: 4px; padding: 26px 28px; position: relative;
  }
  .rec::before {
    content: ""; position: absolute; left: 0; top: 16px; bottom: 16px;
    width: 3px; background: var(--accent);
  }
  .rec-top {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    font-family: "JetBrains Mono", monospace; font-size: 11.5px;
    color: var(--ink-soft); margin-bottom: 14px;
  }
  .cat-icon { width: 20px; height: 20px; color: var(--accent); flex: none; }
  .rec-cat { text-transform: uppercase; letter-spacing: 0.08em; }
  .rec-title {
    font-family: "Source Serif 4", Georgia, serif; font-weight: 600;
    font-size: 25px; line-height: 1.28; margin: 0 0 12px; text-wrap: balance;
  }
  .rec-title a { color: var(--ink); text-decoration: none; border-bottom: 1.5px solid var(--accent); }
  .rec-title a:hover { color: var(--accent); }
  .rec-summary { color: var(--ink-soft); font-size: 15.5px; line-height: 1.65; margin: 0 0 18px; max-width: 62ch; }

  .rec-foot {
    display: flex; justify-content: space-between; align-items: center;
    gap: 12px 18px; flex-wrap: wrap;
    border-top: 1px dashed var(--line-strong); padding-top: 12px;
    font-family: "JetBrains Mono", monospace; font-size: 12px;
  }
  .rec-source { color: var(--gold); }
  .rec-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  /* uzaklik: vurgu rengi baglanti/ikona ayrildigi icin ikincil vurgu kullanir */
  .dist { display: flex; align-items: center; gap: 7px; color: var(--ink-soft); }
  .meter { display: flex; gap: 3px; }
  .tick { width: 12px; height: 3px; background: var(--line-strong); border-radius: 1px; }
  .tick.on { background: var(--accent-2); }
  .rec.far .tick.on { background: var(--gold); }
  .dist-word { letter-spacing: 0.1em; text-transform: uppercase; font-size: 10.5px; }
  .rec.far .dist-word { color: var(--gold); }

  .vote {
    font: inherit; font-size: 11px; letter-spacing: 0.06em;
    background: none; border: 1px solid var(--line-strong); color: var(--ink-soft);
    padding: 5px 10px; border-radius: 3px; cursor: pointer;
    transition: color 0.18s ease, border-color 0.18s ease, background 0.18s ease;
  }
  .vote:hover { color: var(--ink); border-color: var(--ink-soft); }
  .vote[aria-pressed="true"] { background: var(--accent-2); border-color: var(--accent-2); color: var(--paper-raised); }
  .vote.no[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--paper-raised); }
  .go { color: var(--accent); font-weight: 600; text-decoration: none; white-space: nowrap; }
  .go:hover { text-decoration: underline; text-underline-offset: 3px; }

  footer.colophon {
    margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--line);
    display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap;
    font-family: "JetBrains Mono", monospace; font-size: 11px; line-height: 1.7;
    color: var(--ink-soft);
  }
  footer.colophon p { margin: 0; max-width: 46ch; }

  @media (max-width: 620px) {
    .wrap { padding: 32px 18px 72px; }
    .rec { padding: 22px 20px; }
    .rec-foot { flex-direction: column; align-items: flex-start; }
  }

  @media (prefers-reduced-motion: no-preference) {
    .rec { animation: rise 0.45s cubic-bezier(0.2, 0.7, 0.2, 1) both; animation-delay: calc(var(--i) * 55ms); }
    @keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  }
"""

JS = """
  // Geri bildirim once tarayicida tutulur, sonra TEK TIKLA GitHub issue'suna
  // gonderilir; haftalik kosu issue'yu okuyup state.json'a isler ve kapatir.
  // Oylar kayit numarasina degil MAKALE KIMLIGINE (aid) baglanir -- numara her
  // kosuda degisir, kimlik degismez.
  (function () {
    var KEY = "serendipity-oy";
    var store = {};
    try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }

    function govde() {
      var kayitlar = Object.keys(store).map(function (aid) {
        var o = store[aid];
        return { aid: aid, oy: o.v, baslik: o.t, alan: o.c, uzaklik: o.d };
      });
      return "Bu fisin oylari. Asagidaki blok otomatik okunur, elle duzenleme.\\n\\n"
        + "```json\\n" + JSON.stringify({ oylar: kayitlar }, null, 2) + "\\n```\\n";
    }

    function paint() {
      document.querySelectorAll(".vote").forEach(function (b) {
        b.setAttribute("aria-pressed", String(
          store[b.dataset.aid] && store[b.dataset.aid].v === b.dataset.vote));
      });
      var n = Object.keys(store).length;
      var tally = document.getElementById("tally");
      var gonder = document.getElementById("gonder");
      if (tally) tally.textContent = n
        ? n + " kayda not düşüldü"
        : "Henüz not düşülmedi";
      if (gonder) {
        gonder.hidden = !n;
        gonder.href = REPO_ISSUE_URL
          + "?labels=oy&title=" + encodeURIComponent("Keşif Fişi oyları")
          + "&body=" + encodeURIComponent(govde());
      }
    }

    document.querySelectorAll(".vote").forEach(function (b) {
      b.addEventListener("click", function () {
        var aid = b.dataset.aid;
        if (store[aid] && store[aid].v === b.dataset.vote) {
          delete store[aid];
        } else {
          store[aid] = { v: b.dataset.vote, t: b.dataset.baslik,
                         c: b.dataset.alan, d: b.dataset.uzaklik };
        }
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


# e-postadaki PNG ikonlarin rengi (marka kitabinin vurgu rengi)
EMAIL_ICON_COLOR = "#b23a2a"


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
        lure = ' <span title="merak açıcı yem">&#10022;</span>' if art.get("enriched") else ""
        entries.append(f"""
        <article class="rec{' far' if ticks == 3 else ''}" style="--i:{i}">
          <div class="rec-top">
            {_cat_icon(art.get("category", ""))}
            <span>Keşif No. {acc}</span><span>&middot;</span>
            <span class="rec-cat">{_esc(art.get("category", ""))}</span>
          </div>
          <h2 class="rec-title"><a href="{art.get("link", "#")}" target="_blank" rel="noopener">{_esc(art.get("title", ""))}</a></h2>
          <p class="rec-summary">{_esc(art.get("summary") or "Özet yok &mdash; kaynağa gidin.")}</p>
          <div class="rec-foot">
            <span class="rec-source">{_esc(art.get("source", ""))}{lure}</span>
            <div class="rec-tools">
              <span class="dist" title="{_esc(explain)}">
                <span class="meter" role="img" aria-label="{_esc(explain)}">{meter}</span>
                <span class="dist-word">{word}</span>
              </span>
              <button class="vote" type="button" data-aid="{art.get('id', acc)}" data-vote="up"
                data-baslik="{_esc(art.get('title', ''))}" data-alan="{_esc(art.get('category', ''))}"
                data-uzaklik="{word}" aria-pressed="false">&#10022; vay</button>
              <button class="vote no" type="button" data-aid="{art.get('id', acc)}" data-vote="down"
                data-baslik="{_esc(art.get('title', ''))}" data-alan="{_esc(art.get('category', ''))}"
                data-uzaklik="{word}" aria-pressed="false">&#10005; alakasız</button>
              <a class="go" href="{art.get("link", "#")}" target="_blank" rel="noopener">Oku &rarr;</a>
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
<style>{CSS}</style>\n<script>var REPO_ISSUE_URL = "{ISSUE_URL}";</script>
</head>
<body>
  <div class="wrap">
    <header class="masthead">
      <p class="kicker"><span>Serendipity &middot; Haftalık Keşif</span><span>Sayı {issue}</span></p>
      <h1 class="wordmark">Keşif Fişi</h1>
      <p class="dek">Seçkin kurumlardan, hakemli dergilerden ve fikir yazılarından &mdash;
      kontrollü rastgelelikle seçilmiş, ilgili ama beklenmedik okumalar.</p>
      <dl class="runbar">
        <div><dt>Tarih</dt><dd>{run_date}</dd></div>
        <div><dt>Kayıt</dt><dd>{len(selected)}</dd></div>
        <div><dt>Alan</dt><dd>{len(fields)}</dd></div>
        <div><dt>Uzak</dt><dd>{far_count}</dd></div>
      </dl>
    </header>
    <div class="records">{entries_html}
    </div>
    <footer class="colophon">
      <p>Görülmemiş kaynaklar arasından ağırlıklı-rastgele seçildi. Çentikler uzaklığı
      gösterir: o keşif alanı ne kadar zamandır fişe girmemiş.</p>
      <p><span id="tally">Henüz not düşülmedi</span>
      <a id="gonder" hidden href="#" rel="noopener"
         title="Oyların önceden doldurulmuş bir GitHub issue'suna gider; motor onu okur">
         &nbsp;·&nbsp;Oyları gönder &rarr;</a></p>
    </footer>
  </div>
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

# Oylarin dustugu kutu: repo issue'lari. Ayri sunucu/servis gerekmesin diye
# fisin "Oylari gonder" baglantisi onceden doldurulmus bir issue acar; haftalik
# kosu (collect_feedback.py) onu okuyup state.json'a isler ve kapatir.
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "ekinszr/serendipity")
ISSUE_URL = f"https://github.com/{REPO_SLUG}/issues/new"


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
              <tr><td style="padding:0 0 18px;border-bottom:1px solid #dcdcd2;">
                <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.14em;text-transform:uppercase;color:#b23a2a;padding-bottom:6px;"><img src="{icon_url}" width="18" height="18" alt="" style="vertical-align:-4px;margin-right:7px;border:0;">{_esc(a.get("category",""))}</div>
                <a href="{a.get("link","#")}" style="font:400 20px/1.3 Georgia,'Times New Roman',serif;color:#1c1e16;text-decoration:none;">{_esc(a.get("title",""))}</a>
                <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.12em;text-transform:uppercase;color:#7a5e24;padding-top:7px;">{_esc(a.get("source",""))}</div>
              </td></tr>
              <tr><td style="height:18px;line-height:18px;">&nbsp;</td></tr>""")

    html = f"""<!DOCTYPE html>
<html lang="tr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#eeefe7;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_esc(appetizer)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eeefe7;">
    <tr><td align="center" style="padding:30px 16px 52px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:540px;background:#f8f8f2;border:1px solid #dcdcd2;">

        <tr><td style="padding:30px 30px 20px;border-bottom:3px solid #1c1e16;">
          <div style="font:11px/1.4 Menlo,Consolas,monospace;letter-spacing:.16em;text-transform:uppercase;color:#b23a2a;padding-bottom:10px;">Serendipity &middot; Haftalık Keşif</div>
          <div style="font:italic 44px/0.95 Georgia,'Times New Roman',serif;color:#1c1e16;">Keşif Fişi</div>
          <div style="font:15px/1.5 Georgia,serif;color:#4b4e3f;padding-top:14px;">{_esc(appetizer)}</div>
        </td></tr>

        <tr><td style="padding:26px 30px 6px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            {"".join(rows)}
          </table>
        </td></tr>

        <tr><td align="center" style="padding:8px 30px 32px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td align="center" bgcolor="#b23a2a" style="border-radius:3px;">
              <a href="{page_url}" style="display:inline-block;padding:15px 34px;font:600 13px/1 Menlo,Consolas,monospace;letter-spacing:.16em;text-transform:uppercase;color:#f8f8f2;text-decoration:none;">Fişi aç &rarr;</a>
            </td>
          </tr></table>
          <div style="font:12px/1.6 Menlo,Consolas,monospace;color:#4b4e3f;padding-top:16px;">
            Yem özetler ve uzaklık çentikleri fişte.
          </div>
        </td></tr>

        <tr><td style="padding:0 30px 26px;border-top:1px solid #dcdcd2;">
          <div style="font:11px/1.7 Menlo,Consolas,monospace;color:#4b4e3f;padding-top:16px;">
            {run_date} &middot; {len(selected)} kayıt &middot; {len(fields)} alan &middot; görülmemiş kaynaklardan ağırlıklı-rastgele seçildi.
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
