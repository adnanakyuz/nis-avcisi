#!/usr/bin/env python3
"""
NİŞ AVCISI — YouTube'da az videolu / az aboneli ama çok izlenen kanalları ve
yükselen konuları otomatik bulur. Konu vermene gerek yok: tüm kategorileri tarar.

Kurulum (bir kere):
  1) https://console.cloud.google.com  → yeni proje → "YouTube Data API v3"ü etkinleştir
     → Kimlik bilgileri → API anahtarı oluştur.
  2) Anahtarı kaydet:
       Windows:  setx YT_API_KEY "ANAHTARIN"     (sonra terminali kapatıp aç)
       Mac/Linux: export YT_API_KEY="ANAHTARIN"
  3) Python 3.8+ yeterli, ek paket gerekmez.

Kullanım:
  python nis_avcisi.py                      # global, son 14 gün: bir kere tara, rapor üret
  python nis_avcisi.py --bolge TR --dil tr  # sadece Türkiye / Türkçe içerik
  python nis_avcisi.py --gun 7              # farklı zaman aralığı
  python nis_avcisi.py --kelime "yapay zeka" --kelime "tarım"   # ek konu tohumu (isteğe bağlı)
  python nis_avcisi.py --surekli 6          # her 6 saatte bir tekrar tara (sürekli takip)

Çıktı (betiğin klasörüne yazılır): nis_raporu.html (tarayıcıda aç) + nis_gecmis.db
(büyüme takibi için geçmiş).
Kota: Ücretsiz günlük kota 10.000 birim. Varsayılan bir tarama ~1.420 birim harcar
(14 kategori × 100 birim + birkaç ucuz detay sorgusu; her --kelime +100), yani günde
~7 tarama yapabilirsin. --surekli 6 (günde 4 tarama) güvenli aralıktadır.
"""
import argparse, collections, datetime as dt, html, json, math, os, re, sqlite3, sys, time
import urllib.error, urllib.parse, urllib.request

API = "https://www.googleapis.com/youtube/v3/"
KATEGORILER = {
    "1": "Film & Animasyon", "2": "Otomobil", "10": "Müzik", "15": "Hayvanlar",
    "17": "Spor", "19": "Seyahat", "20": "Oyun", "22": "Kişiler & Bloglar",
    "23": "Komedi", "24": "Eğlence", "25": "Haber", "26": "Nasıl Yapılır & Stil",
    "27": "Eğitim", "28": "Bilim & Teknoloji",
}
STOP = set("""
ve ile bir bu şu o da de mi mu mü mı için gibi çok en ne ki ama veya hem daha her
nasıl neden niye kadar sonra önce olan oldu olur ben sen biz siz onlar benim senin
var yok değil ya yani artık hiç tüm bütün bunu şunu onu bunlar şey olarak ilk son yeni
the a an and or of to in on for with at by from is are was be this that it its my your
you i we how why what when who vs part full video videos official new shorts short
episode ep day days best top 2024 2025 2026 live amp quot feat ft lyrics music hd 4k
into out get got can will just about after like our their they them his her not all
have has had one two from more most very really only this these those than then there
beni bana sana seni bize size gibi bile çünkü şimdi bugün yarın dün
""".split())


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
                govde = e.read().decode("utf-8", "ignore")
                if "quotaexceeded" in govde.lower() or "dailylimitexceeded" in govde.lower():
                    raise KotaBitti("Günlük API kotası doldu. Yarın tekrar dene.")
                if "keyinvalid" in govde.lower() or "api key not valid" in govde.lower():
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


