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

Kota: günlük 10.000 birim. Genel tarama ~4.750 birim (12 kategori × 2 zaman dilimi global
+ 12 kategori Türkiye, her biri 100; + 5 konunun fırsat ölçümü + en güçlü 3 kanalın İngilizce
ve Türkiye pazar ölçümü, her biri 100; + detaylar). Çeviriler kota harcamaz.
Global konulu tarama 1 konu için ~1.600 birim (Türkçe + İngilizce 150'şer video + ana konu,
İngilizcesi ve 8 alt konunun fırsat ölçümü); alt konu önerileri ve çeviri kota harcamaz.
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
    """)
    return db


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
        r = yt.get("channels", 1, part="snippet,statistics", id=",".join(kanal_ids[i:i + 50]))
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
    adaylar = [(t, p) for t, p in terim_puan.items() if len(terim_kanal[t]) >= (2 if " " in t else 3)]
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
    konular = [re.sub(r"_", " ", u.rsplit("/", 1)[-1]) for u in c.get("topicDetails", {}).get("topicCategories", [])]
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
            video_sayisi=ilk["video_sayisi"], kanal_izlenme=ilk["kanal_izlenme"], abone=ilk["abone"],
            video_basi=ilk["video_basi"], kanal_yasi=ilk["kanal_yasi"],
            kategori=collections.Counter(v["kaynak"] for v in vids).most_common(1)[0][0],
            basliklar=[[v["baslik"]] + v["etiketler"] for v in vids],
            puan=max(v["puan"] for v in vids),
            en_iyi=max(vids, key=lambda v: v["izlenme"]),
            yeni=all(v.get("yeni") for v in vids),
            hizli=any(v.get("hizlanma") and v["hizlanma"] > v["saatlik"] * 1.2 for v in vids),
            kisa=sum(0 < v["sure"] <= 180 for v in vids) / len(vids) > .6,
        ))
    return sorted(kanallar, key=lambda k: -k["puan"])


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
    """Kanalın konusu: önce trend raporundaki ortak konu, yoksa videolarında en sık geçen ifade."""
    if k["kanal_id"] in terim_haritasi:
        return terim_haritasi[k["kanal_id"]]
    return en_sik_ifade(k["basliklar"], en_az=1) or k["kategori"]


def sure_metni(gun):
    return f"{max(gun, 1)} günde" if gun < 45 else f"{round(gun / 30)} ayda"


def incele_dugmesi(kanal_id):
    """Kanal daha önce incelendiyse sayfa açılınca JS bunu "İncelemeyi aç" bağlantısına çevirir."""
    return (f'<a class="incele" data-id="{html.escape(kanal_id)}" href="{TARA_LINKI}" target="_blank" '
            f'rel="noopener">🔍 İncele</a>')


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
        if en_iyi and (not tr_iyi or FIRSAT_SIRA[en["seviye"]] <= FIRSAT_SIRA[trp["seviye"]]):
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
@media (prefers-color-scheme:dark){:root{--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a;--uyari:#e3b341;--zemin2:#272522}}
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
// Genel tarama her sabah yenilenir; 30 saatten eskiyse bir şeyler ters gitmiştir.
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
    var inc = el("a", "incele", "🔍 İncele"); inc.dataset.id = x.id; inc.href = document.body.dataset.tara;
    inc.target = "_blank"; inc.rel = "noopener";
    oge.append(a, b, inc); liste.append(oge);
  });
  kutu.hidden = !l.length;
  inceleIsaretle();
}

// İncele: daha önce incelenen kanallarda düğme doğrudan inceleme sayfasını açar;
// incelenmemişse kanal linkini panoya kopyalar ve GitHub'daki tarama sayfasını açar.
var INCELENEN = {};
function inceleIsaretle() {
  document.querySelectorAll(".incele").forEach(function (b) {
    var dosya = INCELENEN[b.dataset.id];
    if (dosya) { b.href = KOK + dosya; b.target = "_self"; b.textContent = "🔍 İncelemeyi aç"; b.classList.add("hazir"); }
  });
}
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

    def kanal_kart(k):
        abone = "abone gizli" if k["abone"] is None else f'{kisa(k["abone"])} abone'
        av = (f'<img class="av" src="{e(k["kapak"])}" alt="" loading="lazy">' if k["kapak"]
              else f'<span class="av bos">{e(k["kanal"][:1].upper())}</span>')
        v = k["en_iyi"]
        konusu = kanal_konusu(k, terim_haritasi)
        kazanc = kazanc_seviyesi(k["ulke"], k["dil"])
        maddeler = kanal_onerileri(k, kazanc, pazarlar.get(k["kanal_id"]), trend_firsati.get(konusu))
        oneri = ('<div class="oneri"><b>💡 Öneri</b><ul>' + "".join(f"<li>{m}</li>" for m in maddeler) + "</ul></div>"
                 if maddeler else "")
        kz = f'<span class="rz kz {kazanc}">{KAZANC_ETIKET[kazanc]}</span>' if kazanc else ""
        baslik_tr = tr.get(v["baslik"])
        ozet = f'{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla {kisa(k["kanal_izlenme"])} izlenme'
        yildiz = (f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(k["kanal_id"])}" '
                  f'data-ad="{e(k["kanal"])}" data-konu="{e(konusu)}" data-ozet="{e(ozet)}" data-ulke="{bayrak(k["ulke"])}">☆</button>')
        return (f'<article class="kart">{yildiz}'
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
                f'{incele_dugmesi(k["kanal_id"])}</div>'
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
                f'</dl><div class="cipler">{cipler}</div></article>')

    def harita_kart(terim, f, ana=False):
        cls, etiket = FIRSAT_ETIKET[f["seviye"]]
        cipler = "".join(f'<a class="cip" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{e(k["kanal"])}</a>'
                         for k in f["yeniler"])
        return (f'<article class="kart nis{" ana" if ana else ""}"><div class="nis-ust"><span class="nis-ad">{cevirili(terim)}</span>'
                f'<span class="fr {cls}">{etiket}</span></div>'
                f'<p class="kucuk aciklama">{"Ana konu · " if ana else ""}{firsat_metni(f)}</p>'
                + (f'<p class="kucuk">Bu konuda tutan yeni kanallar:</p><div class="cipler">{cipler}</div>' if cipler else "")
                + f'<a class="ara" href="{arama_link(terim)}" target="_blank" rel="noopener">YouTube\'da ara ›</a></article>')

    if a.konular:  # konulu tarama: önce fırsat haritası, sonra her konu kendi bölümünde
        baslik = "📌 " + e(", ".join(a.konular))
        govde = ""
        for k, (ana, altlar) in getattr(a, "harita", {}).items():
            altlar = sorted(altlar, key=lambda x: (FIRSAT_SIRA[x[1]["seviye"]], -x[1]["pay"]))
            bos = sum(f["seviye"] == "bos" for _, f in altlar)
            govde += (f'<h2>🧭 Fırsat haritası: {e(k)}</h2><p class="alt-baslik">İnsanların YouTube\'da aradığı alt konular, '
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
        ust = f'<a class="geri" href="{kok}index.html">← Ana sayfa</a>'
        aramalar = ""
    else:
        baslik = "🎯 Niş Avcısı"
        govde = ('<h2>🔥 Patlayan kanallar</h2><p class="alt-baslik">Dünya genelinde ve Türkiye\'de son 1 yılda açılıp '
                 'az videoyla patlayan kanallar. Her alandan karışık: konusuna bak, beğendiğine gir.</p>'
                 + ("".join(kanal_kart(k) for k in genis_sirala(kanallara_grupla(sonuc))[:40]) or
                    '<p class="bos-not">Bu taramada uygun kanal çıkmadı. Bir sonraki taramayı bekle.</p>'))
        ust = ""
        aramalar = ('<div id="fav-kutu" hidden><h2 id="begendiklerim">⭐ Beğendiklerim</h2>'
                    '<p class="alt-baslik">Yıldızladığın kanallar. Dokun, kanala gir; yıldıza basarsan listeden çıkar.</p>'
                    '<div class="kart" id="fav-liste"></div></div>'
                    '<div id="aramalar-kutu" hidden><h2>Aramalarım</h2>'
                    '<p class="alt-baslik">Konu aramaların burada saklanır. Dokun, sonuçları aç.</p>'
                    '<div class="kart aramalar" id="aramalar"></div></div>')
    trend = "".join(trend_kart(n) for n in nisler) or '<p class="bos-not">Henüz birden fazla kanalda ortak konu çıkmadı.</p>'
    yarim = (f'<p class="uyari">⚠️ Kota tarama sırasında bitti, sonuçlar eksik olabilir. {KOTA_NOTU}</p>'
             if getattr(a, "kota_yarim", False) else "")

    sayfa_yaz(yol, kok=kok, baslik=baslik, ust=ust, genel=not a.konular,
              meta=f'{"Arama" if a.konular else "Son tarama"}: {tarih} · {kapsam} · son {a.gun} gün',
              govde=f"""{yarim}
{aramalar}
{govde}

