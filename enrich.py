#!/usr/bin/env python3
"""
enrich.py — Secilen makalelerin abstract/ozetini, merak uyandiran kisa bir
Turkce "yem"e cevirir (Claude ile). Yalnizca SECILEN ~7 makaleye tek bir toplu
cagri yapar; ucuz ve hizli. API anahtari yoksa ya da cagri basarisiz olursa
sessizce mevcut ozete geri duser -- fis asla kirilmaz.

Kullanim (digest.py icinden):
    from enrich import enrich_hooks
    enrich_hooks(selected)   # listedeki 'summary' alanlarini yerinde gunceller

Ortam:
    ANTHROPIC_API_KEY (ya da 'ant auth login' profili) gerekir.
    'pip install anthropic' kurulu olmali.
"""
import json
import os

MODEL = "claude-opus-4-8"

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


def enrich_hooks(selected: list) -> None:
    """Secilen makalelerin 'summary' alanlarini merak-acici yemlerle degistirir.
    Basarisizlikta sessizce hicbir sey yapmaz (mevcut ozetler korunur)."""
    if not selected:
        return
    try:
        import anthropic
    except ImportError:
        print("  [yem atlandi] 'anthropic' paketi kurulu degil; ham ozet kullanilacak.")
        return
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        print("  [yem atlandi] ANTHROPIC_API_KEY yok; ham ozet kullanilacak.")
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

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": user_msg}],
        )
        if resp.stop_reason == "refusal":
            print("  [yem atlandi] model istegi reddetti; ham ozet kullanilacak.")
            return
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
    except Exception as e:
        print(f"  [yem atlandi] LLM zenginlestirme hatasi ({e}); ham ozet kullanilacak.")
        return

    count = 0
    for h in data.get("hooks", []):
        i = h.get("index")
        hook = (h.get("hook") or "").strip()
        if isinstance(i, int) and 0 <= i < len(selected) and hook:
            selected[i]["summary"] = hook
            selected[i]["enriched"] = True
            count += 1
    print(f"  [yem] {count}/{len(selected)} madde merak-acici yeme cevrildi.")
