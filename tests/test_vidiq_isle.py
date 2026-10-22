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
            dict(kanal_id="UCtr", kanal="Türk", dil="tr", ulke="TR", puan=30),
            dict(kanal_id="UCen1", kanal="Bir", dil="en", ulke="US", puan=20),
            dict(kanal_id="UCen1", kanal="Bir", dil="en", ulke="US", puan=19),
            dict(kanal_id="UCen2", kanal="İki", dil="en", ulke="GB", puan=10)]))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_plan_turkce_adayi_atlar_ve_turleri_karistirir(self):
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
            dict(channelId="UCortak", channelTitle="Ortak", subscriberCount=3000, subsGrowth30d=5, languages="en",
                 niche="Space", subNiches=["Rockets"], breakoutChannel=True)]})
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
