#!/usr/bin/env python3
"""
FİKİR KUTUSU — Niş Avcısı'nın bulduğu fırsatları, kullanıcının YouTube kanalları için "fikir kartı" dosyaları olarak
tek bir klasöre yazar. Kahve Stüdyo bu klasörü okur; aradaki tek bağlantı bu dosyalardır (sözleşme: FIKIR_KUTUSU.md).

Kullanım (YT_API_KEY ortam değişkeni gerekli):
  python fikir_kutusu.py --veri <özel_depo_klasörü> --nis <niş_avcısı_klasörü>
      Her saat çalışır: takip edilen kanalların yeni videoları + zamanı gelen ölçümler (kaçırılanlar telafi edilir).
      Niş Avcısı'nın yeni günlük taraması geldiyse fikir kartlarını üretir; haftada bir geri bildirimi değerlendirir.
  --gunluk / --haftalik : o işi şimdi zorla yap.

Kurallar: Başkalarının videoları indirilmez/kopyalanmaz; sadece resmi YouTube Data API (kazıma yok).
Veri yoksa null yazılır, değer uydurulmaz. Anahtar hiçbir dosyaya ya da log'a yazılmaz.
"""
import argparse, collections, datetime as dt, glob, json, math, os, re, shutil, sqlite3, sys

import nis_avcisi as na
import fk_platformlar
import fk_puanlar as P

SURUM = 1
KOD_KLASORU = os.path.dirname(os.path.abspath(__file__))
TR = na.TR_SAAT
RISK_KURALLARI = [
    # Desenler kelime başına bağlıdır (fk_puanlar._kelime_basi): "war" → "software", "kaza" → "kazan" eşleşmez.
    ("telif", r"music video|official video|full movie|film izle|dizi\b|dizisi|episode|bölüm|highlights|maç özeti|"
              r"clips?\b|trailer|fragman|lyrics|şarkı sözleri|cover\b|covers\b"),
    ("yeniden_kullanilmis_icerik", r"compilation|derleme|best moments|en iyi anlar|reaction|react\b|tepki|tiktok|"
                                   r"reupload|fails\b|funny moments"),
    ("yatirim_tavsiyesi", r"borsa|hisse|kripto|crypto|bitcoin|coins?\b|stock|invest|yatırım|al sat|altın fiyat|forex|"
                          r"trading"),
    ("saglik", r"sağlık|hastalık|tedavi|ilaç|diyet|kilo ver|health|cure\b|cures\b|disease|diet\b|diets\b|weight loss|"
               r"doktor|doctor"),
    ("marka_guvenligi", r"savaş|wars?\b|warfare|ölüm|death|kaza\b|kazası|kazada|kazalar|accident|şiddet|violence|"
                        r"silah|guns?\b|gunfire|prank|siyaset|politic|skandal|scandal|kan\b|blood"),
]
RISK_ACIKLAMA = {
    "telif": "başkasına ait film, müzik, maç ya da dizi görüntüsü kullanılırsa telif ihlali olur; kendi görüntünü üret",
    "yeniden_kullanilmis_icerik": "derleme/tepki türü; YouTube 'yeniden kullanılmış içerik' sayıp para kazanmayı kapatabilir",
    "yatirim_tavsiyesi": "al/sat tavsiyesi verme; 'yatırım tavsiyesi değildir' uyarısı ekle",
    "saglik": "sağlık iddialarında kaynak göster; YouTube'un tıbbi yanlış bilgi kuralları geçerli",
    "marka_guvenligi": "hassas konu; reklamverenler kaçınabilir (sarı dolar riski)",
}


