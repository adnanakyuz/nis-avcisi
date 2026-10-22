#!/usr/bin/env python3
"""
NİŞ AVCISI — YouTube'da son 1 yılda açılmış, az videoyla çok izlenen kanalları ve
yükselen konuları bulur. GitHub Actions'ta çalışır (.github/workflows/tara.yml),
sonucu GitHub Pages'te telefona uygun bir sayfa olarak yayınlar.

Elle çalıştırmak için (Python 3.8+, ek paket gerekmez; YT_API_KEY ortam değişkeni gerekli):
  python nis_avcisi.py                          # genel tarama: global, son 14 gün → site/index.html
  python nis_avcisi.py --konu "tarih, kripto"   # konulu tarama → site/konu/<konu>.html
  python nis_avcisi.py --bolge TR --dil tr      # sadece Türkiye / Türkçe içerik
  python nis_avcisi.py --kanal "https://youtube.com/@kanal"   # tek kanal incelemesi → site/kanal/<id>.html (~210 birim)

Kota (15 Ekim 2026'dan beri): arama (search.list) ayrı kotada, günde 100 çağrı; diğer istekler 10.000 birim.
Genel tarama ~50 arama (12 kategori × 2 zaman dilimi global + 12 kategori Türkiye + Finans 3 + 5 konunun fırsat
ölçümü + en güçlü 3 kanalın İngilizce ve Türkiye pazar ölçümü). Haftada bir +12 (evergreen). Global konulu
tarama konu başına ~17 arama; kanal incelemesi 2. Fikir Kutusu en fazla 20. Çeviriler kota harcamaz.
(Sayfadaki "kota birimi" sayısı eski kurala göre hesaplanır: arama başına 100 sayar.)
"""
import argparse, collections, concurrent.futures, datetime as dt, html, json, math, os, re, sqlite3, sys, time
import urllib.error, urllib.parse, urllib.request

API = "https://www.googleapis.com/youtube/v3/"
# Müzik ve Haber bilerek yok: plak şirketleri / büyük haber kanalları niş sinyali vermiyor.
KATEGORILER = {
    "1": "Film & Animasyon", "2": "Otomobil", "15": "Hayvanlar", "17": "Spor",
    "19": "Seyahat", "20": "Oyun", "22": "Kişiler & Bloglar", "23": "Komedi",
    "24": "Eğlence", "26": "Nasıl Yapılır & Stil", "27": "Eğitim", "28": "Bilim & Teknoloji",
}
STOP = set("""
ve ile bir bu şu o da de mi mu mü mı için gibi çok en ne ki ama veya hem daha her
nasıl neden niye kadar sonra önce olan oldu olur ben sen biz siz onlar benim senin
var yok değil ya yani artık hiç tüm bütün bunu şunu onu bunlar şey olarak ilk son yeni
the a an and or of to in on for with at by from is are was be this that it its my your
you i we how why what when who vs part full video videos official new shorts short
episode ep day days best top 2024 2025 2026 live amp quot feat ft lyrics music hd 4k
into out get got can will just about after like our their they them his her not all
have has had one two more most very really only these those than then there
beni bana sana seni bize size bile çünkü şimdi bugün yarın dün
""".split())
# Tek başına niş olamayacak kadar genel kelimeler: sadece ikili ifadelerin parçası olabilirler.
GENEL = set("""
funny love baby viral challenge prank trending comedy song game games family kids cute
reaction vlog story life world first time real cat dog asmr satisfying edit trend meme
komik aşk bebek oyun aile çocuk eğlenceli hikaye hayat dünya kedi köpek tepki
""".split())
# YouTube kategorisi olmayan ama genel taramada her gün aranan konular: ad → (global sorgu, Türkçe sorgu).
# "|" YouTube aramasında "veya" demektir (resmi doküman). Her konu günde 3 arama harcar.
SABIT_KONULAR = {
    "Finans": ("personal finance|investing|stock market|money tips|crypto", "finans|borsa|yatırım|ekonomi|kripto"),
}
TARA_LINKI = "https://github.com/adnanakyuz/nis-avcisi/actions/workflows/tara.yml"
ONERI_API = "https://suggestqueries.google.com/complete/search"
CEVIRI_API = "https://translate.googleapis.com/translate_a/single"
TR_SAAT = dt.timezone(dt.timedelta(hours=3))  # Türkiye'de yaz saati yok
KOTA_NOTU = "Kota her gün sabah 10–11 arası yenilenir."


class KotaBitti(Exception):
    pass


class ApiHatasi(Exception):
    pass


class YT:
    def __init__(self, key):
        self.key, self.harcanan = key, 0

    def get(self, ucnokta, maliyet=1, **p):
        p["key"] = self.key
        url = API + ucnokta + "?" + urllib.parse.urlencode({k: v for k, v in p.items() if v is not None})
        for deneme in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    self.harcanan += maliyet
                    return json.load(r)
            except urllib.error.HTTPError as e:
                self.harcanan += maliyet  # hatalı istekler de kotadan düşer
                govde = e.read().decode("utf-8", "ignore").lower()
                if "quotaexceeded" in govde or "dailylimitexceeded" in govde:
                    raise KotaBitti("Günlük API kotası doldu.")
                if "keyinvalid" in govde or "api key not valid" in govde:
                    sys.exit("API anahtarı geçersiz. YT_API_KEY değerini kontrol et.")
                if e.code >= 500 and deneme < 2:
                    time.sleep(2 * (deneme + 1))
                    continue
                raise ApiHatasi(f"YouTube API hatası ({e.code}): {govde[:400]}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if deneme < 2:
                    time.sleep(2 * (deneme + 1))
                    continue
                raise ApiHatasi(f"Bağlantı hatası: {e}")


# ---------- yardımcılar ----------
def sure_sn(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def zaman(s):
    # YouTube bazen kesirli saniye döndürür (ör. ".85Z"); eski Python'lar bunu okuyamaz.
    s = re.sub(r"\.\d+", "", s).replace("Z", "+00:00")
    return dt.datetime.fromisoformat(s)


def kisa(n):
    n = float(n or 0)
    for esik, harf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= esik:
            return f"{n / esik:.1f}".rstrip("0").rstrip(".") + harf
    return str(int(n))


def kelimeler(metin):
    # "İ".lower() → "i̇" (birleşik nokta) olur ve kelimeyi ikiye böler; önce düzelt.
    metin = metin.replace("İ", "i").lower().replace("̇", "")
    metin = re.sub(r"[#@]\w+|https?://\S+", " ", metin)
    return [w for w in re.findall(r"[a-zçğıöşüâîû0-9]+", metin) if len(w) > 2 and w not in STOP and not w.isdigit()]


def cevir(metin, hedef, kaynak="auto"):
    """Ücretsiz çeviri servisi (kota harcamaz); ulaşılamazsa boş döner, program çevirisiz devam eder."""
    url = CEVIRI_API + "?" + urllib.parse.urlencode(dict(client="gtx", sl=kaynak, tl=hedef, dt="t", q=metin))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return "".join(p[0] for p in json.load(r)[0]).strip()
    except Exception:
        return ""


def ingilizce(metin):
    """Türkçe konuyu İngilizceye çevir; global aramada dünya genelindeki videolar da gelsin.
    Kaynak dil sabit Türkçe: "auto" kısa kelimeleri yanlış tanıyor ("borsa" → İtalyanca "purse")."""
    return cevir(metin, "en", "tr").lower()


def turkceye(metinler):
    """Yabancı metinleri toplu ve paralel olarak Türkçeye çevir. Zaten Türkçe olanlar boş döner."""
    metinler = list(dict.fromkeys(m for m in metinler if m))
    with concurrent.futures.ThreadPoolExecutor(8) as havuz:
        sonuc = dict(zip(metinler, havuz.map(lambda m: cevir(m, "tr"), metinler)))
    return {m: t for m, t in sonuc.items() if t and t.lower() != m.lower()}


# Reklam gelirinin izleyici ülkesine göre kabaca seviyesi (1.000 izlenme başına kazanç).
# Kesin değil: konuya ve döneme göre değişir; finans/teknoloji gibi konular her ülkede daha yüksektir.
KAZANC_ULKE = {
    "yuksek": "US GB CA AU NZ DE CH AT NO SE DK FI NL BE IE LU IS SG JP AE QA IL",
    "orta": "FR ES IT PT KR TW HK SA KW BH OM PL CZ SK SI HU EE LV LT HR GR CL UY CY MT ZA",
    "dusuk": "TR BR MX AR CO PE RU UA RO BG RS TH MY MA DZ TN JO LB",
    "cok_dusuk": "IN PK BD ID PH VN NG EG KE NP LK MM KH GH ET",
}
KAZANC_ULKE = {u: s for s, l in KAZANC_ULKE.items() for u in l.split()}
# Ülke belirtilmemişse videonun dilinden tahmin.
KAZANC_DIL = {"en": "yuksek", "de": "yuksek", "ja": "yuksek", "nl": "yuksek", "sv": "yuksek", "no": "yuksek",
              "fr": "orta", "it": "orta", "ko": "orta", "pl": "orta", "es": "dusuk", "pt": "dusuk", "tr": "dusuk",
              "ru": "dusuk", "ar": "dusuk", "th": "dusuk", "hi": "cok_dusuk", "bn": "cok_dusuk", "ur": "cok_dusuk",
              "ta": "cok_dusuk", "te": "cok_dusuk", "mr": "cok_dusuk", "id": "cok_dusuk", "vi": "cok_dusuk",
              "fil": "cok_dusuk", "tl": "cok_dusuk"}
KAZANC_ETIKET = {"yuksek": "💰💰💰 Yüksek kazanç", "orta": "💰💰 Orta kazanç",
                 "dusuk": "💰 Düşük kazanç", "cok_dusuk": "💰 Çok düşük kazanç"}
KAZANC_SIRA = {"yuksek": 0, "orta": 1, "dusuk": 2, "cok_dusuk": 3}
ULKE_AD = {"US": "ABD", "GB": "İngiltere", "CA": "Kanada", "AU": "Avustralya", "DE": "Almanya", "TR": "Türkiye",
           "IN": "Hindistan", "PK": "Pakistan", "BD": "Bangladeş", "ID": "Endonezya", "PH": "Filipinler",
           "BR": "Brezilya", "MX": "Meksika", "ES": "İspanya", "FR": "Fransa", "IT": "İtalya", "RU": "Rusya",
           "VN": "Vietnam", "NG": "Nijerya", "EG": "Mısır", "JP": "Japonya", "KR": "Güney Kore", "SA": "S. Arabistan",
           "AE": "BAE", "AR": "Arjantin", "CO": "Kolombiya", "TH": "Tayland", "MY": "Malezya", "NL": "Hollanda"}


def kazanc_seviyesi(ulke, dil):
    if ulke in KAZANC_ULKE:
        return KAZANC_ULKE[ulke]
    return KAZANC_DIL.get((dil or "").split("-")[0].lower())


def bayrak(ulke):
    if not ulke or len(ulke) != 2 or not ulke.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(h) - 65) for h in ulke.upper())


def ifadeler(parcalar, haric=(), tekli=True):
    """Başlık ve etiketlerin her biri ayrı ele alınır: "...decor idea" başlığı ile "home decor" etiketi
    yan yana gelip "idea home" gibi sahte ifadeler üretmesin. 1, 2 ve 3 kelimelik terimleri döndürür."""
    terimler = set()
    for parca in parcalar:
        ws = [w for w in kelimeler(parca) if not any(w == h or (len(h) >= 4 and w.startswith(h)) for h in haric)]
        if tekli:
            terimler |= {w for w in ws if w not in GENEL}
        terimler |= {f"{a} {b}" for a, b in zip(ws, ws[1:])}
        terimler |= {f"{a} {b} {c}" for a, b, c in zip(ws, ws[1:], ws[2:])}
    return terimler


DIL_AD = {"en": "İngilizce", "tr": "Türkçe", "ur": "Urduca", "hi": "Hintçe", "bn": "Bengalce", "es": "İspanyolca",
          "pt": "Portekizce", "ar": "Arapça", "de": "Almanca", "fr": "Fransızca", "ru": "Rusça", "id": "Endonezce",
          "it": "İtalyanca", "ja": "Japonca", "ko": "Korece", "ta": "Tamilce", "te": "Telugu dili", "vi": "Vietnamca",
          "th": "Tayca", "fil": "Filipince", "pl": "Lehçe", "nl": "Felemenkçe"}


def yt_kategorileri(kanal):
    """YouTube'un kanala verdiği konu kategorileri, okunur halde: "Lifestyle_(sociology)" → "lifestyle"."""
    return [re.sub(r"\s*\(.*?\)", "", u.rsplit("/", 1)[-1].replace("_", " ")).strip().lower()
            for u in kanal.get("topicDetails", {}).get("topicCategories", [])]


def dil_adi(kod):
    return DIL_AD.get((kod or "").split("-")[0].lower(), kod or "belirtilmemiş")


def slug(metin):
    metin = metin.translate(str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosucgiosuaiu")).lower()
    return re.sub(r"[^a-z0-9]+", "-", metin).strip("-")[:60] or "konu"


def json_yaz(yol, veri):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)


def json_oku(yol, varsayilan):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return varsayilan


# ---------- veritabanı ----------
def db_ac(yol):
    db = sqlite3.connect(yol)
    db.executescript("""
    DROP TABLE IF EXISTS video;
    CREATE TABLE IF NOT EXISTS anlik(video_id TEXT, ts TEXT, izlenme INTEGER);
    CREATE INDEX IF NOT EXISTS ix_anlik ON anlik(video_id, ts);
    CREATE TABLE IF NOT EXISTS terim(ts TEXT, terim TEXT, kanal_sayisi INTEGER, video_sayisi INTEGER, toplam INTEGER);
    CREATE INDEX IF NOT EXISTS ix_terim ON terim(terim, ts);
    CREATE TABLE IF NOT EXISTS kanal_kayit(ts TEXT, kanal_id TEXT, kanal TEXT, izlenme INTEGER, abone INTEGER,
                                           video_sayisi INTEGER);
    CREATE INDEX IF NOT EXISTS ix_kanal ON kanal_kayit(kanal_id, ts);
    CREATE TABLE IF NOT EXISTS firsat_kayit(ts TEXT, terim TEXT, seviye TEXT, pay REAL);
    CREATE INDEX IF NOT EXISTS ix_firsat ON firsat_kayit(terim, ts);
    """)
    return db


def eski_sil(db, gun=30):
    """YouTube API kuralları: API'den gelen veri 30 günden uzun saklanmaz."""
    sinir = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=gun)).isoformat()
    for tablo in ("anlik", "terim", "kanal_kayit", "firsat_kayit"):
        db.execute(f"DELETE FROM {tablo} WHERE ts < ?", (sinir,))
    db.commit()