# ---------- veritabanı ----------
def db_ac(yol):
    db = sqlite3.connect(yol)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS video(id TEXT PRIMARY KEY, ilk_gorulme TEXT);
    CREATE TABLE IF NOT EXISTS anlik(video_id TEXT, ts TEXT, izlenme INTEGER);
    CREATE INDEX IF NOT EXISTS ix_anlik ON anlik(video_id, ts);
    """)
    return db


# ---------- tarama ----------
def tara(yt, a):
    simdi = dt.datetime.now(dt.timezone.utc)
    once = (simdi - dt.timedelta(days=a.gun)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids, kaynak = [], {}

    def ekle(ids, etiket):
        for v in ids:
            if v not in kaynak:
                video_ids.append(v)
                kaynak[v] = etiket

    def ara(etiket, **p):
        r = yt.get("search", 100, part="id", type="video", order="viewCount", publishedAfter=once,
                   regionCode=a.bolge or None, relevanceLanguage=a.dil or None, maxResults=50, **p)
        ekle([i["id"]["videoId"] for i in r.get("items", []) if "videoId" in i.get("id", {})], etiket)

    # 1) Konu vermeden: her kategoride son X günün en çok izlenenleri
    # 2) İsteğe bağlı kelime tohumları
    # Kota arama sırasında biterse elde edilenle devam et (detay sorguları sadece 1 birim).
    aramalar = [(ad, dict(videoCategoryId=kid)) for kid, ad in KATEGORILER.items()]
    aramalar += [(f"“{k}”", dict(q=k)) for k in a.kelime]
    for etiket, p in aramalar:
        try:
            ara(etiket, **p)
            print(f"  ✓ {etiket}")
        except ApiHatasi:
            print(f"  – {etiket} (bu bölgede aranamadı, atlandı)")
        except KotaBitti:
            print("  ! Kota bitti, şimdiye kadar bulunanlarla devam ediliyor.")
            break

    # 3) Trend listesi (ucuz: 1 birim). YouTube burada ülke ister; globalde en büyük liste ABD.
    try:
        r = yt.get("videos", 1, part="id", chart="mostPopular", regionCode=a.bolge or "US", maxResults=50)
        ekle([i["id"] for i in r.get("items", [])], "Trendler")
    except ApiHatasi:
        pass

    # Video detayları
    videolar = {}
    for i in range(0, len(video_ids), 50):
        r = yt.get("videos", 1, part="snippet,statistics,contentDetails", id=",".join(video_ids[i:i + 50]))
        for v in r.get("items", []):
            videolar[v["id"]] = v

    # Kanal detayları
    kanal_ids = list({v["snippet"]["channelId"] for v in videolar.values()})
    kanallar = {}
    for i in range(0, len(kanal_ids), 50):
        r = yt.get("channels", 1, part="snippet,statistics", id=",".join(kanal_ids[i:i + 50]))
        for c in r.get("items", []):
            kanallar[c["id"]] = c

    sonuc = []
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
            continue  # otomatik müzik / resmi şarkıcı kanalları; gerçek niş sinyali değil
        saat = max((simdi - zaman(v["snippet"]["publishedAt"])).total_seconds() / 3600, 1)
        kanal_yasi = (simdi - zaman(c["snippet"]["publishedAt"])).days
        # Gizli aboneli kanalda oran bilinmez; 100 abone varsaymak puanı yapay şişiriyordu.
        oran = None if abone is None else izl / max(abone, 100)
        video_basi = k_izl / vsay
        puan = (math.log10(video_basi + 1) * 1.2 + (math.log10(oran + 1) * 1.5 if oran is not None else 0)
                + math.log10(izl / saat + 1) + (1 if kanal_yasi < 365 else 0) - vsay / 60)
        sonuc.append(dict(
            id=vid, baslik=v["snippet"]["title"], kanal=c["snippet"]["title"], kanal_id=c["id"],
            kanal_kapak=c["snippet"].get("thumbnails", {}).get("default", {}).get("url", ""),
            kapak=v["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            izlenme=izl, abone=abone, video_sayisi=vsay, kanal_izlenme=k_izl,
            video_basi=video_basi, oran=oran, saatlik=izl / saat, kanal_yasi=kanal_yasi,
            sure=sure_sn(v.get("contentDetails", {}).get("duration")),
            yayin=v["snippet"]["publishedAt"][:10], kaynak=kaynak[vid],
            etiketler=v["snippet"].get("tags", [])[:15], puan=puan,
        ))
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
        db.execute("INSERT OR IGNORE INTO video VALUES(?,?)", (s["id"], ts))
        db.execute("INSERT INTO anlik VALUES(?,?,?)", (s["id"], ts, s["izlenme"]))
    db.commit()


def nis_cikar(sonuc, adet=12):
    """Farklı küçük kanallarda tekrar eden kelime/ikili kelimeleri bul = yükselen konu."""
    terim_kanal = collections.defaultdict(set)
    terim_puan = collections.Counter()
    terim_video = collections.defaultdict(list)
    for s in sonuc:
        ws = kelimeler(s["baslik"] + " " + " ".join(s["etiketler"]))
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
        secilen.append(dict(terim=t, kanal_sayisi=len(terim_kanal[t]),
                            toplam=sum(v["izlenme"] for v in vids), ornekler=vids[:3],
                            kisa_oran=sum(0 < v["sure"] <= 180 for v in vids) / len(vids)))
        kullanilan.add(frozenset(t.split()))
        if len(secilen) >= adet:
            break
    return secilen


# ---------- rapor ----------
TARA_LINKI = "https://github.com/adnanakyuz/nis-avcisi/actions/workflows/tara.yml"


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


def rapor(sonuc, nisler, a, yol, harcanan):
    e = html.escape
    tarih = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")
    kanallar = kanallara_grupla(sonuc)
    kapsam = "Global" if not a.bolge else e(a.bolge)

    def rozetler(k):
        r = ""
        if k["hizli"]:
            r += '<span class="rz hiz">🔥 Hızlanıyor</span>'
        if k["yeni"]:
            r += '<span class="rz yeni">Yeni bulundu</span>'
        if k["kanal_yasi"] < 365:
            r += '<span class="rz">Genç kanal</span>'
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

    def nis_kart(n):
        tur = "çoğu kısa video" if n["kisa_oran"] > .6 else ("çoğu uzun video" if n["kisa_oran"] < .3 else "kısa + uzun karışık")
        gorulen, cipler = set(), ""
        for o in n["ornekler"]:
            if o["kanal_id"] in gorulen:
                continue
            gorulen.add(o["kanal_id"])
            cipler += f'<a class="cip" href="{kanal_link(o["kanal_id"])}" target="_blank" rel="noopener">{e(o["kanal"])}</a>'
        return (f'<article class="kart nis"><div class="nis-ad">{e(n["terim"])}</div>'
                f'<div class="kucuk"><b>{n["kanal_sayisi"]} küçük kanal</b> bu konuda tutuyor · {kisa(n["toplam"])} izlenme · {tur}</div>'
                f'<div class="cipler">{cipler}</div></article>')

    def video_satir(s):
        return (f'<a class="satir" href="{video_link(s["id"])}" target="_blank" rel="noopener">'
                f'<img src="{e(s["kapak"])}" alt="" loading="lazy"><span><span class="vb">{e(s["baslik"])}</span>'
                f'<span class="kucuk">{e(s["kanal"])} · {kisa(s["izlenme"])} izlenme</span></span></a>')

    kanal_html = "".join(kanal_kart(k) for k in kanallar[:40]) or \
        '<p class="bos-not">Bu taramada uygun kanal çıkmadı. Bir sonraki taramayı bekle.</p>'
    nis_html = "".join(nis_kart(n) for n in nisler) or '<p class="bos-not">Henüz ortak konu çıkmadı.</p>'

    sayfa = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Niş Avcısı</title>
<meta name="theme-color" content="#d4361c"><link rel="manifest" href="manifest.json">
<link rel="icon" href="icon-192.png"><link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Niş Avcısı">
<style>
:root{{--bg:#f6f5f2;--kart:#fff;--yazi:#1c1b19;--soluk:#6d6a63;--cizgi:#e4e1da;--vurgu:#d4361c;--iyi:#1f7a4d;--zemin2:#f0eee9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a;--zemin2:#272522}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--yazi);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-tap-highlight-color:transparent}}
a{{color:inherit;text-decoration:none}}
main{{max-width:720px;margin:auto;padding:max(16px,env(safe-area-inset-top)) 16px 40px}}
header{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
h1{{font-size:24px;margin:0;flex:1}}
.btn{{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:10px 16px;font-weight:600;font-size:15px;border:0;cursor:pointer}}
.btn.ana{{background:var(--vurgu);color:#fff}} .btn.ikinci{{background:var(--zemin2);color:var(--yazi)}}
.meta{{color:var(--soluk);font-size:13px;margin:6px 0 0}}
.ipucu{{background:var(--zemin2);border-radius:12px;padding:10px 14px;font-size:14px;margin:14px 0 0;color:var(--soluk)}}
.ipucu[hidden]{{display:none}}
h2{{font-size:19px;margin:32px 0 4px}} .alt-baslik{{color:var(--soluk);font-size:14px;margin:0 0 12px}}
.kart{{background:var(--kart);border:1px solid var(--cizgi);border-radius:14px;margin-bottom:12px;overflow:hidden}}
.kanal{{display:flex;align-items:center;gap:12px;padding:14px}}
.kanal:active,.video:active,.satir:active,.cip:active{{opacity:.6}}
.av{{width:52px;height:52px;border-radius:50%;flex:none;object-fit:cover;background:var(--zemin2)}}
.av.bos{{display:grid;place-items:center;font-weight:700;color:var(--vurgu)}}
.ic{{flex:1;min-width:0;display:flex;flex-direction:column}}
.ad{{font-weight:700;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.buyuk{{font-size:15px}} .buyuk b{{color:var(--vurgu)}}
.kucuk{{display:block;color:var(--soluk);font-size:13px}}
.ok{{font-size:28px;color:var(--soluk);line-height:1}}
.rzs{{padding:0 14px;display:flex;flex-wrap:wrap;gap:6px}} .rzs:empty{{display:none}}
.rz{{font-size:12px;font-weight:600;background:var(--zemin2);border-radius:999px;padding:3px 10px}}
.rz.hiz{{color:var(--vurgu)}} .rz.yeni{{color:var(--iyi)}}
.video,.satir{{display:flex;gap:10px;align-items:center;padding:12px 14px;border-top:1px solid var(--cizgi);margin-top:12px}}
.video img,.satir img{{width:96px;aspect-ratio:16/9;object-fit:cover;border-radius:8px;flex:none;background:var(--zemin2)}}
.vb{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:14px;font-weight:500}}
.satir{{margin:0;background:var(--kart)}}
.nis{{padding:14px}} .nis-ad{{font-size:18px;font-weight:700;color:var(--vurgu);text-transform:capitalize}}
.cipler{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.cip{{background:var(--zemin2);border-radius:999px;padding:6px 12px;font-size:14px;font-weight:500}}
.cip::after{{content:" ›";color:var(--soluk)}}
details{{background:var(--kart);border:1px solid var(--cizgi);border-radius:14px;overflow:hidden}}
summary{{padding:14px;font-weight:600;cursor:pointer}}
.bos-not{{color:var(--soluk)}}
footer{{color:var(--soluk);font-size:12px;margin-top:28px}}
</style></head><body><main>
<header><h1>🎯 Niş Avcısı</h1>
<a class="btn ana" href="{TARA_LINKI}" target="_blank" rel="noopener" id="tara">Şimdi tara</a>
<button class="btn ikinci" onclick="location.reload()">↻</button></header>
<p class="meta">Son tarama: {tarih} · {kapsam} · son {a.gun} gün</p>
<p class="ipucu" id="ipucu" hidden>Açılan GitHub sayfasında <b>Run workflow</b> → yeşil <b>Run workflow</b>'a bas. 2 dakika sonra buraya dönüp ↻ ile yenile.</p>

<h2>Keşfedilen kanallar</h2>
<p class="alt-baslik">Az videoyla çok izlenen kanallar. Karta dokun, kanal YouTube'da açılsın.</p>
{kanal_html}

<h2>Yükselen konular</h2>
<p class="alt-baslik">Birden fazla küçük kanalda aynı anda tutan konular. Kanal adına dokun, incele.</p>
{nis_html}

<h2>Tüm videolar</h2>
<details><summary>{len(sonuc)} videoyu göster</summary>{"".join(video_satir(s) for s in sonuc[:150])}</details>

<footer>Filtre: ≤{kisa(a.max_abone)} abone · ≤{a.max_video} video · ≥{kisa(a.min_izlenme)} izlenme · {harcanan} kota birimi.
Otomatik tarama günde 2 kez (07:17 ve 19:17).</footer>
</main>
<script>document.getElementById("tara").addEventListener("click",function(){{document.getElementById("ipucu").hidden=false}})</script>
</body></html>"""
    with open(yol, "w", encoding="utf-8") as f:
        f.write(sayfa)


