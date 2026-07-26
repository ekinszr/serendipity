#!/usr/bin/env python3
"""collect_feedback.py — Fisten gelen oylari state.json'a isler.

Akis: fisteki "Oylari gonder" baglantisi, oylari JSON blogu halinde tasiyan
onceden doldurulmus bir GitHub issue acar (etiket: `oy`). Bu betik haftalik
kosuda o issue'lari okur, `state.json` icindeki `feedback` sozlugune yazar,
issue'yu kisa bir notla kapatir.

Neden issue: ayri sunucu, veritabani ya da ucuncu hesap gerektirmiyor -- repo
zaten var, kimlik dogrulama Actions'in kendi GITHUB_TOKEN'i.

Ortam:
    GITHUB_TOKEN       (Actions verir; `issues: write` izni gerekir)
    GITHUB_REPOSITORY  (Actions verir; ornek "ekinszr/serendipity")

Calistir:  python collect_feedback.py
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(__file__).parent / "state.json"
API = "https://api.github.com"
ETIKET = "oy"

_JSON_BLOK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _istek(yol: str, method: str = "GET", govde: dict = None) -> object:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN yok")
    data = json.dumps(govde).encode("utf-8") if govde is not None else None
    req = urllib.request.Request(
        f"{API}{yol}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "SerendipityDigest/1.0",
        })
    raw = urllib.request.urlopen(req, timeout=30).read()
    return json.loads(raw) if raw else None


def _oylari_ayikla(govde: str) -> list:
    """Issue govdesindeki JSON blogundan oy kayitlarini cikarir."""
    m = _JSON_BLOK.search(govde or "")
    if not m:
        return []
    try:
        veri = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    cikti = []
    for kayit in veri.get("oylar", []):
        aid = str(kayit.get("aid") or "").strip()
        oy = str(kayit.get("oy") or "").strip()
        if aid and oy in ("up", "down"):
            cikti.append({
                "aid": aid,
                "oy": oy,
                "baslik": kayit.get("baslik") or "",
                "alan": kayit.get("alan") or "",
                "uzaklik": kayit.get("uzaklik") or "",
            })
    return cikti


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("[oy] GITHUB_REPOSITORY yok; toplama atlandi.")
        return
    try:
        issues = _istek(f"/repos/{repo}/issues?state=open&labels={ETIKET}&per_page=50")
    except Exception as e:
        print(f"[oy] issue listesi alinamadi ({e}); toplama atlandi.")
        return

    issues = [i for i in (issues or []) if "pull_request" not in i]
    if not issues:
        print("[oy] yeni oy issue'su yok.")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    feedback = state.setdefault("feedback", {})
    simdi = datetime.now(timezone.utc).isoformat(timespec="seconds")

    toplam_yeni = 0
    for issue in issues:
        numara = issue["number"]
        oylar = _oylari_ayikla(issue.get("body", ""))
        if not oylar:
            print(f"[oy] #{numara}: okunabilir JSON blogu yok, atlandi (acik birakildi).")
            continue
        for kayit in oylar:
            # Ayni makaleye sonradan verilen oy oncekini gunceller.
            feedback[kayit["aid"]] = {
                "oy": kayit["oy"],
                "baslik": kayit["baslik"],
                "alan": kayit["alan"],
                "uzaklik": kayit["uzaklik"],
                "tarih": simdi,
                "issue": numara,
            }
        toplam_yeni += len(oylar)
        try:
            _istek(f"/repos/{repo}/issues/{numara}/comments", "POST",
                   {"body": f"{len(oylar)} oy `state.json`'a islendi. Tesekkurler."})
            _istek(f"/repos/{repo}/issues/{numara}", "PATCH", {"state": "closed"})
            print(f"[oy] #{numara}: {len(oylar)} oy islendi, issue kapatildi.")
        except Exception as e:
            print(f"[oy] #{numara}: islendi ama kapatilamadi ({e}).")

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    up = sum(1 for v in feedback.values() if v["oy"] == "up")
    down = len(feedback) - up
    print(f"[oy] bu kosuda {toplam_yeni} oy islendi; "
          f"toplam hafiza: {len(feedback)} makale ({up} vay / {down} alakasiz).")


if __name__ == "__main__":
    main()
