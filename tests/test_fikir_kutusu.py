"""Fikir Kutusu birim ve uçtan uca testleri (ek paket gerekmez).

  python -m unittest discover -s tests -v
"""
import datetime as dt, glob, json, os, shutil, sqlite3, sys, tempfile, unittest, urllib.request
from unittest import mock

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
import nis_avcisi as na          # noqa: E402
import fk_puanlar as P           # noqa: E402
import fk_platformlar            # noqa: E402
import fikir_kutusu as fk        # noqa: E402
import fikir_kutusu_dogrula as dg  # noqa: E402


def _oku(yol):
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


def _metin(yol):
    with open(yol, encoding="utf-8") as f:
        return f.read()


AGIRLIK = _oku(os.path.join(KOK, "sablonlar", "fikir_ayarlari.json"))["agirliklar"]


class PuanTestleri(unittest.TestCase):
    def test_patlama_kucuk_kanal_sira_disi_yuksek(self):
        p, a = P.patlama(2_000_000, 40_000, 20_000, 30)  # normalinin 50 katı
        self.assertEqual(p, 100)
        self.assertIn("50.0 katı", a)

    def test_patlama_buyuk_kanal_normal_dusuk(self):
        p, _ = P.patlama(1_000_000, 1_000_000, 5_000_000, 500)  # tam normali
        self.assertEqual(p, 0)

    def test_patlama_yeni_kanal_abone_destekli(self):
        oran = P.patlama_orani(100_000, 10_000, 1_000, 2)  # 2 video: medyan (10x) ve abone (100x) birlikte
        self.assertAlmostEqual(oran, (10 * 100) ** .5)

    def test_patlama_veri_yoksa_null(self):
        self.assertIsNone(P.patlama(100, None, None, None)[0])

    def test_hiz(self):
        self.assertIsNone(P.hiz([], [100])[0])
        self.assertIsNone(P.hiz([(3, 300)], [])[0])
        self.assertEqual(P.hiz([(10, 1000)], [100, 100, 100])[0], 50)        # referansla aynı
        self.assertGreater(P.hiz([(10, 4000)], [100])[0], 90)                  # 4 kat hızlı
        hizlanan = P.hiz([(1, 100), (3, 300), (6, 1500)], [100])
        sabit = P.hiz([(1, 100), (3, 300), (6, 1500 - 900)], [100])
        self.assertIn("Hızlanıyor", hizlanan[1])
        self.assertGreater(hizlanan[0], sabit[0])

    def test_etkilesim(self):
        self.assertAlmostEqual(P.etkilesim_orani(100, 50, 1000), .2)
        self.assertEqual(P.etkilesim(100, 50, 1000, .2)[0], 50)
        self.assertGreater(P.etkilesim(400, 0, 1000, .1)[0], 90)
        self.assertIsNone(P.etkilesim(None, 5, 1000, .1)[0])
        self.assertIsNone(P.etkilesim(10, 5, 1000, None)[0])

    def test_rekabet_boslugu(self):
        self.assertIsNone(P.rekabet_boslugu(None, None, None)[0])
        yuksek = P.rekabet_boslugu(.9, 10_000_000, 25)[0]
        dusuk_talep = P.rekabet_boslugu(.9, 1_000, 25)[0]
        doymus = P.rekabet_boslugu(.05, 10_000_000, 25)[0]
        self.assertGreater(yuksek, dusuk_talep)
        self.assertGreater(yuksek, doymus)
        self.assertEqual(yuksek, 90)

    def test_turkce_boslugu(self):
        self.assertIsNone(P.turkce_boslugu("tr", 3, 100)[0])
        self.assertIsNone(P.turkce_boslugu("en", None, None)[0])
        self.assertEqual(P.turkce_boslugu("en", 0, 0)[0], 100)
        self.assertLess(P.turkce_boslugu("en", 25, 5_000_000)[0], 5)

    def test_omur(self):
        self.assertEqual(P.omur([(48, 1000), (168, 2000)], "")[0], "kalici")
        self.assertEqual(P.omur([(48, 1000), (168, 1050)], "")[0], "haber")
        self.assertEqual(P.omur([], "x", evergreen_mi=True)[0], "kalici")
        self.assertEqual(P.omur([], "Son dakika: deprem")[0], "haber")
        self.assertIsNone(P.omur([], "kedi videosu")[0])

    def test_kazanc(self):
        self.assertEqual(P.kazanc_potansiyeli("borsa ve altın")[0], "yuksek")
        self.assertEqual(P.kazanc_potansiyeli("minecraft funny")[0], "dusuk")
        self.assertEqual(P.kazanc_potansiyeli("seyahat")[0], "orta")
        self.assertIsNone(P.kazanc_potansiyeli("")[0])

    def test_uretilebilirlik(self):
        kolay = P.uretilebilirlik("Top 10 history facts explained", "uzun")
        zor = P.uretilebilirlik("My puppy rescue vlog", "uzun")
        self.assertGreaterEqual(kolay[0], 70)
        self.assertEqual(kolay[2], "stok+seslendirme")
        self.assertLess(zor[0], 40)

    def test_kanal_uyumu(self):
        profil = dict(id="k", nisler=["finans"], anahtar_kelimeler=["altın", "dolar"], formatlar=["shorts"],
                      kacinilacak=["siyaset"])
        self.assertIsNone(P.kanal_uyumu(dict(id="b", nisler=[], anahtar_kelimeler=[]), "x", "uzun", "tr")[0])
        self.assertEqual(P.kanal_uyumu(profil, "finans altın dolar", "shorts", "tr")[0], 100)
        self.assertGreaterEqual(P.kanal_uyumu(profil, "altın fiyatı neden yükseldi", "shorts", "tr")[0], 40)  # tek kelime yeter
        self.assertEqual(P.kanal_uyumu(profil, "finans ve siyaset", "shorts", "tr")[0], 0)
        self.assertEqual(P.kanal_uyumu(profil, "kedi videosu", "uzun", "tr")[0], 0)

    def test_toplam(self):
        self.assertIsNone(P.toplam({}, AGIRLIK))
        self.assertEqual(P.toplam({"patlama": 80, "hiz": None}, AGIRLIK), 80)  # null hesaba katılmaz
        self.assertEqual(P.toplam({"patlama": 100, "kazanc_potansiyeli": "dusuk"}, {"patlama": .5, "kazanc_potansiyeli": .5}), 65)


