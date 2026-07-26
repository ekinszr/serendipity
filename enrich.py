#!/usr/bin/env python3
"""
enrich.py — Secilen makalelerin abstract/ozetini, merak uyandiran kisa bir
Turkce "yem"e cevirir. Yalnizca SECILEN ~7 makaleye tek bir toplu cagri yapar;
ucuz ve hizli. Saglayici yoksa ya da cagri basarisiz olursa sessizce mevcut
ozete geri duser -- fis asla kirilmaz.

Kullanim (digest.py icinden):
    from enrich import enrich_hooks
    enrich_hooks(selected)   # listedeki 'summary' alanlarini yerinde gunceller

Saglayicilar (sirayla denenir):
    1. Anthropic  — ANTHROPIC_API_KEY varsa. Ucretli, en iyi sonuc.
    2. GitHub Models — GITHUB_TOKEN varsa. UCRETSIZ; Actions icinde ayri
       anahtar gerekmez, workflow'a "models: read" izni yeterli. Yerelde
       denemek icin: gh auth refresh -s models  &&  export GITHUB_TOKEN=$(gh auth token)
"""
import json
import os
import urllib.error
import urllib.request

ANTHROPIC_MODEL = "claude-opus-4-8"

# GitHub Models: OpenAI uyumlu uc nokta. Model adi degistirilebilir olsun diye
# ortamdan okunur (katalog zaman icinde degisir).
GH_MODELS_URL = "https://models.github.ai/inference/chat/completions"
GH_MODEL = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4.1-mini")
USER_AGENT = "SerendipityDigest/1.0 (personal reading tool)"

SYSTEM = (
    "Sen bir kesif dergisinin editorusun. Gorevin: akademik makale ya da haber "
    "ozetlerini, okuyucuda MERAK uyandiran kisa bir Turkce 'yem'e cevirmek. "
    "Kurallar: (1) Ozet DEGIL merak acici yaz -- 'ne hakkinda + neden carpici' "
    "sezdir, cevabi kaynakta birak. (2) 40-60 kelime, en fazla 3 cumle. "
    "(3) Sadece verilen bilgiye sadik kal, ASLA uydurma; bilgi yetersizse "
    "elindekiyle yetin. (4) Akademik jargon yerine sade, davetkar dil kullan. "
    "(5) Turkce yaz. Ciktida sadece istenen JSON'u ver."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "hook": {"type": "string"},
                },
                "required": ["index", "hook"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["hooks"],
    "additionalProperties": False,
}


def _call_anthropic(system: str, user_msg: str) -> str:
    """Anthropic ile cagri; duz metin (JSON bekleniyor) doner."""
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user_msg}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model istegi reddetti")
    return next((b.text for b in resp.content if b.type == "text"), "")


def _call_github_models(system: str, user_msg: str) -> str:
    """GitHub Models (ucretsiz, OpenAI uyumlu) ile cagri. Ek paket gerekmez."""
    token = (os.environ.get("GITHUB_MODELS_TOKEN")
             or os.environ.get("GITHUB_TOKEN", "")).strip()
    body = json.dumps({
        "model": GH_MODEL,
        "messages": [
            # Sema JSON modunda serbest oldugu icin istenen sekli acikca yaz.
            {"role": "system", "content": system + (
                " Cikti sadece su JSON olsun: "
                '{"hooks":[{"index":<int>,"hook":"<metin>"}]}')},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }).encode("utf-8")
    req = urllib.request.Request(GH_MODELS_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        raw = urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code} — {detail}") from None
    data = json.loads(raw)
    return data["choices"][0]["message"]["content"]


def _parse_hooks(text: str) -> dict:
    """Model cevabini JSON'a cevirir; kod cit isaretlerini toleransla temizler."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        t = t.rsplit("```", 1)[0]
    return json.loads(t)


def enrich_hooks(selected: list) -> None:
    """Secilen makalelerin 'summary' alanlarini merak-acici yemlerle degistirir.
    Basarisizlikta sessizce hicbir sey yapmaz (mevcut ozetler korunur)."""
    if not selected:
        return

    # Saglayici ZINCIRI: sirayla denenir, ilki calisan kazanir.
    # Anthropic anahtari tanimli ama bakiyesiz olabilir -- o yuzden "secip
    # birakmak" yerine basarisiz olani atlayip bir sonrakine geciyoruz.
    chain = []
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        try:
            import anthropic  # noqa: F401
            chain.append(("anthropic", ANTHROPIC_MODEL))
        except ImportError:
            pass
    if os.getenv("GITHUB_MODELS_TOKEN") or os.getenv("GITHUB_TOKEN"):
        chain.append(("github", GH_MODEL))
    if not chain:
        print("  [yem atlandi] saglayici yok (ANTHROPIC_API_KEY / GITHUB_TOKEN); "
              "ham ozet kullanilacak.")
        return

    items = [
        {
            "index": i,
            "baslik": a.get("title", ""),
            "kaynak": a.get("source", ""),
            "alan": a.get("category", ""),
            "ozet": (a.get("summary") or "")[:700],
        }
        for i, a in enumerate(selected)
    ]
    user_msg = (
        "Asagida bu haftanin kesif fisi icin secilmis maddeler var (JSON). "
        "Her biri icin 'index'i ayni kalacak sekilde 40-60 kelimelik bir Turkce "
        "yem uret. Ozeti olmayan ya da cok kisa olan maddelerde basligindan "
        "yola cikarak merak acici bir giris yaz.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )

    data = None
    used = None
    for provider, model_adi in chain:
        try:
            text = (_call_anthropic(SYSTEM, user_msg) if provider == "anthropic"
                    else _call_github_models(SYSTEM, user_msg))
            data = _parse_hooks(text)
            used = (provider, model_adi)
            break
        except Exception as e:
            print(f"  [yem] {provider} basarisiz ({e})")
    if data is None:
        print("  [yem atlandi] hicbir saglayici calismadi; ham ozet kullanilacak.")
        return

    count = 0
    for h in data.get("hooks", []):
        i = h.get("index")
        hook = (h.get("hook") or "").strip()
        if isinstance(i, int) and 0 <= i < len(selected) and hook:
            selected[i]["summary"] = hook
            selected[i]["enriched"] = True
            count += 1
    print(f"  [yem] {count}/{len(selected)} madde merak-acici yeme cevrildi "
          f"({used[0]}: {used[1]}).")