def kanal_kaydet(db, kanallar, firsatlar=()):
    """Bu taramada görülen kanalların ve ölçülen konu fırsatlarının anlık kaydı (karne için)."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    db.executemany("INSERT INTO kanal_kayit VALUES(?,?,?,?,?,?)",
                   [(ts, k["kanal_id"], k["kanal"], k["kanal_izlenme"], k["abone"], k["video_sayisi"]) for k in kanallar])
    db.executemany("INSERT INTO firsat_kayit VALUES(?,?,?,?)",
                   [(ts, t, f["seviye"], f["pay"]) for t, f in firsatlar if f])
    db.commit()


def ilk_gorulme(db):
    """kanal_id → ilk görüldüğü zaman (ISO)."""
    return dict(db.execute("SELECT kanal_id, MIN(ts) FROM kanal_kayit GROUP BY kanal_id"))


def karne_hesapla(db, yt):
    """Geçmiş karnesi: 3–30 gün önce bulduğumuz kanallar ve 🟢/🟡 dediğimiz konular bugün ne durumda?
    Kanalların güncel izlenmesi için 50 kanal başına 1 kota birimi harcanır (en fazla 100 kanal = 2 birim)."""
    simdi = dt.datetime.now(dt.timezone.utc)
    ust = (simdi - dt.timedelta(days=3)).isoformat()
    ilkler = db.execute("""SELECT k.kanal_id, k.kanal, k.izlenme, k.ts FROM kanal_kayit k
                           JOIN (SELECT kanal_id, MIN(ts) ilk FROM kanal_kayit GROUP BY kanal_id) m
                             ON m.kanal_id = k.kanal_id AND m.ilk = k.ts
                           WHERE k.ts < ? ORDER BY k.ts LIMIT 100""", (ust,)).fetchall()
    kanallar = []
    if ilkler:
        guncel = {}
        try:
            ids = [r[0] for r in ilkler]
            for i in range(0, len(ids), 50):
                for c in yt.get("channels", 1, part="statistics", id=",".join(ids[i:i + 50])).get("items", []):
                    guncel[c["id"]] = int(c.get("statistics", {}).get("viewCount", 0))
        except (KotaBitti, ApiHatasi):
            pass
        for kid, ad, ilk_izl, ts in ilkler:
            if kid in guncel and ilk_izl:
                kanallar.append(dict(kanal_id=kid, kanal=ad, ilk=ilk_izl, simdi=guncel[kid],
                                     buyume=guncel[kid] / ilk_izl - 1, gun=(simdi - zaman(ts)).days))
    kanallar.sort(key=lambda k: -k["buyume"])
    konular = []
    for terim, ts, seviye in db.execute("""SELECT f.terim, f.ts, f.seviye FROM firsat_kayit f
                                           JOIN (SELECT terim, MIN(ts) ilk FROM firsat_kayit GROUP BY terim) m
                                             ON m.terim = f.terim AND m.ilk = f.ts
                                           WHERE f.ts < ? AND f.seviye IN ('bos', 'orta')""", (ust,)):
        ilk = db.execute("SELECT kanal_sayisi FROM terim WHERE terim=? ORDER BY ts LIMIT 1", (terim,)).fetchone()
        son = db.execute("SELECT kanal_sayisi, ts FROM terim WHERE terim=? ORDER BY ts DESC LIMIT 1", (terim,)).fetchone()
        if ilk and son:
            konular.append(dict(terim=terim, seviye=seviye, gun=(simdi - zaman(ts)).days,
                                ilk=ilk[0], son=son[0], son_gun=(simdi - zaman(son[1])).days))
    if not kanallar and not konular:
        en_eski = db.execute("SELECT MIN(ts) FROM kanal_kayit").fetchone()[0]
        return dict(bos=True, gun=(simdi - zaman(en_eski)).days if en_eski else 0)
    iki_kat = sum(k["buyume"] >= 1 for k in kanallar)
    return dict(bos=False, kanallar=kanallar, konular=konular,
                orta_buyume=orta_deger([k["buyume"] for k in kanallar]) if kanallar else 0,
                iki_kat_oran=iki_kat / len(kanallar) if kanallar else 0)


def hafta_ozeti(db):
    """Son 7 günde kanal sayısı en çok artan konular (kota harcamaz)."""
    sinir = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    satirlar = collections.defaultdict(list)
    for terim, ts, ks, top in db.execute("SELECT terim, ts, kanal_sayisi, toplam FROM terim WHERE ts >= ? ORDER BY ts",
                                         (sinir,)):
        satirlar[terim].append((ks, top))
    sonuc = []
    for terim, l in satirlar.items():
        sonuc.append(dict(terim=terim, gorulme=len(l), artis=l[-1][0] - l[0][0], kanal=l[-1][0], toplam=l[-1][1]))
    sonuc.sort(key=lambda x: (-x["artis"], -x["gorulme"], -x["toplam"]))
    return [x for x in sonuc if x["artis"] > 0 or x["gorulme"] >= 2][:5]


def sira_disi(sonuc, esik=3, adet=10):
    """Kanalının normalinin `esik` katından fazla izlenen videolar (kota harcamaz).
    Normal = videonun kendisi hariç, kanalın video başına ortalama izlenmesi."""
    l = []
    for s in sonuc:
        if s["video_sayisi"] < 3:
            continue
        normal = (s["kanal_izlenme"] - s["izlenme"]) / (s["video_sayisi"] - 1)
        if normal > 0 and s["izlenme"] / normal >= esik:
            l.append(dict(s, oran=s["izlenme"] / normal, normal=normal))
    l.sort(key=lambda x: -x["oran"])
    tekil, gorulen = [], set()
    for x in l:  # kanal başına en fazla 1 video
        if x["kanal_id"] not in gorulen:
            gorulen.add(x["kanal_id"])
            tekil.append(x)
    return tekil[:adet]


# ---------- evergreen (kalıcı içerik) ----------
def evergreen_tara(yt, a, adet=30):
    """Haftada bir: 6 ay – 2 yıl önce yüklenmiş ama hâlâ çok izlenen, küçük kanallara ait videolar.
    12 kategori × 100 birim ≈ 1.250 birim. Kanal yaşı sınırı yok (video eski, kanal da eski olabilir)."""
    simdi = dt.datetime.now(dt.timezone.utc)
    bas, bit = tarih_str(simdi - dt.timedelta(days=730)), tarih_str(simdi - dt.timedelta(days=180))
    kaynak = {}
    for kid, ad in KATEGORILER.items():
        try:
            r = yt.get("search", 100, part="id", type="video", order="viewCount", publishedAfter=bas,
                       publishedBefore=bit, videoCategoryId=kid, maxResults=50)
        except ApiHatasi:
            continue
        except KotaBitti:
            a.kota_yarim = True
            break
        for i in r.get("items", []):
            v = i.get("id", {}).get("videoId")
            if v and v not in kaynak:
                kaynak[v] = ad
        print(f"  🌲 {ad}")
    ids = list(kaynak)
    videolar, kanallar = {}, {}
    try:
        for i in range(0, len(ids), 50):
            for v in yt.get("videos", 1, part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).get("items", []):
                videolar[v["id"]] = v
        kids = list({v["snippet"]["channelId"] for v in videolar.values()})
        for i in range(0, len(kids), 50):
            for c in yt.get("channels", 1, part="snippet,statistics,topicDetails", id=",".join(kids[i:i + 50])).get("items", []):
                kanallar[c["id"]] = c
    except (KotaBitti, ApiHatasi):
        a.kota_yarim = True
    liste = []
    for vid, v in videolar.items():
        c = kanallar.get(v["snippet"]["channelId"])
        if not c or v["snippet"].get("liveBroadcastContent", "none") != "none":
            continue
        st, cs = v.get("statistics", {}), c.get("statistics", {})
        izl = int(st.get("viewCount", 0))
        abone = None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0))
        if izl < 200_000 or (abone is not None and abone > 250_000) or c["snippet"]["title"].endswith((" - Topic", "VEVO")):
            continue
        if "likeCount" in st and (int(st["likeCount"]) + int(st.get("commentCount", 0))) / izl < 0.0015:
            continue  # reklamla şişmiş
        yas = max((simdi - zaman(v["snippet"]["publishedAt"])).days, 1)
        gunluk = izl / yas
        liste.append(dict(
            id=vid, baslik=v["snippet"]["title"], kapak=v["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            kanal=c["snippet"]["title"], kanal_id=c["id"], ulke=c["snippet"].get("country", ""),
            dil=v["snippet"].get("defaultAudioLanguage") or v["snippet"].get("defaultLanguage") or "",
            izlenme=izl, abone=abone, video_sayisi=int(cs.get("videoCount", 0)), kanal_izlenme=int(cs.get("viewCount", 0)),
            yayin_ts=v["snippet"]["publishedAt"], yas_gun=yas, gunluk_ort=gunluk, kaynak=kaynak[vid],
            etiketler=v["snippet"].get("tags", [])[:15], yt_konular=yt_kategorileri(c),
            sure=sure_sn(v.get("contentDetails", {}).get("duration")),
            puan=math.log10(gunluk + 1) + .5 * math.log10(izl / max(abone or 0, 100) + 1)))
    liste.sort(key=lambda x: -x["puan"])
    tekil, gorulen = [], set()
    for x in liste:  # kanal başına 1 video
        if x["kanal_id"] not in gorulen:
            gorulen.add(x["kanal_id"])
            tekil.append(x)
    return tekil[:adet]


def evergreen_guncelle(yt, db, liste):
    """Her gün: evergreen videoların güncel izlenmesi (50 video = 1 birim) ve son ölçümden beri günlük kazanç.
    "1,5 yıllık video hâlâ günde 12 bin izlenme alıyor" = kalıcı olduğunun gerçek kanıtı."""
    if not liste:
        return
    simdi = dt.datetime.now(dt.timezone.utc)
    guncel = {}
    try:
        ids = [x["id"] for x in liste]
        for i in range(0, len(ids), 50):
            for v in yt.get("videos", 1, part="statistics", id=",".join(ids[i:i + 50])).get("items", []):
                guncel[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))
    except (KotaBitti, ApiHatasi):
        return
    ts = simdi.isoformat()
    for x in liste:
        if x["id"] not in guncel:
            continue
        onceki = db.execute("SELECT ts, izlenme FROM anlik WHERE video_id=? ORDER BY ts DESC LIMIT 1", (x["id"],)).fetchone()
        x["izlenme"] = guncel[x["id"]]
        x["yas_gun"] = max((simdi - zaman(x["yayin_ts"])).days, 1)
        x["gunluk_ort"] = x["izlenme"] / x["yas_gun"]
        if onceki:
            gun = (simdi - zaman(onceki[0])).total_seconds() / 86400
            if gun >= .5:
                x["gunluk_son"] = max(guncel[x["id"]] - onceki[1], 0) / gun
                x["olcum_gun"] = round(gun)
        db.execute("INSERT INTO anlik VALUES(?,?,?)", (x["id"], ts, guncel[x["id"]]))
    db.commit()


def evergreen_konular(liste, adet=6):
    """Evergreen videolarda birden fazla kanalın ortak işlediği konular (kota harcamaz)."""
    kanal, toplam = collections.defaultdict(set), collections.Counter()
    for x in liste:
        for t in ifadeler([x["baslik"]] + x["etiketler"]):
            if " " in t:
                kanal[t].add(x["kanal_id"])
                toplam[t] += x["izlenme"]
    adaylar = sorted((t for t in kanal if len(kanal[t]) >= 2), key=lambda t: (-len(kanal[t]), -toplam[t]))
    secilen = []
    for t in adaylar:
        if not any(set(t.split()) & set(s.split()) for s, _, _ in secilen):
            secilen.append((t, len(kanal[t]), toplam[t]))
        if len(secilen) >= adet:
            break
    return secilen


def kalicilik_olc(yt, a, terim):
    """Konulu arama için: 6 ay – 2 yıl önceki videolar hâlâ çok izleniyor mu? (100 birim)
    Eski videoların tipik izlenmesi yüksekse konu kalıcıdır (evergreen), düşükse geçici bir trenddir."""
    simdi = dt.datetime.now(dt.timezone.utc)
    try:
        r = yt.get("search", 100, part="id", type="video", order="viewCount", q=terim,
                   publishedAfter=tarih_str(simdi - dt.timedelta(days=730)),
                   publishedBefore=tarih_str(simdi - dt.timedelta(days=180)),
                   regionCode=a.bolge or None, relevanceLanguage=a.dil or None, maxResults=25)
        ids = [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
        izl = [int(v.get("statistics", {}).get("viewCount", 0))
               for v in yt.get("videos", 1, part="statistics", id=",".join(ids)).get("items", [])] if ids else []
    except (KotaBitti, ApiHatasi):
        return None
    medyan = orta_deger(izl)
    if len(izl) >= 5 and medyan >= 100_000:
        seviye, etiket = "kalici", "🌲 Kalıcı konu"
    elif len(izl) >= 5 and medyan >= 20_000:
        seviye, etiket = "yari", "🌗 Yarı kalıcı"
    else:
        seviye, etiket = "gecici", "⚡ Daha çok geçici trend"
    return dict(seviye=seviye, etiket=etiket, medyan=medyan, adet=len(izl))


SORU_ON_EK = {"tr": ["nasıl", "neden", "en iyi", "nedir"], "en": ["how to", "why", "what is", "best"]}


def video_fikirleri(terimler, adet=6):
    """Her konu için insanların YouTube'da gerçekten aradığı sorular → video fikri (YouTube kotası harcamaz)."""
    def getir(terim):
        dil = "tr" if re.search(r"[çğıöşü]", terim) else "en"
        sorgular = [f"{on} {terim}" for on in SORU_ON_EK[dil]] + [f"{terim} "]
        puan = collections.Counter()
        ilk_kelime = terim.split()[0]
        for q in sorgular:
            url = ONERI_API + "?" + urllib.parse.urlencode(dict(client="firefox", ds="yt", hl=dil, q=q))
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    veri = json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8", "replace"))
            except Exception:
                continue
            for sira, o in enumerate(veri[1] if len(veri) > 1 else []):
                o = re.sub(r"\s+", " ", o.lower()).strip()
                if ilk_kelime in o and o != terim and len(o.split()) >= 3:
                    puan[o] += 10 - min(sira, 9) + (5 if q.strip() != terim else 0)
        return terim, [o for o, _ in puan.most_common(adet)]
    terimler = list(dict.fromkeys(t for t in terimler if t))
    with concurrent.futures.ThreadPoolExecutor(6) as havuz:
        return {t: l for t, l in havuz.map(getir, terimler) if l}


