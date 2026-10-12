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
  python nis_avcisi.py                      # bir kere tara, rapor üret
  python nis_avcisi.py --bolge US --dil en  # ABD / İngilizce içerik
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
                   regionCode=a.bolge, relevanceLanguage=a.dil, maxResults=50, **p)
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

    # 3) Trend listesi (ucuz: 1 birim)
    try:
        r = yt.get("videos", 1, part="id", chart="mostPopular", regionCode=a.bolge, maxResults=50)
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
        saat = max((simdi - zaman(v["snippet"]["publishedAt"])).total_seconds() / 3600, 1)
        kanal_yasi = (simdi - zaman(c["snippet"]["publishedAt"])).days
        # Gizli aboneli kanalda oran bilinmez; 100 abone varsaymak puanı yapay şişiriyordu.
        oran = None if abone is None else izl / max(abone, 100)
        video_basi = k_izl / vsay
        puan = (math.log10(video_basi + 1) * 1.2 + (math.log10(oran + 1) * 1.5 if oran is not None else 0)
                + math.log10(izl / saat + 1) + (1 if kanal_yasi < 365 else 0) - vsay / 60)
        sonuc.append(dict(
            id=vid, baslik=v["snippet"]["title"], kanal=c["snippet"]["title"], kanal_id=c["id"],
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
def rapor(sonuc, nisler, a, yol, harcanan):
    e = html.escape
    # Sunucu UTC'de çalışır; raporda Türkiye saati (UTC+3, yaz saati yok) gösterilsin.
    tarih = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")

    def nis_kart(n):
        tur = "çoğu Shorts" if n["kisa_oran"] > .6 else ("çoğu uzun video" if n["kisa_oran"] < .3 else "karışık format")
        ornek = "".join(
            f'<li><a href="https://youtu.be/{o["id"]}" target="_blank">{e(o["baslik"][:80])}</a>'
            f'<span>{e(o["kanal"])} · {kisa(o["izlenme"])} izlenme</span></li>' for o in n["ornekler"])
        return (f'<article class="nis"><h3>{e(n["terim"])}</h3>'
                f'<p class="ozet"><b>{n["kanal_sayisi"]}</b> küçük kanal · toplam <b>{kisa(n["toplam"])}</b> izlenme · {tur}</p>'
                f'<p class="oner">Bu konuda video çek: küçük kanallar bile tutturuyor, rekabet henüz düşük.</p>'
                f'<ul>{ornek}</ul></article>')

    def satir(s):
        rozet = ""
        if s.get("yeni"):
            rozet += '<i class="r yeni">YENİ</i>'
        if s.get("hizlanma") and s["hizlanma"] > s["saatlik"] * 1.2:
            rozet += '<i class="r hiz">HIZLANIYOR</i>'
        if 0 < s["sure"] <= 180:
            rozet += '<i class="r">≤3 dk</i>'
        abone = "gizli" if s["abone"] is None else kisa(s["abone"])
        return (f'<tr><td class="v"><a href="https://youtu.be/{s["id"]}" target="_blank">'
                f'<img src="{e(s["kapak"])}" alt="" loading="lazy"></a><div><a href="https://youtu.be/{s["id"]}" target="_blank">{e(s["baslik"])}</a>'
                f'<small><a href="https://youtube.com/channel/{s["kanal_id"]}" target="_blank">{e(s["kanal"])}</a> · {e(s["kaynak"])} · {s["yayin"]} {rozet}</small></div></td>'
                f'<td data-l="İzlenme">{kisa(s["izlenme"])}</td><td data-l="Abone">{abone}</td>'
                f'<td data-l="Video">{s["video_sayisi"]}</td><td data-l="Kanal top.">{kisa(s["kanal_izlenme"])}</td>'
                f'<td data-l="Video başı">{kisa(s["video_basi"])}</td><td data-l="Hız">{kisa(s["saatlik"])}/sa</td>'
                f'<td data-l="Puan"><b>{s["puan"]:.1f}</b></td></tr>')

    vurgu = "".join(
        f'<div class="vur"><b>{e(s["kanal"])}</b> — sadece <b>{s["video_sayisi"]} videoyla</b> '
        f'<b>{kisa(s["kanal_izlenme"])}</b> izlenme' + (f', {kisa(s["abone"])} abone' if s["abone"] is not None else "") + "</div>"
        for s in sorted({x["kanal_id"]: x for x in sonuc}.values(), key=lambda x: -x["video_basi"])[:6])

    sayfa = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Niş Avcısı</title>
<meta name="theme-color" content="#d4361c"><link rel="manifest" href="manifest.json">
<link rel="icon" href="icon-192.png"><link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Niş Avcısı">
<style>
:root{{--bg:#f6f5f2;--kart:#fff;--yazi:#1c1b19;--soluk:#6d6a63;--cizgi:#e4e1da;--vurgu:#d4361c;--iyi:#1f7a4d}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--kart:#1e1d1b;--yazi:#efede8;--soluk:#a19d94;--cizgi:#33312d;--vurgu:#ff6a4d;--iyi:#4cc38a}}}}
body{{margin:0;background:var(--bg);color:var(--yazi);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:auto;padding:28px 16px}}
h1{{font-size:30px;margin:0}} h2{{margin:36px 0 12px;font-size:20px}}
.meta{{color:var(--soluk);font-size:13px}} a{{color:inherit}}
.vurlar{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px;margin-top:16px}}
.vur{{background:var(--kart);border:1px solid var(--cizgi);border-radius:8px;padding:10px 12px}}
.vur b:not(:first-child){{color:var(--vurgu)}}
.nisler{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
.nis{{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:16px}}
.nis h3{{margin:0 0 4px;font-size:19px;text-transform:capitalize;color:var(--vurgu)}}
.ozet{{margin:0;color:var(--soluk);font-size:13px}} .oner{{margin:8px 0;font-size:13px;color:var(--iyi)}}
.nis ul{{margin:0;padding-left:18px;font-size:13px}} .nis li span{{display:block;color:var(--soluk);font-size:12px}}
.tablo{{overflow-x:auto;background:var(--kart);border:1px solid var(--cizgi);border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--cizgi);text-align:right;white-space:nowrap}}
th{{color:var(--soluk);font-weight:600;font-size:12px;position:sticky;top:0;background:var(--kart)}}
td.v{{text-align:left;white-space:normal;display:flex;gap:10px;min-width:360px}}
td.v img{{width:120px;border-radius:6px}} td.v small{{display:block;color:var(--soluk)}}
.r{{font-style:normal;font-size:10px;font-weight:700;border:1px solid var(--cizgi);border-radius:4px;padding:0 4px;margin-left:4px}}
.r.yeni{{color:var(--iyi);border-color:var(--iyi)}} .r.hiz{{color:var(--vurgu);border-color:var(--vurgu)}}
@media (max-width:700px){{
main{{padding:20px 12px}} h1{{font-size:24px}} h2{{margin-top:28px}}
.vurlar,.nisler{{grid-template-columns:1fr}}
.tablo{{overflow:visible;background:none;border:0}}
table,tbody,tr,td{{display:block;width:auto}} thead{{display:none}}
tr{{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:10px;margin-bottom:10px}}
td{{display:inline-block;border:0;padding:2px 10px 2px 0;text-align:left}}
td[data-l]::before{{content:attr(data-l) " ";color:var(--soluk);font-size:11px}}
td.v{{display:flex;min-width:0;padding:0 0 6px}} td.v img{{width:96px;flex:none}}
}}
</style></head><body><main>
<h1>Niş Avcısı</h1>
<p class="meta">{tarih} · Bölge {e(a.bolge)} · son {a.gun} gün · ≤{kisa(a.max_abone)} abone, ≤{a.max_video} video, ≥{kisa(a.min_izlenme)} izlenme · {len(sonuc)} aday · {harcanan} kota birimi</p>
<h2>Az videoyla patlayan kanallar</h2><div class="vurlar">{vurgu or "<p>Bu taramada kriterlere uyan kanal çıkmadı. Filtreleri gevşet.</p>"}</div>
<h2>Önerilen nişler</h2><p class="meta">Birden fazla küçük kanalda aynı anda tutan konular. Ne kadar çok kanal, o kadar güçlü sinyal.</p>
<div class="nisler">{"".join(nis_kart(n) for n in nisler) or "<p>Henüz tekrar eden konu yok.</p>"}</div>
<h2>Tüm adaylar</h2>
<div class="tablo"><table><thead><tr><th style="text-align:left">Video</th><th>İzlenme</th><th>Abone</th><th>Video sayısı</th><th>Kanal toplam</th><th>Video başı</th><th>Hız</th><th>Puan</th></tr></thead>
<tbody>{"".join(satir(s) for s in sonuc[:200])}</tbody></table></div>
<p class="meta">YENİ = ilk kez görüldü · HIZLANIYOR = son taramadan beri ortalamasından hızlı büyüyor (2. taramadan itibaren çalışır).</p>
</main></body></html>"""
    with open(yol, "w", encoding="utf-8") as f:
        f.write(sayfa)


def calistir(a):
    key = a.anahtar or os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("API anahtarı yok. YT_API_KEY ortam değişkenini ayarla ya da --anahtar ile ver (dosyanın başındaki kurulum adımlarına bak).")
    yt = YT(key)
    print(f"\n[{dt.datetime.now():%H:%M}] Taranıyor ({a.bolge})...")
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
    p.add_argument("--bolge", default="TR", help="Ülke kodu: TR, US, DE, GB...")
    p.add_argument("--dil", default="tr", help="İçerik dili: tr, en, de...")
    p.add_argument("--gun", type=int, default=30, help="Son kaç günde yayınlanan videolar")
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
    a.bolge = a.bolge.upper()
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
