"""vidiq_isle.py testleri (ek paket gerekmez).

  python -m unittest discover -s tests -v
"""
import datetime as dt, json, os, shutil, sqlite3, sys, tempfile, unittest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)
import vidiq_isle as V  # noqa: E402

SIMDI = dt.datetime(2026, 10, 22, 9, 30, tzinfo=dt.timezone.utc)


def yaz(yol, veri):
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f)


def oku(yol):
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


class KrediTestleri(unittest.TestCase):
    def test_kredi_kalan_gunlere_bolunur(self):
        # 175 kredi = 35 çağrı, 21 gün → günde 2 (yukarı yuvarlanır, dönem sonunda hepsi harcanır)
        self.assertEqual(V.gunluk_cagri(175, "2026-11-12T00:27:18Z", SIMDI), 2)

    def test_son_gun_kalanin_hepsi_harcanir_ama_sinirli(self):
        self.assertEqual(V.gunluk_cagri(20, "2026-11-12T00:00:00Z", dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)), 4)
        self.assertEqual(V.gunluk_cagri(150, "2026-11-12T00:00:00Z", dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)),
                         V.GUNLUK_EN_FAZLA)

    def test_kredi_yoksa_cagri_yok(self):
        self.assertEqual(V.gunluk_cagri(4, "2026-11-12T00:00:00Z", SIMDI), 0)


class TurkceTestleri(unittest.TestCase):
    def test_turkce_kanallar_elenir(self):
        self.assertTrue(V.turkce_mi("tr", None))
        self.assertTrue(V.turkce_mi("tr, en", "US"))
        self.assertTrue(V.turkce_mi("en", "TR"))
        self.assertFalse(V.turkce_mi("en, tr", "US"))
        self.assertFalse(V.turkce_mi("hi", "IN"))
        self.assertFalse(V.turkce_mi(None, None))


class NisOzetiTestleri(unittest.TestCase):
    def test_kanallar_nise_gore_gruplanir_cok_kanalli_nis_ustte(self):
        l = [dict(id="a", ad="0989xx", nis="Sports News, MLB, Phillies", buyume30=170, ort_izlenme=2_000_000, abone=60000),
             dict(id="b", ad="yugal", nis="sports news, MLB", buyume30=120, ort_izlenme=1_000_000, abone=60000, ortak=True),
             dict(id="c", ad="Tek", nis="Celebrity News", buyume30=190, abone=4000),
             dict(id="d", ad="Etiketsiz", nis="", buyume30=50)]
        n = V.nis_ozeti(l)
        self.assertEqual([x["nis"] for x in n], ["Sports News", "Celebrity News"])
        spor = n[0]
        self.assertEqual((spor["kanal_sayisi"], spor["ort_buyume30"], spor["ort_izlenme"], spor["ortak"]),
                         (2, 145.0, 1_500_000, True))
        self.assertEqual(spor["alt"][0], "MLB")
        self.assertEqual([o["id"] for o in spor["ornekler"]], ["a", "b"])   # anlamsız adlı kanal da örnek olarak kalır


class OrtakKonuTestleri(unittest.TestCase):
    def test_nis_avcisi_konusu_ya_da_iki_aday_basligi_eslesir(self):
        konular = ["mlb trade deadline", "food animation"]
        self.assertEqual(V.ortak_konu(["Sports News", "MLB"], konular, {}), "MLB")
        metinler = {"a": "Giant crane lift fail", "b": "Crane lift gone wrong", "c": "cat video"}
        self.assertEqual(V.ortak_konu(["Heavy Machinery", "Crane"], [], metinler), "Crane")
        self.assertIsNone(V.ortak_konu(["Crane"], [], {"a": "crane", "b": "cranes and more"}))  # tek kanal yetmez
        self.assertIsNone(V.ortak_konu(["Food"], ["seafood boil"], {}))   # kelime içinde geçmesi sayılmaz

    def test_ozet_ortak_konuyu_isaretler(self):
        n = V.nis_ozeti([dict(id="a", ad="x", nis="Sports News, MLB", buyume30=50)], 40, ["mlb trade deadline"], {})
        self.assertEqual((n[0]["ortak"], n[0]["ortak_konu"]), (True, "MLB"))


class YuksekGelirTestleri(unittest.TestCase):
    def test_dusuk_gelirli_diller_alinmaz(self):
        self.assertTrue(V.yuksek_gelirli("en", "IN"))       # dil belirleyici: İngilizce içerik
        self.assertTrue(V.yuksek_gelirli("de-DE", None))
        self.assertFalse(V.yuksek_gelirli("hi", "IN"))
        self.assertFalse(V.yuksek_gelirli("ar, en", None))
        self.assertTrue(V.yuksek_gelirli(None, "US"))        # dil yoksa ülkeye bakılır
        self.assertFalse(V.yuksek_gelirli(None, None))
        k = V.kanal_al(dict(channelId="UCx", subscriberCount=5000, subsGrowth30d=50, languages="vi"), "x", "t")
        self.assertFalse(V.ise_yarar(k))

    def test_yukselen_arama_dil_doner_ve_onceki_sonuctan_konu_turetmez(self):
        d = tempfile.mkdtemp()
        try:
            yaz(os.path.join(d, "vidiq.json"), dict(kanallar=[dict(id="UCm", nis="Mechanical Engineering", buyume30=500)]))
            hedefler = set()
            for gun in range(4):
                p = V.plan_yap(10, "2026-10-23T00:00:00Z", d, SIMDI + dt.timedelta(days=gun))
                for c in p["cagrilar"]:
                    self.assertEqual(c["tip"], "yukselen")       # aday yok → hepsi yükselen arama
                    self.assertIn(c["parametreler"]["query"], V.ARAMA_NISLERI)
                    self.assertEqual(len(c["parametreler"]["languages"]), 1)
                    hedefler.add(c["hedef"])
            self.assertFalse(any("mechanical" in h for h in hedefler))
            self.assertGreater(len({h.split(" [")[0] for h in hedefler}), 1)   # farklı alanlar
        finally:
            shutil.rmtree(d)