class EslesmeTestleri(unittest.TestCase):
    """İyileştirme 1: kelimeler metin içinde parça olarak değil, tam kelime olarak eşleşir."""

    def test_parca_eslesmez(self):
        self.assertFalse(P.eslesir("kumar", "Suryakumar Yadav defends two balls"))
        self.assertFalse(P.eslesir("earn", "Learn English fast"))
        self.assertFalse(P.eslesir("tax", "Taxi driver story"))
        self.assertFalse(P.eslesir("rent", "Every parent knows this"))
        self.assertFalse(P.eslesir("war", "Best software for beginners"))

    def test_ekler_eslesir(self):
        self.assertTrue(P.eslesir("earn", "How much do pilots earn?"))
        self.assertTrue(P.eslesir("earn", "His earnings in 2026"))
        self.assertTrue(P.eslesir("tax", "Taxes explained"))
        self.assertTrue(P.eslesir("rent", "Rent vs buy"))
        self.assertTrue(P.eslesir("politic", "Politics and money"))
        self.assertTrue(P.eslesir("politic", "a political scandal"))
        self.assertTrue(P.eslesir("salary", "Salaries of NBA players"))
        self.assertTrue(P.eslesir("price", "Pricing secrets"))
        self.assertTrue(P.eslesir("retire", "Retiring at 40"))

    def test_turkce_ekler(self):
        self.assertTrue(P.eslesir("maaş", "Asgari maaşı ne kadar?"))
        self.assertTrue(P.eslesir("maaş", "Bu maaşla geçinilir mi"))
        self.assertTrue(P.eslesir("maaş", "MAAŞLARIN gerçek değeri"))
        self.assertTrue(P.eslesir("faiz", "Faizi kim belirliyor"))
        self.assertTrue(P.eslesir("borç", "Kredi kartı borcu nasıl kapanır"))     # ç → c yumuşaması
        self.assertTrue(P.eslesir("dolar", "Doların yükselişi"))
        self.assertTrue(P.eslesir("kumar", "Kumar bağımlılığı"))
        self.assertFalse(P.eslesir("kumar", "Suryakumar'ın vuruşu"))
        self.assertTrue(P.eslesir("İstanbul", "istanbul'da kira"))                # büyük İ doğru küçülür

    def test_cok_kelimeli_ve_isaret(self):
        self.assertTrue(P.eslesir("credit card", "Best Credit Cards of 2026"))
        self.assertTrue(P.eslesir("credit card", "credit  card debt"))
        self.assertFalse(P.eslesir("credit card", "credit scores and cards"))
        self.assertTrue(P.eslesir("how much does", "How much does a tow truck driver make?"))
        self.assertTrue(P.eslesir("kredi kartı", "Kredi kartını kapatmalı mıyım"))
        self.assertTrue(P.eslesir("$", "I saved $5,000 in a year"))
        self.assertFalse(P.eslesir("$", "I saved 5000 dollars"))
        self.assertFalse(P.eslesir("", "her şey"))

    def test_kanal_uyumu_kacinilacak_tam_kelime(self):
        profil = dict(id="k", nisler=["finance"], anahtar_kelimeler=["earn", "tax"], kacinilacak=["kumar", "politic"])
        self.assertNotEqual(P.kanal_uyumu(profil, "Suryakumar finance tips", "uzun", "en")[0], 0)  # eskiden 0'dı
        self.assertEqual(P.kanal_uyumu(profil, "Political finance", "uzun", "en")[0], 0)
        self.assertIsNone(P.kanal_uyumu(dict(id="b"), "x", "uzun", "en")[0])
        p, a = P.kanal_uyumu(profil, "Learn about taxi apps", "uzun", "en")
        self.assertEqual(p, 10)                                   # "earn"/"tax" artık eşleşmiyor: sadece format
        self.assertIn("eşleşme yok", a)

    def test_kural_listeleri_kelime_basinda(self):
        self.assertEqual(fk.riskleri_bul("Best software deals")[0], [])           # "war" yok
        self.assertEqual(fk.riskleri_bul("Kazan kazan stratejisi")[0], [])        # "kaza" yok
        self.assertIn("marka_guvenligi", fk.riskleri_bul("The war in 1914")[0])
        self.assertIn("marka_guvenligi", fk.riskleri_bul("Trafik kazası")[0])
        self.assertNotEqual(P.kazanc_potansiyeli("Taxi driver day in life")[0], "yuksek")
        self.assertEqual(P.kazanc_potansiyeli("Taxes for freelancers")[0], "yuksek")


