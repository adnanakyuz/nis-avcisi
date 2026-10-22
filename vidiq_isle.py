"""vidIQ ile Niş Avcısı'nın kaçırdığı kanalları bulma (bulutta çalışan günlük Claude görevi kullanır).

GitHub Actions vidIQ'ya bağlanamaz; vidIQ çağrılarını bulut görevi yapar, bu betik ise kararları ve süzmeyi
makine gibi tutarlı yapar:

  python vidiq_isle.py plan --bakiye 175 --sifirlanma 2026-11-12T00:27:18Z --klasor /tmp/vidiq
      → bugün hangi vidIQ çağrılarının yapılacağını <klasor>/plan.json'a yazar ve ekrana basar.
        Kredi, yenilenme tarihine kadar kalan günlere eşit bölünür; böylece dönem sonunda hepsi harcanmış olur.
  python vidiq_isle.py isle --klasor /tmp/vidiq
      → görevin plan.json'daki her çağrı için kaydettiği yanıt dosyalarını okur, Türkçe ve işe yaramayan
        kanalları eler, site/vidiq.json'a ekler.

Ek paket gerekmez.
"""
import argparse, datetime as dt, json, math, os, sqlite3, sys

KLASOR = os.path.dirname(os.path.abspath(__file__))
KREDI = 5                 # vidIQ'da her veri çağrısı 5 kredi
GUNLUK_EN_FAZLA = 6       # tek günde en fazla bu kadar çağrı (bir gün atlanırsa birikeni de yavaş yavaş harcar)
BENZER_BEKLEME = 30       # aynı kanala 30 gün içinde tekrar "benzer kanallar" sorulmaz
ARAMA_BEKLEME = 14        # aynı niş 14 gün içinde tekrar aranmaz
SAKLAMA_GUN = 30          # vidiq.json'da son 30 günde görülen kanallar tutulur
EN_FAZLA_KANAL = 200
EN_AZ_ABONE, EN_FAZLA_ABONE = 1_000, 300_000
EN_AZ_BUYUME = 10         # 30 günlük abone artışı en az %10 (duran kanal işe yaramaz)
# vidIQ çok yeni kanalları henüz tanımıyor (6 günlük, 1 videolu kanal için boş yanıt döndü, 5 kredi boşa gitti);
# "benzer kanallar" yalnızca bu kadar oturmuş adaylar için sorulur.
BENZER_EN_AZ_YAS, BENZER_EN_AZ_VIDEO = 14, 5
# İzleyici kitlesi değil içerik türü; Niş Avcısı'nın aradığı "yeni, tekrarlanabilir niş" ile ilgisi olmayanlar.
ISTENMEYEN = ("islam", "religio", "quran", "bible", "christian", "prayer", "quote", "motivational", "spiritual",
              "astrolog", "tarot")
YEDEK_NISLER = ("engineering explainers", "science explainers", "history documentary", "space and astronomy",
                "wildlife and animals", "restoration", "how it's made", "geography what if", "finance explainers",
                "true crime stories", "military history", "ocean and deep sea")


def json_oku(yol, varsayilan):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return varsayilan


def json_yaz(yol, veri):
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)


def tarih(s):
    """ISO metni → UTC datetime ("Z" sonekini de kabul eder); boş/bozuksa None."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def turkce_mi(dil, ulke):
    """Kullanıcı Türkçe kanal istemiyor: ana dili Türkçe ya da Türkiye merkezli kanallar elenir."""
    diller = [d.strip().lower() for d in (dil or "").replace(";", ",").split(",") if d.strip()]
    return (bool(diller) and diller[0].split("-")[0] == "tr") or (ulke or "").upper() == "TR"


def nis_avcisi_kanallari(site, db_yol):
    """Niş Avcısı'nın bugüne kadar gördüğü kanal kimlikleri (geçmiş veritabanı + son tarama)."""
    ids = {v.get("kanal_id") for v in json_oku(os.path.join(site, "adaylar.json"), {}).get("videolar", [])}
    if os.path.exists(db_yol):
        db = sqlite3.connect(db_yol)
        try:
            ids |= {r[0] for r in db.execute("select distinct kanal_id from kanal_kayit")}
        except sqlite3.Error:
            pass
        finally:
            db.close()
    ids.discard(None)
    return ids


def gunluk_cagri(bakiye, sifirlanma, simdi):
    """Bugün kaç çağrı: kalan çağrı hakkı / yenilenmeye kalan gün (yukarı yuvarlanır)."""
    hak = int(bakiye) // KREDI
    if hak <= 0:
        return 0
    son = tarih(sifirlanma)
    kalan_gun = max(1, math.ceil((son - simdi).total_seconds() / 86400)) if son else 30
    return min(GUNLUK_EN_FAZLA, hak, math.ceil(hak / kalan_gun))


