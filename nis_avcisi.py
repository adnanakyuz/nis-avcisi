#!/usr/bin/env python3
"""
NİŞ AVCISI — YouTube'da son 1 yılda açılmış, az videoyla çok izlenen kanalları ve
yükselen konuları bulur. GitHub Actions'ta çalışır (.github/workflows/tara.yml),
sonucu GitHub Pages'te telefona uygun bir sayfa olarak yayınlar.

Elle çalıştırmak için (Python 3.8+, ek paket gerekmez; YT_API_KEY ortam değişkeni gerekli):
  python nis_avcisi.py                          # genel tarama: global, son 14 gün → site/index.html
  python nis_avcisi.py --konu "tarih, kripto"   # konulu tarama → site/konu/<konu>.html
  python nis_avcisi.py --bolge TR --dil tr      # sadece Türkiye / Türkçe içerik

Kota: günlük 10.000 birim. Genel tarama ~4.150 birim (12 kategori × 2 zaman dilimi global
+ 12 kategori Türkiye, her biri 100; + 5 konunun fırsat ölçümü × 100 + detaylar).
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


def ingilizce(metin):
    """Türkçe konuyu İngilizceye çevir; global aramada dünya genelindeki videolar da gelsin.
    Ücretsiz çeviri servisi; ulaşılamazsa boş döner ve sadece Türkçe aranır."""
    url = CEVIRI_API + "?" + urllib.parse.urlencode(dict(client="gtx", sl="tr", tl="en", dt="t", q=metin))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return "".join(p[0] for p in json.load(r)[0]).strip().lower()
    except Exception:
        return ""


def bayrak(ulke):
    if not ulke or len(ulke) != 2 or not ulke.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(h) - 65) for h in ulke.upper())


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
        ws = [w for w in kelimeler(s["baslik"] + " " + " ".join(s["etiketler"]))
              if not any(w == h or (len(h) >= 4 and w.startswith(h)) for h in haric)]
        # Tek kelimeler genel kalıyor ("funny", "love"); 2-3 kelimelik ifadeler asıl nişi verir.
        terimler = ({w for w in ws if w not in GENEL} | {f"{a} {b}" for a, b in zip(ws, ws[1:])}
                    | {f"{a} {b} {c}" for a, b, c in zip(ws, ws[1:], ws[2:])})
        for t in terimler:
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


def firsat_olc(yt, a, terimler):
    """Her terim için son 30 günün en çok izlenen 25 videosuna bak: izlenmeler yeni/küçük kanallara mı,
    yoksa büyük ve eski kanallara mı gidiyor? Terim başına 100 kota birimi."""
    simdi = dt.datetime.now(dt.timezone.utc)
    once = tarih_str(simdi - dt.timedelta(days=30))
    arama = {}
    for t in terimler:
        try:
            r = yt.get("search", 100, part="id", type="video", order="viewCount", q=t, publishedAfter=once,
                       regionCode=a.bolge or None, relevanceLanguage=a.dil or None, maxResults=25)
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
            video_sayisi=ilk["video_sayisi"], kanal_izlenme=ilk["kanal_izlenme"], abone=ilk["abone"],
            video_basi=ilk["video_basi"], kanal_yasi=ilk["kanal_yasi"],
            kategori=collections.Counter(v["kaynak"] for v in vids).most_common(1)[0][0],
            basliklar=[v["baslik"] + " " + " ".join(v["etiketler"]) for v in vids],
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
    sayac = collections.Counter()
    for metin in k["basliklar"]:
        ws = [w for w in kelimeler(metin) if w not in GENEL]
        sayac.update({f"{a} {b}" for a, b in zip(ws, ws[1:])})
        sayac.update({w: 0.5 for w in ws})
    return sayac.most_common(1)[0][0] if sayac else k["kategori"]


def sure_metni(gun):
    return f"{max(gun, 1)} günde" if gun < 45 else f"{round(gun / 30)} ayda"


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
.cip::after{content:" ›";color:var(--soluk)}
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
.gir{display:block;text-align:center;margin:12px 14px 14px;padding:11px;border-radius:12px;background:var(--vurgu);color:#fff;font-weight:700}
.gir:active{opacity:.7}
article.kart{position:relative} .kanal{padding-right:44px}
.yildiz{position:absolute;top:6px;right:6px;width:44px;height:44px;border:0;background:none;font-size:26px;line-height:1;color:var(--soluk);cursor:pointer;z-index:1}
.yildiz.on{color:#f5b301} .yildiz:active{transform:scale(.85)}
.fav-oge{position:relative;border-top:1px solid var(--cizgi)} .fav-oge:first-child{border-top:0}
.fav-oge .arama{padding-right:56px} .fav-oge .yildiz{top:50%;margin-top:-22px}
.btn.ikon{padding:10px 12px} header .btn{flex:none}
footer{color:var(--soluk);font-size:12px;margin-top:28px}
"""