# ---------- tarama ----------
def tarih_str(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def tara(yt, a):
    simdi = dt.datetime.now(dt.timezone.utc)
    once = tarih_str(simdi - dt.timedelta(days=a.gun))
    orta = tarih_str(simdi - dt.timedelta(days=a.gun / 2))
    video_ids, kaynak = [], {}

    def ara(etiket, sayfa=1, bas=once, bit=None, bolge=a.bolge, dil=a.dil, **p):
        token = None
        for _ in range(sayfa):
            r = yt.get("search", 100, part="id", type="video", order="viewCount", publishedAfter=bas,
                       publishedBefore=bit, regionCode=bolge or None, relevanceLanguage=dil or None,
                       maxResults=50, pageToken=token, **p)
            for i in r.get("items", []):
                v = i.get("id", {}).get("videoId")
                if v and v not in kaynak:
                    video_ids.append(v)
                    kaynak[v] = etiket
            token = r.get("nextPageToken")
            if not token:
                break

    # Konu verildiyse sadece o konular derinlemesine (3 sayfa = 150 video) aranır.
    # Verilmediyse her kategori iki zaman dilimine bölünür (son yarı / ilk yarı): YouTube her aramada
    # sadece en çok izlenen 50'yi verdiği için bölmek, yakalanan videoyu ikiye katlar ve yeni
    # çıkan (henüz en üstte olmayan) konuların gözden kaçmasını azaltır.
    # Kota arama sırasında biterse elde edilenle devam et (detay sorguları sadece 1 birim).
    if a.konular:
        # Global aramada konu İngilizcesiyle de aranır; sonuçlar yine aynı konu başlığı altında toplanır.
        aramalar = [(k, k, dict(q=k, sayfa=3)) for k in a.konular]
        aramalar += [(k, f"{k} → {en}", dict(q=en, sayfa=3)) for k, en in a.ceviriler.items()]
    else:
        aramalar = [(ad, f"{ad} ({'son' if bas == orta else 'ilk'} yarı)", dict(videoCategoryId=kid, bas=bas, bit=bit))
                    for kid, ad in KATEGORILER.items() for bas, bit in ((orta, None), (once, orta))]
        if not a.bolge:
            # Global sonuçlar İngilizce ağırlıklı; Türkiye ayrıca taranır ki Türk kanalları da yakalansın.
            aramalar += [(ad, f"{ad} (Türkiye)", dict(videoCategoryId=kid, bolge="TR", dil="tr"))
                         for kid, ad in KATEGORILER.items()]
            # YouTube'da kategorisi olmayan ama değerli konular: konu aramasıyla eklenir (konu başına 3 arama).
            for ad, (q_en, q_tr) in SABIT_KONULAR.items():
                aramalar += [(ad, f"{ad} (son yarı)", dict(q=q_en, bas=orta, bit=None)),
                             (ad, f"{ad} (ilk yarı)", dict(q=q_en, bas=once, bit=orta)),
                             (ad, f"{ad} (Türkiye)", dict(q=q_tr, bolge="TR", dil="tr"))]
    for etiket, gorunen, p in aramalar:
        try:
            ara(etiket, **p)
            print(f"  ✓ {gorunen}")
        except ApiHatasi:
            print(f"  – {gorunen} (aranamadı, atlandı)")
        except KotaBitti:
            print("  ! Kota bitti, şimdiye kadar bulunanlarla devam ediliyor.")
            a.kota_yarim = True
            break
    if not video_ids and getattr(a, "kota_yarim", False):
        raise KotaBitti("Kota ilk aramada bitti.")  # eski sayfanın üstüne boş sayfa yazılmasın

    videolar = {}
    for i in range(0, len(video_ids), 50):
        r = yt.get("videos", 1, part="snippet,statistics,contentDetails", id=",".join(video_ids[i:i + 50]))
        for v in r.get("items", []):
            videolar[v["id"]] = v

    kanal_ids = list({v["snippet"]["channelId"] for v in videolar.values()})
    kanallar = {}
    for i in range(0, len(kanal_ids), 50):
        r = yt.get("channels", 1, part="snippet,statistics,topicDetails", id=",".join(kanal_ids[i:i + 50]))
        for c in r.get("items", []):
            kanallar[c["id"]] = c

    sonuc, reklam = [], 0
    for vid, v in videolar.items():
        c = kanallar.get(v["snippet"]["channelId"])
        if not c:
            continue
        st, cs = v.get("statistics", {}), c.get("statistics", {})
        izl = int(st.get("viewCount", 0))
        abone = None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0))
        vsay = max(int(cs.get("videoCount", 0)), 1)
        k_izl = int(cs.get("viewCount", 0))
        if izl < a.min_izlenme or vsay > a.max_video or (abone is not None and abone > a.max_abone):
            continue
        if v["snippet"].get("liveBroadcastContent", "none") != "none":
            continue  # canlı / yaklaşan yayınlar niş sinyali değil
        if c["snippet"]["title"].endswith((" - Topic", "VEVO")):
            continue  # otomatik müzik / resmi şarkıcı kanalları
        kanal_yasi = (simdi - zaman(c["snippet"]["publishedAt"])).days
        if kanal_yasi > a.max_kanal_yasi:
            continue  # yerleşik kanal: pastası yenmiş, yeni niş sinyali değil
        # Reklamla şişmiş video: izlenme yüksek ama neredeyse kimse beğenmiyor/yorum yapmıyor.
        # Doğal videolarda bu oran genelde %1-5; %0,15 altı reklam gösterimi demektir.
        if "likeCount" in st and izl >= 50_000:
            if (int(st["likeCount"]) + int(st.get("commentCount", 0))) / izl < 0.0015:
                reklam += 1
                continue
        saat = max((simdi - zaman(v["snippet"]["publishedAt"])).total_seconds() / 3600, 1)
        # Gizli aboneli kanalda oran bilinmez; o bileşen puana katılmaz.
        oran = None if abone is None else izl / max(abone, 100)
        video_basi = k_izl / vsay
        puan = (math.log10(video_basi + 1) * 1.2 + (math.log10(oran + 1) * 1.5 if oran is not None else 0)
                + math.log10(izl / saat + 1) + max(0, 1 - kanal_yasi / 365) - vsay / 60)
        sonuc.append(dict(
            id=vid, baslik=v["snippet"]["title"], kanal=c["snippet"]["title"], kanal_id=c["id"],
            kanal_kapak=c["snippet"].get("thumbnails", {}).get("default", {}).get("url", ""),
            ulke=c["snippet"].get("country", ""),
            yt_konular=yt_kategorileri(c),
            kapak=v["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            izlenme=izl, abone=abone, video_sayisi=vsay, kanal_izlenme=k_izl,
            video_basi=video_basi, saatlik=izl / saat, kanal_yasi=kanal_yasi,
            sure=sure_sn(v.get("contentDetails", {}).get("duration")),
            yayin=v["snippet"]["publishedAt"][:10], yayin_ts=v["snippet"]["publishedAt"], kaynak=kaynak[vid],
            etiketler=v["snippet"].get("tags", [])[:15], puan=puan,
            dil=v["snippet"].get("defaultAudioLanguage") or v["snippet"].get("defaultLanguage") or "",
        ))
    if reklam:
        print(f"  {reklam} reklamla şişmiş video elendi.")
    return sorted(sonuc, key=lambda x: -x["puan"])


def gecmisle_karsilastir(db, sonuc):
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    for s in sonuc:
        onceki = db.execute("SELECT ts, izlenme FROM anlik WHERE video_id=? ORDER BY ts DESC LIMIT 1", (s["id"],)).fetchone()
        s["yeni"] = onceki is None
        s["hizlanma"] = None
        if onceki:
            fark_saat = max((zaman(ts) - zaman(onceki[0])).total_seconds() / 3600, 0.5)
            s["hizlanma"] = (s["izlenme"] - onceki[1]) / fark_saat  # taramalar arası saatlik izlenme
        db.execute("INSERT INTO anlik VALUES(?,?,?)", (s["id"], ts, s["izlenme"]))
    db.commit()


def nis_cikar(sonuc, adet=12, haric=()):
    """Farklı küçük kanallarda tekrar eden kelime/ikili kelimeleri bul = yükselen konu.
    haric: aranan konunun kendi kelimeleri (ör. "kripto" aranınca her videoda geçer, niş değildir)."""
    haric = set(kelimeler(" ".join(haric)))
    terim_kanal = collections.defaultdict(set)
    terim_puan = collections.Counter()
    terim_video = collections.defaultdict(list)
    for s in sonuc:
        # Türkçe ekler için kök eşleşmesi: "tarih" aranınca "tarihi", "tarihte" de konu sayılmaz.
        # Tek kelimeler genel kalıyor ("funny", "love"); 2-3 kelimelik ifadeler asıl nişi verir.
        for t in ifadeler([s["baslik"]] + s["etiketler"], haric):
            terim_kanal[t].add(s["kanal_id"])
            terim_puan[t] += s["puan"] * {1: 1, 2: 2, 3: 2.4}[t.count(" ") + 1]
            terim_video[t].append(s)
    # Tek kelime en az 3 farklı kanalda, ifade en az 2 kanalda geçmeli.
    # Tek kelime ("car", "dad") çok genel kalıyor: en az 4 farklı kanalda geçmeli; 2-3 kelimelik ifade 2 kanal yeter.
    adaylar = [(t, p) for t, p in terim_puan.items() if len(terim_kanal[t]) >= (2 if " " in t else 4)]
    adaylar.sort(key=lambda x: -x[1] * len(terim_kanal[x[0]]))
    secilen, kullanilan = [], set()
    for t, p in adaylar:
        # Kelime bazında çakışma: "oyun" ile "oyun haritası" aynı niş; ama "art" ile "party" değil.
        if any(set(t.split()) & k for k in kullanilan):
            continue
        vids = sorted({v["id"]: v for v in terim_video[t]}.values(), key=lambda x: -x["puan"])
        secilen.append(dict(terim=t, kanal_sayisi=len(terim_kanal[t]), video_sayisi=len(vids),
                            toplam=sum(v["izlenme"] for v in vids),
                            kanallar=list({v["kanal_id"]: v for v in vids}.values()),
                            ilk=min(vids, key=lambda v: v["yayin_ts"]),
                            en_hizli=max(vids, key=lambda v: v["saatlik"]),
                            kategori=collections.Counter(v["kaynak"] for v in vids).most_common(1)[0][0],
                            kisa_oran=sum(0 < v["sure"] <= 180 for v in vids) / len(vids)))
        kullanilan.add(frozenset(t.split()))
        if len(secilen) >= adet:
            break
    return secilen


def trend_gecmisi(db, nisler):
    """Her konunun ilk ne zaman görüldüğünü ve önceki taramaya göre kaç kanal arttığını ekle."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    for n in nisler:
        ilk = db.execute("SELECT MIN(ts) FROM terim WHERE terim=?", (n["terim"],)).fetchone()[0]
        onceki = db.execute("SELECT kanal_sayisi FROM terim WHERE terim=? ORDER BY ts DESC LIMIT 1",
                            (n["terim"],)).fetchone()
        n["takip_basi"] = ilk or ts
        n["kanal_artis"] = None if onceki is None else n["kanal_sayisi"] - onceki[0]
        db.execute("INSERT INTO terim VALUES(?,?,?,?,?)",
                   (ts, n["terim"], n["kanal_sayisi"], n["video_sayisi"], n["toplam"]))
    db.commit()


# ---------- fırsat (doygunluk) ve alt konu keşfi ----------
def oneriler(konu, dil, adet=8):
    """YouTube arama önerilerinden insanların gerçekten aradığı alt konuları çıkar (kota harcamaz).
    "konu", "konu a", "konu b"... için öneriler alınır; üstte ve sık çıkan öneri daha önemlidir."""
    kok = konu.lower().strip()

    def getir(ek):
        url = ONERI_API + "?" + urllib.parse.urlencode(dict(client="firefox", ds="yt", hl=dil or "en", q=kok + ek))
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                veri = json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8", "replace"))
            return ek, veri[1] if len(veri) > 1 else []
        except Exception:
            return ek, []

    puan = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(8) as havuz:
        for ek, liste in havuz.map(getir, [""] + [f" {h}" for h in "abcdefghiklmnoprstuvyz"]):
            for sira, o in enumerate(liste):
                o = re.sub(r"\s+", " ", o.lower()).strip()
                # Sadece konunun gerçek alt ifadeleri: "kripton gezegeni" ya da "kripto 2026" değil.
                if not o.startswith(kok + " ") or re.fullmatch(re.escape(kok) + r" \d+", o):
                    continue
                # Düz aramanın önerileri çoğunlukla zaten büyük olan kanal adları; harfli açılımlar
                # ise uzun kuyruk alt konuları getirir. Bu yüzden onlara daha çok ağırlık verilir.
                puan[o] += (10 - min(sira, 9)) * (0.5 if ek == "" else 1)
    secilen = []
    for o, _ in puan.most_common():
        if not any(o.startswith(s) or s.startswith(o) for s in secilen):  # "x y" ile "x y z" aynı alt konu
            secilen.append(o)
        if len(secilen) >= adet:
            break
    return secilen


def firsat_olc(yt, a, terimler, bolge=None, dil=None):
    """Her terim için son 30 günün en çok izlenen 25 videosuna bak: izlenmeler yeni/küçük kanallara mı,
    yoksa büyük ve eski kanallara mı gidiyor? Terim başına 100 kota birimi.
    bolge/dil verilirse o pazar ölçülür (ör. İngilizce ya da Türkiye); verilmezse taramanın ayarı."""
    bolge = a.bolge if bolge is None else bolge
    dil = a.dil if dil is None else dil
    simdi = dt.datetime.now(dt.timezone.utc)
    once = tarih_str(simdi - dt.timedelta(days=30))
    arama = {}
    for t in terimler:
        try:
            r = yt.get("search", 100, part="id", type="video", order="viewCount", q=t, publishedAfter=once,
                       regionCode=bolge or None, relevanceLanguage=dil or None, maxResults=25)
        except ApiHatasi:
            continue
        except KotaBitti:
            a.kota_yarim = True
            break
        arama[t] = [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
    videolar, kanallar = {}, {}
    try:
        ids = list(dict.fromkeys(v for l in arama.values() for v in l))
        for i in range(0, len(ids), 50):
            for v in yt.get("videos", 1, part="snippet,statistics", id=",".join(ids[i:i + 50])).get("items", []):
                videolar[v["id"]] = v
        kids = list({v["snippet"]["channelId"] for v in videolar.values()})
        for i in range(0, len(kids), 50):
            for c in yt.get("channels", 1, part="snippet,statistics", id=",".join(kids[i:i + 50])).get("items", []):
                kanallar[c["id"]] = c
    except (KotaBitti, ApiHatasi):
        a.kota_yarim = True
    sonuc = {}
    for t, liste in arama.items():
        toplam = yeni_izl = 0
        buyukler, yeniler = set(), {}
        for vid in liste:
            v = videolar.get(vid)
            c = v and kanallar.get(v["snippet"]["channelId"])
            if not c:
                continue
            izl = int(v.get("statistics", {}).get("viewCount", 0))
            cs = c.get("statistics", {})
            abone = None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0))
            yas = (simdi - zaman(c["snippet"]["publishedAt"])).days
            toplam += izl
            if abone is not None and abone >= 500_000:
                buyukler.add(c["id"])
            if yas <= a.max_kanal_yasi and (abone or 0) <= a.max_abone:
                yeni_izl += izl
                onceki = yeniler.get(c["id"])
                if not onceki or onceki["izl"] < izl:
                    yeniler[c["id"]] = dict(kanal_id=c["id"], kanal=c["snippet"]["title"], izl=izl)
        if not liste:
            continue
        pay = yeni_izl / toplam if toplam else 0
        if toplam < 100_000:
            seviye = "dusuk"
        elif pay >= .5:
            seviye = "bos"
        elif pay >= .25:
            seviye = "orta"
        else:
            seviye = "doymus"
        sonuc[t] = dict(seviye=seviye, pay=pay, toplam=toplam, video=len(liste), buyuk=len(buyukler),
                        yeniler=sorted(yeniler.values(), key=lambda x: -x["izl"])[:4])
    return sonuc


def pazar_olc(yt, a, kanallar, terim_haritasi, adet=3):
    """En güçlü kanalların konusunu İngilizce pazarda ve Türkiye'de ayrı ayrı ölç (kanal başına 200 birim).
    Konusu sadece kategori adı olan (belirgin konusu çıkmamış) kanallar atlanır."""
    secilen = []
    for k in kanallar:
        konu = kanal_konusu(k, terim_haritasi)
        if konu != k["kategori"]:
            secilen.append((k["kanal_id"], konu))
        if len(secilen) >= adet:
            break
    en = {kid: (cevir(konu, "en") or konu).lower() for kid, konu in secilen}
    tr = {kid: (cevir(konu, "tr") or konu).lower() for kid, konu in secilen}
    olc_en = firsat_olc(yt, a, list(dict.fromkeys(en.values())), bolge="", dil="en")
    olc_tr = firsat_olc(yt, a, list(dict.fromkeys(tr.values())), bolge="TR", dil="tr")
    return {kid: dict(en=olc_en.get(en[kid]), tr=olc_tr.get(tr[kid])) for kid, _ in secilen}


# ---------- kanal incelemesi ----------
# 1.000 izlenme başına içerik üreticisine kalan tahmini kazanç (USD), (düşük, yüksek). Kaba tahmin.
RPM_UZUN = {"yuksek": (3, 10), "orta": (1.5, 4), "dusuk": (0.5, 2), "cok_dusuk": (0.1, 0.8)}
RPM_SHORTS = {"yuksek": (0.05, 0.15), "orta": (0.03, 0.08), "dusuk": (0.01, 0.05), "cok_dusuk": (0.01, 0.03)}
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def kanal_bul(yt, girdi):
    """Kanal linki, @kullanıcıadı, UC... kimliği ya da kanal adından kanalı bul."""
    g = urllib.parse.unquote(girdi.strip())
    parca = dict(part="snippet,statistics,contentDetails,topicDetails")
    m = re.search(r"(UC[\w-]{22})", g)
    if m:
        r = yt.get("channels", 1, id=m.group(1), **parca)
    elif re.search(r"@([\w.\-]+)", g):
        r = yt.get("channels", 1, forHandle="@" + re.search(r"@([\w.\-]+)", g).group(1), **parca)
    else:
        m = re.search(r"youtube\.com/(?:c|user)/([^/?#]+)", g)
        r = yt.get("channels", 1, forUsername=m.group(1), **parca) if m else {}
        if not r.get("items"):  # son çare: kanal adıyla ara (100 birim)
            ara = yt.get("search", 100, part="snippet", type="channel", q=m.group(1) if m else g, maxResults=1)
            ids = [i.get("id", {}).get("channelId") for i in ara.get("items", []) if i.get("id", {}).get("channelId")]
            r = yt.get("channels", 1, id=ids[0], **parca) if ids else {}
    items = r.get("items") or []
    return items[0] if items else None


def kanal_videolari(yt, kanal, en_fazla=200):
    """Kanalın son yüklediği videoları (en fazla 200) ayrıntılarıyla getir. 200 video ≈ 8 kota birimi."""
    yuklenen = kanal.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    ids, token = [], None
    while yuklenen and len(ids) < en_fazla:
        try:
            r = yt.get("playlistItems", 1, part="contentDetails", playlistId=yuklenen, maxResults=50, pageToken=token)
        except ApiHatasi:
            break
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    vids = []
    for i in range(0, len(ids), 50):
        for v in yt.get("videos", 1, part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            if sn.get("liveBroadcastContent", "none") != "none":
                continue
            vids.append(dict(id=v["id"], baslik=sn["title"], kapak=sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                             izl=int(st.get("viewCount", 0)), begeni=int(st.get("likeCount", 0)),
                             yorum=int(st.get("commentCount", 0)), sure=sure_sn(v.get("contentDetails", {}).get("duration")),
                             ts=zaman(sn["publishedAt"]), etiketler=sn.get("tags", [])[:15],
                             dil=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or ""))
    return sorted(vids, key=lambda v: v["ts"])  # eskiden yeniye


def orta_deger(liste):
    liste = sorted(liste)
    if not liste:
        return 0
    n = len(liste)
    return liste[n // 2] if n % 2 else (liste[n // 2 - 1] + liste[n // 2]) / 2


def en_sik_ifade(videolar, en_az=2):
    """videolar: her video için [başlık, etiket1, etiket2...]. En çok videoda geçen 2 kelimelik ifade
    (en az `en_az` videoda), yoksa en sık kelime."""
    ikili, tekli = collections.Counter(), collections.Counter()
    for parcalar in videolar:
        terimler = ifadeler(parcalar)
        ikili.update(t for t in terimler if t.count(" ") == 1)
        tekli.update(t for t in terimler if " " not in t)
    if ikili and ikili.most_common(1)[0][1] >= en_az:
        return ikili.most_common(1)[0][0]
    return tekli.most_common(1)[0][0] if tekli else ""


def kanal_analiz(c, vids):
    """Kanalın ve videolarının tüm ölçümleri (kota harcamaz)."""
    simdi = dt.datetime.now(dt.timezone.utc)
    sn, cs = c["snippet"], c.get("statistics", {})
    abone = None if cs.get("hiddenSubscriberCount") else int(cs.get("subscriberCount", 0))
    toplam, vsay = int(cs.get("viewCount", 0)), max(int(cs.get("videoCount", 0)), 1)
    yas = max((simdi - zaman(sn["publishedAt"])).days, 1)
    ulke = sn.get("country", "")
    dil = collections.Counter(v["dil"] for v in vids if v["dil"]).most_common(1)
    dil = dil[0][0] if dil else sn.get("defaultLanguage", "")
    seviye = kazanc_seviyesi(ulke, dil)
    izl = [v["izl"] for v in vids]
    kisalar = [v for v in vids if 0 < v["sure"] <= 180]
    uzunlar = [v for v in vids if v["sure"] > 180]
    top_izl = sum(izl) or 1
    pay_kisa = sum(v["izl"] for v in kisalar) / top_izl
    son, onceki = izl[-10:], izl[-20:-10]
    gidisat = (orta_deger(son) / orta_deger(onceki) - 1) if len(onceki) >= 3 and orta_deger(onceki) > 0 else None
    aralik = (vids[-1]["ts"] - vids[0]["ts"]).days / (len(vids) - 1) if len(vids) >= 2 else None
    basliklar = [v["baslik"] for v in vids]
    metinler = [[v["baslik"]] + v["etiketler"] for v in vids]
    en_iyiler = sorted(vids, key=lambda v: -v["izl"])[:5]
    sik = collections.Counter(w for m in metinler for w in ifadeler(m) if " " not in w)
    konular = yt_kategorileri(c)
    # Tahmini kazanç: aylık izlenme ≈ toplam izlenme / kanal yaşı (ay); Shorts payı ayrı fiyatlanır.
    s = seviye or "orta"
    aylik = toplam / max(yas / 30, 1)

    def gelir(izlenme, i):
        return izlenme * (1 - pay_kisa) / 1000 * RPM_UZUN[s][i] + izlenme * pay_kisa / 1000 * RPM_SHORTS[s][i]

    return dict(
        id=c["id"], ad=sn["title"], handle=sn.get("customUrl", ""), ulke=ulke, dil=dil, seviye=seviye,
        kapak=sn.get("thumbnails", {}).get("medium", {}).get("url", ""), aciklama=sn.get("description", "")[:400],
        abone=abone, toplam=toplam, video_sayisi=vsay, yas=yas, video_basi=toplam / vsay, analiz_edilen=len(vids),
        ort=sum(izl) / len(izl) if izl else 0, medyan=orta_deger(izl), gidisat=gidisat,
        son10=vids[-10:], kisa_oran=len(kisalar) / len(vids) if vids else 0, pay_kisa=pay_kisa,
        ort_kisa=sum(v["izl"] for v in kisalar) / len(kisalar) if kisalar else 0,
        ort_uzun=sum(v["izl"] for v in uzunlar) / len(uzunlar) if uzunlar else 0,
        ort_sure=sum(v["sure"] for v in uzunlar) / len(uzunlar) if uzunlar else 0,
        kisa_sayi=len(kisalar), uzun_sayi=len(uzunlar),
        aralik=aralik, son30=sum((simdi - v["ts"]).days <= 30 for v in vids),
        son_yukleme=(simdi - vids[-1]["ts"]).days if vids else None,
        etkilesim=sum(v["begeni"] + v["yorum"] for v in vids) / top_izl,
        gunler=collections.Counter(GUNLER[v["ts"].astimezone(TR_SAAT).weekday()] for v in vids).most_common(2),
        saatler=collections.Counter(v["ts"].astimezone(TR_SAAT).hour for v in vids).most_common(1),
        en_iyiler=en_iyiler, konusu=en_sik_ifade(metinler) or (konular[0] if konular else ""),
        sira_disi=sorted([dict(v, oran=v["izl"] / orta_deger(izl)) for v in vids
                          if orta_deger(izl) and v["izl"] >= 3 * orta_deger(izl)], key=lambda v: -v["oran"])[:5],
        en_iyi_tema=en_sik_ifade([[v["baslik"]] + v["etiketler"] for v in en_iyiler]),
        youtube_konulari=konular, sik_kelimeler=[w for w, _ in sik.most_common(8)],
        baslik_uzunluk=sum(len(b) for b in basliklar) / len(basliklar) if basliklar else 0,
        sayi_oran=sum(bool(re.search(r"\d", b)) for b in basliklar) / max(len(basliklar), 1),
        soru_oran=sum("?" in b for b in basliklar) / max(len(basliklar), 1),
        emoji_oran=sum(bool(EMOJI.search(b)) for b in basliklar) / max(len(basliklar), 1),
        buyuk_oran=sum(bool(re.search(r"\b[A-ZÇĞİÖŞÜ]{3,}\b", b)) for b in basliklar) / max(len(basliklar), 1),
        aylik_gelir=(gelir(aylik, 0), gelir(aylik, 1)), toplam_gelir=(gelir(toplam, 0), gelir(toplam, 1)),
        seviye_tahmini=seviye is None,
    )


def kanal_ozel_oneriler(x):
    """İnceleme sayfasına özel, kanalın kendi verisinden çıkan dersler."""
    m = []
    if x["gidisat"] is not None:
        y = round(abs(x["gidisat"]) * 100)
        if x["gidisat"] > .3:
            m.append(f'📈 <b>Yükselişte</b>: son 10 video önceki 10\'dan %{y} daha çok izleniyor. Konu ısınıyor, tam zamanı.')
        elif x["gidisat"] < -.3:
            m.append(f'📉 <b>Düşüşte</b>: son 10 video önceki 10\'dan %{y} daha az izleniyor. Konu soğuyor olabilir, '
                     f'girersen farklı bir açı bul.')
    if x["kisa_sayi"] >= 3 and x["uzun_sayi"] >= 3 and x["ort_kisa"] and x["ort_uzun"]:
        if x["ort_uzun"] > x["ort_kisa"]:
            m.append(f'🎬 Uzun videoları Shorts\'tan <b>{x["ort_uzun"] / x["ort_kisa"]:.1f} kat</b> daha çok izleniyor: '
                     f'bu konuda uzun video daha iyi çalışıyor.')
        else:
            m.append(f'📱 Shorts\'ları uzun videolardan <b>{x["ort_kisa"] / x["ort_uzun"]:.1f} kat</b> daha çok izleniyor: '
                     f'büyümek için Shorts, kazanç için uzun video.')
    if x["en_iyi_tema"]:
        m.append(f'🏆 En çok izlenen videolarının ortak teması: <b>{html.escape(x["en_iyi_tema"])}</b>. İşe yarayan bu.')
    if x["aralik"]:
        m.append(f'🗓️ Ortalama <b>{x["aralik"]:.0f} günde bir</b> video yüklüyor (son 30 günde {x["son30"]}). '
                 f'Rekabet edeceksen en az bu tempoyu hedefle.')
    ipucu = []
    if x["sayi_oran"] > .5:
        ipucu.append("sayı kullanıyor")
    if x["soru_oran"] > .3:
        ipucu.append("soru soruyor")
    if x["emoji_oran"] > .4:
        ipucu.append("emoji kullanıyor")
    if x["buyuk_oran"] > .4:
        ipucu.append("BÜYÜK HARF vurgusu yapıyor")
    if ipucu:
        m.append(f'✍️ Başlıklarının çoğu {", ".join(ipucu)}; ortalama {x["baslik_uzunluk"]:.0f} karakter.')
    return m


# ---------- rapor ----------
def yas_metni(gun):
    if gun < 31:
        return f"{max(gun, 1)} gün önce açıldı"
    if gun < 365:
        return f"{gun // 30} ay önce açıldı"
    return f"{gun // 365} yıl önce açıldı"


def kanal_link(kid):
    return f"https://www.youtube.com/channel/{kid}"


def video_link(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def arama_link(metin):
    return "https://www.youtube.com/results?" + urllib.parse.urlencode(dict(search_query=metin))


FIRSAT_ETIKET = {"bos": ("bos", "🟢 Boş alan"), "orta": ("orta", "🟡 Rekabet orta"),
                 "dusuk": ("dusuk", "⚪ Talep az"), "doymus": ("doymus", "🔴 Doymuş")}
FIRSAT_SIRA = {"bos": 0, "orta": 1, "dusuk": 2, "doymus": 3}


def firsat_metni(f):
    if f["seviye"] == "dusuk":
        return f'Son 30 günde bu konudaki en popüler videolar bile toplam {kisa(f["toplam"])} izlenmede kalmış: ilgi henüz az.'
    return (f'Son 30 günün en çok izlenen {f["video"]} videosunda yeni ve küçük kanalların izlenme payı: '
            f'<b>%{round(f["pay"] * 100)}</b> · {f["buyuk"]} büyük kanal (500K+ abone) · toplam {kisa(f["toplam"])} izlenme')


def kanallara_grupla(sonuc):
    """Videoları kanal bazında topla: her kanal bir kart."""
    gruplar = collections.OrderedDict()
    for s in sonuc:
        gruplar.setdefault(s["kanal_id"], []).append(s)
    kanallar = []
    for kid, vids in gruplar.items():
        ilk = vids[0]
        kanallar.append(dict(
            kanal_id=kid, kanal=ilk["kanal"], kapak=ilk.get("kanal_kapak", ""), ulke=ilk.get("ulke", ""),
            dil=collections.Counter(v.get("dil", "") for v in vids).most_common(1)[0][0],
            yt_konular=ilk.get("yt_konular", []),
            video_sayisi=ilk["video_sayisi"], kanal_izlenme=ilk["kanal_izlenme"], abone=ilk["abone"],
            video_basi=ilk["video_basi"], kanal_yasi=ilk["kanal_yasi"],
            kategori=collections.Counter(v["kaynak"] for v in vids).most_common(1)[0][0],
            basliklar=[[v["baslik"]] + v["etiketler"] for v in vids],
            puan=max(v["puan"] for v in vids),
            en_iyi=max(vids, key=lambda v: v["izlenme"]),
            yeni=all(v.get("yeni") for v in vids),
            hizli=any(v.get("hizlanma") and v["hizlanma"] > v["saatlik"] * 1.2 for v in vids),
            hizlanma=max((v.get("hizlanma") or 0) for v in vids),
            kisa=sum(0 < v["sure"] <= 180 for v in vids) / len(vids) > .6,
        ))
    return sorted(kanallar, key=lambda k: -k["puan"])


def one_cikanlar(kanallar, nisler):
    """Ana sayfanın en üstündeki "Bugün öne çıkanlar" kartları (kota harcamaz).
    Her kart farklı bir kanal gösterir; uygun aday yoksa o kart hiç çıkmaz."""
    kullanilan, kartlar = set(), []

    def sec(adaylar, anahtar):
        for k in sorted(adaylar, key=anahtar):
            if k["kanal_id"] not in kullanilan:
                kullanilan.add(k["kanal_id"])
                return k

    def kazanc(k):
        return kazanc_seviyesi(k["ulke"], k["dil"])

    def yer(k):
        return ULKE_AD.get(k["ulke"]) or k["ulke"] or "yabancı"

    k = sec([k for k in kanallar if k["video_sayisi"] <= 10] or kanallar, lambda k: -k["video_basi"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="⭐", baslik="Günün yıldızı", k=k,
                            neden=f'Sadece <b>{k["video_sayisi"]} videoyla {kisa(k["kanal_izlenme"])}</b> izlenme · '
                                  f'video başına {kisa(k["video_basi"])}'))
    # Konu: önce 🟢 boş alanlar, sonra en çok kanalın aynı anda tutturduğu.
    if nisler:
        # Önce 2-3 kelimelik konular ("parenting humor"), çünkü tek kelime ("car") niş değil kategori gibi kalıyor.
        n = min(nisler, key=lambda n: (" " not in n["terim"],
                                       FIRSAT_SIRA[n["firsat"]["seviye"]] if n.get("firsat") else 1.5, -n["kanal_sayisi"]))
        f = n.get("firsat")
        kartlar.append(dict(tip="konu", ikon="🎯", baslik="Günün konusu", n=n,
                            neden=f'<b>{n["kanal_sayisi"]} yeni kanal</b> aynı anda tutturdu · toplam {kisa(n["toplam"])} izlenme'
                                  + (f' · {FIRSAT_ETIKET[f["seviye"]][1]}' if f else "")))
    k = sec([k for k in kanallar if k["hizlanma"] > 0], lambda k: -k["hizlanma"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="🚀", baslik="En hızlı yükselen", k=k,
                            neden=f'Son taramadan beri <b>saatte {kisa(k["hizlanma"])}</b> izlenme alıyor'))
    k = sec([k for k in kanallar if k["kanal_izlenme"] >= 1_000_000] or
            [k for k in kanallar if k["kanal_izlenme"] >= 300_000], lambda k: k["kanal_yasi"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="🌱", baslik="En taze patlama", k=k,
                            neden=f'<b>{k["kanal_yasi"]} günlük</b> kanal, şimdiden {kisa(k["kanal_izlenme"])} izlenme'))
    k = sec([k for k in kanallar if kazanc(k) == "yuksek"], lambda k: -k["puan"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="💰", baslik="Para kokan fırsat", k=k,
                            neden=f'Yüksek kazançlı <b>{html.escape(yer(k))}</b> pazarında {k["video_sayisi"]} videoyla '
                                  f'{kisa(k["kanal_izlenme"])} izlenme'))
    k = sec([k for k in kanallar if k["ulke"] == "TR" or (k["dil"] or "").startswith("tr")], lambda k: -k["puan"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="🇹🇷", baslik="Türkiye'nin yıldızı", k=k,
                            neden=f'{k["video_sayisi"]} videoyla <b>{kisa(k["kanal_izlenme"])}</b> izlenme'))
    k = sec([k for k in kanallar if kazanc(k) in ("dusuk", "cok_dusuk") and k["ulke"] != "TR"],
            lambda k: -k["video_basi"])
    if k:
        kartlar.append(dict(tip="kanal", ikon="🔁", baslik="Taşınacak fikir", k=k,
                            neden=f'<b>{html.escape(yer(k))}</b> pazarında patladı ama orada kazanç düşük: '
                                  f'İngilizce ya da Türkçeye taşı'))
    return kartlar


def genis_sirala(kanallar):
    """Listeyi tek bir alan doldurmasın: kategoriler arasında sırayla dağıt (her turda her
    kategorinin en iyisi), böylece oyun, eğitim, hayvanlar, bilim... hepsi üstte görünür."""
    gruplar = collections.OrderedDict()
    for k in kanallar:  # kanallar zaten puana göre sıralı
        gruplar.setdefault(k["kategori"], []).append(k)
    sirali = []
    while any(gruplar.values()):
        tur = [g.pop(0) for g in gruplar.values() if g]
        sirali += sorted(tur, key=lambda k: -k["puan"])
    return sirali


def kanal_konusu(k, terim_haritasi):
    """Kanalın konusu, en güvenilirden başlayarak:
    1) trend raporundaki ortak konu, 2) kanalın en az 2 videosunda geçen ifade,
    3) videolarının etiketleri (kanal sahibinin kendi seçtiği konu), 4) YouTube'un kanal kategorisi,
    5) tarandığı kategori. Tek başlıktan rastgele iki kelime seçmek "found right" gibi saçma sonuç veriyordu."""
    if k["kanal_id"] in terim_haritasi:
        return terim_haritasi[k["kanal_id"]]
    if len(k["basliklar"]) >= 2:
        ifade = en_sik_ifade(k["basliklar"], en_az=2)
        if " " in ifade:
            return ifade
    etiketler = collections.Counter()
    for parcalar in k["basliklar"]:
        for et in parcalar[1:]:
            et = re.sub(r"\s+", " ", et.lower()).strip("# ")
            if 3 <= len(et) <= 40 and not any(w in GENEL for w in et.split()) and kelimeler(et):
                etiketler[et] += 1
    if etiketler:
        return etiketler.most_common(1)[0][0]
    if k.get("yt_konular"):
        return k["yt_konular"][0].lower()
    return k["kategori"]


def sure_metni(gun):
    return f"{max(gun, 1)} günde" if gun < 45 else f"{round(gun / 30)} ayda"


def incele_dugmesi(kanal_id):
    """Sadece uygulama sahibine görünür (sahip modu). Kanal daha önce incelendiyse JS bunu herkese açık
    "İncelemeyi aç" bağlantısına çevirir."""
    return (f'<a class="incele sahip" data-id="{html.escape(kanal_id)}" href="{TARA_LINKI}" target="_blank" '
            f'rel="noopener">🔍 İncele</a>')


def fikir_kutusu(fikirler, tr):
    """İnsanların gerçekten aradığı sorular, dokununca açılan kutu; her biri YouTube aramasına gider."""
    if not fikirler:
        return ""
    e = html.escape
    satirlar = "".join(
        f'<li><a href="{arama_link(f)}" target="_blank" rel="noopener">{e(f)}</a>'
        + (f' <span class="ceviri">({e(tr[f])})</span>' if tr.get(f) else "") + "</li>" for f in fikirler)
    return (f'<details class="fikir"><summary>💡 Video fikirleri <span class="kucuk">({len(fikirler)} gerçek arama)</span>'
            f'</summary><p class="kucuk">İnsanların YouTube\'da bu konuda aradığı şeyler. Her biri bir video fikri:</p>'
            f'<ul>{satirlar}</ul></details>')


def kapak_galerisi(videolar, adet=12):
    """En çok izlenen videoların kapakları yan yana: görsel ilham."""
    e = html.escape
    l = sorted([v for v in videolar if v.get("kapak")], key=lambda v: -v["izlenme"])[:adet]
    if not l:
        return ""
    return ('<div class="galeri">' + "".join(
        f'<a href="{video_link(v["id"])}" target="_blank" rel="noopener"><img src="{e(v["kapak"])}" alt="{e(v["baslik"])}" '
        f'loading="lazy"><span>▶ {kisa(v["izlenme"])}</span></a>' for v in l) + "</div>")


def terim_haritasi_kur(nisler):
    """kanal_id → kanalın yer aldığı (en güçlü) trend konusu."""
    harita = {}
    for n in nisler:
        for v in n["kanallar"]:
            harita.setdefault(v["kanal_id"], n["terim"])
    return harita


def pazar_metni(f):
    return FIRSAT_ETIKET[f["seviye"]][1] if f else "ölçülemedi"


def kanal_onerileri(k, kazanc, pazar, trend_f):
    """Kanal için en fazla 4 maddelik, duruma göre değişen öneri: hangi dilde, hangi formatta,
    ne kadar acele, ne kadar kanıtlanmış. Kota harcamaz (pazar ölçümü önceden yapılmışsa onu kullanır)."""
    e = html.escape
    m = []
    yer = ULKE_AD.get(k["ulke"]) or (f'{k["ulke"]}' if k["ulke"] else "kaynak")
    # 1) Dil / pazar önerisi
    if pazar and (pazar.get("en") or pazar.get("tr")):
        en, trp = pazar.get("en"), pazar.get("tr")
        en_iyi = en and en["seviye"] in ("bos", "orta")
        tr_iyi = trp and trp["seviye"] in ("bos", "orta")
        olcum = f'Ölçüm: 🌍 İngilizce {pazar_metni(en)} · 🇹🇷 Türkiye {pazar_metni(trp)}.'
        if kazanc in ("dusuk", "cok_dusuk") and k["ulke"] != "TR":
            olcum = f'Fikir {e(yer)} pazarında patlamış ama orada kazanç düşük; taşımaya değer. ' + olcum
        kaynak_en = (k["dil"] or "").startswith("en") or k["ulke"] in ("US", "GB", "CA", "AU", "NZ", "IE")
        if kaynak_en and tr_iyi and (not en_iyi or FIRSAT_SIRA[trp["seviye"]] <= FIRSAT_SIRA[en["seviye"]]):
            m.append(f'🇹🇷 <b>Türkçesini yap</b>: fikir İngilizcede kanıtlanmış; Türkçe versiyonla Türkiye\'de '
                     f'öne çıkabilirsin. {olcum}')
        elif kaynak_en and en_iyi:
            m.append(f'🌍 <b>İngilizcede yer var ama</b> bu kanalla aynı pazarda yarışırsın: aynı konuyu farklı bir '
                     f'açıyla yap. {olcum}')
        elif en_iyi and (not tr_iyi or FIRSAT_SIRA[en["seviye"]] <= FIRSAT_SIRA[trp["seviye"]]):
            m.append(f'🌍 <b>İngilizce yap</b>: İngilizce pazarda yer var ve kazanç en yüksek orada. {olcum}')
        elif tr_iyi:
            m.append(f'🇹🇷 <b>Türkçe yap</b>: Türkiye\'de rakip az, ilk olma şansın var; İngilizce daha kalabalık. {olcum}')
        else:
            m.append(f'⚠️ <b>Birebir taşıma</b>: iki pazarda da kalabalık. Aynı fikri farklı bir açıyla ya da '
                     f'alt konusuyla yap. {olcum}')
    elif kazanc in ("dusuk", "cok_dusuk") and k["ulke"] != "TR":
        m.append(f'🌍 <b>Fikri İngilizceye taşı</b>: {e(yer)} pazarında patlamış ama orada izlenme başına kazanç düşük. '
                 f'Aynı fikir İngilizce aynı izlenmeyle kat kat fazla kazandırır; Türkçesi de yoksa Türkiye\'de ilk olabilirsin.')
    elif k["ulke"] == "TR" or (k["dil"] or "").startswith("tr"):
        m.append('🌍 <b>İngilizcesini de düşün</b>: Türkiye\'de tutuyor; İngilizce versiyonu hem çok daha büyük '
                 'bir pazar hem de birkaç kat yüksek kazanç demek.')
    elif kazanc in ("yuksek", "orta"):
        m.append('🇹🇷 <b>Türkçesini yap</b>: fikir zaten kazancı yüksek bir pazarda kanıtlanmış. Türkçe versiyonu '
                 'yoksa Türkiye\'de ilk sen ol; İngilizce yapacaksan farklı bir açı bul, orada rakip çıkacaktır.')
    else:
        m.append('🌍 <b>Dil</b>: İngilizce en yüksek kazancı verir; Türkçede rakip genelde daha azdır.')
    # 2) Format
    if k["kisa"]:
        m.append('📱 <b>Shorts ile başla</b>: bu fikir kısa videoda tutuyor ve hızlı büyütür, ama Shorts kazancı düşüktür. '
                 'Kanal tutunca aynı konuyu uzun videoya da taşı.')
    else:
        m.append('🎬 <b>Uzun video</b>: bu konu uzun formatta tutuyor; izlenme başına kazancı Shorts\'tan çok daha yüksek.')
    # 3) Zamanlama
    if k["kanal_yasi"] < 90 or k["hizli"]:
        neden = f'kanal sadece {k["kanal_yasi"]} günlük' if k["kanal_yasi"] < 90 else "izlenmeleri hâlâ hızlanıyor"
        m.append(f'⏱️ <b>Acele et</b>: {neden}; dalga sürüyor, büyük kanallar fark etmeden 1–2 hafta içinde başla.')
    # 4) Kanıt / genel fırsat
    if trend_f and trend_f["seviye"] == "bos":
        m.append('🟢 <b>Konu genel olarak hâlâ boş</b>: bu konudaki izlenmelerin çoğu yeni kanallara gidiyor.')
    elif trend_f and trend_f["seviye"] == "doymus":
        m.append('🔴 <b>Konu doymaya başlamış</b>: büyük kanallar girmiş; ancak çok farklı bir açıyla dene.')
    elif k["video_sayisi"] <= 6 and k["video_basi"] >= 300_000:
        m.append(f'✅ <b>Format kanıtlanmış</b>: sadece {k["video_sayisi"]} videoyla bu sonuç. Birebir kopyalama, '
                 f'kendi yorumunu ve tarzını kat.')
    return m[:4]


CSS = """
:root{--bg:#f6f5f2;--kart:#fff;--yazi:#1c1b19;--soluk:#6d6a63;--cizgi:#e4e1da;--vurgu:#d4361c;--iyi:#1f7a4d;--uyari:#9a6700;--zemin2:#f0eee9}
/* Görünüm: varsayılan telefonun ayarı (otomatik); Profil'den Açık/Koyu seçilirse html[data-tema] onu zorlar. */
@media (prefers-color-scheme:dark){:root:not([data-tema=acik]){--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a;--uyari:#e3b341;--zemin2:#272522}}
:root[data-tema=koyu]{--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a;--uyari:#e3b341;--zemin2:#272522}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--yazi);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-tap-highlight-color:transparent}
a{color:inherit;text-decoration:none}
main{max-width:720px;margin:auto;padding:max(16px,env(safe-area-inset-top)) 16px 40px}
header{display:flex;align-items:center;gap:8px}
h1{font-size:22px;margin:0;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.btn{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:10px 16px;font-weight:600;font-size:15px;border:0;cursor:pointer;font-family:inherit}
.btn.ana{background:var(--vurgu);color:#fff} .btn.ikinci{background:var(--zemin2);color:var(--yazi)}
.meta{color:var(--soluk);font-size:13px;margin:6px 0 0}
.geri{display:inline-block;color:var(--soluk);font-size:15px;font-weight:600;padding:4px 0 10px}
.ipucu,.uyari{border-radius:12px;padding:10px 14px;font-size:14px;margin:14px 0 0}
.ipucu{background:var(--zemin2);color:var(--soluk)} .uyari{border:1px solid var(--uyari);color:var(--uyari);font-weight:600}
[hidden]{display:none!important}
h2{font-size:19px;margin:32px 0 4px} .alt-baslik{color:var(--soluk);font-size:14px;margin:0 0 12px}
.kart{background:var(--kart);border:1px solid var(--cizgi);border-radius:14px;margin-bottom:12px;overflow:hidden}
.kanal,.arama{display:flex;align-items:center;gap:12px;padding:14px}
.kanal:active,.video:active,.cip:active,.arama:active{opacity:.6}
.av{width:52px;height:52px;border-radius:50%;flex:none;object-fit:cover;background:var(--zemin2)}
.av.bos{display:grid;place-items:center;font-weight:700;color:var(--vurgu)}
.ic{flex:1;min-width:0;display:flex;flex-direction:column}
.ad{font-weight:700;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.buyuk{font-size:15px} .buyuk b{color:var(--vurgu)}
.kucuk{display:block;color:var(--soluk);font-size:13px}
.ok{font-size:28px;color:var(--soluk);line-height:1}
.rzs{padding:0 14px;display:flex;flex-wrap:wrap;gap:6px} .rzs:empty{display:none}
.rz{font-size:12px;font-weight:600;background:var(--zemin2);border-radius:999px;padding:3px 10px}
.rz.hiz{color:var(--vurgu)} .rz.yeni{color:var(--iyi)}
.video{display:flex;gap:10px;align-items:center;padding:12px 14px;border-top:1px solid var(--cizgi);margin-top:12px}
.video img{width:96px;aspect-ratio:16/9;object-fit:cover;border-radius:8px;flex:none;background:var(--zemin2)}
.vb{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:14px;font-weight:500}
.aramalar .arama{border-top:1px solid var(--cizgi)} .aramalar .arama:first-child{border-top:0}
.nis{padding:14px} .nis-ad{font-size:18px;font-weight:700;color:var(--vurgu)}
.cipler{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.cip{background:var(--zemin2);border-radius:999px;padding:6px 12px;font-size:14px;font-weight:500}
.cip::after{content:" ›";color:var(--soluk)} .cip.duz::after{content:none}
.nis-ust{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.durum{font-size:12px;font-weight:700;background:var(--zemin2);border-radius:999px;padding:4px 10px;color:var(--soluk)}
.durum.sicak{color:var(--vurgu)} .durum.yeni{color:var(--iyi)}
.bilgi{margin:10px 0 0;font-size:14px} .bilgi dt{color:var(--soluk);font-size:12px;margin-top:8px}
.bilgi dd{margin:0} .bilgi a{text-decoration:underline;text-underline-offset:2px}
.fr{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:3px 10px;background:var(--zemin2);white-space:nowrap}
.fr.bos{color:var(--iyi)} .fr.orta{color:var(--uyari)} .fr.doymus{color:var(--vurgu)} .fr.dusuk{color:var(--soluk)}
.nis.ana{border-width:2px} .aciklama{margin:8px 0 0;font-size:14px;color:var(--yazi)}
.ara{display:inline-block;margin-top:12px;font-size:14px;font-weight:600;color:var(--vurgu)}
.bos-not{color:var(--soluk)}
.patlama{font-size:15px;margin-top:2px} .patlama b{color:var(--vurgu)}
.konusu{font-size:14px;margin-top:4px} .konusu b{color:var(--yazi)}
.eylem{display:flex;gap:8px;margin:12px 14px 14px}
.gir,.incele{flex:1;display:block;text-align:center;padding:11px;border-radius:12px;font-weight:700}
.gir{background:var(--vurgu);color:#fff} .incele{background:var(--zemin2);color:var(--yazi)}
.incele.hazir{color:var(--iyi)} .gir:active,.incele:active{opacity:.7}
.fav-oge .incele{display:inline-block;flex:none;padding:6px 12px;margin:0 0 12px 14px;font-size:14px}
.istat{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:0 14px 14px}
.istat div{background:var(--zemin2);border-radius:10px;padding:8px 6px;text-align:center}
.istat b{display:block;font-size:17px} .istat span{font-size:12px;color:var(--soluk)}
.bolum{padding:14px} .bolum h3{margin:0 0 8px;font-size:16px}
.satirlar{margin:0;padding:0;list-style:none;font-size:14px} .satirlar li{margin:6px 0}
.cubuk-satir{display:flex;align-items:center;gap:8px;font-size:13px;margin:6px 0}
.cubuk-satir .ad{flex:1;font-size:13px;font-weight:500} .cubuk-dis{width:90px;flex:none;height:8px;background:var(--zemin2);border-radius:4px}
.cubuk{height:8px;background:var(--vurgu);border-radius:4px} .cubuk-satir .sayi{width:46px;flex:none;text-align:right;color:var(--soluk)}
.av.buyuk-av{width:72px;height:72px}
/* Öne çıkanlar: telefonda parmakla yana kaydırılır (yumuşak durma) ve ‹ › oklarıyla da geçilebilir */
.kaydir{position:relative}
.one-cikanlar{display:flex;gap:10px;overflow-x:auto;overflow-y:hidden;scroll-snap-type:x proximity;margin:0 -16px;
  padding:0 16px 6px;scroll-padding:0 16px;scrollbar-width:none;-webkit-overflow-scrolling:touch;
  overscroll-behavior-x:contain;touch-action:pan-x pan-y;scroll-behavior:smooth}
.one-cikanlar::-webkit-scrollbar{display:none}
.kart.one{flex:0 0 84%;max-width:340px;scroll-snap-align:start;margin-bottom:0;display:flex;flex-direction:column}
.ok-dugme{position:absolute;top:42%;z-index:3;width:38px;height:38px;border-radius:50%;border:1px solid var(--cizgi);
  background:var(--kart);color:var(--yazi);font-size:24px;line-height:1;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.ok-dugme.sol{left:-6px} .ok-dugme.sag{right:-6px} .ok-dugme[disabled]{opacity:0;pointer-events:none}
.noktalar{display:flex;justify-content:center;gap:6px;margin-top:8px}
.noktalar span{width:7px;height:7px;border-radius:50%;background:var(--cizgi)} .noktalar span.aktif{background:var(--vurgu)}
.kart.one .eylem{margin-top:auto}
.one-etiket{padding:12px 56px 0 14px;font-weight:800;font-size:15px;color:var(--vurgu)}
.kart.one .nis-ad{padding:8px 14px 0} .kart.one .cipler{padding:0 14px 14px}
.one-neden{margin:4px 14px 0;font-size:14px} .kart.one .kanal{padding-bottom:4px}
/* sahip modu: Tara / İncele sadece uygulama sahibine */
.sahip{display:none!important} body.sahip-modu .sahip{display:inline-flex!important}
body.sahip-modu .incele.sahip,.incele.hazir{display:block!important}
main{padding-bottom:90px}
.alt-menu{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--kart);border-top:1px solid var(--cizgi);
  padding:4px 4px max(4px,env(safe-area-inset-bottom));z-index:10}
.alt-menu a{flex:1;display:flex;flex-direction:column;align-items:center;font-size:11px;color:var(--soluk);padding:4px 0;gap:1px}
.alt-menu a span{font-size:20px;line-height:1.2} .alt-menu a.aktif{color:var(--vurgu);font-weight:700}
section{scroll-margin-top:12px}
.filtreler{display:flex;gap:8px;overflow-x:auto;margin:0 -16px 10px;padding:0 16px;scrollbar-width:none}
.filtreler::-webkit-scrollbar{display:none} .filtreler.sarili{flex-wrap:wrap;overflow:visible;margin:0 0 10px;padding:0}
.filtre{flex:none;border:1px solid var(--cizgi);background:var(--kart);color:var(--yazi);border-radius:999px;padding:7px 13px;
  font:inherit;font-size:14px;cursor:pointer} .filtre.secili{background:var(--vurgu);border-color:var(--vurgu);color:#fff}
.ara-kutu{width:100%;padding:11px 14px;border:1px solid var(--cizgi);border-radius:12px;background:var(--kart);color:var(--yazi);
  font:inherit;margin-bottom:12px}
.kanal-kart[hidden]{display:none}
.paylas{flex:none;width:46px;border:0;border-radius:12px;background:var(--zemin2);color:var(--yazi);font-size:18px;cursor:pointer}
details.oneri,details.fikir{margin:12px 14px 0;padding:10px 12px;border-radius:12px;background:var(--zemin2);font-size:14px}
details.fikir{margin:12px 0 0} details summary{cursor:pointer;font-weight:700;list-style-position:inside}
details.fikir ul{margin:6px 0 0;padding-left:18px} details.fikir li{margin:5px 0} details.fikir a{text-decoration:underline}
.galeri{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
/* Bilgisayar / geniş ekran: sayfa ekranı doldurur, kartlar sütunlara dizilir (telefon görünümü değişmez) */
@media (min-width:900px){
  body[data-genel="1"] main{max-width:1320px;padding-left:32px;padding-right:32px}
  body[data-genel="0"] main{max-width:960px}
  h1{font-size:26px}
  #kanallar,#evergreen,#trendler{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px;
    align-items:start}
  #kanallar>:not(article),#evergreen>:not(article),#trendler>:not(article){grid-column:1/-1}
  #kanallar>article,#evergreen>article,#trendler>article{margin-bottom:0}
  #kanallar>.filtreler{margin:0;padding:0;flex-wrap:wrap;overflow:visible}
  #sira-disi>.kart{display:grid;grid-template-columns:repeat(2,1fr)} #sira-disi>.kart>.video{margin-top:0}
  .galeri{grid-template-columns:repeat(6,1fr);gap:10px}
  .kaydir .one-cikanlar{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;overflow:visible;
    margin:0;padding:0}
  .kaydir .kart.one{max-width:none}
  .kaydir .ok-dugme,.kaydir+.noktalar,.mobil-ipucu{display:none}
  #benim{max-width:760px}
  .alt-menu{left:50%;right:auto;transform:translateX(-50%);width:640px;border:1px solid var(--cizgi);border-bottom:0;
    border-radius:16px 16px 0 0}
  .bildirim{bottom:96px}
}
.galeri a{position:relative;border-radius:8px;overflow:hidden;aspect-ratio:16/9;background:var(--zemin2)}
.galeri img{width:100%;height:100%;object-fit:cover}
.galeri span{position:absolute;left:4px;bottom:4px;background:rgba(0,0,0,.7);color:#fff;font-size:11px;padding:1px 5px;border-radius:4px}
.video.sd{margin-top:0} .kat{color:var(--vurgu)} .artis{color:var(--iyi);font-weight:600}
.selam{font-size:18px;font-weight:700;margin:18px 0 0}
.karsilama{padding:14px;margin-top:14px;border-width:2px} .karsilama ul{padding-left:0;list-style:none} .karsilama li{margin:8px 0}
.rz.yeni-ziyaret{background:var(--iyi);color:#fff} .rz.ilgi{color:var(--vurgu)}
.profil-link{display:flex;justify-content:space-between;align-items:center;padding:16px;font-weight:600;margin-top:20px}
.profil label{display:block;font-weight:600;margin:10px 0}
.profil input:not([type=checkbox]){display:block;width:100%;margin-top:6px;padding:11px 12px;border:1px solid var(--cizgi);
  border-radius:10px;background:var(--bg);color:var(--yazi);font:inherit}
.profil .etiket{font-weight:600;margin:16px 0 8px} .profil .anahtar{font-weight:400}
.avatarlar{display:flex;flex-wrap:wrap;gap:8px}
.avatar-sec{width:46px;height:46px;font-size:24px;border-radius:50%;border:2px solid var(--cizgi);background:var(--zemin2);cursor:pointer}
.avatar-sec.secili{border-color:var(--vurgu)} .profil .eylem{margin:18px 0 0}
.metin p{font-size:15px} .metin a{text-decoration:underline}
footer a{text-decoration:underline}
.kalicilik{margin:6px 0 10px;padding:10px 12px;border-radius:12px;background:var(--zemin2);font-size:14px}
.kalicilik.kalici b{color:var(--iyi)} .kalicilik.yari b{color:var(--uyari)} .kalicilik.gecici b{color:var(--vurgu)}
.eg-kart .video{padding-right:52px} .eg-kart .one-neden{margin-top:0}
.bildirim{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);background:var(--yazi);color:var(--bg);
  padding:10px 16px;border-radius:999px;font-size:14px;z-index:20;max-width:90%}
article.kart{position:relative} .kanal{padding-right:44px}
.yildiz{position:absolute;top:6px;right:6px;width:44px;height:44px;border:0;background:none;font-size:26px;line-height:1;color:var(--soluk);cursor:pointer;z-index:1}
.yildiz.on{color:#f5b301} .yildiz:active{transform:scale(.85)}
.fav-oge{position:relative;border-top:1px solid var(--cizgi)} .fav-oge:first-child{border-top:0}
.fav-oge .arama{padding-right:56px} .fav-oge .yildiz{top:50%;margin-top:-22px}
.btn.ikon{padding:10px 12px} header .btn{flex:none}
.ceviri{color:var(--soluk);font-weight:400}
.rz.kz.yuksek{color:var(--iyi)} .rz.kz.orta{color:var(--uyari)} .rz.kz.dusuk,.rz.kz.cok_dusuk{color:var(--soluk)}
.oneri{margin:12px 14px 0;padding:10px 12px;border-radius:12px;background:var(--zemin2);font-size:14px}
.oneri ul{margin:6px 0 0;padding-left:0;list-style:none} .oneri li{margin-top:6px}
footer{color:var(--soluk);font-size:12px;margin-top:28px}
"""

# Sayfa açılınca durum.json'dan kota uyarısını, ana sayfada aramalar.json'dan "Aramalarım" listesini okur.
JS = """
var KOK = document.body.dataset.kok;
document.getElementById("tara").addEventListener("click", function () { document.getElementById("ipucu").hidden = false; });
function uyar(metin) { var u = document.getElementById("uyari"); if (u.hidden) { u.textContent = metin; u.hidden = false; } }
fetch(KOK + "durum.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (d) {
  if (d.uyari) uyar(d.uyari);
}).catch(function () {});
// Genel tarama her gün yenilenir; 30 saatten eskiyse bir şeyler ters gitmiştir.
var saat = (Date.now() - Date.parse(document.body.dataset.uret)) / 36e5;
if (document.body.dataset.genel === "1" && saat > 30)
  uyar("⚠️ Son genel tarama " + Math.floor(saat / 24) + " gün önce yapıldı, yeni tarama gelmedi. "
       + "GitHub'da Actions sekmesine bak ya da Claude'a söyle.");
var liste = document.getElementById("aramalar");
if (liste) fetch("aramalar.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (l) {
  l.slice(0, 10).forEach(function (x) {
    var a = document.createElement("a"); a.className = "arama"; a.href = x.dosya;
    var ic = document.createElement("span"); ic.className = "ic";
    var ad = document.createElement("span"); ad.className = "ad"; ad.textContent = (x.tip === "kanal" ? "🔍 " : "📌 ") + x.baslik;
    var alt = document.createElement("span"); alt.className = "kucuk";
    alt.textContent = x.tip === "kanal" ? x.tarih + " · kanal incelemesi · " + x.ozet
      : x.tarih + " · " + x.dil + " · " + x.kanal + " kanal" + (x.bos ? " · 🟢 " + x.bos + " boş alan" : "");
    var ok = document.createElement("span"); ok.className = "ok"; ok.textContent = "›";
    ic.append(ad, alt); a.append(ic, ok); liste.append(a);
  });
  if (l.length) document.getElementById("aramalar-kutu").hidden = false;
}).catch(function () {});

// Beğendiklerim: telefonun kendi hafızasında (localStorage) tutulur; her gün yeni tarama gelse de kaybolmaz.
var FAV = "nis-begendiklerim";
function favOku() { try { return JSON.parse(localStorage.getItem(FAV)) || []; } catch (e) { return []; } }
function favYaz(l) { try { localStorage.setItem(FAV, JSON.stringify(l)); } catch (e) {} }
function el(etiket, sinif, metin) { var x = document.createElement(etiket); if (sinif) x.className = sinif; if (metin) x.textContent = metin; return x; }
function favCiz() {
  var l = favOku(), ids = {};
  l.forEach(function (x) { ids[x.id] = 1; });
  document.querySelectorAll(".yildiz").forEach(function (b) {
    var on = !!ids[b.dataset.id];
    b.textContent = on ? "★" : "☆"; b.classList.toggle("on", on);
    b.setAttribute("aria-label", on ? "Beğendiklerimden çıkar" : "Beğendiklerime ekle");
  });
  var sayac = document.getElementById("fav-sayi");
  if (sayac) sayac.textContent = l.length ? "⭐ " + l.length : "⭐";
  var kutu = document.getElementById("fav-kutu"), liste = document.getElementById("fav-liste");
  if (!kutu) return;
  liste.textContent = "";
  l.forEach(function (x) {
    var oge = el("div", "fav-oge");
    var a = el("a", "arama"); a.href = "https://www.youtube.com/channel/" + x.id; a.target = "_blank"; a.rel = "noopener";
    var ic = el("span", "ic");
    ic.append(el("span", "ad", x.ulke + " " + x.ad), el("span", "kucuk", "Konusu: " + x.konu + " · " + x.ozet),
              el("span", "kucuk", "Eklendi: " + x.tarih));
    a.append(ic);
    var b = el("button", "yildiz on", "★"); b.type = "button"; b.dataset.id = x.id; b.setAttribute("aria-label", "Beğendiklerimden çıkar");
    var inc = el("a", "incele sahip", "🔍 İncele"); inc.dataset.id = x.id; inc.href = document.body.dataset.tara;
    inc.target = "_blank"; inc.rel = "noopener";
    oge.append(a, b, inc); liste.append(oge);
  });
  var bos = document.getElementById("fav-bos"); if (bos) bos.hidden = !!l.length;
  liste.hidden = !l.length; kutu.hidden = false;
  inceleIsaretle();
}

// İncele: daha önce incelenen kanallarda düğme doğrudan inceleme sayfasını açar;
// incelenmemişse kanal linkini panoya kopyalar ve GitHub'daki tarama sayfasını açar.
var INCELENEN = {};
function inceleIsaretle() {
  document.querySelectorAll(".incele").forEach(function (b) {
    var dosya = INCELENEN[b.dataset.id];
    if (dosya) { b.href = KOK + dosya; b.target = "_self"; b.textContent = "🔍 İncelemeyi aç"; b.classList.add("hazir"); b.classList.remove("sahip"); }
  });
}
// vidiq.json'ı bulutta çalışan günlük görev yazar (vidiq_isle.py): vidIQ'nun bulduğu, Türkçe olmayan, büyüyen kanallar.
var VK = document.getElementById("vidiq-kutu");
if (VK) fetch(KOK + "vidiq.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (d) {
  var l = (d.kanallar || []).slice(0, 15);
  if (!l.length) return;
  function k(n) { return n >= 1e6 ? (n / 1e6).toFixed(1).replace(".0", "") + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n || 0); }
  function esc(t) { var e = document.createElement("span"); e.textContent = t || ""; return e.innerHTML; }
  VK.innerHTML = '<h2>🔎 vidIQ&#39;nun bulduğu kanallar</h2><p class="alt-baslik">Niş Avcısı&#39;nın adaylarına benzeyen ve hızla '
    + 'büyüyen kanallar. ✅ olanları iki sistem de buldu.</p><div class="kart bolum"><ul class="satirlar">'
    + l.map(function (x) {
        return '<li>' + (x.ortak ? '✅ ' : '') + '<a href="https://www.youtube.com/channel/' + encodeURIComponent(x.id)
          + '" target="_blank" rel="noopener"><b>' + esc(x.ad) + '</b></a> · ' + k(x.abone) + ' abone'
          + (x.buyume30 ? ' · <span class="artis">30 günde +%' + Math.round(x.buyume30) + '</span>' : '')
          + (x.ort_izlenme ? ' · video başı ' + k(x.ort_izlenme) : '')
          + (x.nis ? '<br><span class="ceviri">' + esc(x.nis.split(",").slice(0, 2).join(", ")) + ' · ' + esc(x.kaynak) + '</span>' : '')
          + '</li>';
      }).join("") + '</ul></div>';
  VK.hidden = false;
}).catch(function () {});
fetch(KOK + "aramalar.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (l) {
  l.forEach(function (x) { if (x.tip === "kanal" && x.kanal_id) INCELENEN[x.kanal_id] = x.dosya; });
  inceleIsaretle();
}).catch(function () {});
document.addEventListener("click", function (e) {
  var inc = e.target.closest(".incele");
  if (inc && !inc.classList.contains("hazir")) {
    var link = "https://www.youtube.com/channel/" + inc.dataset.id;
    try { navigator.clipboard.writeText(link); } catch (h) {}
    document.getElementById("ipucu-incele").hidden = false;
    return;  // bağlantı yeni sekmede GitHub'ı açar
  }
  var b = e.target.closest(".yildiz");
  if (!b) return;
  e.preventDefault();
  var l = favOku(), i = l.findIndex(function (x) { return x.id === b.dataset.id; });
  if (i >= 0) l.splice(i, 1);
  else l.unshift({id: b.dataset.id, ad: b.dataset.ad, konu: b.dataset.konu, ozet: b.dataset.ozet, ulke: b.dataset.ulke,
                  tarih: new Date().toLocaleDateString("tr-TR")});
  favYaz(l); favCiz();
});
favCiz();

// ---- Yardımcılar
function oku(k, v) { try { var x = localStorage.getItem(k); return x === null ? v : JSON.parse(x); } catch (e) { return v; } }
function yaz(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
function bildir(metin) {
  var b = document.getElementById("bildirim"); b.textContent = metin; b.hidden = false;
  clearTimeout(bildir.t); bildir.t = setTimeout(function () { b.hidden = true; }, 2500);
}

// ---- Sahip modu: Tara / İncele düğmeleri sadece uygulama sahibine görünür (misafirler tarama yapamaz).
if (/[?&]sahip=1/.test(location.search)) yaz("nis-sahip", true);
if (oku("nis-sahip", false)) document.body.classList.add("sahip-modu");

// ---- Profil (sadece bu cihazda)
var PROFIL = oku("nis-profil", {ad: "", avatar: "", ilgi: [], kelimeler: []});
if (PROFIL.avatar) document.getElementById("profil-dugme").textContent = PROFIL.avatar;
var selam = document.getElementById("selam");
if (selam && PROFIL.ad) { selam.textContent = "Merhaba " + PROFIL.ad + " 👋"; selam.hidden = false; }

// ---- İlk açılışta kısa tanıtım
var karsilama = document.getElementById("karsilama");
if (karsilama && !oku("nis-tanitim", false)) {
  karsilama.hidden = false;
  document.getElementById("karsilama-kapat").addEventListener("click", function () {
    yaz("nis-tanitim", true); karsilama.hidden = true;
  });
}

// ---- Kartlar: son ziyaretten beri yeni olanlar ve ilgi alanına uyanlar işaretlenir.
var oncekiZiyaret = oku("nis-son-ziyaret", "");
var kartlar = Array.prototype.slice.call(document.querySelectorAll(".kanal-kart"));
var yeniSayi = 0;
function ilgiliMi(k) {
  var metin = k.dataset.metin || "";
  return PROFIL.ilgi.indexOf(k.dataset.kat) >= 0 ||
         PROFIL.kelimeler.some(function (w) { return w && metin.indexOf(w.toLowerCase()) >= 0; });
}
kartlar.forEach(function (k) {
  var rzs = k.querySelector(".rzs");
  k.dataset.yeni = oncekiZiyaret && k.dataset.ilk && k.dataset.ilk > oncekiZiyaret ? "1" : "0";
  if (k.dataset.yeni === "1") { yeniSayi++; rzs.prepend(el("span", "rz yeni-ziyaret", "🆕 Son ziyaretinden beri yeni")); }
  k.dataset.ilgi = ilgiliMi(k) ? "1" : "0";
  if (k.dataset.ilgi === "1") rzs.prepend(el("span", "rz ilgi", "🙋 İlgi alanın"));
});
if (selam && yeniSayi) { selam.textContent = (PROFIL.ad ? "Merhaba " + PROFIL.ad + " 👋 " : "") + "Son ziyaretinden beri "
                         + yeniSayi + " yeni kanal var."; selam.hidden = false; }
if (document.body.dataset.genel === "1") yaz("nis-son-ziyaret", new Date().toISOString());

// ---- Filtreler ve arama
var aktifFiltre = "hepsi";
function filtrele() {
  var q = ((document.getElementById("ara-kutu") || {}).value || "").toLocaleLowerCase("tr").trim(), gorunen = 0;
  kartlar.forEach(function (k) {
    var d = k.dataset, uy = {hepsi: true, ilgi: d.ilgi === "1", yeni: d.yeni === "1" || (!oncekiZiyaret && d.ilk >= document.body.dataset.uret.slice(0, 10)),
      yuksek: d.kazanc === "yuksek", tr: d.tr === "1", kisa: d.kisa === "1"}[aktifFiltre];
    var goster = uy && (!q || (d.metin || "").indexOf(q) >= 0);
    k.hidden = !goster; if (goster) gorunen++;
  });
  var bos = document.getElementById("filtre-bos"); if (bos) bos.hidden = gorunen > 0;
}
document.querySelectorAll("#filtreler .filtre").forEach(function (b) {
  b.addEventListener("click", function () {
    if (b.dataset.f === "ilgi" && !PROFIL.ilgi.length && !PROFIL.kelimeler.length) {
      bildir("Önce Profil'den ilgi alanlarını seç 👤"); setTimeout(function () { location.href = KOK + "profil.html"; }, 900); return;
    }
    document.querySelectorAll("#filtreler .filtre").forEach(function (x) { x.classList.remove("secili"); });
    b.classList.add("secili"); aktifFiltre = b.dataset.f; filtrele();
  });
});
var araKutu = document.getElementById("ara-kutu");
if (araKutu) araKutu.addEventListener("input", filtrele);

// ---- Paylaş
document.addEventListener("click", function (e) {
  var p = e.target.closest(".paylas");
  if (!p) return;
  var veri = {title: "Niş Avcısı", text: p.dataset.baslik, url: p.dataset.url};
  if (navigator.share) navigator.share(veri).catch(function () {});
  else { try { navigator.clipboard.writeText(p.dataset.baslik + " " + p.dataset.url); bildir("Link kopyalandı 📋"); } catch (h) {} }
});

// ---- Alt menü: görünen bölümü işaretle
var menu = document.querySelectorAll(".alt-menu a");
function menuIsaretle(id) { menu.forEach(function (a) { a.classList.toggle("aktif", a.dataset.b === id); }); }
if ("IntersectionObserver" in window && document.body.dataset.genel === "1") {
  var gozcu = new IntersectionObserver(function (l) {
    l.forEach(function (g) { if (g.isIntersecting) menuIsaretle(g.target.id); });
  }, {rootMargin: "-40% 0px -55% 0px"});
  document.querySelectorAll("section[id]").forEach(function (s) { gozcu.observe(s); });
}

// ---- Profil sayfası
var form = document.getElementById("profil-form");
if (form) {
  var ad = document.getElementById("p-ad"), kelime = document.getElementById("p-kelime"), sahip = document.getElementById("p-sahip");
  ad.value = PROFIL.ad; kelime.value = PROFIL.kelimeler.join(", "); sahip.checked = !!oku("nis-sahip", false);
  var secAvatar = PROFIL.avatar;
  document.querySelectorAll(".avatar-sec").forEach(function (b) {
    b.classList.toggle("secili", b.dataset.a === secAvatar);
    b.addEventListener("click", function () {
      secAvatar = b.dataset.a;
      document.querySelectorAll(".avatar-sec").forEach(function (x) { x.classList.toggle("secili", x === b); });
    });
  });
  // Görünüm seçimi anında uygulanır ve kaydedilir (Kaydet'e basmaya gerek yok).
  var tema = oku("nis-tema", "oto");
  document.querySelectorAll(".tema-sec").forEach(function (b) {
    b.classList.toggle("secili", b.dataset.tema === tema);
    b.addEventListener("click", function () {
      tema = b.dataset.tema; yaz("nis-tema", tema);
      if (tema === "oto") delete document.documentElement.dataset.tema; else document.documentElement.dataset.tema = tema;
      document.querySelectorAll(".tema-sec").forEach(function (x) { x.classList.toggle("secili", x === b); });
    });
  });
  document.querySelectorAll(".ilgi-sec").forEach(function (b) {
    b.classList.toggle("secili", PROFIL.ilgi.indexOf(b.dataset.kat) >= 0);
    b.addEventListener("click", function () { b.classList.toggle("secili"); });
  });
  document.getElementById("p-kaydet").addEventListener("click", function () {
    PROFIL = {ad: ad.value.trim(), avatar: secAvatar || "",
              ilgi: Array.prototype.map.call(document.querySelectorAll(".ilgi-sec.secili"), function (b) { return b.dataset.kat; }),
              kelimeler: kelime.value.split(",").map(function (w) { return w.trim(); }).filter(Boolean)};
    yaz("nis-profil", PROFIL); yaz("nis-sahip", sahip.checked);
    document.body.classList.toggle("sahip-modu", sahip.checked);
    if (PROFIL.avatar) document.getElementById("profil-dugme").textContent = PROFIL.avatar;
    bildir("Kaydedildi ✅");
  });
  document.getElementById("p-sil").addEventListener("click", function () {
    if (!confirm("Profilin, beğendiklerin ve ayarların bu cihazdan silinsin mi?")) return;
    ["nis-profil", "nis-sahip", "nis-begendiklerim", "nis-son-ziyaret", "nis-tanitim", "nis-tema"].forEach(function (k) {
      try { localStorage.removeItem(k); } catch (h) {}
    });
    bildir("Silindi"); setTimeout(function () { location.reload(); }, 800);
  });
}

// ---- Öne çıkanlar: ‹ › okları ve hangi kartta olduğunu gösteren noktalar
var serit = document.getElementById("one-cikanlar");
if (serit) {
  var sol = document.querySelector(".ok-dugme.sol"), sag = document.querySelector(".ok-dugme.sag");
  var noktalar = document.querySelectorAll("#noktalar span");
  var adim = function () { var k = serit.querySelector(".kart"); return k ? k.getBoundingClientRect().width + 10 : 300; };
  var kartlarS = serit.querySelectorAll(".kart");
  var git = function (yon) {  // bir sonraki/önceki kartı tam başa hizala
    var i = Math.max(0, Math.min(kartlarS.length - 1, Math.round(serit.scrollLeft / adim()) + yon));
    var fark = kartlarS[i].getBoundingClientRect().left - serit.getBoundingClientRect().left - 16;
    serit.scrollTo({left: serit.scrollLeft + fark, behavior: "smooth"});
  };
  sol.addEventListener("click", function () { git(-1); });
  sag.addEventListener("click", function () { git(1); });
  var guncelle = function () {
    var en = serit.scrollWidth - serit.clientWidth;
    sol.disabled = serit.scrollLeft <= 4;
    sag.disabled = serit.scrollLeft >= en - 4;
    var i = Math.round(serit.scrollLeft / adim());
    noktalar.forEach(function (n, j) { n.classList.toggle("aktif", j === Math.min(i, noktalar.length - 1)); });
  };
  serit.addEventListener("scroll", guncelle, {passive: true});
  window.addEventListener("resize", guncelle);
  guncelle();
}

// ---- Çevrimdışı açılabilsin (uygulama mağazası için de gerekli)
if ("serviceWorker" in navigator) navigator.serviceWorker.register(KOK + "sw.js").catch(function () {});
"""


def rapor(sonuc, nisler, a, yol, harcanan, kok=""):
    e = html.escape
    simdi = dt.datetime.now(dt.timezone.utc)
    tarih = simdi.astimezone(TR_SAAT).strftime("%d.%m.%Y %H:%M")
    kapsam = "Türkçe" if a.bolge == "TR" else ("Global" if not a.bolge else e(a.bolge))

    def rozetler(k):
        r = ""
        if k["hizli"]:
            r += '<span class="rz hiz">🔥 Hızlanıyor</span>'
        if k["yeni"]:
            r += '<span class="rz yeni">Yeni bulundu</span>'
        if k["kanal_yasi"] < 120:
            r += '<span class="rz">Çok yeni kanal</span>'
        if k["kisa"]:
            r += '<span class="rz">Kısa video</span>'
        return r

    terim_haritasi = terim_haritasi_kur(nisler)
    trend_firsati = {n["terim"]: n.get("firsat") for n in nisler}
    tr = getattr(a, "ceviri_tr", {})  # yabancı metin → Türkçesi
    pazarlar = getattr(a, "pazarlar", {})  # kanal_id → İngilizce/Türkiye pazar ölçümü

    def cevirili(metin):
        t = tr.get(metin)
        return f'{e(metin)} <span class="ceviri">({e(t)})</span>' if t else e(metin)

    def fikir_html(terim):
        return fikir_kutusu(getattr(a, "fikirler", {}).get(terim, []), tr)

    def evergreen_html():
        veri = getattr(a, "evergreen_veri", None) or {}
        liste = veri.get("liste", [])
        bas = ('<section id="evergreen"><h2>🌲 Kalıcı içerik (evergreen)</h2><p class="alt-baslik">6 ay – 2 yıl önce '
               'yüklenmiş ama hâlâ izlenen, küçük kanallara ait videolar. Trend gibi sönmez; yıllarca izlenip '
               'kazandırmaya devam eder.</p>')
        if not liste:
            return bas + '<p class="bos-not">İlk evergreen taraması bir sonraki genel taramada yapılacak.</p></section>'
        konular = evergreen_konular(liste)
        cipler = "".join(f'<a class="cip" href="{arama_link(t)}" target="_blank" rel="noopener">{cevirili(t)} · '
                         f'{n} kanal</a>' for t, n, _ in konular)
        kartlar = ""
        for x in liste[:20]:
            konusu = kanal_konusu(dict(kanal_id=x["kanal_id"], basliklar=[[x["baslik"]] + x["etiketler"]],
                                       kategori=x["kaynak"], yt_konular=x.get("yt_konular", [])), {})
            kazanc = kazanc_seviyesi(x["ulke"], x["dil"])
            ay = round(x["yas_gun"] / 30)
            abone = "abone gizli" if x["abone"] is None else f'{kisa(x["abone"])} abone'
            ozet = f'{ay} ay önce yüklenen videosu {kisa(x["izlenme"])} izlenmede, hâlâ izleniyor'
            canli = (f'<span class="kucuk"><b class="artis">✅ Hâlâ canlı: son {x["olcum_gun"]} günde günde '
                     f'{kisa(x["gunluk_son"])} izlenme</b></span>' if x.get("gunluk_son") else "")
            kartlar += (
                f'<article class="kart eg-kart">'
                f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(x["kanal_id"])}" '
                f'data-ad="{e(x["kanal"])}" data-konu="{e(konusu)}" data-ozet="{e(ozet)}" data-ulke="{bayrak(x["ulke"])}">☆</button>'
                f'<a class="video" href="{video_link(x["id"])}" target="_blank" rel="noopener" style="margin-top:0;border-top:0">'
                f'<img src="{e(x["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(x["baslik"])}</span>'
                + (f'<span class="kucuk ceviri">{e(tr[x["baslik"]])}</span>' if tr.get(x["baslik"]) else "")
                + f'<span class="kucuk">🌲 <b>{ay} ay önce</b> yüklendi · {kisa(x["izlenme"])} izlenme · '
                  f'ortalama günde {kisa(x["gunluk_ort"])}</span>{canli}</span></a>'
                f'<p class="one-neden">{bayrak(x["ulke"])} <b>{e(x["kanal"])}</b> · {abone} · '
                f'Konusu: <b>{cevirili(konusu)}</b>'
                + (f' · <span class="rz kz {kazanc}">{KAZANC_ETIKET[kazanc]}</span>' if kazanc else "") + '</p>'
                f'<div class="eylem"><a class="gir" href="{kanal_link(x["kanal_id"])}" target="_blank" rel="noopener">'
                f'Kanala gir ›</a>{incele_dugmesi(x["kanal_id"])}'
                f'<button class="paylas" type="button" aria-label="Paylaş" data-url="{video_link(x["id"])}" '
                f'data-baslik="{e(x["kanal"])}: {e(ozet)}">↗</button></div></article>')
        tarih = zaman(veri["ts"]).astimezone(TR_SAAT).strftime("%d.%m")
        return (bas + (f'<p class="kucuk">Kalıcı konular (dokun, YouTube\'da ara):</p><div class="cipler" '
                       f'style="margin-bottom:12px">{cipler}</div>' if cipler else "")
                + kartlar + f'<p class="kucuk">Evergreen listesi {tarih} tarihinde tarandı, haftada bir yenilenir; '
                            f'"hâlâ canlı" ölçümü her gün güncellenir.</p></section>')

    def hafta_html():
        l = getattr(a, "hafta", [])
        if not l:
            return ""
        satirlar = "".join(
            f'<li><b>{cevirili(x["terim"])}</b> · {x["kanal"]} kanal'
            + (f' · <span class="artis">+{x["artis"]} kanal bu hafta</span>' if x["artis"] > 0
               else f' · {x["gorulme"]} taramada görüldü')
            + f' · {kisa(x["toplam"])} izlenme</li>' for x in l)
        return ('<h2>📅 Bu hafta</h2><p class="alt-baslik">Son 7 günde en çok yükselen konular.</p>'
                f'<div class="kart bolum"><ul class="satirlar">{satirlar}</ul></div>')

    def karne_html():
        k = getattr(a, "karne", None)
        if not k:
            return ""
        bas = ('<h2>📋 Geçmiş karnesi</h2><p class="alt-baslik">Önceden bulduklarımız sonradan ne oldu? '
               'Uygulamanın isabetini buradan takip et.</p>')
        if k["bos"]:
            return (bas + f'<div class="kart bolum"><p>⏳ Karne birikiyor. Bulduğumuz kanalları ve konuları en az 3 gün '
                          f'takip ettikten sonra sonuçlarını burada göstereceğim. ({k["gun"]} gündür veri toplanıyor.)</p></div>')
        govde = ""
        if k["kanallar"]:
            ozet = (f'<p>Önceden bulduğumuz <b>{len(k["kanallar"])} kanal</b> o günden beri ortalama '
                    f'<b>%{k["orta_buyume"] * 100:.0f}</b> büyüdü. İzlenmesini en az ikiye katlayanların oranı: '
                    f'<b>%{k["iki_kat_oran"] * 100:.0f}</b>.</p>')
            satirlar = "".join(
                f'<li><a href="{kanal_link(x["kanal_id"])}" target="_blank" rel="noopener"><b>{e(x["kanal"])}</b></a> · '
                f'{x["gun"]} gün önce bulundu · {kisa(x["ilk"])} → <b>{kisa(x["simdi"])}</b> '
                f'<span class="artis">(%{x["buyume"] * 100:+.0f})</span></li>' for x in k["kanallar"][:5])
            govde += f'<div class="kart bolum"><h3>📺 Bulduğumuz kanallar</h3>{ozet}<ul class="satirlar">{satirlar}</ul></div>'
        if k["konular"]:
            buyuyen = sum(x["son"] > x["ilk"] for x in k["konular"])
            satirlar = "".join(
                f'<li>{"✅" if x["son"] > x["ilk"] else "➖"} <b>{cevirili(x["terim"])}</b> · {x["gun"]} gün önce '
                f'{FIRSAT_ETIKET[x["seviye"]][1]} dedik · {x["ilk"]} → <b>{x["son"]}</b> kanal</li>'
                for x in k["konular"][:8])
            govde += (f'<div class="kart bolum"><h3>🎯 Fırsat dediğimiz konular</h3><p>{len(k["konular"])} konudan '
                      f'<b>{buyuyen}</b> tanesinde o günden beri daha çok yeni kanal tuttu.</p><ul class="satirlar">{satirlar}</ul></div>')
        return bas + govde

    def kanal_kart(k):
        abone = "abone gizli" if k["abone"] is None else f'{kisa(k["abone"])} abone'
        av = (f'<img class="av" src="{e(k["kapak"])}" alt="" loading="lazy">' if k["kapak"]
              else f'<span class="av bos">{e(k["kanal"][:1].upper())}</span>')
        v = k["en_iyi"]
        konusu = kanal_konusu(k, terim_haritasi)
        kazanc = kazanc_seviyesi(k["ulke"], k["dil"])
        maddeler = kanal_onerileri(k, kazanc, pazarlar.get(k["kanal_id"]), trend_firsati.get(konusu))
        # Öneriler kapalı gelir, dokununca açılır: kart sade kalsın.
        oneri = (f'<details class="oneri"><summary>💡 Ne yapmalıyım? <span class="kucuk">({len(maddeler)} öneri)</span></summary><ul>'
                 + "".join(f"<li>{m}</li>" for m in maddeler) + "</ul></details>" if maddeler else "")
        kz = f'<span class="rz kz {kazanc}">{KAZANC_ETIKET[kazanc]}</span>' if kazanc else ""
        baslik_tr = tr.get(v["baslik"])
        ozet = f'{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla {kisa(k["kanal_izlenme"])} izlenme'
        yildiz = (f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(k["kanal_id"])}" '
                  f'data-ad="{e(k["kanal"])}" data-konu="{e(konusu)}" data-ozet="{e(ozet)}" data-ulke="{bayrak(k["ulke"])}">☆</button>')
        # Filtreler (Senin için, Yüksek kazanç, Türkiye, Shorts, Yeni) için kart bilgileri.
        veri = (f'data-kazanc="{kazanc or ""}" data-tr="{1 if k["ulke"] == "TR" or (k["dil"] or "").startswith("tr") else 0}" '
                f'data-kisa="{1 if k["kisa"] else 0}" data-kat="{e(k["kategori"])}" '
                f'data-ilk="{e(getattr(a, "ilk_gorulme", {}).get(k["kanal_id"], ""))}" '
                f'data-metin="{e((k["kanal"] + " " + konusu + " " + tr.get(konusu, "") + " " + k["kategori"]).lower())}"')
        paylas = (f'<button class="paylas" type="button" aria-label="Paylaş" data-url="{kanal_link(k["kanal_id"])}" '
                  f'data-baslik="{e(k["kanal"])}: {e(ozet)}">↗</button>')
        return (f'<article class="kart kanal-kart" {veri}>{yildiz}'
                f'<a class="kanal" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{av}'
                f'<span class="ic"><span class="ad">{bayrak(k["ulke"])} {e(k["kanal"])}</span>'
                f'<span class="patlama">🔥 Patlamış: <b>{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla '
                f'{kisa(k["kanal_izlenme"])}</b> izlenme</span>'
                f'<span class="kucuk konusu">Konusu: <b>{cevirili(konusu)}</b> · {e(k["kategori"])}</span>'
                f'<span class="kucuk">Video başına {kisa(k["video_basi"])} · {abone}</span></span>'
                f'<span class="ok">›</span></a>'
                f'<div class="rzs">{kz}{rozetler(k)}</div>'
                f'{oneri}'
                f'<a class="video" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
                f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
                + (f'<span class="kucuk ceviri">{e(baslik_tr)}</span>' if baslik_tr else "")
                + f'<span class="kucuk">▶ En çok izlenen: {kisa(v["izlenme"])} · {v["yayin"]}</span></span></a>'
                f'<div class="eylem"><a class="gir" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">Kanala gir ›</a>'
                f'{incele_dugmesi(k["kanal_id"])}{paylas}</div>'
                f'</article>')

    def gun_once(iso):
        g = (simdi - zaman(iso)).days
        return "bugün" if g < 1 else f"{g} gün önce"

    def trend_kart(n):
        tur = "çoğu kısa video" if n["kisa_oran"] > .6 else ("çoğu uzun video" if n["kisa_oran"] < .3 else "kısa + uzun karışık")
        ilk, hizli = n["ilk"], n["en_hizli"]
        takip_gun = (simdi - zaman(n.get("takip_basi", simdi.isoformat()))).days
        ilk_gun = (simdi - zaman(ilk["yayin_ts"])).days
        artis = n.get("kanal_artis")
        if takip_gun < 7 and ilk_gun <= 7:
            durum = '<span class="durum sicak">🔥 Bu hafta patladı</span>'
        elif artis and artis > 0:
            durum = f'<span class="durum sicak">📈 Yükseliyor · +{artis} kanal</span>'
        elif artis is None:
            durum = '<span class="durum yeni">🆕 İlk kez görüldü</span>'
        else:
            durum = f'<span class="durum">⏳ {takip_gun} gündür takipte</span>'
        cipler = "".join(f'<a class="cip" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{e(k["kanal"])}</a>'
                         for k in n["kanallar"][:6])
        f = n.get("firsat")
        firsat = (f'<dt>Fırsat</dt><dd><span class="fr {FIRSAT_ETIKET[f["seviye"]][0]}">{FIRSAT_ETIKET[f["seviye"]][1]}</span> '
                  f'{firsat_metni(f)}</dd>') if f else ""
        return (f'<article class="kart nis"><div class="nis-ust"><span class="nis-ad">{cevirili(n["terim"])}</span>{durum}</div>'
                f'<dl class="bilgi">{firsat}'
                f'<dt>Nereden çıktı</dt><dd><a href="{video_link(ilk["id"])}" target="_blank" rel="noopener">“{e(ilk["baslik"][:70])}”</a>'
                f' · {e(ilk["kanal"])} · {gun_once(ilk["yayin_ts"])}</dd>'
                f'<dt>Kaç kanal yapıyor</dt><dd><b>{n["kanal_sayisi"]} küçük kanal</b> · {n["video_sayisi"]} video · toplam {kisa(n["toplam"])} izlenme</dd>'
                f'<dt>En hızlısı</dt><dd>{e(hizli["kanal"])} · saatte {kisa(hizli["saatlik"])} izlenme</dd>'
                f'<dt>Tür</dt><dd>{tur} · {e(n["kategori"])}</dd>'
                f'</dl><div class="cipler">{cipler}</div>{fikir_html(n["terim"])}</article>')

    def harita_kart(terim, f, ana=False):
        cls, etiket = FIRSAT_ETIKET[f["seviye"]]
        cipler = "".join(f'<a class="cip" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{e(k["kanal"])}</a>'
                         for k in f["yeniler"])
        return (f'<article class="kart nis{" ana" if ana else ""}"><div class="nis-ust"><span class="nis-ad">{cevirili(terim)}</span>'
                f'<span class="fr {cls}">{etiket}</span></div>'
                f'<p class="kucuk aciklama">{"Ana konu · " if ana else ""}{firsat_metni(f)}</p>'
                + (f'<p class="kucuk">Bu konuda tutan yeni kanallar:</p><div class="cipler">{cipler}</div>' if cipler else "")
                + fikir_html(terim)
                + f'<a class="ara" href="{arama_link(terim)}" target="_blank" rel="noopener">YouTube\'da ara ›</a></article>')

    if a.konular:  # konulu tarama: önce fırsat haritası, sonra her konu kendi bölümünde
        baslik = "📌 " + e(", ".join(a.konular))
        govde = ""
        for k, (ana, altlar) in getattr(a, "harita", {}).items():
            altlar = sorted(altlar, key=lambda x: (FIRSAT_SIRA[x[1]["seviye"]], -x[1]["pay"]))
            bos = sum(f["seviye"] == "bos" for _, f in altlar)
            kl = getattr(a, "kalicilik", {}).get(k)
            kalici = (f'<p class="kalicilik {kl["seviye"]}"><b>{kl["etiket"]}</b> · 6 ay – 2 yıl önceki videoların tipik '
                      f'izlenmesi {kisa(kl["medyan"])}'
                      + (": eski videolar hâlâ izleniyor, yıllarca kazandırabilir." if kl["seviye"] == "kalici" else
                         ": eski videolar orta düzeyde izleniyor." if kl["seviye"] == "yari" else
                         ": eski videolar pek izlenmiyor, ilgi geçici olabilir.") + "</p>") if kl else ""
            govde += (f'<h2>🧭 Fırsat haritası: {e(k)}</h2>{kalici}<p class="alt-baslik">İnsanların YouTube\'da aradığı alt konular, '
                      f'en boştan en doyguna. {bos} boş alan bulundu.</p>'
                      + (harita_kart(k, ana, ana=True) if ana else "")
                      + ("".join(harita_kart(t, f) for t, f in altlar) or
                         '<p class="bos-not">Alt konu önerisi alınamadı.</p>'))
        for k in a.konular:
            ks = kanallara_grupla([s for s in sonuc if s["kaynak"] == k])
            govde += (f'<h2>📌 {e(k)}</h2><p class="alt-baslik">{len(ks)} yeni ve patlayan kanal. Karta dokun, kanal açılsın.</p>'
                      + ("".join(kanal_kart(x) for x in ks[:20]) or
                         '<p class="bos-not">Bu konuda son günlerde patlayan yeni küçük kanal bulunamadı. '
                         'Konu ya çok doymuş ya da henüz boş.</p>'))
        galeri = kapak_galerisi(sonuc, 9)
        if galeri:
            govde += ('<h2>🖼️ Kapak ilhamı</h2><p class="alt-baslik">Bu konularda en çok izlenen videoların kapakları. '
                      'Dokun, videoyu aç.</p>' + galeri)
        ust = f'<a class="geri" href="{kok}index.html">← Ana sayfa</a>'
        aramalar = ""
    else:
        baslik = "🎯 Niş Avcısı"
        tum_kanallar = kanallara_grupla(sonuc)
        filtreler = ('<div class="filtreler" id="filtreler">'
                     '<button class="filtre secili" data-f="hepsi">Hepsi</button>'
                     '<button class="filtre" data-f="ilgi">🙋 Senin için</button>'
                     '<button class="filtre" data-f="yeni">🆕 Yeni</button>'
                     '<button class="filtre" data-f="yuksek">💰 Yüksek kazanç</button>'
                     '<button class="filtre" data-f="tr">🇹🇷 Türkiye</button>'
                     '<button class="filtre" data-f="kisa">📱 Shorts</button></div>'
                     '<input class="ara-kutu" id="ara-kutu" type="search" placeholder="Kanal ya da konu ara…" '
                     'aria-label="Kanal ya da konu ara">'
                     '<p class="bos-not" id="filtre-bos" hidden>Bu filtreye uyan kanal yok.</p>')
        govde = ('<section id="kanallar"><h2>🔥 Patlayan kanallar</h2><p class="alt-baslik">Dünya genelinde ve Türkiye\'de '
                 'son 1 yılda açılıp az videoyla patlayan kanallar. Her alandan karışık: konusuna bak, beğendiğine gir.</p>'
                 + filtreler
                 + ("".join(kanal_kart(k) for k in genis_sirala(tum_kanallar)[:40]) or
                    '<p class="bos-not">Bu taramada uygun kanal çıkmadı. Bir sonraki taramayı bekle.</p>')
                 + "</section>")
        # Sıra dışı videolar + kapak ilhamı
        sd = sira_disi(sonuc)
        sd_html = "".join(
            f'<a class="video sd" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
            f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
            + (f'<span class="kucuk ceviri">{e(tr[v["baslik"]])}</span>' if tr.get(v["baslik"]) else "")
            + f'<span class="kucuk"><b class="kat">{v["oran"]:.0f}× normal</b> · {kisa(v["izlenme"])} izlenme · '
              f'kanalın normali {kisa(v["normal"])} · {bayrak(v["ulke"])} {e(v["kanal"])}</span></span></a>'
            for v in sd)
        govde += ('<section id="sira-disi"><h2>🎯 Sıra dışı videolar</h2><p class="alt-baslik">Kanalının normalinin '
                  'kat kat üstünde izlenen videolar. Bir konunun tuttuğunun en net işareti.</p>'
                  + (f'<div class="kart">{sd_html}</div>' if sd_html else
                     '<p class="bos-not">Bu taramada sıra dışı video çıkmadı.</p>')
                  + '<h2>🖼️ Kapak ilhamı</h2><p class="alt-baslik">En çok izlenen videoların kapakları. Dokun, videoyu aç.</p>'
                  + kapak_galerisi(sonuc) + "</section>")
        ust = ""
        govde += evergreen_html()

        def one_kart(c):
            ust_satir = f'<div class="one-etiket">{c["ikon"]} {c["baslik"]}</div>'
            if c["tip"] == "konu":
                n = c["n"]
                cipler = "".join(f'<a class="cip" href="{kanal_link(v["kanal_id"])}" target="_blank" rel="noopener">'
                                 f'{e(v["kanal"])}</a>' for v in n["kanallar"][:4])
                return (f'<article class="kart one">{ust_satir}<div class="nis-ad">{cevirili(n["terim"])}</div>'
                        f'<p class="one-neden">{c["neden"]}</p><div class="cipler">{cipler}</div></article>')
            k = c["k"]
            konusu = kanal_konusu(k, terim_haritasi)
            ozet = f'{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla {kisa(k["kanal_izlenme"])} izlenme'
            av = (f'<img class="av" src="{e(k["kapak"])}" alt="" loading="lazy">' if k["kapak"]
                  else f'<span class="av bos">{e(k["kanal"][:1].upper())}</span>')
            return (f'<article class="kart one">'
                    f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(k["kanal_id"])}" '
                    f'data-ad="{e(k["kanal"])}" data-konu="{e(konusu)}" data-ozet="{e(ozet)}" '
                    f'data-ulke="{bayrak(k["ulke"])}">☆</button>{ust_satir}'
                    f'<a class="kanal" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{av}'
                    f'<span class="ic"><span class="ad">{bayrak(k["ulke"])} {e(k["kanal"])}</span>'
                    f'<span class="kucuk">Konusu: <b>{cevirili(konusu)}</b></span></span></a>'
                    f'<p class="one-neden">{c["neden"]}</p>'
                    f'<div class="eylem"><a class="gir" href="{kanal_link(k["kanal_id"])}" target="_blank" '
                    f'rel="noopener">Kanala gir ›</a>{incele_dugmesi(k["kanal_id"])}</div></article>')

        kartlar = one_cikanlar(tum_kanallar, nisler)
        one = ('<h2>✨ Bugün öne çıkanlar</h2><p class="alt-baslik">Taramanın en dikkat çekenleri.<span class="mobil-ipucu"> '
               'Parmağınla yana kaydır ya da ‹ › oklarına bas.</span></p><div class="kaydir">'
               '<button class="ok-dugme sol" type="button" aria-label="Önceki" disabled>‹</button>'
               '<div class="one-cikanlar" id="one-cikanlar">' + "".join(one_kart(c) for c in kartlar) + '</div>'
               '<button class="ok-dugme sag" type="button" aria-label="Sonraki">›</button></div>'
               '<div class="noktalar" id="noktalar">' + '<span></span>' * len(kartlar) + '</div>') if kartlar else ""
        karsilama = ('<div class="kart karsilama" id="karsilama" hidden><b>👋 Niş Avcısı\'na hoş geldin!</b><ul>'
                     '<li>🔥 Her gün dünyada ve Türkiye\'de <b>az videoyla patlayan yeni kanalları</b> bulur.</li>'
                     '<li>🟢 Bir konunun <b>boş mu doymuş mu</b> olduğunu ölçer, hangi dilde yapman gerektiğini söyler.</li>'
                     '<li>⭐ Beğendiğin kanalı yıldızla; <b>👤 Profil</b>\'den ilgi alanlarını seç, sana özel filtrelensin.</li>'
                     '</ul><button class="btn ana" type="button" id="karsilama-kapat">Başlayalım</button></div>')
        aramalar = (f'<section id="bugun">{karsilama}<p class="selam" id="selam" hidden></p>{one}{hafta_html()}<div id="vidiq-kutu" hidden></div></section>')
        benim = ('<section id="benim"><div id="fav-kutu"><h2 id="begendiklerim">⭐ Beğendiklerim</h2>'
                 '<p class="alt-baslik">Yıldızladığın kanallar. Dokun, kanala gir; yıldıza basarsan listeden çıkar.</p>'
                 '<div class="kart" id="fav-liste"></div><p class="bos-not" id="fav-bos">Henüz yıldızladığın kanal yok. '
                 'Beğendiğin kanalın köşesindeki ☆\'a bas.</p></div>'
                 '<div id="aramalar-kutu" hidden><h2>🔍 Aramalar ve incelemeler</h2>'
                 '<p class="alt-baslik">Konu aramaları ve kanal incelemeleri burada saklanır. Dokun, sonuçları aç.</p>'
                 '<div class="kart aramalar" id="aramalar"></div></div>'
                 f'<a class="kart profil-link" href="{kok}profil.html"><span>👤 Profilim ve ilgi alanlarım</span>'
                 '<span class="ok">›</span></a></section>')
    trend = "".join(trend_kart(n) for n in nisler)
    trend_bolum = ('<h2>📊 Trend raporu</h2><p class="alt-baslik">Birden fazla yeni kanalda aynı anda patlayan konular: '
                   'nereden çıktı, kaç kanal yapıyor, ne kadar hızlı.</p>' + trend) if trend else ""
    yarim = (f'<p class="uyari">⚠️ Kota tarama sırasında bitti, sonuçlar eksik olabilir. {KOTA_NOTU}</p>'
             if getattr(a, "kota_yarim", False) else "")
    if not a.konular:
        trend_bolum = f'<section id="trendler">{trend_bolum or ""}{karne_html()}</section>{benim}'

    sayfa_yaz(yol, kok=kok, baslik=baslik, ust=ust, genel=not a.konular,
              meta=f'{"Arama" if a.konular else "Son tarama"}: {tarih} · {kapsam} · son {a.gun} gün',
              govde=f"""{yarim}
{aramalar}
{govde}
{trend_bolum}""",
              footer=f"""Sadece son {a.max_kanal_yasi} günde açılmış kanallar · ≤{kisa(a.max_abone)} abone · ≤{a.max_video} video ·
≥{kisa(a.min_izlenme)} izlenme · reklamla şişmiş videolar elendi · {harcanan} kota birimi.
Otomatik genel tarama her gün 11:17'de (kota yenilendikten sonra).<br>
💰 Kazanç etiketleri kanalın ülkesine/diline göre kaba bir tahmindir; gerçek kazanç konuya ve izleyiciye göre değişir.
Öneriler kurallara dayalı otomatik yorumlardır, son karar senin.""")


def sayfa_yaz(yol, *, kok, baslik, meta, govde, footer, ust="", genel=False):
    """Tüm sayfaların ortak iskeleti: başlık, Tara / ⭐ / ↻ düğmeleri, uyarılar, içerik, alt not."""
    uret = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    sayfa = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Niş Avcısı</title>
<meta name="theme-color" content="#d4361c"><link rel="manifest" href="{kok}manifest.json">
<link rel="icon" href="{kok}icon-192.png"><link rel="apple-touch-icon" href="{kok}icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Niş Avcısı">
<script>try{{var t=JSON.parse(localStorage.getItem("nis-tema"));if(t==="acik"||t==="koyu")document.documentElement.dataset.tema=t}}catch(e){{}}</script>
<style>{CSS}</style></head><body data-kok="{kok}" data-uret="{uret}" data-genel="{1 if genel else 0}"
data-tara="{TARA_LINKI}"><main>
{ust}<header><h1>{baslik}</h1>
<a class="btn ana sahip" href="{TARA_LINKI}" target="_blank" rel="noopener" id="tara">Tara</a>
<a class="btn ikinci ikon" href="{kok}index.html#begendiklerim" id="fav-sayi" aria-label="Beğendiklerim">⭐</a>
<a class="btn ikinci ikon" href="{kok}profil.html" id="profil-dugme" aria-label="Profil">👤</a>
<button class="btn ikinci ikon" onclick="location.reload()" aria-label="Yenile">↻</button></header>
<p class="meta">{meta}</p>
<p class="uyari" id="uyari" hidden></p>
<p class="ipucu" id="ipucu" hidden>GitHub'da <b>Run workflow</b>'a bas. Konu aramak için <b>Konu</b> kutusuna yaz
(ör. <i>tarih, borsa</i>); bir kanalı incelemek için <b>Kanal</b> kutusuna linkini yapıştır; ikisi de boşsa genel
tarama yapar. Yeşil <b>Run workflow</b>'a bas, 2 dakika sonra ↻ ile yenile.</p>
<p class="ipucu" id="ipucu-incele" hidden>📋 <b>Kanal linki kopyalandı.</b> Açılan GitHub sayfasında <b>Run workflow</b>'a bas,
<b>Kanal</b> kutusuna yapıştır, yeşil <b>Run workflow</b>'a bas. 1–2 dakika sonra inceleme <b>Aramalarım</b>'da çıkar.</p>
{govde}

<footer>{footer}<br><a href="{kok}gizlilik.html">Gizlilik ve kullanım</a> · YouTube verileri
<a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener">YouTube API Hizmetleri</a> ile alınır.</footer>
</main>
<nav class="alt-menu" aria-label="Bölümler">
<a href="{kok}index.html#bugun" data-b="bugun"><span>🏠</span>Bugün</a>
<a href="{kok}index.html#kanallar" data-b="kanallar"><span>🔥</span>Kanallar</a>
<a href="{kok}index.html#sira-disi" data-b="sira-disi"><span>🎯</span>Sıra dışı</a>
<a href="{kok}index.html#evergreen" data-b="evergreen"><span>🌲</span>Kalıcı</a>
<a href="{kok}index.html#trendler" data-b="trendler"><span>📊</span>Trendler</a>
<a href="{kok}index.html#benim" data-b="benim"><span>⭐</span>Benim</a></nav>
<div class="bildirim" id="bildirim" hidden></div>
<script>{JS}</script>
</body></html>"""
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(sayfa)


AVATARLAR = "😎 🦊 🐯 🚀 🎬 🎮 🎨 📚 🔥 🌍 💡 🎧".split()


def yardimci_sayfalar(site):
    """Profil ve gizlilik sayfaları (her taramada yeniden yazılır, kota harcamaz)."""
    kategoriler = "".join(f'<button type="button" class="filtre ilgi-sec" data-kat="{html.escape(k)}">{html.escape(k)}</button>'
                          for k in list(KATEGORILER.values()) + list(SABIT_KONULAR))
    avatarlar = "".join(f'<button type="button" class="avatar-sec" data-a="{a}">{a}</button>' for a in AVATARLAR)
    sayfa_yaz(os.path.join(site, "profil.html"), kok="", baslik="👤 Profilim",
              ust='<a class="geri" href="index.html">← Ana sayfa</a>', meta="Profilin sadece bu telefonda saklanır.",
              govde=f"""<section class="kart bolum profil" id="profil-form">
<label>Adın<input id="p-ad" maxlength="30" placeholder="Örn. Adnan" autocomplete="nickname"></label>
<p class="etiket">Avatarın</p><div class="avatarlar">{avatarlar}</div>
<p class="etiket">🎯 İlgi alanların <span class="kucuk">(Senin için filtresi bunlara göre çalışır)</span></p>
<div class="filtreler sarili">{kategoriler}</div>
<label>Kendi kelimelerin <span class="kucuk">(virgülle ayır)</span>
<input id="p-kelime" placeholder="Örn. yapay zeka, kripto, minecraft"></label>
<p class="etiket">🎨 Görünüm</p>
<div class="filtreler sarili"><button type="button" class="filtre tema-sec" data-tema="oto">📱 Otomatik (telefona göre)</button>
<button type="button" class="filtre tema-sec" data-tema="acik">☀️ Açık</button>
<button type="button" class="filtre tema-sec" data-tema="koyu">🌙 Koyu</button></div>
<p class="etiket">⚙️ Ayarlar</p>
<label class="anahtar"><input type="checkbox" id="p-sahip"> Tarama ve İncele düğmelerini göster
<span class="kucuk">(sadece uygulamanın sahibi için; diğer kullanıcılar tarama yapamaz)</span></label>
<div class="eylem"><button class="gir" type="button" id="p-kaydet">Kaydet</button>
<button class="incele" type="button" id="p-sil">Verilerimi sil</button></div>
</section>""",
              footer="Profil, beğendiklerin ve ayarların hiçbir sunucuya gönderilmez; sadece bu cihazın tarayıcı hafızasında durur.")
    sayfa_yaz(os.path.join(site, "gizlilik.html"), kok="", baslik="🔒 Gizlilik",
              ust='<a class="geri" href="index.html">← Ana sayfa</a>', meta="Gizlilik politikası ve kullanım koşulları",
              govde="""<section class="kart bolum metin">
<h3>Hangi verileri topluyoruz?</h3>
<p><b>Hiçbirini.</b> Niş Avcısı'nda hesap yoktur. Profilin (adın, avatarın, ilgi alanların), beğendiğin kanallar ve
ayarların yalnızca kendi cihazının tarayıcı hafızasında (localStorage) saklanır; hiçbir sunucuya gönderilmez.
Ayarlar sayfasındaki "Verilerimi sil" düğmesiyle hepsini istediğin an silebilirsin.</p>
<h3>YouTube verileri</h3>
<p>Uygulamadaki kanal, video ve izlenme bilgileri <a href="https://developers.google.com/youtube" target="_blank"
rel="noopener">YouTube API Hizmetleri</a> kullanılarak herkese açık YouTube verilerinden alınır. Uygulamayı kullanarak
<a href="https://www.youtube.com/t/terms" target="_blank" rel="noopener">YouTube Hizmet Şartları</a>'nı kabul etmiş
olursun. Google'ın verileri nasıl işlediği için:
<a href="https://policies.google.com/privacy" target="_blank" rel="noopener">Google Gizlilik Politikası</a>.
YouTube'dan alınan veriler en fazla 30 gün saklanır, sonra silinir.</p>
<h3>Tahminler hakkında</h3>
<p>Kazanç seviyeleri, fırsat puanları ve öneriler herkese açık verilere dayanan otomatik ve kaba tahminlerdir;
yatırım ya da finansal tavsiye değildir. Son karar her zaman senindir.</p>
<h3>Çerezler ve reklam</h3>
<p>Uygulama çerez, reklam ya da takip aracı kullanmaz.</p>
<h3>İletişim</h3>
<p>Sorular ve veri talepleri için iletişim adresi yakında eklenecek.</p>
</section>""",
              footer="Son güncelleme: " + dt.datetime.now(TR_SAAT).strftime("%d.%m.%Y"))


def kanal_rapor(x, pazar, tr, yol, harcanan, kok="../"):
    """Tek bir kanalın inceleme sayfası."""
    e = html.escape
    tarih = dt.datetime.now(TR_SAAT).strftime("%d.%m.%Y %H:%M")

    def cevirili(metin):
        t = tr.get(metin)
        return f'{e(metin)} <span class="ceviri">({e(t)})</span>' if t else e(metin)

    def dolar(ab):
        """Kesin rakam yerine büyüklük: tahmin kaba olduğu için "$213–$1.7K" yanıltıcı derecede kesin görünüyordu."""
        orta = (max(ab[0], 0.01) * max(ab[1], 0.01)) ** .5
        for sinir, metin in ((100, "100 doların altı"), (1_000, "yüzlerce dolar"), (10_000, "binlerce dolar"),
                             (100_000, "on binlerce dolar"), (1_000_000, "yüz binlerce dolar")):
            if orta < sinir:
                return metin
        return "milyonlarca dolar"

    abone = "gizli" if x["abone"] is None else kisa(x["abone"])
    av = (f'<img class="av buyuk-av" src="{e(x["kapak"])}" alt="" loading="lazy">' if x["kapak"]
          else f'<span class="av buyuk-av bos">{e(x["ad"][:1].upper())}</span>')
    ulke = ULKE_AD.get(x["ulke"], x["ulke"] or "belirtilmemiş")
    seviye = KAZANC_ETIKET.get(x["seviye"], "💰 Bilinmiyor")
    ozet = f'{sure_metni(x["yas"])} {x["video_sayisi"]} videoyla {kisa(x["toplam"])} izlenme'
    yildiz = (f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(x["id"])}" '
              f'data-ad="{e(x["ad"])}" data-konu="{e(x["konusu"])}" data-ozet="{e(ozet)}" data-ulke="{bayrak(x["ulke"])}">☆</button>')

    ust_kart = (f'<article class="kart">{yildiz}<a class="kanal" href="{kanal_link(x["id"])}" target="_blank" rel="noopener">{av}'
                f'<span class="ic"><span class="ad">{bayrak(x["ulke"])} {e(x["ad"])}</span>'
                f'<span class="kucuk">{e(x["handle"])} · {e(ulke)} · {yas_metni(x["yas"])}</span>'
                f'<span class="patlama">🔥 <b>{ozet}</b></span></span></a>'
                f'<div class="istat">'
                f'<div><b>{abone}</b><span>abone</span></div>'
                f'<div><b>{kisa(x["toplam"])}</b><span>toplam izlenme</span></div>'
                f'<div><b>{x["video_sayisi"]}</b><span>video</span></div>'
                f'<div><b>{kisa(x["video_basi"])}</b><span>video başına</span></div>'
                f'<div><b>{kisa(x["medyan"])}</b><span>tipik video*</span></div>'
                f'<div><b>%{x["etkilesim"] * 100:.1f}</b><span>etkileşim</span></div></div>'
                f'<div class="eylem"><a class="gir" href="{kanal_link(x["id"])}" target="_blank" rel="noopener">Kanala gir ›</a>'
                f'<button class="paylas" type="button" aria-label="Paylaş" data-url="{kanal_link(x["id"])}" '
                f'data-baslik="{e(x["ad"])}: {e(ozet)}">↗</button></div>'
                f'</article>')

    ypp = ("" if x["abone"] is None or x["abone"] >= 1000 else
           '<li>⚠️ 1.000 abonenin altında: büyük ihtimalle henüz YouTube para kazanma programında değil, şu an reklam geliri yok.</li>')
    kazanc = (f'<article class="kart bolum"><h3>💰 Tahmini kazanç</h3><ul class="satirlar">'
              f'<li>Pazar: <b>{seviye}</b>{" (dilden tahmin)" if x["seviye_tahmini"] else ""} · {e(ulke)}</li>'
              f'<li>Aylık ortalama: <b>{dolar(x["aylik_gelir"])}</b> seviyesi</li>'
              f'<li>Kanal açıldığından beri toplam: <b>{dolar(x["toplam_gelir"])}</b> seviyesi</li>'
              f'<li>İzlenmelerde Shorts payı: %{round(x["pay_kisa"] * 100)} (Shorts çok daha az kazandırır).</li>{ypp}'
              f'</ul><p class="kucuk">Sadece YouTube reklam geliri, kaba tahmin. Sponsorluk ve bağış dahil değil.</p></article>')

    youtube = ", ".join(cevirili(k) for k in x["youtube_konulari"]) or "—"
    cipler = "".join(f'<span class="cip duz">{e(w)}</span>' for w in x["sik_kelimeler"])
    aciklama = (f'<p class="kucuk">Açıklama: {e(x["aciklama"])}'
                + (f'<br><span class="ceviri">{e(tr[x["aciklama"]])}</span>' if tr.get(x["aciklama"]) else "")
                + '</p>') if x["aciklama"] else ""
    konu = (f'<article class="kart bolum"><h3>🎯 Konusu</h3><ul class="satirlar">'
            f'<li>Ana konu: <b>{cevirili(x["konusu"])}</b></li>'
            f'<li>YouTube\'un kategorileri: {youtube}</li>'
            f'<li>Dil: {e(dil_adi(x["dil"]))}</li></ul>'
            f'<p class="kucuk">Başlıklarında en sık geçenler:</p><div class="cipler">{cipler}</div>{aciklama}</article>')

    enb = max([v["izl"] for v in x["son10"]] or [1]) or 1
    cubuklar = "".join(
        f'<a class="cubuk-satir" href="{video_link(v["id"])}" target="_blank" rel="noopener"><span class="ad">{e(v["baslik"])}</span>'
        f'<span class="cubuk-dis"><span class="cubuk" style="display:block;width:{max(v["izl"] / enb * 100, 2):.0f}%"></span></span>'
        f'<span class="sayi">{kisa(v["izl"])}</span></a>' for v in reversed(x["son10"]))
    if x["gidisat"] is None:
        gidis = "Karşılaştırma için yeterli video yok."
    else:
        gidis = (f'{"📈 Yükselişte" if x["gidisat"] > .1 else ("📉 Düşüşte" if x["gidisat"] < -.1 else "➡️ Yatay")}: '
                 f'son 10 video önceki 10\'a göre %{x["gidisat"] * 100:+.0f}')
    performans = (f'<article class="kart bolum"><h3>📈 Performans</h3><ul class="satirlar">'
                  f'<li>Ortalama izlenme: <b>{kisa(x["ort"])}</b> · tipik video (medyan): <b>{kisa(x["medyan"])}</b></li>'
                  f'<li>{gidis}</li></ul><p class="kucuk">Son 10 video (en yeni üstte):</p>{cubuklar}</article>')

    if x["kisa_sayi"] and x["uzun_sayi"]:
        kim = "uzun videolar" if x["ort_uzun"] >= x["ort_kisa"] else "Shorts"
        karsi = f'<li>Daha çok izlenen: <b>{kim}</b> (Shorts ort. {kisa(x["ort_kisa"])} · uzun ort. {kisa(x["ort_uzun"])})</li>'
    else:
        karsi = ""
    format_ = (f'<article class="kart bolum"><h3>🎬 Format</h3><ul class="satirlar">'
               f'<li>Shorts oranı: %{round(x["kisa_oran"] * 100)} ({x["kisa_sayi"]} Shorts, {x["uzun_sayi"]} uzun video)</li>'
               + (f'<li>Uzun videoların ortalama süresi: <b>{x["ort_sure"] / 60:.0f} dakika</b></li>' if x["ort_sure"] else "")
               + f'{karsi}</ul></article>')

    gunler = " ve ".join(g for g, _ in x["gunler"]) or "—"
    saat = f'{x["saatler"][0][0]:02d}:00 civarı' if x["saatler"] else "—"
    duzen = (f'<article class="kart bolum"><h3>🗓️ Yükleme düzeni</h3><ul class="satirlar">'
             + (f'<li>Ortalama <b>{x["aralik"]:.0f} günde bir</b> video</li>' if x["aralik"] else "")
             + f'<li>Son 30 günde <b>{x["son30"]}</b> video · son yükleme {x["son_yukleme"]} gün önce</li>'
             f'<li>En çok <b>{gunler}</b> günleri, saat <b>{saat}</b> (Türkiye saati) yüklüyor</li></ul></article>'
             if x["son_yukleme"] is not None else "")

    basliklar = (f'<article class="kart bolum"><h3>✍️ Başlık kalıpları</h3><ul class="satirlar">'
                 f'<li>Ortalama uzunluk: <b>{x["baslik_uzunluk"]:.0f} karakter</b></li>'
                 f'<li>Sayı içeren: %{round(x["sayi_oran"] * 100)} · soru soran: %{round(x["soru_oran"] * 100)} · '
                 f'emoji: %{round(x["emoji_oran"] * 100)} · BÜYÜK HARF: %{round(x["buyuk_oran"] * 100)}</li></ul></article>')

    en_iyiler = "".join(
        f'<a class="video" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
        f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
        + (f'<span class="kucuk ceviri">{e(tr[v["baslik"]])}</span>' if tr.get(v["baslik"]) else "")
        + f'<span class="kucuk">▶ {kisa(v["izl"])} izlenme · {v["ts"].astimezone(TR_SAAT):%d.%m.%Y}</span></span></a>'
        for v in x["en_iyiler"])
    en_iyi = f'<article class="kart"><div class="bolum" style="padding-bottom:0"><h3>🏆 En çok izlenen videoları</h3></div>{en_iyiler}</article>'
    sd = "".join(
        f'<a class="video" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
        f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
        + (f'<span class="kucuk ceviri">{e(tr[v["baslik"]])}</span>' if tr.get(v["baslik"]) else "")
        + f'<span class="kucuk"><b class="kat">{v["oran"]:.0f}× normal</b> · {kisa(v["izl"])} izlenme</span></span></a>'
        for v in x["sira_disi"])
    sira_disi_html = (f'<article class="kart"><div class="bolum" style="padding-bottom:0"><h3>🎯 Sıra dışı videoları</h3>'
                      f'<p class="kucuk">Kanalın tipik videosunun ({kisa(x["medyan"])}) en az 3 katı izlenenler: '
                      f'izleyicinin asıl istediği bunlar.</p></div>{sd}</article>') if sd else ""
    fikir = (f'<article class="kart bolum">{fikir_kutusu(x.get("fikirler", []), tr)}</article>'
             if x.get("fikirler") else "")

    if pazar and (pazar.get("en") or pazar.get("tr")):
        pz = "".join(f'<li>{ad}: <span class="fr {FIRSAT_ETIKET[f["seviye"]][0]}">{FIRSAT_ETIKET[f["seviye"]][1]}</span> '
                     f'{firsat_metni(f)}</li>' for ad, f in (("🌍 İngilizce", pazar.get("en")), ("🇹🇷 Türkiye", pazar.get("tr"))) if f)
        pazar_html = (f'<article class="kart bolum"><h3>🌍 Bu konu başka pazarlarda ne durumda?</h3>'
                      f'<p class="kucuk">"{e(x["konusu"])}" son 30 günde:</p><ul class="satirlar">{pz}</ul></article>')
    else:
        pazar_html = ""

    kd = dict(ulke=x["ulke"], dil=x["dil"], kisa=x["kisa_oran"] > .6, kanal_yasi=x["yas"],
              hizli=bool(x["gidisat"] and x["gidisat"] > .3), video_sayisi=x["video_sayisi"], video_basi=x["video_basi"])
    maddeler = kanal_onerileri(kd, x["seviye"], pazar, None) + kanal_ozel_oneriler(x)
    oneri = ('<article class="kart"><div class="oneri" style="margin:14px"><b>💡 Öneriler</b><ul>'
             + "".join(f"<li>{m}</li>" for m in maddeler) + "</ul></div></article>")

    sayfa_yaz(yol, kok=kok, baslik="🔍 " + e(x["ad"]), ust=f'<a class="geri" href="{kok}index.html">← Ana sayfa</a>',
              meta=f'Kanal incelemesi: {tarih} · son {x["analiz_edilen"]} video incelendi',
              govde=(ust_kart + oneri + fikir + sira_disi_html + kazanc + konu + pazar_html + performans + format_ + duzen
                     + basliklar + en_iyi),
              footer=f'* Tipik video: videoların ortanca (medyan) izlenmesi; birkaç viral video ortalamayı şişirmesin diye. '
                     f'Kazanç rakamları ülke ve Shorts oranına göre kaba tahmindir. {harcanan} kota birimi.')


def adaylari_yaz(yol, sonuc, nisler):
    """Fikir Kutusu (fikir_kutusu.py) için: bu taramanın aday videoları ve trend konuları, makine okunur halde.
    Sayfada zaten herkese açık gösterilen bilgilerdir; mevcut hiçbir davranışı değiştirmez."""
    alanlar = ("id", "baslik", "kanal", "kanal_id", "ulke", "dil", "izlenme", "abone", "video_sayisi", "kanal_izlenme",
               "yayin_ts", "sure", "etiketler", "kaynak", "puan", "kapak", "kanal_yasi", "yt_konular")
    json_yaz(yol, dict(
        surum=1, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        videolar=[{k: s.get(k) for k in alanlar} for s in sonuc],
        konular=[dict(terim=n["terim"], kanal_sayisi=n["kanal_sayisi"], toplam=n["toplam"],
                      kanal_idler=[v["kanal_id"] for v in n["kanallar"]], firsat=n.get("firsat")) for n in nisler]))


def calistir_kanal(a, yt, durum_yol):
    """--kanal verildiyse: sadece o kanalı incele, sayfasını yaz ve Aramalarım'a ekle."""
    simdi_tr = lambda: dt.datetime.now(TR_SAAT)
    print(f"Kanal inceleniyor: {a.kanal}")
    try:
        c = kanal_bul(yt, a.kanal)
        if not c:
            print("! Kanal bulunamadı.")
            json_yaz(durum_yol, dict(uyari=f"⚠️ Kanal bulunamadı: {a.kanal[:80]}. Linki kontrol edip tekrar dene.",
                                     ts=simdi_tr().isoformat(timespec="minutes")))
            return
        vids = kanal_videolari(yt, c)
    except KotaBitti:
        json_yaz(durum_yol, dict(uyari=f"⚠️ Bugünün YouTube kotası bitti, kanal incelenemedi. {KOTA_NOTU}",
                                 ts=simdi_tr().isoformat(timespec="minutes")))
        return
    x = kanal_analiz(c, vids)
    x["fikirler"] = video_fikirleri([x["konusu"]]).get(x["konusu"], []) if x["konusu"] else []
    yardimci_sayfalar(a.site)
    # Konunun İngilizce ve Türkiye pazarındaki durumu (200 birim).
    pazar = None
    if x["konusu"]:
        en = (cevir(x["konusu"], "en") or x["konusu"]).lower()
        trc = (cevir(x["konusu"], "tr") or x["konusu"]).lower()
        pazar = dict(en=firsat_olc(yt, a, [en], bolge="", dil="en").get(en),
                     tr=firsat_olc(yt, a, [trc], bolge="TR", dil="tr").get(trc))
    tr = turkceye([x["konusu"], x["aciklama"]] + x["youtube_konulari"] + [v["baslik"] for v in x["en_iyiler"]]
                  + [v["baslik"] for v in x["sira_disi"]] + x["fikirler"])
    dosya = f"kanal/{x['id']}.html"
    kanal_rapor(x, pazar, tr, os.path.join(a.site, dosya), yt.harcanan)
    aramalar_yol = os.path.join(a.site, "aramalar.json")
    liste = [y for y in json_oku(aramalar_yol, []) if y.get("dosya") != dosya]
    liste.insert(0, dict(baslik=x["ad"], dosya=dosya, tip="kanal", kanal_id=x["id"],
                         tarih=simdi_tr().strftime("%d.%m %H:%M"),
                         ozet=f'{"gizli" if x["abone"] is None else kisa(x["abone"])} abone · {kisa(x["toplam"])} izlenme'))
    json_yaz(aramalar_yol, liste[:30])
    json_yaz(durum_yol, dict(uyari="⚠️ Son taramada kota yarıda bitti, sonuçlar eksik olabilir. " + KOTA_NOTU
                             if getattr(a, "kota_yarim", False) else None, ts=simdi_tr().isoformat(timespec="minutes")))
    print(f"{x['ad']}: {x['analiz_edilen']} video incelendi, konusu: {x['konusu']}, {yt.harcanan} kota birimi.")
    print(f"Rapor: {os.path.join(a.site, dosya)}")


def calistir(a):
    key = os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("API anahtarı yok. YT_API_KEY ortam değişkenini ayarla.")
    durum_yol = os.path.join(a.site, "durum.json")
    yt = YT(key)
    if a.kanal:
        calistir_kanal(a, yt, durum_yol)
        return
    a.ceviriler = {}
    if a.konular and not a.bolge:
        for k in a.konular:
            en = ingilizce(k)
            if en and en != k.lower():
                a.ceviriler[k] = en
    konu = f", konu: {', '.join(a.konular)}" if a.konular else ""
    print(f"Taranıyor ({a.bolge or 'global'}, son {a.gun} gün{konu})...")
    try:
        sonuc = tara(yt, a)
    except KotaBitti:
        print("! Günlük kota doldu, tarama yapılamadı.")
        json_yaz(durum_yol, dict(uyari=f"⚠️ Bugünün YouTube kotası bitti, son tarama yapılamadı. {KOTA_NOTU}",
                                 ts=dt.datetime.now(TR_SAAT).isoformat(timespec="minutes")))
        return
    nisler = nis_cikar(sonuc, haric=a.konular + list(a.ceviriler.values()))
    if a.konular:
        # Alt konu keşfi: her konu için arama önerileri + hepsinin doygunluk ölçümü.
        # Kotayı korumak için konu sayısı arttıkça konu başına alt konu azalır (1 konu → 8, 2 → 4, 3+ → 3).
        # Global aramada alt konuların yarısı Türkçe, yarısı İngilizce önerilerden gelir (geniş yelpaze).
        alt_adet = max(3, 8 // len(a.konular))
        a.harita = {}
        for k in a.konular:
            en = a.ceviriler.get(k)
            if en:
                yari = (alt_adet + 1) // 2
                altlar = [en] + oneriler(k, "tr", alt_adet - yari) + oneriler(en, "en", yari)
            else:
                altlar = oneriler(k, a.dil, alt_adet)
            print(f"  {k}: {len(altlar)} alt konu → {', '.join(altlar) or 'öneri alınamadı'}")
            olcum = firsat_olc(yt, a, [k] + altlar)
            a.harita[k] = (olcum.get(k), [(t, olcum[t]) for t in altlar if t in olcum])
    else:
        # En güçlü 5 trend konusunun doygunluğunu ölç.
        olcum = firsat_olc(yt, a, [n["terim"] for n in nisler[:5]])
        for n in nisler:
            n["firsat"] = olcum.get(n["terim"])
    kanallar = kanallara_grupla(sonuc)
    th = terim_haritasi_kur(nisler)
    # En güçlü 3 kanalın fikri İngilizce ve Türkiye pazarında ayrı ayrı ölçülür → "hangi dilde yap" önerisi.
    a.pazarlar = {} if a.konular else pazar_olc(yt, a, kanallar, th, adet=3)
    # Yabancı konu isimleri ve video başlıkları Türkçeye çevrilir (kota harcamaz).
    metinler = [kanal_konusu(k, th) for k in kanallar] + [k["en_iyi"]["baslik"] for k in kanallar]
    metinler += [n["terim"] for n in nisler]
    metinler += [t for ana, altlar in getattr(a, "harita", {}).values() for t, _ in altlar]
    a.ceviri_tr = turkceye(metinler)
    # Video fikirleri: en güçlü trend konuları (genel) ya da boş/orta alt konular (konulu) için gerçek aramalar.
    if a.konular:
        fikir_terimleri = [t for _, altlar in a.harita.values() for t, f in altlar if f["seviye"] in ("bos", "orta")][:6]
    else:
        fikir_terimleri = [n["terim"] for n in nisler[:5]]
    a.fikirler = video_fikirleri(fikir_terimleri)
    a.ceviri_tr.update(turkceye([s for l in a.fikirler.values() for s in l]))
    # Evergreen: konulu aramada konunun kalıcılığı (konu başına 100 birim);
    # genel taramada haftada bir evergreen taraması (~1.250 birim), aradaki günler sadece takip (~1 birim).
    a.kalicilik = {k: kalicilik_olc(yt, a, k) for k in a.konular}
    eg_yol = os.path.join(a.site, "evergreen.json")
    eg = {} if a.konular else json_oku(eg_yol, {})
    if not a.konular:
        eski = not eg.get("ts") or (dt.datetime.now(dt.timezone.utc) - zaman(eg["ts"])).days >= 6
        if eski or a.evergreen:
            print("Evergreen taraması (haftalık)...")
            eg = dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), liste=evergreen_tara(yt, a))
        a.ceviri_tr.update(turkceye([x["baslik"] for x in eg.get("liste", [])]
                                    + [t for t, _, _ in evergreen_konular(eg.get("liste", []))]))
    db = db_ac(a.db)
    try:
        gecmisle_karsilastir(db, sonuc)
        trend_gecmisi(db, nisler)
        firsatlar = [(n["terim"], n.get("firsat")) for n in nisler]
        firsatlar += [(t, f) for _, altlar in getattr(a, "harita", {}).values() for t, f in altlar]
        kanal_kaydet(db, kanallar, firsatlar)
        a.ilk_gorulme = ilk_gorulme(db)
        if not a.konular:
            a.karne = karne_hesapla(db, yt)
            a.hafta = hafta_ozeti(db)
            evergreen_guncelle(yt, db, eg.get("liste", []))
        eski_sil(db)
    finally:
        db.close()
    if not a.konular and eg:
        json_yaz(eg_yol, eg)
        a.evergreen_veri = eg
    yardimci_sayfalar(a.site)

    if a.konular:
        dosya = f"konu/{slug(','.join(a.konular))}{'-tr' if a.bolge == 'TR' else ''}.html"
        rapor(sonuc, nisler, a, os.path.join(a.site, dosya), yt.harcanan, kok="../")
        aramalar_yol = os.path.join(a.site, "aramalar.json")
        liste = [x for x in json_oku(aramalar_yol, []) if x.get("dosya") != dosya]
        liste.insert(0, dict(baslik=", ".join(a.konular), dosya=dosya,
                             tarih=dt.datetime.now(TR_SAAT).strftime("%d.%m %H:%M"),
                             dil="Türkçe" if a.bolge == "TR" else "Global",
                             kanal=len({s["kanal_id"] for s in sonuc}),
                             bos=sum(f["seviye"] == "bos" for _, altlar in a.harita.values() for _, f in altlar)))
        json_yaz(aramalar_yol, liste[:30])
    else:
        dosya = "index.html"
        rapor(sonuc, nisler, a, os.path.join(a.site, dosya), yt.harcanan)
        adaylari_yaz(os.path.join(a.site, "adaylar.json"), sonuc, nisler)
    uyari = (f"⚠️ Son taramada kota yarıda bitti, sonuçlar eksik olabilir. {KOTA_NOTU}"
             if getattr(a, "kota_yarim", False) else None)
    json_yaz(durum_yol, dict(uyari=uyari, ts=dt.datetime.now(TR_SAAT).isoformat(timespec="minutes")))

    print(f"\n{len(sonuc)} aday bulundu, {yt.harcanan} kota birimi harcandı.")
    for s in sorted({x["kanal_id"]: x for x in sonuc}.values(), key=lambda x: -x["video_basi"])[:5]:
        print(f"  • {s['kanal']}: {s['video_sayisi']} video → {kisa(s['kanal_izlenme'])} izlenme")
    if nisler:
        print("Yükselen konular: " + ", ".join(n["terim"] for n in nisler[:6]))
    print(f"Rapor: {os.path.join(a.site, dosya)}")


def main():
    klasor = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="YouTube niş bulucu")
    p.add_argument("--konu", default="", help='Virgülle ayrılmış konular, ör. "tarih, borsa, kripto" (boş = genel tarama)')
    p.add_argument("--kanal", default="", help="İncelenecek kanalın linki, @adı ya da UC... kimliği (verilirse sadece o incelenir)")
    p.add_argument("--bolge", default="", help="Ülke kodu: TR, US, DE... (boş = global)")
    p.add_argument("--dil", default="", help="İçerik dili: tr, en, de... (boş = hepsi)")
    p.add_argument("--gun", type=int, default=14, help="Son kaç günde yayınlanan videolar")
    p.add_argument("--max-kanal-yasi", type=int, default=365, help="Bundan eski kanallar elenir (gün)")
    p.add_argument("--evergreen", action="store_true", help="Haftalık evergreen taramasını şimdi yap (normalde 6 günde bir otomatik)")
    p.add_argument("--max-abone", type=int, default=100_000)
    p.add_argument("--max-video", type=int, default=40, help="Kanalın en fazla video sayısı")
    p.add_argument("--min-izlenme", type=int, help="Varsayılan: genel taramada 50K, konulu taramada 20K")
    p.add_argument("--site", default=os.path.join(klasor, "site"), help="Sayfaların yazılacağı klasör")
    p.add_argument("--db", default=os.path.join(klasor, "nis_gecmis.db"))
    a = p.parse_args()
    a.bolge, a.dil = a.bolge.upper().strip(), a.dil.strip()
    a.konular = list(dict.fromkeys(k.strip() for k in a.konu.split(",") if k.strip()))[:5]
    if a.min_izlenme is None:
        # Konulu aramada daha erken aşamadaki kanallar da yakalansın.
        a.min_izlenme = 20_000 if a.konular else 50_000
    try:
        calistir(a)
    except ApiHatasi as h:
        sys.exit(str(h))


if __name__ == "__main__":
    main()