<h2>📊 Trend raporu</h2>
<p class="alt-baslik">Birden fazla yeni kanalda aynı anda patlayan konular: nereden çıktı, kaç kanal yapıyor, ne kadar hızlı.</p>
{trend}""",
              footer=f"""Sadece son {a.max_kanal_yasi} günde açılmış kanallar · ≤{kisa(a.max_abone)} abone · ≤{a.max_video} video ·
≥{kisa(a.min_izlenme)} izlenme · reklamla şişmiş videolar elendi · {harcanan} kota birimi.
Otomatik genel tarama her gün 07:17'de.<br>
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
<style>{CSS}</style></head><body data-kok="{kok}" data-uret="{uret}" data-genel="{1 if genel else 0}"
data-tara="{TARA_LINKI}"><main>
{ust}<header><h1>{baslik}</h1>
<a class="btn ana" href="{TARA_LINKI}" target="_blank" rel="noopener" id="tara">Tara</a>
<a class="btn ikinci ikon" href="{kok}index.html#begendiklerim" id="fav-sayi" aria-label="Beğendiklerim">⭐</a>
<button class="btn ikinci ikon" onclick="location.reload()" aria-label="Yenile">↻</button></header>
<p class="meta">{meta}</p>
<p class="uyari" id="uyari" hidden></p>
<p class="ipucu" id="ipucu" hidden>GitHub'da <b>Run workflow</b>'a bas. Konu aramak için <b>Konu</b> kutusuna yaz
(ör. <i>tarih, borsa</i>); bir kanalı incelemek için <b>Kanal</b> kutusuna linkini yapıştır; ikisi de boşsa genel
tarama yapar. Yeşil <b>Run workflow</b>'a bas, 2 dakika sonra ↻ ile yenile.</p>
<p class="ipucu" id="ipucu-incele" hidden>📋 <b>Kanal linki kopyalandı.</b> Açılan GitHub sayfasında <b>Run workflow</b>'a bas,
<b>Kanal</b> kutusuna yapıştır, yeşil <b>Run workflow</b>'a bas. 1–2 dakika sonra inceleme <b>Aramalarım</b>'da çıkar.</p>
{govde}

