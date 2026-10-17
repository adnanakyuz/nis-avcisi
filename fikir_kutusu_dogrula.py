#!/usr/bin/env python3
"""Fikir kartlarının sözleşmedeki şemaya (FIKIR_KUTUSU.md) uyduğunu doğrular. Ek paket gerektirmez.

  python fikir_kutusu_dogrula.py <fikir_kutusu_klasörü>      → tüm kartları denetler, hata varsa çıkış kodu 1
"""
import glob, json, os, re, sys

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$")
SAYISAL = ("toplam", "patlama", "hiz", "etkilesim", "rekabet_boslugu", "turkce_boslugu", "uretilebilirlik", "kanal_uyumu")
RISKLER = {"telif", "yeniden_kullanilmis_icerik", "yatirim_tavsiyesi", "saglik", "marka_guvenligi"}
YONTEMLER = {"stok+seslendirme", "yapay_zeka_video", "avatar", "karma"}
PLATFORMLAR = {"youtube", "tiktok", "instagram_reels", "facebook_reels"}
KAYNAK_ALANLARI = {"platform": str, "id": str, "baslik": str, "kanal": str, "abone": int, "izlenme": int,
                   "kanal_medyan_izlenme": (int, float), "yayin": str, "sure_sn": int, "dil": str,
                   "patlama_orani": (int, float)}
ALANLAR = ["surum", "id", "kanal_id", "olusturma", "guncelleme", "durum", "konu", "ozet", "nis", "format", "hedef_dil",
           "onerilen_aci", "kanca_onerileri", "baslik_kaliplari", "kapak_gozlemleri", "izleyici_sorulari", "puanlar",
           "puan_aciklamasi", "uretim", "riskler", "risk_aciklamasi", "platformlar", "kaynak_videolar"]


def _tip(deger, tip, bos_olabilir=True):
    return (deger is None and bos_olabilir) or (isinstance(deger, tip) and not isinstance(deger, bool)
                                                if tip in (int, float, (int, float)) else isinstance(deger, tip))


def dogrula(k, dosya_adi=None):
    """Kartın şemaya uygunluğu; hata listesi döner (boş liste = geçerli)."""
    h = []
    if not isinstance(k, dict):
        return ["kart bir JSON nesnesi değil"]
    eksik = [a for a in ALANLAR if a not in k]
    if eksik:
        h.append("eksik alanlar: " + ", ".join(eksik))
    fazla = [a for a in k if a not in ALANLAR]
    if fazla:
        h.append("şemada olmayan alanlar: " + ", ".join(fazla))
    if k.get("surum") != 1:
        h.append("surum 1 olmalı")
    if not re.fullmatch(r"fk_\d{8}_\d{4}", str(k.get("id", ""))):
        h.append("id biçimi fk_YYYYMMDD_NNNN olmalı")
    if dosya_adi and os.path.splitext(dosya_adi)[0] != k.get("id"):
        h.append("dosya adı id ile aynı olmalı")
    for a in ("olusturma", "guncelleme"):
        if not ISO.match(str(k.get(a, ""))):
            h.append(f"{a} ISO 8601 olmalı")
    if k.get("durum") not in ("yeni",) and not isinstance(k.get("durum"), str):
        h.append("durum metin olmalı")
    for a in ("kanal_id", "konu", "ozet", "puan_aciklamasi", "risk_aciklamasi"):
        if not isinstance(k.get(a), str) or not k.get(a):
            h.append(f"{a} dolu bir metin olmalı")
    if k.get("format") not in ("shorts", "uzun"):
        h.append("format 'shorts' ya da 'uzun' olmalı")
    for a in ("nis", "hedef_dil", "onerilen_aci", "kapak_gozlemleri"):
        if not _tip(k.get(a), str):
            h.append(f"{a} metin ya da null olmalı")
    for a in ("kanca_onerileri", "baslik_kaliplari", "izleyici_sorulari"):
        v = k.get(a)
        if not (v is None or (isinstance(v, list) and all(isinstance(x, str) for x in v))):
            h.append(f"{a} metin listesi ya da null olmalı")
    p = k.get("puanlar") or {}
    for a in SAYISAL:
        if a not in p:
            h.append(f"puanlar.{a} eksik")
        elif p[a] is not None and not (isinstance(p[a], (int, float)) and 0 <= p[a] <= 100):
            h.append(f"puanlar.{a} 0-100 arası sayı ya da null olmalı")
    if p.get("omur", "yok") not in ("haber", "kalici", None):
        h.append("puanlar.omur 'haber', 'kalici' ya da null olmalı")
    if p.get("kazanc_potansiyeli", "yok") not in ("dusuk", "orta", "yuksek", None):
        h.append("puanlar.kazanc_potansiyeli dusuk/orta/yuksek ya da null olmalı")
    u = k.get("uretim") or {}
    if u.get("tahmini_yontem") not in YONTEMLER | {None}:
        h.append("uretim.tahmini_yontem geçersiz")
    if not (u.get("gerekli_gorseller") is None or isinstance(u.get("gerekli_gorseller"), list)):
        h.append("uretim.gerekli_gorseller liste ya da null olmalı")
    if not isinstance(u.get("yapay_zeka_icerik_beyani_gerekli"), bool):
        h.append("uretim.yapay_zeka_icerik_beyani_gerekli true/false olmalı")
    r = k.get("riskler")
    if not isinstance(r, list) or not set(r) <= RISKLER:
        h.append("riskler izin verilen değerlerden oluşan bir liste olmalı")
    pl = k.get("platformlar") or {}
    if set(pl) != PLATFORMLAR or any(not isinstance(v, dict) or v.get("uygun") not in (True, False, None)
                                     for v in pl.values()):
        h.append("platformlar youtube/tiktok/instagram_reels/facebook_reels ve {uygun: true/false/null} içermeli")
    kv = k.get("kaynak_videolar")
    if not isinstance(kv, list) or not kv:
        h.append("kaynak_videolar boş olmayan bir liste olmalı")
    else:
        for i, x in enumerate(kv):
            for a, tip in KAYNAK_ALANLARI.items():
                if a not in x:
                    h.append(f"kaynak_videolar[{i}].{a} eksik")
                elif not _tip(x[a], tip, bos_olabilir=a not in ("platform", "id", "baslik", "yayin")):
                    h.append(f"kaynak_videolar[{i}].{a} tipi yanlış")
            if x.get("yayin") and not ISO.match(x["yayin"]):
                h.append(f"kaynak_videolar[{i}].yayin ISO 8601 olmalı")
    return h


def klasoru_dogrula(klasor):
    sonuc, toplam = {}, 0
    for yol in sorted(glob.glob(os.path.join(klasor, "*", "fk_*.json"))):
        toplam += 1
        with open(yol, encoding="utf-8") as f:
            hatalar = dogrula(json.load(f), os.path.basename(yol))
        if hatalar:
            sonuc[os.path.relpath(yol, klasor)] = hatalar
    return toplam, sonuc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    toplam, hatali = klasoru_dogrula(sys.argv[1])
    for yol, hatalar in hatali.items():
        print(f"✗ {yol}: " + "; ".join(hatalar))
    print(f"{toplam} kart denetlendi, {len(hatali)} hatalı.")
    sys.exit(1 if hatali or not toplam else 0)
