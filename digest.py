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
OPENALEX_MAILTO = "you@example.com"  # nazik kullanim havuzu (polite pool)


def _openalex_get(path: str) -> dict:
    import json as _json
    url = f"{OPENALEX_API}/{path}"
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

def render_html(selected: list, run_date: str, accession_start: int) -> str:
    cards = []
    for i, art in enumerate(selected):
        acc_no = f"{accession_start + i:04d}"
        summary = art["summary"] or "Ozet mevcut degil — kaynaga gidin."
        cards.append(f"""
        <article class="card">
          <div class="card-top">
            <span class="acc-no">Kesif No. {acc_no}</span>
            <span class="category-tag">{art['category']}</span>
          </div>
          <h2 class="title"><a href="{art['link']}" target="_blank" rel="noopener">{art['title']}</a></h2>
          <p class="summary">{summary}</p>
          <div class="card-bottom">
            <span class="source">{art['source']}</span>
            <a class="read-link" href="{art['link']}" target="_blank" rel="noopener">Oku &rarr;</a>
          </div>
        </article>""")

    cards_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kesif Fisi — {run_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #23281f;
    --paper: #f1ede1;
    --paper-card: #fbf8f0;
    --brass: #9c7a3c;
    --sage: #56624a;
    --line: #cfc6ac;
    --shadow: rgba(35, 40, 31, 0.12);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--ink);
    color: var(--ink);
    font-family: 'Fraunces', serif;
  }}
  .wrap {{
    max-width: 760px;
    margin: 0 auto;
    padding: 56px 24px 80px;
  }}
  header {{
    color: var(--paper);
    margin-bottom: 40px;
  }}
  .eyebrow {{
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--brass);
    display: block;
    margin-bottom: 10px;
  }}
  h1 {{
    font-size: 40px;
    font-weight: 700;
    font-variation-settings: 'opsz' 40;
    margin: 0 0 8px;
    line-height: 1.1;
  }}
  .subtitle {{
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    color: #b9c2ad;
    margin: 0;
  }}
  .card {{
    background: var(--paper-card);
    border: 1px solid var(--line);
    border-radius: 2px;
    padding: 24px 26px;
    margin-bottom: 18px;
    box-shadow: 0 3px 0 var(--shadow);
    position: relative;
  }}
  .card::before {{
    content: "";
    position: absolute;
    left: -1px; top: 14px; bottom: 14px;
    width: 3px;
    background: var(--brass);
  }}
  .card-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    margin-bottom: 12px;
  }}
  .acc-no {{ color: var(--brass); }}
  .category-tag {{
    color: var(--sage);
    text-transform: uppercase;
    text-align: right;
  }}
  .title {{
    font-size: 22px;
    line-height: 1.3;
    margin: 0 0 10px;
    font-weight: 600;
  }}
  .title a {{
    color: var(--ink);
    text-decoration: none;
    background-image: linear-gradient(var(--brass), var(--brass));
    background-repeat: no-repeat;
    background-size: 100% 1px;
    background-position: 0 100%;
  }}
  .title a:hover {{ color: var(--brass); }}
  .summary {{
    font-size: 15px;
    line-height: 1.6;
    color: #3c4033;
    margin: 0 0 16px;
  }}
  .card-bottom {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px dashed var(--line);
    padding-top: 12px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
  }}
  .source {{ color: var(--sage); }}
  .read-link {{ color: var(--brass); text-decoration: none; font-weight: 700; }}
  .read-link:hover {{ text-decoration: underline; }}
  footer {{
    color: #8b937e;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    text-align: center;
    margin-top: 40px;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <span class="eyebrow">Serendipity Digest &middot; {len(selected)} kesif</span>
      <h1>Kesif Fisi</h1>
      <p class="subtitle">{run_date} &mdash; akademik ve dusunce-kurulusu kaynaklarindan rastgele secilen okumalar</p>
    </header>
    {cards_html}
    <footer>Bu liste, gorulmemis kaynaklar arasindan agirlikli-rastgele secim ile olusturulmustur.</footer>
  </div>
</body>
</html>"""


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Serendipity Digest uretici")
    parser.add_argument("--count", type=int, default=7, help="Kac madde secilsin (varsayilan 7)")
    parser.add_argument("--category", type=str, default=None, help="Sadece belirli bir kategoriden tara")
    parser.add_argument("--reset", action="store_true", help="Gorulmus makale hafizasini sifirla")
    parser.add_argument("--no-llm", action="store_true", help="LLM yem katmanini atla (sadece ham feed ozeti)")
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
    run_date = datetime.now(timezone.utc).astimezone().strftime("%d %B %Y")
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

    print(f"\n{len(selected)} madde secildi:")
    for a in selected:
        print(f"  - [{a['category']}] {a['title']}  ({a['source']})")
    print(f"\nCikti dosyasi: {out_file}")


if __name__ == "__main__":
    main()