# ---------- zaman ----------
def simdi():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def pt_gunu(t):
    """YouTube kotası Pasifik saatiyle gece yarısı sıfırlanır. Yaz saati: Mart'ın 2. pazarı – Kasım'ın 1. pazarı.
    (Windows'ta saat dilimi verisi olmayabildiği için kural elle hesaplanır.)"""
    y = t.year

    def pazar(ay, n):
        d = dt.datetime(y, ay, 1, tzinfo=dt.timezone.utc)
        d += dt.timedelta(days=(6 - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)
    yaz = pazar(3, 2).replace(hour=10) <= t < pazar(11, 1).replace(hour=9)
    return (t + dt.timedelta(hours=-7 if yaz else -8)).date().isoformat()


# ---------- dosya ----------
def guvenli_yaz(yol, veri):
    """Önce geçici dosyaya yaz, sonra yeniden adlandır: okuyan taraf asla yarım dosya görmez. UTF-8."""
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(gecici, yol)


def json_oku(yol, varsayilan=None):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return varsayilan


# ---------- ayarlar ----------
def ayar_yukle(veri):
    """kanallar.json ve fikir_ayarlari.json yoksa şablondan oluşturur. Ayarlardaki eksik anahtarlar şablondan tamamlanır."""
    for ad in ("kanallar.json", "fikir_ayarlari.json"):
        yol = os.path.join(veri, ad)
        if not os.path.exists(yol):
            shutil.copy(os.path.join(KOD_KLASORU, "sablonlar", ad), yol)
    kanallar = json_oku(os.path.join(veri, "kanallar.json"), {}).get("kanallar", [])
    sablon = json_oku(os.path.join(KOD_KLASORU, "sablonlar", "fikir_ayarlari.json"), {})
    ayar = json_oku(os.path.join(veri, "fikir_ayarlari.json"), {})
    for k, v in sablon.items():
        ayar.setdefault(k, v)
        if isinstance(v, dict):
            for kk, vv in v.items():
                ayar[k].setdefault(kk, vv)
    return kanallar, ayar


# ---------- veritabanı ----------
def db_ac(yol):
    """fk_veri.db: Fikir Kutusu'nun kendi veritabanı (Niş Avcısı'nın veritabanına dokunmaz).
    Değişiklikten önce yedek alınır (son 3 yedek tutulur). Tablolar sadece eklenir."""
    if os.path.exists(yol):
        yedek = f"{yol}.yedek-{simdi():%Y%m%d}"
        if not os.path.exists(yedek):
            shutil.copy(yol, yedek)
        for eski in sorted(glob.glob(f"{yol}.yedek-*"))[:-3]:
            os.remove(eski)
    db = sqlite3.connect(yol)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS kota(gun TEXT PRIMARY KEY, birim INTEGER DEFAULT 0, arama INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS takip(video_id TEXT PRIMARY KEY, kanal_id TEXT, yayin_ts TEXT, kaynak TEXT,
        eklendi TEXT, bitti INTEGER DEFAULT 0, nis TEXT, format TEXT, baslik TEXT);
    CREATE TABLE IF NOT EXISTS olcum(video_id TEXT, ts TEXT, yas_saat REAL, hedef_saat INTEGER, izlenme INTEGER,
        begeni INTEGER, yorum INTEGER);
    CREATE INDEX IF NOT EXISTS ix_olcum ON olcum(video_id, ts);
    CREATE TABLE IF NOT EXISTS kanal_ist(kanal_id TEXT PRIMARY KEY, ts TEXT, medyan REAL, abone INTEGER,
        video_sayisi INTEGER, uploads TEXT, ad TEXT);
    CREATE TABLE IF NOT EXISTS takip_kanal(kanal_id TEXT PRIMARY KEY, son_kontrol TEXT);
    CREATE TABLE IF NOT EXISTS kart(id TEXT PRIMARY KEY, kanal_id TEXT, ana_video TEXT, konu_anahtar TEXT,
        olusturma TEXT, puanlar TEXT, dosya TEXT);
    CREATE TABLE IF NOT EXISTS kart_sayac(gun TEXT PRIMARY KEY, son INTEGER);
    CREATE TABLE IF NOT EXISTS geri_bildirim(youtube_id TEXT PRIMARY KEY, fikir_id TEXT, kanal_id TEXT,
        yayin_ts TEXT, format TEXT, okundu TEXT);
    CREATE TABLE IF NOT EXISTS agirlik_gecmisi(ts TEXT, eski TEXT, yeni TEXT, neden TEXT);
    CREATE TABLE IF NOT EXISTS islem(ad TEXT PRIMARY KEY, son TEXT);
    """)
    return db


def islem_son(db, ad):
    r = db.execute("SELECT son FROM islem WHERE ad=?", (ad,)).fetchone()
    return r[0] if r else None


def islem_yaz(db, ad, ts):
    db.execute("INSERT OR REPLACE INTO islem VALUES(?,?)", (ad, ts))
    db.commit()


# ---------- kota ----------
class ButceDoldu(Exception):
    """Fikir Kutusu'nun günlük kota bütçesi doldu (YouTube'un kendisi değil; bizim koyduğumuz sınır)."""


class Kota:
    """İki ayrı YouTube kotası sayılır (15 Ekim 2026'dan beri): arama (search.list, günde 100 çağrı, Niş Avcısı'yla
    ortak) ve diğer istekler (10.000 birim). Fikir Kutusu kendine bütçe koyar; %80'e gelince sadece önemli işler
    yapılır (ölçümler ve kart yazımı), yeni aramalar durur."""

    def __init__(self, db, ayar):
        self.db, self.ayar = db, ayar["kota"]
        self.gun = pt_gunu(simdi())
        r = db.execute("SELECT birim, arama FROM kota WHERE gun=?", (self.gun,)).fetchone()
        self.birim, self.arama = (r if r else (0, 0))

    def oran(self):
        return max(self.birim / self.ayar["gunluk_birim_butcesi"], self.arama / self.ayar["gunluk_arama_butcesi"])

    def onemli_mod(self):
        return self.oran() >= self.ayar["onemli_mod_esigi"]

    def izin(self, tur):
        if tur == "arama":
            return not self.onemli_mod() and self.arama + 1 <= self.ayar["gunluk_arama_butcesi"]
        return self.birim + 1 <= self.ayar["gunluk_birim_butcesi"]

    def say(self, tur):
        if tur == "arama":
            self.arama += 1
        else:
            self.birim += 1
        self.db.execute("INSERT OR REPLACE INTO kota VALUES(?,?,?)", (self.gun, self.birim, self.arama))
        self.db.commit()

    def durum(self):
        return dict(gun_pt=self.gun, arama=self.arama, arama_butcesi=self.ayar["gunluk_arama_butcesi"],
                    birim=self.birim, birim_butcesi=self.ayar["gunluk_birim_butcesi"],
                    oran=round(self.oran(), 3), onemli_mod=self.onemli_mod(),
                    aciklama="Arama ve diğer istekler YouTube'da ayrı kotalardır; bütçe Fikir Kutusu'nun kendi sınırıdır.")


class Istemci:
    """YouTube isteklerini kota sayacından geçirir. nis_avcisi.YT ile aynı get(uc, maliyet, **p) imzası:
    Niş Avcısı'nın ölçüm fonksiyonları (firsat_olc) doğrudan kullanılabilsin diye."""

    def __init__(self, yt, kota):
        self.yt, self.kota = yt, kota

    def get(self, uc, maliyet=1, **p):
        tur = "arama" if uc == "search" else "birim"
        if not self.kota.izin(tur):
            raise ButceDoldu(tur)
        self.kota.say(tur)  # geçersiz istekler de kotadan düşer: önce say
        return self.yt.get(uc, 1, **p)


# ---------- YouTube yardımcıları ----------
def videolar_getir(ist, ids, part="snippet,statistics,contentDetails"):
    sonuc = {}
    ids = list(dict.fromkeys(i for i in ids if i))
    for i in range(0, len(ids), 50):
        for v in ist.get("videos", part=part, id=",".join(ids[i:i + 50])).get("items", []):
            sonuc[v["id"]] = v
    return sonuc


def kanal_istatistigi(ist, db, kanal_idler, en_eski_saat=72):
    """Kanalların son 30 videosunun medyan izlenmesi, abone ve video sayısı (patlama için). 3 günlük önbellek.
    Kanal başına ~3 birim (channels + playlistItems + videos)."""
    sinir = iso(simdi() - dt.timedelta(hours=en_eski_saat))
    eksik = [k for k in dict.fromkeys(kanal_idler)
             if not db.execute("SELECT 1 FROM kanal_ist WHERE kanal_id=? AND ts>=?", (k, sinir)).fetchone()]
    for i in range(0, len(eksik), 50):
        kanallar = ist.get("channels", part="snippet,statistics,contentDetails", id=",".join(eksik[i:i + 50]))
        for c in kanallar.get("items", []):
            cs = c.get("statistics", {})
            uploads = c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            medyan = None
            if uploads:
                try:
                    pl = ist.get("playlistItems", part="contentDetails", playlistId=uploads, maxResults=30)
                    vids = [x["contentDetails"]["videoId"] for x in pl.get("items", [])]
                    izl = [int(v.get("statistics", {}).get("viewCount", 0))
                           for v in videolar_getir(ist, vids, "statistics").values()]
                    medyan = P._orta(izl) if len(izl) >= 3 else None
                except na.ApiHatasi:
                    pass
            db.execute("INSERT OR REPLACE INTO kanal_ist VALUES(?,?,?,?,?,?,?)",
                       (c["id"], iso(simdi()), medyan,
                        None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0)),
                        int(cs.get("videoCount", 0)), uploads, c["snippet"]["title"]))
    db.commit()
    return {r[0]: dict(medyan=r[1], abone=r[2], video_sayisi=r[3], uploads=r[4], ad=r[5])
            for r in db.execute(f"SELECT kanal_id, medyan, abone, video_sayisi, uploads, ad FROM kanal_ist WHERE kanal_id IN "
                                f"({','.join('?' * len(kanal_idler))})", list(kanal_idler))} if kanal_idler else {}


def format_bul(sure_sn):
    return "shorts" if sure_sn and sure_sn <= 180 else "uzun"


# ---------- takip ve ölçüm takvimi ----------
def takibe_al(db, video_id, kanal_id, yayin_ts, kaynak, nis="", format_="", baslik=""):
    db.execute("INSERT OR IGNORE INTO takip VALUES(?,?,?,?,?,0,?,?,?)",
               (video_id, kanal_id, yayin_ts, kaynak, iso(simdi()), nis, format_, baslik))


