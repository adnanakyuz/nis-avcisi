#!/usr/bin/env python3
"""
NİŞ AVCISI — YouTube'da son 1 yılda açılmış, az videoyla çok izlenen kanalları ve
yükselen konuları bulur. GitHub Actions'ta çalışır (.github/workflows/tara.yml),
sonucu GitHub Pages'te telefona uygun bir sayfa olarak yayınlar.

Elle çalıştırmak için (Python 3.8+, ek paket gerekmez; YT_API_KEY ortam değişkeni gerekli):
  python nis_avcisi.py                          # genel tarama: global, son 14 gün → site/index.html
  python nis_avcisi.py --konu "tarih, kripto"   # konulu tarama → site/konu/<konu>.html
  python nis_avcisi.py --bolge TR --dil tr      # sadece Türkiye / Türkçe içerik

Kota: günlük 10.000 birim. Genel tarama ~1.230 birim (12 kategori × 100 + detaylar),
konulu tarama konu başına ~300 birim.
"""
import argparse, collections, datetime as dt, html, json, math, os, re, sqlite3, sys, time
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
TARA_LINKI = "https://github.com/adnanakyuz/nis-avcisi/actions/workflows/tara.yml"
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
def tara(yt, a):
    simdi = dt.datetime.now(dt.timezone.utc)
    once = (simdi - dt.timedelta(days=a.gun)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids, kaynak = [], {}

    def ara(etiket, sayfa=1, **p):
        token = None
        for _ in range(sayfa):
            r = yt.get("search", 100, part="id", type="video", order="viewCount", publishedAfter=once,
                       regionCode=a.bolge or None, relevanceLanguage=a.dil or None, maxResults=50,
                       pageToken=token, **p)
            for i in r.get("items", []):
                v = i.get("id", {}).get("videoId")
                if v and v not in kaynak:
                    video_ids.append(v)
                    kaynak[v] = etiket
            token = r.get("nextPageToken")
            if not token:
                break

    # Konu verildiyse sadece o konular derinlemesine (3 sayfa = 150 video) aranır;
    # verilmediyse her kategoride son X günün en çok izlenenleri.
    # Kota arama sırasında biterse elde edilenle devam et (detay sorguları sadece 1 birim).
    if a.konular:
        aramalar = [(k, dict(q=k, sayfa=3)) for k in a.konular]
    else:
        aramalar = [(ad, dict(videoCategoryId=kid)) for kid, ad in KATEGORILER.items()]
    for etiket, p in aramalar:
        try:
            ara(etiket, **p)
            print(f"  ✓ {etiket}")
        except ApiHatasi:
            print(f"  – {etiket} (aranamadı, atlandı)")
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
        terimler = set(ws) | {f"{a} {b}" for a, b in zip(ws, ws[1:])}
        for t in terimler:
            terim_kanal[t].add(s["kanal_id"])
            terim_puan[t] += s["puan"] * (1.4 if " " in t else 1)
            terim_video[t].append(s)
    adaylar = [(t, p) for t, p in terim_puan.items() if len(terim_kanal[t]) >= 2]
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


def kanallara_grupla(sonuc):
    """Videoları kanal bazında topla: her kanal bir kart."""
    gruplar = collections.OrderedDict()
    for s in sonuc:
        gruplar.setdefault(s["kanal_id"], []).append(s)
    kanallar = []
    for kid, vids in gruplar.items():
        ilk = vids[0]
        kanallar.append(dict(
            kanal_id=kid, kanal=ilk["kanal"], kapak=ilk.get("kanal_kapak", ""),
            video_sayisi=ilk["video_sayisi"], kanal_izlenme=ilk["kanal_izlenme"], abone=ilk["abone"],
            video_basi=ilk["video_basi"], kanal_yasi=ilk["kanal_yasi"],
            puan=max(v["puan"] for v in vids),
            en_iyi=max(vids, key=lambda v: v["izlenme"]),
            yeni=all(v.get("yeni") for v in vids),
            hizli=any(v.get("hizlanma") and v["hizlanma"] > v["saatlik"] * 1.2 for v in vids),
            kisa=sum(0 < v["sure"] <= 180 for v in vids) / len(vids) > .6,
        ))
    return sorted(kanallar, key=lambda k: -k["puan"])


CSS = """
:root{--bg:#f6f5f2;--kart:#fff;--yazi:#1c1b19;--soluk:#6d6a63;--cizgi:#e4e1da;--vurgu:#d4361c;--iyi:#1f7a4d;--uyari:#9a6700;--zemin2:#f0eee9}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a;--uyari:#e3b341;--zemin2:#272522}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--yazi);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-tap-highlight-color:transparent}
a{color:inherit;text-decoration:none}
main{max-width:720px;margin:auto;padding:max(16px,env(safe-area-inset-top)) 16px 40px}
header{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:24px;margin:0;flex:1}
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
.nis{padding:14px} .nis-ad{font-size:18px;font-weight:700;color:var(--vurgu);text-transform:capitalize}
.cipler{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.cip{background:var(--zemin2);border-radius:999px;padding:6px 12px;font-size:14px;font-weight:500}
.cip::after{content:" ›";color:var(--soluk)}
.nis-ust{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.durum{font-size:12px;font-weight:700;background:var(--zemin2);border-radius:999px;padding:4px 10px;color:var(--soluk)}
.durum.sicak{color:var(--vurgu)} .durum.yeni{color:var(--iyi)}
.bilgi{margin:10px 0 0;font-size:14px} .bilgi dt{color:var(--soluk);font-size:12px;margin-top:8px}
.bilgi dd{margin:0} .bilgi a{text-decoration:underline;text-underline-offset:2px}
.bos-not{color:var(--soluk)}
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
    alt.textContent = x.tarih + " · " + x.dil + " · " + x.kanal + " kanal bulundu";
    var ok = document.createElement("span"); ok.className = "ok"; ok.textContent = "›";
    ic.append(ad, alt); a.append(ic, ok); liste.append(a);
  });
  if (l.length) document.getElementById("aramalar-kutu").hidden = false;
}).catch(function () {});
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

    def kanal_kart(k):
        abone = "abone gizli" if k["abone"] is None else f'{kisa(k["abone"])} abone'
        av = (f'<img class="av" src="{e(k["kapak"])}" alt="" loading="lazy">' if k["kapak"]
              else f'<span class="av bos">{e(k["kanal"][:1].upper())}</span>')
        v = k["en_iyi"]
        return (f'<article class="kart">'
                f'<a class="kanal" href="{kanal_link(k["kanal_id"])}" target="_blank" rel="noopener">{av}'
                f'<span class="ic"><span class="ad">{e(k["kanal"])}</span>'
                f'<span class="buyuk"><b>{k["video_sayisi"]}</b> video → <b>{kisa(k["kanal_izlenme"])}</b> izlenme</span>'
                f'<span class="kucuk">Video başına {kisa(k["video_basi"])} · {abone} · {yas_metni(k["kanal_yasi"])}</span></span>'
                f'<span class="ok">›</span></a>'
                f'<div class="rzs">{rozetler(k)}</div>'
                f'<a class="video" href="{video_link(v["id"])}" target="_blank" rel="noopener">'
                f'<img src="{e(v["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(v["baslik"])}</span>'
                f'<span class="kucuk">▶ {kisa(v["izlenme"])} izlenme · {v["yayin"]}</span></span></a>'
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
        return (f'<article class="kart nis"><div class="nis-ust"><span class="nis-ad">{e(n["terim"])}</span>{durum}</div>'
                f'<dl class="bilgi">'
                f'<dt>Nereden çıktı</dt><dd><a href="{video_link(ilk["id"])}" target="_blank" rel="noopener">“{e(ilk["baslik"][:70])}”</a>'
                f' · {e(ilk["kanal"])} · {gun_once(ilk["yayin_ts"])}</dd>'
                f'<dt>Kaç kanal yapıyor</dt><dd><b>{n["kanal_sayisi"]} küçük kanal</b> · {n["video_sayisi"]} video · toplam {kisa(n["toplam"])} izlenme</dd>'
                f'<dt>En hızlısı</dt><dd>{e(hizli["kanal"])} · saatte {kisa(hizli["saatlik"])} izlenme</dd>'
                f'<dt>Tür</dt><dd>{tur} · {e(n["kategori"])}</dd>'
                f'</dl><div class="cipler">{cipler}</div></article>')

    if a.konular:  # konulu tarama: her konu kendi bölümünde
        baslik = "📌 " + e(", ".join(a.konular))
        govde = ""
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
        govde = ('<h2>Keşfedilen kanallar</h2><p class="alt-baslik">Son 1 yılda açılmış, az videoyla çok izlenen kanallar. '
                 'Karta dokun, kanal YouTube\'da açılsın.</p>'
                 + ("".join(kanal_kart(k) for k in kanallara_grupla(sonuc)[:40]) or
                    '<p class="bos-not">Bu taramada uygun kanal çıkmadı. Bir sonraki taramayı bekle.</p>'))
        ust = ""
        aramalar = ('<div id="aramalar-kutu" hidden><h2>Aramalarım</h2>'
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
<a class="btn ana" href="{TARA_LINKI}" target="_blank" rel="noopener" id="tara">Şimdi tara</a>
<button class="btn ikinci" onclick="location.reload()" aria-label="Yenile">↻</button></header>
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
Otomatik genel tarama günde 2 kez (07:17 ve 19:17).</footer>
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
    konu = f", konu: {', '.join(a.konular)}" if a.konular else ""
    print(f"Taranıyor ({a.bolge or 'global'}, son {a.gun} gün{konu})...")
    try:
        sonuc = tara(yt, a)
    except KotaBitti:
        print("! Günlük kota doldu, tarama yapılamadı.")
        json_yaz(durum_yol, dict(uyari=f"⚠️ Bugünün YouTube kotası bitti, son tarama yapılamadı. {KOTA_NOTU}",
                                 ts=dt.datetime.now(TR_SAAT).isoformat(timespec="minutes")))
        return
    nisler = nis_cikar(sonuc, haric=a.konular)
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
                             kanal=len({s["kanal_id"] for s in sonuc})))
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