class AltyapiTestleri(unittest.TestCase):
    def setUp(self):
        self.klasor = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.klasor, ignore_errors=True)

    def test_guvenli_yaz_yarim_dosya_birakmaz(self):
        yol = os.path.join(self.klasor, "a", "b.json")
        fk.guvenli_yaz(yol, {"ç": "ğüşıöÇ"})
        self.assertEqual(_oku(yol), {"ç": "ğüşıöÇ"})
        self.assertFalse(os.path.exists(yol + ".tmp"))

    def test_pt_gunu(self):
        # Yazın PDT (UTC-7): 18 Ekim 06:59 UTC → 17 Ekim; 07:00 UTC → 18 Ekim
        self.assertEqual(fk.pt_gunu(dt.datetime(2026, 10, 18, 6, 59, tzinfo=dt.timezone.utc)), "2026-10-17")
        self.assertEqual(fk.pt_gunu(dt.datetime(2026, 10, 18, 7, 0, tzinfo=dt.timezone.utc)), "2026-10-18")
        # Kışın PST (UTC-8)
        self.assertEqual(fk.pt_gunu(dt.datetime(2026, 12, 1, 7, 30, tzinfo=dt.timezone.utc)), "2026-11-30")

    def test_kota_butce_ve_onemli_mod(self):
        db = fk.db_ac(os.path.join(self.klasor, "k.db"))
        ayar = {"kota": {"gunluk_arama_butcesi": 5, "gunluk_birim_butcesi": 10, "onemli_mod_esigi": .8}}
        k = fk.Kota(db, ayar)
        for _ in range(3):
            k.say("arama")
        self.assertTrue(k.izin("arama"))
        k.say("arama")                     # 4/5 = %80 → önemli mod: arama durur, ölçüm sürer
        self.assertTrue(k.onemli_mod())
        self.assertFalse(k.izin("arama"))
        self.assertTrue(k.izin("birim"))
        k2 = fk.Kota(db, ayar)             # yeniden açılınca sayaç korunur
        self.assertEqual((k2.arama, k2.birim), (4, 0))
        ist = fk.Istemci(mock.Mock(), k2)
        with self.assertRaises(fk.ButceDoldu):
            ist.get("search", part="id")

    def test_olcum_takvimi_kacirilanlari_telafi_eder(self):
        db = fk.db_ac(os.path.join(self.klasor, "o.db"))
        an = fk.simdi()
        fk.takibe_al(db, "v1", "c1", fk.iso(an - dt.timedelta(hours=30)), "takip_kanal")
        saatler = [1, 3, 6, 12, 24, 48, 168]
        bekleyen = fk.bekleyen_olcumler(db, saatler, an)
        self.assertEqual(bekleyen[0][2], [1, 3, 6, 12, 24])     # program kapalıyken kaçırılan 5 ölçüm
        yt = mock.Mock()
        yt.get.return_value = {"items": [{"id": "v1", "statistics": {"viewCount": "5000", "likeCount": "10"},
                                          "snippet": {"channelId": "c1", "title": "t"}}]}
        ist = fk.Istemci(yt, fk.Kota(db, {"kota": {"gunluk_arama_butcesi": 20, "gunluk_birim_butcesi": 100,
                                                   "onemli_mod_esigi": .8}}))
        self.assertEqual(fk.olcumleri_yap(ist, db, saatler), 1)
        self.assertEqual(yt.get.call_count, 1)                    # tek istekte telafi
        self.assertEqual(fk.bekleyen_olcumler(db, saatler, an), [])
        sonra = an + dt.timedelta(hours=20)                        # 50. saat: 48 hedefi gelir
        self.assertEqual(fk.bekleyen_olcumler(db, saatler, sonra)[0][2], [48])

    def test_kapali_platformlar_istek_atmaz(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("istek atıldı!")):
            etkin, uyarilar = fk_platformlar.etkin_adaptorler({"youtube": True, "tiktok": False, "instagram": False,
                                                               "facebook": False})
            self.assertEqual((etkin, uyarilar), ([], []))
            etkin, uyarilar = fk_platformlar.etkin_adaptorler({"tiktok": True})
            self.assertEqual(etkin, [])
            self.assertTrue(uyarilar)
            for sinif in fk_platformlar.ADAPTORLER.values():
                with self.assertRaises(fk_platformlar.PlatformEtkinDegil):
                    sinif().yeni_videolar(["x"])