def plan_yap(bakiye, sifirlanma, site, simdi=None):
    simdi = simdi or dt.datetime.now(dt.timezone.utc)
    adet = gunluk_cagri(bakiye, sifirlanma, simdi)
    veri = json_oku(os.path.join(site, "vidiq.json"), {})
    gecmis = veri.get("sorgular", [])

    def yakin(tip, hedef, gun):
        sinir = simdi - dt.timedelta(days=gun)
        return any(s.get("tip") == tip and s.get("hedef") == hedef and (tarih(s.get("tarih")) or sinir) > sinir
                   for s in gecmis)

    # Benzer kanallar: günün en yüksek puanlı Türkçe olmayan adayları (kanal başına tek, puan sırasıyla).
    adaylar, gorulen = [], set()
    for v in sorted(json_oku(os.path.join(site, "adaylar.json"), {}).get("videolar", []),
                    key=lambda v: -(v.get("puan") or 0)):
        kid = v.get("kanal_id")
        if (not kid or kid in gorulen or turkce_mi(v.get("dil"), v.get("ulke")) or yakin("benzer", kid, BENZER_BEKLEME)
                or (v.get("kanal_yasi") or 0) < BENZER_EN_AZ_YAS or (v.get("video_sayisi") or 0) < BENZER_EN_AZ_VIDEO):
            continue
        gorulen.add(kid)
        adaylar.append((kid, v.get("kanal") or kid))

    # Yükselen kanal araması için nişler: vidIQ'nun daha önce bulduğu en hızlı büyüyen kanalların nişleri ile
    # yedek liste dönüşümlü sıralanır; böylece aramalar tek bir alana (ör. hep mühendislik) kaymaz.
    ogrenilen = []
    for k in sorted(veri.get("kanallar", []), key=lambda k: -(k.get("buyume30") or 0)):
        n = (k.get("nis") or "").split(",")[0].strip().lower()
        if n and n not in ogrenilen:
            ogrenilen.append(n)
    nisler = []
    for cift in zip(ogrenilen + [None] * len(YEDEK_NISLER), list(YEDEK_NISLER) + [None] * len(ogrenilen)):
        nisler += [n for n in cift if n and n not in nisler]
    nisler = [n for n in nisler if not yakin("yukselen", n, ARAMA_BEKLEME)]

    cagrilar, b, y = [], 0, 0
    while len(cagrilar) < adet and (b < len(adaylar) or y < len(nisler)):
        # Sıra: benzer, yükselen, benzer, yükselen... (biri tükenirse diğeriyle devam)
        if (len(cagrilar) % 2 == 0 and b < len(adaylar)) or y >= len(nisler):
            kid, ad = adaylar[b]
            b += 1
            cagrilar.append(dict(tip="benzer", hedef=kid, ad=ad, dosya=f"benzer_{kid}.json",
                                 arac="vidiq_similar_channels", parametreler=dict(channelId=kid, size=50)))
        else:
            n = nisler[y]
            y += 1
            cagrilar.append(dict(
                tip="yukselen", hedef=n, ad=n, dosya=f"yukselen_{len(cagrilar) + 1}.json",
                arac="vidiq_channel_search",
                parametreler=dict(query=n, breakoutChannel=True, limit=50, subscriberCountMin=EN_AZ_ABONE,
                                  publishedAfter=(simdi - dt.timedelta(days=365)).date().isoformat(),
                                  lastVideoPublishedAfter=(simdi - dt.timedelta(days=14)).date().isoformat())))
    return dict(tarih=simdi.isoformat(timespec="seconds"), bakiye=int(bakiye), sifirlanma=sifirlanma,
                cagri_sayisi=adet, cagrilar=cagrilar)


def kanal_al(ham, kaynak, simdi):
    """vidIQ yanıtındaki bir kanalı sitenin kullandığı sade biçime çevirir."""
    nis = ham.get("niche") or ham.get("niches") or ""
    alt = ham.get("subNiches")
    if isinstance(alt, list) and alt:
        nis = ", ".join([nis] + alt[:2]) if nis else ", ".join(alt[:3])
    return dict(id=ham.get("channelId") or ham.get("id"), ad=(ham.get("channelTitle") or ham.get("title") or "").strip(),
                handle=ham.get("handle"), abone=ham.get("subscriberCount"), ort_izlenme=ham.get("avgViews"),
                buyume30=ham.get("subsGrowth30d"), izlenme_buyume30=ham.get("viewsGrowth30d"), nis=nis,
                dil=ham.get("languages"), ulke=ham.get("country"), kazanc=ham.get("estimatedEarnings"),
                acilis=ham.get("publishedAt"), son_video=ham.get("lastVideoPublished"),
                video_sayisi=ham.get("videoCount"), tur=ham.get("channelType"), yuzsuz=ham.get("isFaceless"),
                kaynak=kaynak, ilk=simdi, son=simdi)


