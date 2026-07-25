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

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(selected: list, run_date: str, accession_start: int) -> str:
    # ust bilgi: kac alan, seckin kurum sayisi
    fields = []
    for a in selected:
        if a.get("category") and a["category"] not in fields:
            fields.append(a["category"])
    entries = []
    for i, art in enumerate(selected):
        numeral = f"{i + 1:02d}"
        summary = _esc(art.get("summary") or "Ozet yok — kaynaga gidin.")
        title = _esc(art.get("title", ""))
        cat = _esc(art.get("category", ""))
        source = _esc(art.get("source", ""))
        link = art.get("link", "#")
        spark = '<span class="spark" title="merak-acici yem">&#10022;</span>' if art.get("enriched") else ""
        entries.append(f"""
      <article class="entry">
        <div class="num" aria-hidden="true">{numeral}</div>
        <div class="body">
          <div class="eyebrow">{cat}</div>
          <h2 class="title"><a href="{link}" target="_blank" rel="noopener">{title}</a></h2>
          <p class="hook">{summary} {spark}</p>
          <div class="meta">
            <span class="source">{source}</span>
            <a class="go" href="{link}" target="_blank" rel="noopener">Kaynaga git &rarr;</a>
          </div>
        </div>
      </article>""")

    entries_html = "\n".join(entries)
    issue = f"{accession_start // 100:03d}" if accession_start else "001"

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kesif Fisi &middot; {run_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;0,9..144,700;1,9..144,400;1,9..144,500&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #14110c;
    --bg2: #1b1710;
    --paper: #efe7d6;
    --muted: #a99e88;
    --faint: #6f6653;
    --gold: #e2a44e;
    --line: #332c20;
    --rule: #3d3527;
  }}
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0;
    background:
      radial-gradient(120% 80% at 100% 0%, #211a10 0%, rgba(33,26,16,0) 55%),
      var(--bg);
    color: var(--paper);
    font-family: 'Fraunces', Georgia, serif;
    -webkit-font-smoothing: antialiased;
  }}
  a {{ color: inherit; }}
  .wrap {{ max-width: 800px; margin: 0 auto; padding: clamp(28px, 6vw, 72px) clamp(20px, 5vw, 40px) 96px; }}

  /* masthead */
  .masthead {{ border-bottom: 2px solid var(--gold); padding-bottom: 18px; margin-bottom: 8px; }}
  .kicker {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 12px; letter-spacing: 0.32em; text-transform: uppercase;
    color: var(--gold); margin: 0 0 14px;
    display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;
  }}
  h1 {{
    font-size: clamp(46px, 12vw, 92px);
    line-height: 0.92; margin: 0; font-weight: 600;
    letter-spacing: -0.02em;
    font-variation-settings: 'opsz' 120;
  }}
  h1 em {{ font-style: italic; font-weight: 500; color: var(--gold); }}
  .standfirst {{
    font-family: 'Space Grotesk', sans-serif;
    color: var(--muted); font-size: 14px; line-height: 1.5;
    margin: 16px 0 0; max-width: 46ch;
  }}
  .runbar {{
    display: flex; gap: 22px; flex-wrap: wrap;
    font-family: 'Space Grotesk', sans-serif; font-size: 12px; letter-spacing: 0.04em;
    color: var(--faint); margin: 40px 0 8px; text-transform: uppercase;
  }}
  .runbar b {{ color: var(--paper); font-weight: 500; }}

  /* entries */
  .entry {{
    display: grid; grid-template-columns: minmax(64px, 92px) 1fr;
    gap: clamp(14px, 3vw, 30px);
    padding: 34px 0; border-top: 1px solid var(--rule);
    position: relative;
  }}
  .entry:first-of-type {{ border-top: none; }}
  .num {{
    font-size: clamp(40px, 9vw, 72px); line-height: 0.9;
    font-weight: 500; color: transparent;
    -webkit-text-stroke: 1px var(--faint);
    font-variation-settings: 'opsz' 72;
    padding-top: 4px; user-select: none;
  }}
  .eyebrow {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--gold); margin-bottom: 10px;
  }}
  .title {{ margin: 0 0 12px; font-size: clamp(23px, 4.4vw, 33px); line-height: 1.14; font-weight: 600; letter-spacing: -0.01em; }}
  .title a {{ text-decoration: none; background: linear-gradient(var(--gold), var(--gold)) no-repeat; background-size: 0% 1.5px; background-position: 0 100%; transition: background-size .35s ease, color .2s ease; }}
  .entry:hover .title a {{ background-size: 100% 1.5px; }}
  .title a:hover {{ color: var(--gold); }}
  .hook {{ font-size: 17px; line-height: 1.62; color: #d8cfbc; margin: 0 0 18px; max-width: 58ch; }}
  .spark {{ color: var(--gold); font-size: 13px; vertical-align: 2px; }}
  .meta {{
    display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap;
    font-family: 'Space Grotesk', sans-serif; font-size: 12.5px; letter-spacing: 0.02em;
  }}
  .source {{ color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; font-size: 11.5px; }}
  .go {{ color: var(--gold); text-decoration: none; font-weight: 600; white-space: nowrap; }}
  .go:hover {{ text-decoration: underline; }}

  footer {{
    margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--rule);
    font-family: 'Space Grotesk', sans-serif; font-size: 12px; line-height: 1.6;
    color: var(--faint); text-align: center;
  }}

  /* light mode */
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #f3ede0; --bg2: #fbf7ee; --paper: #211d15; --muted: #6b6350;
      --faint: #a89c82; --gold: #9a6b21; --line: #ddd3bd; --rule: #ddd2ba;
    }}
    body {{ background: radial-gradient(120% 80% at 100% 0%, #efe6d1 0%, rgba(239,230,209,0) 55%), var(--bg); }}
    .hook {{ color: #453d2c; }}
  }}
  :root[data-theme="light"] {{
    --bg: #f3ede0; --paper: #211d15; --muted: #6b6350; --faint: #a89c82;
    --gold: #9a6b21; --rule: #ddd2ba;
  }}
  :root[data-theme="light"] body {{ background: radial-gradient(120% 80% at 100% 0%, #efe6d1 0%, rgba(239,230,209,0) 55%), var(--bg); }}
  :root[data-theme="light"] .hook {{ color: #453d2c; }}
  :root[data-theme="dark"] {{
    --bg: #14110c; --paper: #efe7d6; --muted: #a99e88; --faint: #6f6653;
    --gold: #e2a44e; --rule: #3d3527;
  }}

  @media (max-width: 520px) {{
    .entry {{ grid-template-columns: 1fr; gap: 6px; }}
    .num {{ font-size: 34px; -webkit-text-stroke: 1px var(--gold); color: transparent; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="masthead">
      <div class="kicker"><span>Serendipity &middot; Kesif Fisi</span><span>No. {issue}</span></div>
      <h1>Kesif<br><em>Fisi</em></h1>
      <p class="standfirst">Seckin kurumlardan, hakemli dergilerden ve fikir yazilarindan &mdash; kontrollu rastgelelikle secilmis, ilgili ama beklenmedik okumalar.</p>
      <div class="runbar">
        <span>{run_date}</span>
        <span><b>{len(selected)}</b> kesif</span>
        <span><b>{len(fields)}</b> alan</span>
      </div>
    </header>
    {entries_html}
    <footer>Gorulmemis kaynaklar arasindan agirlikli-rastgele secildi. Serendipity: aramadan bulmak.</footer>
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