# Sayfa açılınca durum.json'dan kota uyarısını, ana sayfada aramalar.json'dan "Aramalarım" listesini okur.
JS = """
var KOK = document.body.dataset.kok;
document.getElementById("tara").addEventListener("click", function () { document.getElementById("ipucu").hidden = false; });
fetch(KOK + "durum.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (d) {
  if (d.uyari) { var u = document.getElementById("uyari"); u.textContent = d.uyari; u.hidden = false; }
}).catch(function () {});
var liste = document.getElementById("aramalar");
if (liste) fetch("aramalar.json", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (l) {
  l.slice(0, 10).forEach(function (x) {
    var a = document.createElement("a"); a.className = "arama"; a.href = x.dosya;
    var ic = document.createElement("span"); ic.className = "ic";
    var ad = document.createElement("span"); ad.className = "ad"; ad.textContent = "📌 " + x.baslik;
    var alt = document.createElement("span"); alt.className = "kucuk";
    alt.textContent = x.tarih + " · " + x.dil + " · " + x.kanal + " kanal" + (x.bos ? " · 🟢 " + x.bos + " boş alan" : "");
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
    oge.append(a, b); liste.append(oge);
  });
  kutu.hidden = !l.length;
}
document.addEventListener("click", function (e) {
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

    # Her kanalın konusu: trend raporunda yer aldığı ortak konu (varsa).
    terim_haritasi = {}
    for n in nisler:
        for v in n["kanallar"]:
            terim_haritasi.setdefault(v["kanal_id"], n["terim"])

    def kanal_kart(k):
        abone = "abone gizli" if k["abone"] is None else f'{kisa(k["abone"])} abone'
        av = (f'<img class="av" src="{e(k["kapak"])}" alt="" loading="lazy">' if k["kapak"]
              else f'<span class="av bos">{e(k["kanal"][:1].upper())}</span>')
        v = k["en_iyi"]
        konusu = kanal_konusu(k, terim_haritasi)
        ozet = f'{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla {kisa(k["kanal_izlenme"])} izlenme'
        yildiz = (f'<button class="yildiz" type="button" aria-label="Beğendiklerime ekle" data-id="{e(k["kanal_id"])}" '
                  f'data-ad="{e(k["kanal"])}" data-konu="{e(konusu)}" data-ozet="{e(ozet)}" data-ulke="{bayrak(k["ulke"])}">☆</button>')
        return (f'<article class="kart">{yildiz}'
                f'<a class="kanal" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{av}'
                f'<span class="ic"><span class="ad">{bayrak(k["ulke"])} {e(k["kanal"])}</span>'
                f'<span class="patlama">🔥 Patlamış: <b>{sure_metni(k["kanal_yasi"])} {k["video_sayisi"]} videoyla '
                f'{kisa(k["kanal_izlenme"])}</b> izlenme</span>'
                f'<span class="kucuk konusu">Konusu: <b>{e(konusu)}</b> · {e(k["kategori"])}</span>'
                f'<span class="kucuk">Video başına {kisa(k["video_basi"])} · {abone}</span></span>'
                f'<span class="ok">›</span></a>'
                f'<div class="rzs">{rozetler(k)}</div>'
                f'<a class="video" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
                f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
                f'<span class="kucuk">▶ En çok izlenen: {kisa(v["izlenme"])} · {v["yayin"]}</span></span></a>'
                f'<a class="gir" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">Kanala gir, bak ›</a>'
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
        return (f'<article class="kart nis"><div class="nis-ust"><span class="nis-ad">{e(n["terim"])}</span>{durum}</div>'
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
        return (f'<article class="kart nis{" ana" if ana else ""}"><div class="nis-ust"><span class="nis-ad">{e(terim)}</span>'
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

    sayfa = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Niş Avcısı</title>
<meta name="theme-color" content="#d4361c"><link rel="manifest" href="{kok}manifest.json">
<link rel="icon" href="{kok}icon-192.png"><link rel="apple-touch-icon" href="{kok}icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Niş Avcısı">
<style>{CSS}</style></head><body data-kok="{kok}"><main>
{ust}<header><h1>{baslik}</h1>
<a class="btn ana" href="{TARA_LINKI}" target="_blank" rel="noopener" id="tara">Tara</a>
<a class="btn ikinci ikon" href="{kok}index.html#begendiklerim" id="fav-sayi" aria-label="Beğendiklerim">⭐</a>
<button class="btn ikinci ikon" onclick="location.reload()" aria-label="Yenile">↻</button></header>
<p class="meta">{"Arama" if a.konular else "Son tarama"}: {tarih} · {kapsam} · son {a.gun} gün</p>
<p class="uyari" id="uyari" hidden></p>{yarim}
<p class="ipucu" id="ipucu" hidden>GitHub'da <b>Run workflow</b>'a bas. Konu aramak istersen <b>Konu</b> kutusuna yaz
(ör. <i>tarih, borsa</i>), boş bırakırsan genel tarama yapar. Yeşil <b>Run workflow</b>'a bas, 2 dakika sonra ↻ ile yenile.</p>
{aramalar}
{govde}

<h2>📊 Trend raporu</h2>
<p class="alt-baslik">Birden fazla yeni kanalda aynı anda patlayan konular: nereden çıktı, kaç kanal yapıyor, ne kadar hızlı.</p>
{trend}

<footer>Sadece son {a.max_kanal_yasi} günde açılmış kanallar · ≤{kisa(a.max_abone)} abone · ≤{a.max_video} video ·
≥{kisa(a.min_izlenme)} izlenme · reklamla şişmiş videolar elendi · {harcanan} kota birimi.
Otomatik genel tarama her gün 07:17'de.</footer>
</main>
<script>{JS}</script>
</body></html>"""
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(sayfa)


def calistir(a):
    key = os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("API anahtarı yok. YT_API_KEY ortam değişkenini ayarla.")
    durum_yol = os.path.join(a.site, "durum.json")
    yt = YT(key)
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