def ise_yarar(k):
    """Niş Avcısı'nın işine yarayacak mı: Türkçe değil, makul büyüklükte, büyüyor, konu dışı değil."""
    if not k["id"] or turkce_mi(k["dil"], k["ulke"]):
        return False
    abone = k["abone"] or 0
    if not EN_AZ_ABONE <= abone <= EN_FAZLA_ABONE:
        return False
    if any(s in (k["nis"] or "").lower() for s in ISTENMEYEN):
        return False
    return (k["buyume30"] or 0) >= EN_AZ_BUYUME


def yanit_kanallari(yanit):
    if isinstance(yanit, list):
        return yanit
    for anahtar in ("similarChannels", "channels", "kanallar"):
        if isinstance(yanit.get(anahtar), list):
            return yanit[anahtar]
    return []


def isle(klasor, site, db_yol, simdi=None):
    simdi = (simdi or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    plan = json_oku(os.path.join(klasor, "plan.json"), None)
    if not plan:
        sys.exit("plan.json yok; önce 'plan' çalıştırılmalı.")
    yol = os.path.join(site, "vidiq.json")
    veri = json_oku(yol, {})
    kanallar = {k["id"]: k for k in veri.get("kanallar", []) if k.get("id")}
    sorgular = veri.get("sorgular", [])
    nis_avcisi = nis_avcisi_kanallari(site, db_yol)
    ozet = []
    for c in plan["cagrilar"]:
        yanit = json_oku(os.path.join(klasor, c["dosya"]), None)
        if yanit is None:
            ozet.append(f"{c['dosya']}: yanıt dosyası yok, atlandı")
            continue
        ham = yanit_kanallari(yanit)
        kaynak = f"{c['ad']} kanalına benzer" if c["tip"] == "benzer" else f"Yükselen: {c['ad']}"
        alinan = 0
        for h in ham:
            k = kanal_al(h, kaynak, simdi)
            if k["id"] == c["hedef"] or not ise_yarar(k):
                continue
            eski = kanallar.get(k["id"])
            if eski:  # istatistikleri tazele, ilk bulunma ve ilk kaynağı koru
                k["ilk"], k["kaynak"] = eski.get("ilk", simdi), eski.get("kaynak", kaynak)
            kanallar[k["id"]] = k
            alinan += 1
        sorgular.append(dict(tarih=simdi, tip=c["tip"], hedef=c["hedef"], ad=c["ad"], gelen=len(ham), alinan=alinan))
        ozet.append(f"{c['dosya']}: {len(ham)} kanal geldi, {alinan} tanesi işe yarar")

    sinir = tarih(simdi) - dt.timedelta(days=SAKLAMA_GUN)
    liste = [k for k in kanallar.values() if (tarih(k.get("son")) or sinir) >= sinir]
    for k in liste:
        k["ortak"] = k["id"] in nis_avcisi   # iki sistem de bulduysa doğrulanmış sayılır
    liste.sort(key=lambda k: (-(k.get("buyume30") or 0), -(k.get("ort_izlenme") or 0)))
    sorgu_sinir = tarih(simdi) - dt.timedelta(days=max(BENZER_BEKLEME, SAKLAMA_GUN))
    json_yaz(yol, dict(surum=1, guncelleme=simdi, bakiye=plan.get("bakiye"), sifirlanma=plan.get("sifirlanma"),
                       kanallar=liste[:EN_FAZLA_KANAL],
                       sorgular=[s for s in sorgular if (tarih(s.get("tarih")) or sorgu_sinir) >= sorgu_sinir]))
    return ozet, liste


def main():
    p = argparse.ArgumentParser(description="vidIQ sonuçlarını Niş Avcısı'na ekler")
    alt = p.add_subparsers(dest="komut", required=True)
    pp = alt.add_parser("plan", help="bugünkü vidIQ çağrılarını planla")
    pp.add_argument("--bakiye", type=int, required=True, help="vidiq_balance → totalCredits")
    pp.add_argument("--sifirlanma", default="", help="vidiq_balance → renewableResetsAt")
    pi = alt.add_parser("isle", help="kaydedilen vidIQ yanıtlarını süz ve site/vidiq.json'a ekle")
    for x in (pp, pi):
        x.add_argument("--klasor", required=True, help="plan.json ve yanıt dosyalarının klasörü")
        x.add_argument("--site", default=os.path.join(KLASOR, "site"))
        x.add_argument("--db", default=os.path.join(KLASOR, "nis_gecmis.db"))
    a = p.parse_args()
    if a.komut == "plan":
        plan = plan_yap(a.bakiye, a.sifirlanma, a.site)
        json_yaz(os.path.join(a.klasor, "plan.json"), plan)
        print(json.dumps(plan, ensure_ascii=False, indent=1))
    else:
        ozet, liste = isle(a.klasor, a.site, a.db)
        print("\n".join(ozet))
        print(f"site/vidiq.json: {len(liste)} kanal ({sum(k['ortak'] for k in liste)} tanesini Niş Avcısı da bulmuş)")


if __name__ == "__main__":
    main()