def bekleyen_olcumler(db, saatler, an=None):
    """Zamanı gelmiş ama yapılmamış ölçümler: (video_id, hedef_saat). Program kapalıyken kaçırılanlar da burada
    çıkar ve hemen telafi edilir; kaçırılmış birden fazla hedef varsa tek ölçüm hepsini karşılar (gerçek yaş kaydedilir)."""
    an = an or simdi()
    bekleyen = []
    for vid, yayin in db.execute("SELECT video_id, yayin_ts FROM takip WHERE bitti=0"):
        yas = (an - na.zaman(yayin)).total_seconds() / 3600
        yapilan = {r[0] for r in db.execute("SELECT hedef_saat FROM olcum WHERE video_id=? AND hedef_saat IS NOT NULL",
                                            (vid,))}
        gelen = [h for h in saatler if h <= yas and h not in yapilan]
        if gelen:
            bekleyen.append((vid, max(gelen), [h for h in gelen]))
    return bekleyen


def olcumleri_yap(ist, db, saatler):
    """Bekleyen ölçümleri tek seferde (50 video = 1 birim) yapar. 7. gün ölçümü alınan video takipten çıkar."""
    bekleyen = bekleyen_olcumler(db, saatler)
    if not bekleyen:
        return 0
    try:  # snippet ek maliyet getirmez (videos.list her durumda 1 birim); kanal kimliği için gerekli
        veriler = videolar_getir(ist, [b[0] for b in bekleyen], "snippet,statistics")
    except ButceDoldu:
        return 0
    an = simdi()
    for vid, hedef, hepsi in bekleyen:
        v = veriler.get(vid)
        yayin = db.execute("SELECT yayin_ts FROM takip WHERE video_id=?", (vid,)).fetchone()[0]
        yas = (an - na.zaman(yayin)).total_seconds() / 3600
        if not v:  # silinmiş/gizli video
            db.execute("UPDATE takip SET bitti=1 WHERE video_id=?", (vid,))
            continue
        st, sn = v.get("statistics", {}), v.get("snippet", {})
        db.execute("UPDATE takip SET kanal_id=COALESCE(NULLIF(kanal_id, ''), ?), baslik=COALESCE(NULLIF(baslik, ''), ?) "
                   "WHERE video_id=?", (sn.get("channelId", ""), sn.get("title", ""), vid))
        for h in hepsi:  # kaçırılan hedefler bu ölçümle kapanır; hedef_saat kaydı tekrar ölçülmesini engeller
            db.execute("INSERT INTO olcum VALUES(?,?,?,?,?,?,?)",
                       (vid, iso(an), yas, h, int(st.get("viewCount", 0)),
                        int(st["likeCount"]) if "likeCount" in st else None, int(st.get("commentCount", 0))))
        if max(saatler) in hepsi:
            db.execute("UPDATE takip SET bitti=1 WHERE video_id=?", (vid,))
    db.commit()
    return len(bekleyen)


def olcum_serisi(db, video_id):
    """[(yas_saat, izlenme)] artan; aynı anda kapanan hedefler tekilleştirilir."""
    return sorted({(round(r[0], 2), r[1]) for r in db.execute(
        "SELECT yas_saat, izlenme FROM olcum WHERE video_id=?", (video_id,))})


def referans_hizlar(db, video_id, nis, yas):
    """Aynı nişteki (yoksa tüm) takip edilen videoların, benzer yaştaki saatlik izlenmeleri."""
    def topla(kosul, parametre):
        l = []
        for vid, y, izl in db.execute(
                f"SELECT DISTINCT o.video_id, o.yas_saat, o.izlenme FROM olcum o JOIN takip t ON t.video_id=o.video_id "
                f"WHERE o.video_id != ? AND o.yas_saat BETWEEN ? AND ? {kosul}", (video_id, yas * .6, yas * 1.6, *parametre)):
            if y > 0:
                l.append(izl / y)
        return l
    l = topla("AND t.nis = ?", (nis,)) if nis else []
    return l if len(l) >= 3 else topla("", ())


def nis_etkilesim_ortalamasi(db, nis):
    def topla(kosul, parametre):
        l = []
        for _, _, izl, b, y in db.execute(
                f"SELECT DISTINCT o.video_id, o.ts, o.izlenme, o.begeni, o.yorum FROM olcum o JOIN takip t ON t.video_id=o.video_id "
                f"WHERE o.begeni IS NOT NULL AND o.izlenme > 0 {kosul}", parametre):
            l.append((b + 2 * (y or 0)) / izl)
        return l
    l = topla("AND t.nis = ?", (nis,)) if nis else []
    l = l if len(l) >= 5 else topla("", ())
    return sum(l) / len(l) if len(l) >= 5 else None


# ---------- kaynaklar ----------
def takip_kanallarini_tara(ist, db, kanallar, ayar):
    """Profillerdeki takip edilen kanalların yeni videoları (uploads listesi, kanal başına 1 birim).
    Her kanal en fazla `takip_kontrol_araligi_saat` saatte bir kontrol edilir. Arama kotası harcamaz."""
    aralik = ayar["takip_kontrol_araligi_saat"]
    hepsi = {}
    for p in kanallar:
        if p.get("aktif"):
            for kid in p.get("takip_edilen_kanallar", []):
                if re.fullmatch(r"UC[\w-]{22}", kid or ""):
                    hepsi.setdefault(kid, p["id"])
    if not hepsi:
        return 0
    sinir = iso(simdi() - dt.timedelta(hours=aralik))
    zamani = [k for k in hepsi if not db.execute(
        "SELECT 1 FROM takip_kanal WHERE kanal_id=? AND son_kontrol>=?", (k, sinir)).fetchone()]
    yeni = 0
    ist_kanal = kanal_istatistigi(ist, db, zamani) if zamani else {}
    for kid in zamani:
        uploads = ist_kanal.get(kid, {}).get("uploads")
        if not uploads:
            continue
        try:
            pl = ist.get("playlistItems", part="contentDetails", playlistId=uploads, maxResults=10)
        except (ButceDoldu, na.ApiHatasi):
            break
        for x in pl.get("items", []):
            cd = x.get("contentDetails", {})
            yayin = cd.get("videoPublishedAt")
            if yayin and (simdi() - na.zaman(yayin)).days <= 7:
                if not db.execute("SELECT 1 FROM takip WHERE video_id=?", (cd["videoId"],)).fetchone():
                    takibe_al(db, cd["videoId"], kid, yayin, "takip_kanal", nis=hepsi[kid])
                    yeni += 1
        db.execute("INSERT OR REPLACE INTO takip_kanal VALUES(?,?)", (kid, iso(simdi())))
    db.commit()
    return yeni


def geri_bildirim_oku(db, klasor):
    """Stüdyonun yazdığı _geri_bildirim/*.json dosyaları SADECE okunur (değiştirilmez/silinmez)."""
    yeni = 0
    for yol in sorted(glob.glob(os.path.join(klasor, "_geri_bildirim", "*.json"))):
        g = json_oku(yol)
        if not isinstance(g, dict) or not g.get("youtube_id") or not g.get("yayin_zamani"):
            continue
        if db.execute("SELECT 1 FROM geri_bildirim WHERE youtube_id=?", (g["youtube_id"],)).fetchone():
            continue
        db.execute("INSERT INTO geri_bildirim VALUES(?,?,?,?,?,?)",
                   (g["youtube_id"], g.get("fikir_id"), g.get("kanal_id"), g["yayin_zamani"], g.get("format"), iso(simdi())))
        takibe_al(db, g["youtube_id"], "", g["yayin_zamani"], "geri_bildirim", nis=g.get("kanal_id") or "",
                  format_=g.get("format") or "")
        yeni += 1
    db.commit()
    return yeni