<footer>{footer}</footer>
</main>
<script>{JS}</script>
</body></html>"""
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(sayfa)


def kanal_rapor(x, pazar, tr, yol, harcanan, kok="../"):
    """Tek bir kanalın inceleme sayfası."""
    e = html.escape
    tarih = dt.datetime.now(TR_SAAT).strftime("%d.%m.%Y %H:%M")

    def cevirili(metin):
        t = tr.get(metin)
        return f'{e(metin)} <span class="ceviri">({e(t)})</span>' if t else e(metin)

    def dolar(ab):
        lo, hi = ab
        f = lambda d: f"${kisa(d)}" if d >= 1000 else f"${d:,.0f}"
        return f"{f(lo)} – {f(hi)}"

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
                f'<div class="eylem"><a class="gir" href="{kanal_link(x["id"])}" target="_blank" rel="noopener">Kanala gir ›</a></div>'
                f'</article>')

    ypp = ("" if x["abone"] is None or x["abone"] >= 1000 else
           '<li>⚠️ 1.000 abonenin altında: büyük ihtimalle henüz YouTube para kazanma programında değil, şu an reklam geliri yok.</li>')
    kazanc = (f'<article class="kart bolum"><h3>💰 Tahmini kazanç</h3><ul class="satirlar">'
              f'<li>Pazar: <b>{seviye}</b>{" (dilden tahmin)" if x["seviye_tahmini"] else ""} · {e(ulke)}</li>'
              f'<li>Aylık ortalama: <b>{dolar(x["aylik_gelir"])}</b></li>'
              f'<li>Kanal açıldığından beri toplam: <b>{dolar(x["toplam_gelir"])}</b></li>'
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
              govde=ust_kart + oneri + kazanc + konu + pazar_html + performans + format_ + duzen + basliklar + en_iyi,
              footer=f'* Tipik video: videoların ortanca (medyan) izlenmesi; birkaç viral video ortalamayı şişirmesin diye. '
                     f'Kazanç rakamları ülke ve Shorts oranına göre kaba tahmindir. {harcanan} kota birimi.')


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
    # Konunun İngilizce ve Türkiye pazarındaki durumu (200 birim).
    pazar = None
    if x["konusu"]:
        en = (cevir(x["konusu"], "en") or x["konusu"]).lower()
        trc = (cevir(x["konusu"], "tr") or x["konusu"]).lower()
        pazar = dict(en=firsat_olc(yt, a, [en], bolge="", dil="en").get(en),
                     tr=firsat_olc(yt, a, [trc], bolge="TR", dil="tr").get(trc))
    tr = turkceye([x["konusu"], x["aciklama"]] + x["youtube_konulari"] + [v["baslik"] for v in x["en_iyiler"]])
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
    db = db_ac(a.db)
    try:
        gecmisle_karsilastir(db, sonuc)
        trend_gecmisi(db, nisler)
    finally:
        db.close()

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
    p.add_argument("--max-abone", type=int, default=100_000)
    p.add_argument("--max-video", type=int, default=40, help="Kanalın en fazla video sayısı")
    p.add_argument("--min-izlenme", type=int, help="Varsayılan: genel taramada 100K, konulu taramada 20K")
    p.add_argument("--site", default=os.path.join(klasor, "site"), help="Sayfaların yazılacağı klasör")
    p.add_argument("--db", default=os.path.join(klasor, "nis_gecmis.db"))
    a = p.parse_args()
    a.bolge, a.dil = a.bolge.upper().strip(), a.dil.strip()
    a.konular = list(dict.fromkeys(k.strip() for k in a.konu.split(",") if k.strip()))[:5]
    if a.min_izlenme is None:
        # Konulu aramada daha erken aşamadaki kanallar da yakalansın.
        a.min_izlenme = 20_000 if a.konular else 100_000
    try:
        calistir(a)
    except ApiHatasi as h:
        sys.exit(str(h))


if __name__ == "__main__":
    main()