class UctanUcaTestler(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.site = os.path.join(self.d, "site")
        self.k = os.path.join(self.d, "vidiq")
        os.makedirs(self.site), os.makedirs(self.k)
        self.db = os.path.join(self.d, "g.db")
        db = sqlite3.connect(self.db)
        db.execute("create table kanal_kayit(ts, kanal_id, kanal, izlenme, abone, video_sayisi)")
        db.execute("insert into kanal_kayit values ('2026-10-20', 'UCortak', 'Ortak', 1, 1, 1)")
        db.commit(), db.close()
        yaz(os.path.join(self.site, "adaylar.json"), dict(videolar=[
            dict(kanal_id="UCtr", kanal="Türk", dil="tr", ulke="TR", puan=30, kanal_yasi=40, video_sayisi=10),
            dict(kanal_id="UCbebek", kanal="Bebek", dil="en", ulke="US", puan=25, kanal_yasi=6, video_sayisi=1),
            dict(kanal_id="UCen1", kanal="Bir", dil="en", ulke="US", puan=20, kanal_yasi=20, video_sayisi=8),
            dict(kanal_id="UCen1", kanal="Bir", dil="en", ulke="US", puan=19, kanal_yasi=20, video_sayisi=8),
            dict(kanal_id="UCen2", kanal="İki", dil="en", ulke="GB", puan=10, kanal_yasi=30, video_sayisi=6),
            dict(kanal_id="UChi", kanal="Hint", dil="hi", ulke="IN", puan=40, kanal_yasi=30, video_sayisi=9)]))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_plan_turkce_ve_cok_yeni_adayi_atlar_turleri_karistirir(self):
        p = V.plan_yap(30, "2026-10-24T00:00:00Z", self.site, SIMDI)   # 6 çağrı, 2 gün → 3
        self.assertEqual(p["cagri_sayisi"], 3)
        self.assertEqual([c["tip"] for c in p["cagrilar"]], ["benzer", "yukselen", "benzer"])
        self.assertEqual([c["hedef"] for c in p["cagrilar"] if c["tip"] == "benzer"], ["UCen1", "UCen2"])

    def test_isle_suzer_birlestirir_ve_sorgulanani_tekrar_planlamaz(self):
        p = V.plan_yap(10, "2026-10-23T00:00:00Z", self.site, SIMDI)    # 2 çağrı: benzer UCen1 + yükselen
        yaz(os.path.join(self.k, "plan.json"), p)
        b, y = p["cagrilar"]
        yaz(os.path.join(self.k, b["dosya"]), {"similarChannels": [
            dict(channelId="UCiyi", channelTitle="İyi", subscriberCount=20000, subsGrowth30d=40, languages="en", niches="Space"),
            dict(channelId="UCyavas", channelTitle="Yavaş", subscriberCount=20000, subsGrowth30d=2, languages="en"),
            dict(channelId="UCdev", channelTitle="Dev", subscriberCount=5_000_000, subsGrowth30d=50, languages="en"),
            dict(channelId="UCturk", channelTitle="Türk", subscriberCount=20000, subsGrowth30d=50, languages="tr"),
            dict(channelId="UCdin", channelTitle="Din", subscriberCount=20000, subsGrowth30d=50, languages="en",
                 niches="Islamic Religious Content"),
            dict(channelId="UCen1", channelTitle="Kendisi", subscriberCount=20000, subsGrowth30d=50, languages="en")]})
        yaz(os.path.join(self.k, y["dosya"]), {"channels": [
            dict(channelId="UCortak", channelTitle="Ortak", subscriberCount=3000, subsGrowth30d=15, languages="en",
                 niche="Space", subNiches=["Rockets"], breakoutChannel=True),
            dict(channelId="UCduran", channelTitle="Duran", subscriberCount=3000, subsGrowth30d=3, languages="en")]})
        ozet, liste = V.isle(self.k, self.site, self.db, SIMDI)
        veri = oku(os.path.join(self.site, "vidiq.json"))
        self.assertEqual([k["id"] for k in veri["kanallar"]], ["UCiyi", "UCortak"])
        self.assertEqual({k["id"]: k["ortak"] for k in veri["kanallar"]}, {"UCiyi": False, "UCortak": True})
        self.assertEqual(veri["kanallar"][1]["nis"], "Space, Rockets")
        self.assertEqual(len(veri["sorgular"]), 2)
        # Ertesi gün aynı aday ve niş yeniden sorulmaz
        p2 = V.plan_yap(10, "2026-10-24T00:00:00Z", self.site, SIMDI + dt.timedelta(days=1))
        self.assertNotIn("UCen1", [c["hedef"] for c in p2["cagrilar"]])
        self.assertNotIn(y["hedef"], [c["hedef"] for c in p2["cagrilar"]])

    def test_eksik_yanit_dosyasi_atlanir(self):
        p = V.plan_yap(5, "2026-10-23T00:00:00Z", self.site, SIMDI)
        yaz(os.path.join(self.k, "plan.json"), p)
        ozet, liste = V.isle(self.k, self.site, self.db, SIMDI)
        self.assertIn("atlandı", ozet[0])
        self.assertEqual(liste, [])


if __name__ == "__main__":
    unittest.main()