# ---------- uçtan uca: sahte YouTube ----------
def _iso(saat_once):
    return fk.iso(fk.simdi() - dt.timedelta(hours=saat_once))


BASLIKLAR = [("Gold price crash explained", ["gold price", "finance"]), ("Why the dollar is falling", ["dollar", "finance"]),
             ("Altın fiyatı neden yükseldi?", ["altın fiyatı"]), ("Stock market for beginners 2026", ["stock market"]),
             ("Top 10 investing mistakes", ["investing"]), ("Faiz kararı sonrası dolar", ["dolar", "faiz"]),
             ("My puppy rescue vlog", ["puppy"]), ("History of the Ottoman Empire", ["history"]),
             ("Bitcoin crash today", ["bitcoin"]), ("Minecraft funny moments", ["minecraft"])]


class SahteYT:
    def __init__(self):
        self.cagrilar = []

    def get(self, uc, maliyet=1, **p):
        self.cagrilar.append(uc)
        if uc == "search":
            return {"items": [{"id": {"videoId": f"v{i}"}} for i in range(3)]}
        if uc == "videos":
            ids = p["id"].split(",")
            return {"items": [{"id": i, "snippet": {"channelId": "UC" + i[1:].zfill(22), "title": "x",
                                                     "publishedAt": _iso(48)},
                               "statistics": {"viewCount": str(100_000 * (int(i[1:]) + 1)) if i[1:].isdigit() else "1000",
                                              "likeCount": "3000", "commentCount": "200"}} for i in ids]}
        if uc == "channels":
            ids = p.get("id", "").split(",")
            return {"items": [{"id": c, "snippet": {"title": f"Kanal {c[-2:]}", "publishedAt": _iso(5000)},
                               "statistics": {"subscriberCount": "15000", "videoCount": "40", "viewCount": "900000"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c[2:]}}} for c in ids if c]}
        if uc == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": f"m{j}", "videoPublishedAt": _iso(24 * j)}} for j in range(10)]}
        if uc == "commentThreads":
            return {"items": [{"snippet": {"topLevelComment": {"snippet": {"textDisplay": "Bu nasıl çalışıyor, anlatır mısın?",
                                                                          "likeCount": 12}}}}]}
        return {}