def calistir(a):
    key = a.anahtar or os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("API anahtarı yok. YT_API_KEY ortam değişkenini ayarla ya da --anahtar ile ver (dosyanın başındaki kurulum adımlarına bak).")
    yt = YT(key)
    print(f"\n[{dt.datetime.now():%H:%M}] Taranıyor ({a.bolge or 'global'}, son {a.gun} gün)...")
    try:
        sonuc = tara(yt, a)
    except KotaBitti as k:
        print("!", k)
        return
    db = db_ac(a.db)
    try:
        gecmisle_karsilastir(db, sonuc)
    finally:
        db.close()
    nisler = nis_cikar(sonuc)
    rapor(sonuc, nisler, a, a.cikti, yt.harcanan)
    print(f"\n{len(sonuc)} aday bulundu, {yt.harcanan} kota birimi harcandı.")
    for s in sorted({x["kanal_id"]: x for x in sonuc}.values(), key=lambda x: -x["video_basi"])[:5]:
        print(f"  • {s['kanal']}: {s['video_sayisi']} video → {kisa(s['kanal_izlenme'])} izlenme")
    if nisler:
        print("Önerilen nişler: " + ", ".join(n["terim"] for n in nisler[:6]))
    print(f"Rapor: {os.path.abspath(a.cikti)}")