def adaylari_oku(nis_klasoru):
    """Niş Avcısı'nın günlük taramasının çıktısı (site/adaylar.json) ve evergreen listesi."""
    adaylar = json_oku(os.path.join(nis_klasoru, "site", "adaylar.json"), {}) if nis_klasoru else {}
    evergreen = json_oku(os.path.join(nis_klasoru, "site", "evergreen.json"), {}) if nis_klasoru else {}
    return adaylar or {}, evergreen or {}


def adaylari_dbden_kur(ist, nis_klasoru):
    """Yedek yol: adaylar.json yoksa, Niş Avcısı veritabanındaki son genel taramanın (son 36 saat) video
    kimliklerinden adayları kurar. Sadece videos.list ve channels.list kullanır (arama kotası harcamaz)."""
    yol = os.path.join(nis_klasoru or "", "nis_gecmis.db")
    if not os.path.exists(yol):
        return {}
    db = sqlite3.connect(yol)
    try:
        son = db.execute("SELECT MAX(ts) FROM kanal_kayit").fetchone()[0]
        if not son:
            return {}
        sinir = (na.zaman(son) - dt.timedelta(hours=2)).isoformat()
        ids = [r[0] for r in db.execute("SELECT DISTINCT video_id FROM anlik WHERE ts >= ?", (sinir,))]
    finally:
        db.close()
    detay = videolar_getir(ist, ids)
    an = simdi()
    detay = {k: v for k, v in detay.items() if (an - na.zaman(v["snippet"]["publishedAt"])).days <= 14}  # evergreen'ler hariç
    kanallar = {}
    kids = list({v["snippet"]["channelId"] for v in detay.values()})
    for i in range(0, len(kids), 50):
        for c in ist.get("channels", part="snippet,statistics,topicDetails", id=",".join(kids[i:i + 50])).get("items", []):
            kanallar[c["id"]] = c
    videolar = []
    for vid, v in detay.items():
        c = kanallar.get(v["snippet"]["channelId"])
        if not c:
            continue
        sn, st, cs = v["snippet"], v.get("statistics", {}), c.get("statistics", {})
        videolar.append(dict(
            id=vid, baslik=sn["title"], kanal=c["snippet"]["title"], kanal_id=c["id"], ulke=c["snippet"].get("country", ""),
            dil=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "", izlenme=int(st.get("viewCount", 0)),
            abone=None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0)),
            video_sayisi=int(cs.get("videoCount", 0)), kanal_izlenme=int(cs.get("viewCount", 0)),
            yayin_ts=sn["publishedAt"], sure=na.sure_sn(v.get("contentDetails", {}).get("duration")),
            etiketler=sn.get("tags", [])[:15], kaynak=na.KATEGORILER.get(sn.get("categoryId"), ""), puan=None,
            kapak=sn.get("thumbnails", {}).get("medium", {}).get("url", ""), kanal_yasi=None,
            yt_konular=na.yt_kategorileri(c)))
    return dict(surum=1, ts=son, videolar=videolar, konular=[], kaynak="nis_gecmis.db (yedek yol)")


# ---------- kart üretimi ----------
def konu_bul(video, konu_haritasi):
    """Videonun konusu (gruplama ve "nis" alanı için): Niş Avcısı'nın trend konusu varsa o; yoksa etiketler /
    YouTube kategorisi. Kanalın kendi adı olan etiketler (çok yaygın) konu sayılmaz."""
    if video["kanal_id"] in konu_haritasi:
        return konu_haritasi[video["kanal_id"]]
    kanal_adi = re.sub(r"\W", "", (video.get("kanal") or "").lower())
    etiketler = [e for e in (video.get("etiketler") or [])
                 if not kanal_adi or re.sub(r"\W", "", e.lower()) not in kanal_adi]
    k = dict(kanal_id=video["kanal_id"], basliklar=[[video["baslik"]] + etiketler],
             kategori=video.get("kaynak") or "", yt_konular=video.get("yt_konular") or [])
    return na.kanal_konusu(k, {})


def baslik_konusu(baslik, sinir=90):
    """Kartın "konu" alanı: kaynak başlığın hashtag/emoji temizlenmiş Türkçesi (kısa ve net)."""
    temiz = re.sub(r"#\S+|https?://\S+", " ", baslik)
    temiz = re.sub("[️‍⃣]", "", na.EMOJI.sub(" ", temiz))  # emoji ve kalıntı birleştirici karakterler
    temiz = re.sub(r"\s*[|｜]\s*.*$", "", temiz)          # "Konu | Kanal adı" → "Konu"
    temiz = re.sub(r"\s+", " ", temiz).strip(" -–—:") or baslik.strip()
    tr = (na.cevir(temiz, "tr") or temiz).strip()
    if len(tr) > sinir:
        tr = tr[:sinir].rsplit(" ", 1)[0] + "…"
    return tr[:1].upper() + tr[1:]


def baslik_kaliplari(basliklar):
    """Tutan başlıklardan gözlemlenen kalıplar (veriye dayalı; yaratıcı öneri değil)."""
    if not basliklar:
        return []
    n = len(basliklar)
    kaliplar = []
    oran = lambda kosul: sum(bool(kosul(b)) for b in basliklar) / n
    if oran(lambda b: re.search(r"\d", b)) >= .5:
        kaliplar.append("Sayı içeriyor (ör. 'Top 10…', '1000 TL ile…', '24 saatte…')")
    if oran(lambda b: "?" in b) >= .3:
        kaliplar.append("Soru soruyor (merak boşluğu: 'Neden…?', 'Ne olur…?')")
    if oran(lambda b: re.search(r"\b[A-ZÇĞİÖŞÜ]{3,}\b", b)) >= .3:
        kaliplar.append("Tek kelimeyi BÜYÜK HARFLE vurguluyor")
    if oran(lambda b: na.EMOJI.search(b)) >= .3:
        kaliplar.append("Emoji kullanıyor")
    if oran(lambda b: re.search(r"[|:–-]", b)) >= .4:
        kaliplar.append("İki parçalı başlık (konu | merak unsuru)")
    if oran(lambda b: re.search(r"\b(ben|i|my|we|biz)\b", b, re.I)) >= .4:
        kaliplar.append("Birinci ağızdan anlatım ('Denedim', 'I tried…')")
    ort = sum(len(b) for b in basliklar) / n
    kaliplar.append(f"Ortalama uzunluk {ort:.0f} karakter")
    return kaliplar


SORU_KELIMELERI = re.compile(r"\?|\b(nasıl|neden|niye|nedir|ne zaman|hangi|kaç|how|why|what|when|which|where)\b", re.I)


def izleyici_sorulari(ist, video_id, adet=5):
    """Kaynak videonun en beğenilen yorumlarından sorular (commentThreads.list, 1 birim). Kullanıcı adları alınmaz."""
    try:
        r = ist.get("commentThreads", part="snippet", videoId=video_id, order="relevance", maxResults=50,
                    textFormat="plainText")
    except (ButceDoldu, na.ApiHatasi):
        return None  # yorumlar kapalı olabilir ya da bütçe doldu: bilinmiyor
    sorular = []
    for t in r.get("items", []):
        s = t.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        metin = re.sub(r"\s+", " ", s.get("textDisplay", "")).strip()
        if 15 <= len(metin) <= 220 and SORU_KELIMELERI.search(metin) and "http" not in metin:
            sorular.append((s.get("likeCount", 0), metin))
    return [m for _, m in sorted(sorular, reverse=True)[:adet]]