class UctanUcaTest(unittest.TestCase):
    def setUp(self):
        self.veri, self.nis = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(self.nis, "site"))
        videolar = [dict(id=f"v{i}", baslik=b, kanal=f"Kaynak {i}", kanal_id="UC" + str(i).zfill(22), ulke="US",
                         dil="en" if i % 3 else "tr", izlenme=100_000 * (i + 1), abone=15000, video_sayisi=40,
                         kanal_izlenme=900_000, yayin_ts=_iso(48 + i), sure=50 if i % 2 else 600, etiketler=et,
                         kaynak="Eğitim", puan=5, kapak="", kanal_yasi=200, yt_konular=["finance"])
                    for i, (b, et) in enumerate(BASLIKLAR)]
        fk.guvenli_yaz(os.path.join(self.nis, "site", "adaylar.json"), dict(surum=1, ts=fk.iso(fk.simdi()),
                                                                            videolar=videolar, konular=[]))
        kanallar = _oku(os.path.join(KOK, "sablonlar", "kanallar.json"))
        kanallar["kanallar"][0].update(ad="Para Rehberi", nisler=["finance", "finans"],
                                       anahtar_kelimeler=["gold", "dollar", "dolar", "altın", "stock", "invest"],
                                       kacinilacak=["minecraft"])
        fk.guvenli_yaz(os.path.join(self.veri, "kanallar.json"), kanallar)
        self.yamalar = [mock.patch.object(na, "cevir", lambda m, h, k="auto": m),
                        mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("ağ isteği atıldı!"))]
        for y in self.yamalar:
            y.start()

    def tearDown(self):
        for y in self.yamalar:
            y.stop()
        shutil.rmtree(self.veri, ignore_errors=True)
        shutil.rmtree(self.nis, ignore_errors=True)

    def test_en_az_5_gecerli_kart_ve_sozlesme_klasoru(self):
        yt = SahteYT()
        fk.calistir(self.veri, self.nis, yt=yt)
        klasor = os.path.join(self.veri, "fikir_kutusu")
        kartlar = glob.glob(os.path.join(klasor, "kanal1", "fk_*.json"))
        self.assertGreaterEqual(len(kartlar), 5)
        toplam, hatali = dg.klasoru_dogrula(klasor)
        self.assertEqual(hatali, {})
        for yol in kartlar:
            k = _oku(yol)
            self.assertEqual(k["durum"], "yeni")
            self.assertIsNotNone(k["puanlar"]["patlama"])
            self.assertTrue(k["puan_aciklamasi"] and k["kaynak_videolar"] and k["risk_aciklamasi"])
            self.assertNotIn("minecraft", k["konu"].lower())          # kaçınılacak konu bu kanala gelmez
        for yol in ("_bilgi.json", "_yayin_saatleri/kanal1.json"):
            self.assertTrue(os.path.exists(os.path.join(klasor, yol)), yol)
        self.assertTrue(os.path.isdir(os.path.join(klasor, "_geri_bildirim")))
        bilgi = _oku(os.path.join(klasor, "_bilgi.json"))
        self.assertGreater(bilgi["kota"]["birim"], 0)
        self.assertEqual(bilgi["platformlar"]["tiktok"], False)
        # Anahtar hiçbir dosyaya yazılmaz
        for yol in glob.glob(os.path.join(self.veri, "**", "*.json"), recursive=True):
            self.assertNotIn("AIza", _metin(yol))
        # İkinci çalıştırma: aynı günlük tarama tekrar işlenmez, mevcut kartların durum alanına dokunulmaz
        ilk = _oku(kartlar[0])
        ilk["durum"] = "stüdyoda"
        fk.guvenli_yaz(kartlar[0], ilk)
        fk.calistir(self.veri, self.nis, yt=yt)
        self.assertEqual(len(glob.glob(os.path.join(klasor, "kanal1", "fk_*.json"))), len(kartlar))
        self.assertEqual(_oku(kartlar[0])["durum"], "stüdyoda")

    def test_butce_dolunca_pahali_isler_durur(self):
        fk.ayar_yukle(self.veri)
        ayar_yol = os.path.join(self.veri, "fikir_ayarlari.json")
        ayar = _oku(ayar_yol)
        ayar["kota"]["gunluk_arama_butcesi"] = 2
        fk.guvenli_yaz(ayar_yol, ayar)
        yt = SahteYT()
        fk.calistir(self.veri, self.nis, yt=yt)
        self.assertEqual(yt.cagrilar.count("search"), 2)           # bütçe 2: 2'den fazla arama asla yapılmaz
        bilgi = _oku(os.path.join(self.veri, "fikir_kutusu", "_bilgi.json"))
        self.assertEqual(bilgi["kota"]["arama"], 2)
        self.assertTrue(bilgi["kota"]["onemli_mod"])
        # Bütçe dolu olsa da ucuz işler (ölçüm, kart yazımı) devam eder
        self.assertTrue(glob.glob(os.path.join(self.veri, "fikir_kutusu", "*", "fk_*.json")))

    def test_geri_bildirim_sadece_okunur(self):
        gb = os.path.join(self.veri, "fikir_kutusu", "_geri_bildirim")
        os.makedirs(gb)
        icerik = {"fikir_id": "fk_20261017_0001", "kanal_id": "kanal1", "youtube_id": "vX1",
                  "yayin_zamani": _iso(30), "format": "shorts"}
        fk.guvenli_yaz(os.path.join(gb, "vX1.json"), icerik)
        fk.calistir(self.veri, self.nis, yt=SahteYT())
        self.assertEqual(_oku(os.path.join(gb, "vX1.json")), icerik)
        db = sqlite3.connect(os.path.join(self.veri, "fk_veri.db"))
        self.assertEqual(db.execute("SELECT kaynak FROM takip WHERE video_id='vX1'").fetchone()[0], "geri_bildirim")


if __name__ == "__main__":
    unittest.main()