def main():
    p = argparse.ArgumentParser(description="YouTube niş bulucu")
    p.add_argument("--anahtar", help="YouTube Data API anahtarı (yoksa YT_API_KEY kullanılır)")
    p.add_argument("--bolge", default="", help="Ülke kodu: TR, US, DE... (boş = global)")
    p.add_argument("--dil", default="", help="İçerik dili: tr, en, de... (boş = hepsi)")
    p.add_argument("--gun", type=int, default=14, help="Son kaç günde yayınlanan videolar")
    p.add_argument("--max-abone", type=int, default=100_000)
    p.add_argument("--max-video", type=int, default=40, help="Kanalın en fazla video sayısı")
    p.add_argument("--min-izlenme", type=int, default=100_000)
    p.add_argument("--kelime", action="append", default=[], help="İsteğe bağlı konu tohumu (birden çok verilebilir)")
    p.add_argument("--surekli", type=float, default=0, help="Her N saatte bir tekrar tara")
    # Varsayılan dosyalar betiğin yanına yazılır; Görev Zamanlayıcı vb. başka klasörden çalıştırsa da geçmiş kaybolmaz.
    klasor = os.path.dirname(os.path.abspath(__file__))
    p.add_argument("--cikti", default=os.path.join(klasor, "nis_raporu.html"))
    p.add_argument("--db", default=os.path.join(klasor, "nis_gecmis.db"))
    a = p.parse_args()
    a.bolge = a.bolge.upper().strip()
    a.dil = a.dil.strip()
    try:  # Windows konsolu ✓ • → gibi karakterlerde çökmesin
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    while True:
        try:
            calistir(a)
        except ApiHatasi as h:
            if not a.surekli:
                sys.exit(str(h))
            print("!", h)  # sürekli modda tek hata yüzünden durma
        if not a.surekli:
            break
        print(f"Sonraki tarama {a.surekli} saat sonra. Durdurmak için Ctrl+C.")
        time.sleep(a.surekli * 3600)


if __name__ == "__main__":
    main()