def riskleri_bul(metin):
    m = metin or ""
    m = P.normallestir(m)
    riskler = [ad for ad, desen in RISK_KURALLARI if P._kelime_basi(desen).search(m)]
    aciklama = "; ".join(f"{r}: {RISK_ACIKLAMA[r]}" for r in riskler) or "Belirgin bir risk işareti bulunmadı."
    return riskler, aciklama


def kart_id_al(db, an):
    gun = an.strftime("%Y%m%d")
    r = db.execute("SELECT son FROM kart_sayac WHERE gun=?", (gun,)).fetchone()
    son = (r[0] if r else 0) + 1
    db.execute("INSERT OR REPLACE INTO kart_sayac VALUES(?,?)", (gun, son))
    return f"fk_{gun}_{son:04d}"


def firsatlari_hazirla(ist, db, adaylar, evergreen, ayar):
    """Niş Avcısı adaylarından fırsat listesi: patlamaya göre sıralı, konu başına tek fırsat.
    En iyi `pahali_olcum_konu_sayisi` konu için rekabet ve Türkçe boşluğu aramaları yapılır (günlük arama bütçesi)."""
    videolar = adaylar.get("videolar", [])
    if not videolar:
        return []
    konu_haritasi = {}
    for k in adaylar.get("konular", []):
        for kid in k.get("kanal_idler", []):
            konu_haritasi.setdefault(kid, k["terim"])
    konu_firsat = {k["terim"]: k.get("firsat") for k in adaylar.get("konular", [])}
    eg_idler = {x["id"] for x in evergreen.get("liste", [])}
    ist_kanal = kanal_istatistigi(ist, db, [v["kanal_id"] for v in videolar])
    detay = videolar_getir(ist, [v["id"] for v in videolar], "statistics")  # güncel beğeni/yorum
    an = simdi()
    for v in videolar:  # takip + ilk ölçüm (hız ve etkileşim referansı için)
        st = detay.get(v["id"], {}).get("statistics", {})
        takibe_al(db, v["id"], v["kanal_id"], v["yayin_ts"], "aday", nis=v.get("kaynak") or "",
                  format_=format_bul(v.get("sure")), baslik=v["baslik"])
        if st:
            yas = (an - na.zaman(v["yayin_ts"])).total_seconds() / 3600
            if not db.execute("SELECT 1 FROM olcum WHERE video_id=? AND hedef_saat IS NULL AND ts>=?",
                              (v["id"], iso(an - dt.timedelta(hours=20)))).fetchone():
                db.execute("INSERT INTO olcum VALUES(?,?,?,?,?,?,?)",
                           (v["id"], iso(an), yas, None, int(st.get("viewCount", 0)),
                            int(st["likeCount"]) if "likeCount" in st else None, int(st.get("commentCount", 0))))
            v["izlenme"] = int(st.get("viewCount", v["izlenme"]))
            v["begeni"] = int(st["likeCount"]) if "likeCount" in st else None
            v["yorum"] = int(st.get("commentCount", 0))
    db.commit()
    firsatlar, gorulen = [], set()
    for v in videolar:
        ki = ist_kanal.get(v["kanal_id"], {})
        oran = P.patlama_orani(v["izlenme"], ki.get("medyan"), ki.get("abone", v.get("abone")),
                               ki.get("video_sayisi", v.get("video_sayisi")))
        konu = konu_bul(v, konu_haritasi)
        anahtar = (konu or v["baslik"]).lower()
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        ayni_konu = [x for x in videolar if x is not v and konu and konu_bul(x, konu_haritasi) == konu][:2]
        firsatlar.append(dict(video=v, kanal=ki, oran=oran, konu=konu, anahtar=anahtar, ayni_konu=ayni_konu,
                              evergreen=v["id"] in eg_idler, nis_firsat=konu_firsat.get(konu)))
    firsatlar.sort(key=lambda f: -(f["oran"] or 0))
    # Pahalı ölçümler (arama kotası): sadece en iyi konular, bütçe izin verdikçe.
    a = argparse.Namespace(bolge="", dil="", max_kanal_yasi=365, max_abone=100_000, kota_yarim=False)
    for f in firsatlar[:ayar["pahali_olcum_konu_sayisi"]]:
        if not f["konu"]:
            continue
        try:
            if f["nis_firsat"]:  # Niş Avcısı bu konuyu zaten ölçmüş: arama harcama
                f["rekabet"] = f["nis_firsat"]
            elif ist.kota.izin("arama"):
                f["rekabet"] = na.firsat_olc(ist, a, [f["konu"]]).get(f["konu"])
            dil = (f["video"].get("dil") or "").lower()
            if not dil.startswith("tr") and ist.kota.izin("arama"):
                tr_konu = (na.cevir(f["konu"], "tr") or f["konu"]).lower()
                r = ist.get("search", part="id", type="video", q=tr_konu, regionCode="TR", relevanceLanguage="tr",
                            order="viewCount", maxResults=25,
                            publishedAfter=na.tarih_str(simdi() - dt.timedelta(days=365)))
                ids = [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
                izl = [int(x.get("statistics", {}).get("viewCount", 0)) for x in videolar_getir(ist, ids, "statistics").values()]
                f["turkce"] = dict(konu=tr_konu, sayi=len(ids), medyan=P._orta(izl) or 0)
        except (ButceDoldu, na.KotaBitti):  # arama kotası bitti: pahalı ölçümler durur, kartlar yine yazılır (null ile)
            break
        except na.ApiHatasi:
            continue
    return firsatlar


def kart_olustur(ist, db, f, profil, ayar, an, uyum):
    """Tek bir fırsattan tek bir kanal için fikir kartı (şema: FIKIR_KUTUSU.md)."""
    v, ki = f["video"], f["kanal"]
    format_ = format_bul(v.get("sure"))
    metin = " ".join([v["baslik"], f["konu"] or "", " ".join(v.get("etiketler") or []), v.get("kaynak") or ""])
    seri = olcum_serisi(db, v["id"])
    yas = (an - na.zaman(v["yayin_ts"])).total_seconds() / 3600
    pt, at = {}, []
    pt["patlama"], a1 = P.patlama(v["izlenme"], ki.get("medyan"), ki.get("abone", v.get("abone")),
                                  ki.get("video_sayisi", v.get("video_sayisi")))
    pt["hiz"], a2 = P.hiz(seri, referans_hizlar(db, v["id"], v.get("kaynak"), yas))
    pt["etkilesim"], a3 = P.etkilesim(v.get("begeni"), v.get("yorum"), v["izlenme"],
                                      nis_etkilesim_ortalamasi(db, v.get("kaynak")))
    rk = f.get("rekabet")
    pt["rekabet_boslugu"], a4 = P.rekabet_boslugu(rk["pay"] if rk else None, rk["toplam"] if rk else None,
                                                   rk["video"] if rk else None)
    tc = f.get("turkce")
    pt["turkce_boslugu"], a5 = P.turkce_boslugu(v.get("dil") or "", tc["sayi"] if tc else None,
                                                tc["medyan"] if tc else None)
    pt["omur"], a6 = P.omur(seri, v["baslik"], f["evergreen"])
    pt["kazanc_potansiyeli"], a7 = P.kazanc_potansiyeli(metin)
    pt["uretilebilirlik"], a8, yontem = P.uretilebilirlik(metin, format_)
    pt["kanal_uyumu"], a9 = uyum
    pt["toplam"] = P.toplam(pt, ayar["agirliklar"])
    puanlar = {k: pt.get(k) for k in ("toplam", "patlama", "hiz", "etkilesim", "rekabet_boslugu", "turkce_boslugu",
                                      "omur", "kazanc_potansiyeli", "uretilebilirlik", "kanal_uyumu")}
    aciklama = " ".join([f"Toplam {puanlar['toplam']}: ağırlıklı ortalama (null olanlar hariç)." if puanlar["toplam"]
                         is not None else "Toplam: hesaplanamadı.", a1, a2, a3, a4, a5, a6, a7, a8, a9])
    riskler, risk_ac = riskleri_bul(metin)
    gun = max(round(yas / 24), 0)
    ozet = (f"\"{v['kanal']}\" kanalının bu videosu {gun} günde {P._kisa(v['izlenme'])} izlendi"
            + (f"; bu, kanalın normalinin {f['oran']:.1f} katı" if f["oran"] else "") + ".")
    if f["nis_firsat"] or rk:
        ozet += f" Bu konuda son 30 günde izlenmelerin %{(rk or f['nis_firsat'])['pay'] * 100:.0f}'si yeni/küçük kanallara gidiyor."
    kaynaklar = [v] + f["ayni_konu"]
    kid = kart_id_al(db, an)
    kart = {
        "surum": SURUM, "id": kid, "kanal_id": profil["id"], "olusturma": iso(an), "guncelleme": iso(an),
        "durum": "yeni",
        "konu": baslik_konusu(v["baslik"]),
        "ozet": ozet,
        "nis": f"{v.get('kaynak') or ''}/{f['konu'] or ''}".strip("/") or None,
        "format": format_,
        "hedef_dil": profil.get("dil") or "tr",
        "onerilen_aci": None,          # yaratıcı alan: Kahve Stüdyo'daki Claude ajanı doldurur
        "kanca_onerileri": None,       # yaratıcı alan
        "baslik_kaliplari": baslik_kaliplari([k["baslik"] for k in kaynaklar]),
        "kapak_gozlemleri": None,      # görsel analiz gerekir: stüdyo doldurur
        "izleyici_sorulari": izleyici_sorulari(ist, v["id"]),
        "puanlar": puanlar,
        "puan_aciklamasi": aciklama,
        "uretim": {
            "tahmini_yontem": yontem,
            "gerekli_gorseller": None,  # yaratıcı alan: stüdyo doldurur
            "yapay_zeka_icerik_beyani_gerekli": yontem in ("yapay_zeka_video", "avatar"),
        },
        "riskler": riskler,
        "risk_aciklamasi": risk_ac,
        "platformlar": {"youtube": {"uygun": True}, "tiktok": {"uygun": None},
                        "instagram_reels": {"uygun": None}, "facebook_reels": {"uygun": None}},
        "kaynak_videolar": [kaynak_video(k, f["kanal"] if k is v else {}, k is v and f["oran"]) for k in kaynaklar],
    }
    return kart


def kaynak_video(v, ki, oran):
    return {"platform": "youtube", "id": v["id"], "baslik": v["baslik"], "kanal": v["kanal"],
            "abone": ki.get("abone", v.get("abone")), "izlenme": v.get("izlenme"),
            "kanal_medyan_izlenme": ki.get("medyan"), "yayin": v["yayin_ts"], "sure_sn": v.get("sure"),
            "dil": (v.get("dil") or None), "patlama_orani": round(oran, 2) if oran else None}


def kartlari_uret(ist, db, kanallar, ayar, klasor, firsatlar):
    """Fırsatları kanallara atar ve kart dosyalarını yazar. Her kanal günlük limitine kadar kart alır.
    Profiller boşsa (niş/anahtar kelime yok) fırsatlar aktif kanallara sırayla dağıtılır."""
    an = simdi()
    aktif = [p for p in kanallar if p.get("aktif")]
    bugun = an.strftime("%Y%m%d")
    sayac = {p["id"]: db.execute("SELECT COUNT(*) FROM kart WHERE kanal_id=? AND id LIKE ?",
                                 (p["id"], f"fk_{bugun}_%")).fetchone()[0] for p in aktif}
    esik, yazilan, sira = ayar["kanal_uyumu_esigi"], 0, 0
    for f in firsatlar:
        v = f["video"]
        metin = " ".join([v["baslik"], f["konu"] or "", " ".join(v.get("etiketler") or []), v.get("kaynak") or "",
                          na.cevir(f["konu"], "tr") if f["konu"] else ""])
        uyumlar = {p["id"]: P.kanal_uyumu(p, metin, format_bul(v.get("sure")), p.get("dil")) for p in aktif}
        hedefler = [p for p in aktif if uyumlar[p["id"]][0] is not None and uyumlar[p["id"]][0] >= esik]
        if not hedefler:
            # Dolu profillerin hiçbirine uymayan fikir, profili boş kanallara sırayla gider ("genel" kutu gibi).
            bos = [p for p in aktif if uyumlar[p["id"]][0] is None and sayac[p["id"]] < p.get("gunluk_fikir_limiti", 5)]
            if bos:
                hedefler = [bos[sira % len(bos)]]
                sira += 1
        for p in hedefler:
            if sayac[p["id"]] >= p.get("gunluk_fikir_limiti", 5):
                continue
            if db.execute("SELECT 1 FROM kart WHERE kanal_id=? AND ana_video=?", (p["id"], v["id"])).fetchone():
                continue
            uyum = uyumlar[p["id"]]
            if uyum[0] is None:
                uyum = (None, uyum[1] + " Profil boş olduğu için fikir bu kanala sırayla atandı.")
            try:
                kart = kart_olustur(ist, db, f, p, ayar, an, uyum)
            except ButceDoldu:
                return yazilan
            yol = os.path.join(klasor, p["id"], kart["id"] + ".json")
            guvenli_yaz(yol, kart)
            db.execute("INSERT INTO kart VALUES(?,?,?,?,?,?,?)",
                       (kart["id"], p["id"], v["id"], f["anahtar"], kart["olusturma"],
                        json.dumps(kart["puanlar"], ensure_ascii=False), os.path.relpath(yol, klasor)))
            db.commit()
            sayac[p["id"]] += 1
            yazilan += 1
    return yazilan


def kartlari_guncelle(ist, db, klasor):
    """Var olan kartlarda sadece istatistik güncellenir (durum alanına dokunulmaz).
    YouTube kuralı: API verisi 30 günden uzun saklanmaz → 30 günü geçen kartlarda istatistikler null yapılır."""
    an = simdi()
    yeni_ler, eski_ler = [], []
    for kid, dosya, olusturma in db.execute("SELECT id, dosya, olusturma FROM kart"):
        (yeni_ler if (an - na.zaman(olusturma)).days < 30 else eski_ler).append(os.path.join(klasor, dosya))
    for yol in eski_ler:
        k = json_oku(yol)
        if k and any(x.get("izlenme") is not None for x in k.get("kaynak_videolar", [])):
            for x in k["kaynak_videolar"]:
                for alan in ("abone", "izlenme", "kanal_medyan_izlenme", "patlama_orani"):
                    x[alan] = None
            k["guncelleme"] = iso(an)
            guvenli_yaz(yol, k)
    kartlar = [(y, json_oku(y)) for y in yeni_ler]
    ids = [x["id"] for _, k in kartlar if k for x in k.get("kaynak_videolar", [])]
    try:
        guncel = videolar_getir(ist, ids, "statistics") if ids else {}
    except ButceDoldu:
        return
    for yol, k in kartlar:
        if not k:
            continue
        degisti = False
        for x in k.get("kaynak_videolar", []):
            st = guncel.get(x["id"], {}).get("statistics")
            if st and int(st.get("viewCount", 0)) != x.get("izlenme"):
                x["izlenme"] = int(st.get("viewCount", 0))
                if x.get("kanal_medyan_izlenme"):
                    x["patlama_orani"] = round(x["izlenme"] / x["kanal_medyan_izlenme"], 2)
                degisti = True
        if degisti:
            k["guncelleme"] = iso(an)
            guvenli_yaz(yol, k)


# ---------- yayın saatleri ----------
def yayin_saatleri(db, kanallar, ayar, klasor):
    """Her kanal için: nişteki videoların yayın günü/saati (Europe/Istanbul) ile 3., 6. ve 24. saatteki hızları.
    Sadece kendi ölçümlerimizden; genel "en iyi saat" listelerinden değer uydurulmaz."""
    an = simdi()
    for p in [p for p in kanallar if p.get("aktif")]:
        nisler = [n.lower() for n in p.get("nisler", []) + p.get("anahtar_kelimeler", []) if n.strip()]
        satirlar = collections.defaultdict(lambda: collections.defaultdict(list))
        for vid, yayin, fmt, baslik, nis in db.execute("SELECT video_id, yayin_ts, format, baslik, nis FROM takip"):
            metin = f"{baslik} {nis}".lower()
            if nisler and not P.eslesenler(nisler, metin):  # tam kelime ("earn" ≠ "learn")
                continue
            seri = dict((round(y), i) for y, i in olcum_serisi(db, vid))
            hizlar = {}
            for h in (3, 6, 24):
                yakin = [(abs(y - h), i / y) for y, i in seri.items() if y and abs(y - h) <= h * .5]
                if yakin:
                    hizlar[h] = min(yakin)[1]
            if not hizlar:
                continue
            t = na.zaman(yayin).astimezone(TR)
            omur, _ = P.omur(list(seri.items()), baslik or "")
            grup = (fmt or "bilinmiyor", "hafta_sonu" if t.weekday() >= 5 else "hafta_ici", omur or "bilinmiyor")
            dilim = f"{t.hour // 3 * 3:02d}:00-{(t.hour // 3 * 3 + 3) % 24:02d}:00"
            satirlar[grup][dilim].append(hizlar)
        gruplar, toplam_ornek = [], 0
        for (fmt, gun_tipi, omur), dilimler in sorted(satirlar.items()):
            oneriler = []
            for dilim, liste in dilimler.items():
                n = len(liste)
                toplam_ornek += n
                oneriler.append(dict(
                    saat_araligi=dilim, ornek_sayisi=n,
                    medyan_hiz_3s=P._orta([x.get(3) for x in liste]), medyan_hiz_6s=P._orta([x.get(6) for x in liste]),
                    medyan_hiz_24s=P._orta([x.get(24) for x in liste]),
                    guven="yuksek" if n >= 15 else ("orta" if n >= 5 else "dusuk")))
            oneriler.sort(key=lambda o: -(o["medyan_hiz_24s"] or o["medyan_hiz_6s"] or o["medyan_hiz_3s"] or 0))
            gruplar.append(dict(format=fmt, gun_tipi=gun_tipi, omur=omur, ornek_sayisi=sum(o["ornek_sayisi"] for o in oneriler),
                                yetersiz_veri=sum(o["ornek_sayisi"] for o in oneriler) < 10, oneriler=oneriler))
        guvenli_yaz(os.path.join(klasor, "_yayin_saatleri", f"{p['id']}.json"), dict(
            surum=SURUM, kanal_id=p["id"], guncelleme=iso(an), saat_dilimi="Europe/Istanbul",
            yetersiz_veri=toplam_ornek < 10, ornek_sayisi=toplam_ornek,
            kapsam="kanalın nişleri" if nisler else "tüm nişler (kanal profili boş)",
            hiz_birimi="saat başına izlenme", gruplar=gruplar,
            not_="Değerler sadece Fikir Kutusu'nun kendi ölçümlerinden hesaplanır. Örnek sayısı düşükse güvenme."))


# ---------- geri bildirim ve ağırlık öğrenme ----------
def geri_bildirimi_degerlendir(ist, db, kanallar, ayar, veri):
    """Haftalık: stüdyonun yayınladığı videolarda kartın tahmini puanları ile gerçek sonuç (7. gün izlenmesi ÷ kendi
    kanalının medyanı) karşılaştırılır. Başarılı videolarda yüksek çıkan sinyallerin ağırlığı küçük ve sınırlı
    adımlarla artar, ters olanlarınki azalır. Her değişiklik kaydedilir (agirlik_gecmisi.json → geri alınabilir)."""
    ogren = ayar["agirlik_ogrenme"]
    ornekler = collections.defaultdict(list)
    for yid, fikir_id, kanal_id in db.execute("SELECT youtube_id, fikir_id, kanal_id FROM geri_bildirim"):
        r = db.execute("SELECT izlenme FROM olcum WHERE video_id=? AND hedef_saat=168", (yid,)).fetchone()
        k = db.execute("SELECT puanlar FROM kart WHERE id=?", (fikir_id,)).fetchone()
        if not r or not k:
            continue
        kanal_id_yt = db.execute("SELECT kanal_id FROM takip WHERE video_id=?", (yid,)).fetchone()
        medyan = None
        if kanal_id_yt and kanal_id_yt[0]:
            medyan = kanal_istatistigi(ist, db, [kanal_id_yt[0]]).get(kanal_id_yt[0], {}).get("medyan")
        if not medyan:
            continue
        ornekler[kanal_id].append((json.loads(k[0]), r[0] / medyan >= 1))
    ozet, tum = {}, [o for l in ornekler.values() for o in l]
    sinyaller = [s for s in ayar["agirliklar"] if s != "kazanc_potansiyeli"]

    def farklar(liste):
        sonuc = {}
        for s in sinyaller:
            iyi = [p[s] for p, b in liste if b and isinstance(p.get(s), (int, float))]
            kotu = [p[s] for p, b in liste if not b and isinstance(p.get(s), (int, float))]
            if iyi and kotu:
                sonuc[s] = sum(iyi) / len(iyi) - sum(kotu) / len(kotu)
        return sonuc
    for kanal_id, liste in ornekler.items():
        f = farklar(liste)
        if len(liste) >= 3 and f:
            en_iyi = sorted(f.items(), key=lambda x: -x[1])[:3]
            ozet[kanal_id] = (f"{len(liste)} yayından: başarılı videolarda en belirgin yüksek sinyaller " +
                              ", ".join(f"{s} (+{d:.0f})" for s, d in en_iyi if d > 0)) if any(d > 0 for _, d in en_iyi) \
                else f"{len(liste)} yayından: henüz belirgin bir sinyal yok."
    if len(tum) >= ogren["en_az_ornek"]:
        f = farklar(tum)
        eski = dict(ayar["agirliklar"])
        yeni = dict(eski)
        for s, d in f.items():
            if abs(d) >= 10:
                yeni[s] = min(ogren["en_cok"], max(ogren["en_az"], yeni[s] + (ogren["adim"] if d > 0 else -ogren["adim"])))
        top = sum(yeni.values())
        yeni = {s: round(w / top, 4) for s, w in yeni.items()}
        if yeni != eski:
            neden = f"{len(tum)} geri bildirimden; sinyal farkları: " + ", ".join(f"{s} {d:+.0f}" for s, d in f.items())
            db.execute("INSERT INTO agirlik_gecmisi VALUES(?,?,?,?)",
                       (iso(simdi()), json.dumps(eski), json.dumps(yeni), neden))
            db.commit()
            gecmis_yol = os.path.join(veri, "agirlik_gecmisi.json")
            gecmis = json_oku(gecmis_yol, []) or []
            gecmis.append(dict(ts=iso(simdi()), eski=eski, yeni=yeni, neden=neden,
                               geri_al="fikir_ayarlari.json içindeki 'agirliklar' alanına 'eski' değerleri yazılır"))
            guvenli_yaz(gecmis_yol, gecmis)
            ayar_yol = os.path.join(veri, "fikir_ayarlari.json")
            dosya = json_oku(ayar_yol, {})
            dosya["agirliklar"] = yeni
            guvenli_yaz(ayar_yol, dosya)
            ayar["agirliklar"] = yeni
    return ozet


# ---------- _bilgi.json ----------
def bilgi_yaz(db, kanallar, ayar, klasor, kota, uyarilar, sinyal_ozeti):
    onceki = json_oku(os.path.join(klasor, "_bilgi.json"), {}) or {}
    guvenli_yaz(os.path.join(klasor, "_bilgi.json"), {
        "surum": SURUM,
        "son_guncelleme": iso(simdi()),
        "kanallar": [dict(id=p["id"], ad=p.get("ad"), aktif=bool(p.get("aktif")), klasor=p["id"],
                          kart_sayisi=db.execute("SELECT COUNT(*) FROM kart WHERE kanal_id=?", (p["id"],)).fetchone()[0])
                     for p in kanallar],
        "kota": kota.durum(),
        "platformlar": ayar["platformlar"],
        "agirliklar": ayar["agirliklar"],
        "sinyal_ozeti": sinyal_ozeti or onceki.get("sinyal_ozeti", {}),
        "uyarilar": uyarilar,
        "sozlesme": "FIKIR_KUTUSU.md (Niş Avcısı deposu)",
    })


# ---------- ana akış ----------
def calistir(veri, nis_klasoru, gunluk=False, haftalik=False, yt=None):
    kanallar, ayar = ayar_yukle(veri)
    klasor = os.path.join(veri, ayar["klasor"])
    for p in kanallar:
        os.makedirs(os.path.join(klasor, p["id"]), exist_ok=True)
    os.makedirs(os.path.join(klasor, "_geri_bildirim"), exist_ok=True)
    os.makedirs(os.path.join(klasor, "_yayin_saatleri"), exist_ok=True)
    db = db_ac(os.path.join(veri, "fk_veri.db"))
    kota = Kota(db, ayar)
    if yt is None:
        key = os.environ.get("YT_API_KEY")
        if not key:
            sys.exit("API anahtarı yok: YT_API_KEY ortam değişkenini ayarla.")
        yt = na.YT(key)
    ist = Istemci(yt, kota)
    _, uyarilar = fk_platformlar.etkin_adaptorler(ayar["platformlar"])  # kapalıyken hiçbir istek atılmaz
    sinyal_ozeti = None
    try:
        gb = geri_bildirim_oku(db, klasor)
        yeni = takip_kanallarini_tara(ist, db, kanallar, ayar)
        olculen = olcumleri_yap(ist, db, ayar["olcum_saatleri"])
        print(f"Geri bildirim: {gb} yeni · takip edilen kanallardan {yeni} yeni video · {olculen} ölçüm")
        adaylar, evergreen = adaylari_oku(nis_klasoru)
        if not adaylar.get("ts") and (gunluk or not islem_son(db, "gunluk")):
            adaylar = adaylari_dbden_kur(ist, nis_klasoru)
        if adaylar.get("ts") and (gunluk or (islem_son(db, "gunluk") or "") < adaylar["ts"]):
            print("Günlük fikir üretimi...")
            firsatlar = firsatlari_hazirla(ist, db, adaylar, evergreen, ayar)
            yazilan = kartlari_uret(ist, db, kanallar, ayar, klasor, firsatlar)
            kartlari_guncelle(ist, db, klasor)
            yayin_saatleri(db, kanallar, ayar, klasor)
            islem_yaz(db, "gunluk", adaylar["ts"])
            print(f"{len(firsatlar)} fırsat · {yazilan} yeni fikir kartı")
        son_hafta = islem_son(db, "haftalik")
        if haftalik or not son_hafta or (simdi() - na.zaman(son_hafta)).days >= 7:
            sinyal_ozeti = geri_bildirimi_degerlendir(ist, db, kanallar, ayar, veri)
            islem_yaz(db, "haftalik", iso(simdi()))
    except ButceDoldu as b:
        uyarilar.append(f"Günlük {b} bütçesi doldu; kalan işler sonraki çalışmada yapılacak.")
    except na.KotaBitti:
        uyarilar.append("YouTube günlük kotası doldu; kalan işler kota yenilenince yapılacak.")
    finally:
        bilgi_yaz(db, kanallar, ayar, klasor, kota, uyarilar, sinyal_ozeti)
        db.close()
    print(f"Kota: {kota.arama}/{ayar['kota']['gunluk_arama_butcesi']} arama, "
          f"{kota.birim}/{ayar['kota']['gunluk_birim_butcesi']} birim" + (" · önemli mod" if kota.onemli_mod() else ""))
    for u in uyarilar:
        print("!", u)


def main():
    p = argparse.ArgumentParser(description="Fikir Kutusu: fırsatları kanallar için fikir kartlarına çevirir")
    p.add_argument("--veri", required=True, help="Özel depo klasörü (kanallar.json, fikir_ayarlari.json, fikir_kutusu/)")
    p.add_argument("--nis", default=KOD_KLASORU, help="Niş Avcısı klasörü (site/adaylar.json için)")
    p.add_argument("--gunluk", action="store_true", help="Fikir üretimini şimdi zorla yap")
    p.add_argument("--haftalik", action="store_true", help="Geri bildirim değerlendirmesini şimdi zorla yap")
    a = p.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    calistir(a.veri, a.nis, a.gunluk, a.haftalik)


if __name__ == "__main__":
    main()
