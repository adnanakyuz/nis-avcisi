"""Fikir Kutusu — diğer platformlar için adaptör iskeletleri (TikTok, Instagram Reels, Facebook Reels).

Şimdilik HİÇBİRİ ETKİN DEĞİL. Kapalıyken hiçbir ağ isteği atılmaz, hiçbir anahtar istenmez.
Bu platformların resmi API'leri onay süreci ve iş hesabı gerektirebilir; ayrıntılar PLATFORMLAR.md'de.
Etkinleştirmek için önce resmi erişim alınmalı, sonra bu sınıflar resmi API ile doldurulmalı.
Kazıma (scraping) yapılmaz.
"""

ETKIN_DEGIL = "henüz etkin değil"


class PlatformEtkinDegil(Exception):
    """Kapalı ya da henüz yazılmamış bir platform kullanılmak istendiğinde."""


class Adaptor:
    """Tüm platform adaptörlerinin ortak arayüzü. YouTube, fikir_kutusu.py içinde doğrudan resmi API ile çalışır."""
    ad = "temel"

    def __init__(self, acik=False):
        self.acik = acik

    def yeni_videolar(self, hesaplar, son_saat=24):
        """Takip edilen hesapların son `son_saat` saatteki videoları → [{platform, id, baslik, kanal, yayin, ...}]"""
        raise PlatformEtkinDegil(f"{self.ad}: {ETKIN_DEGIL}")

    def video_istatistikleri(self, video_idler):
        """Videoların güncel izlenme, beğeni, yorum sayıları → {id: {izlenme, begeni, yorum}}"""
        raise PlatformEtkinDegil(f"{self.ad}: {ETKIN_DEGIL}")

    def ara(self, sorgu, dil=None, bolge=None):
        """Konu araması → video listesi"""
        raise PlatformEtkinDegil(f"{self.ad}: {ETKIN_DEGIL}")


class TikTokAdaptoru(Adaptor):
    ad = "tiktok"


class InstagramAdaptoru(Adaptor):
    ad = "instagram"


class FacebookAdaptoru(Adaptor):
    ad = "facebook"


ADAPTORLER = {"tiktok": TikTokAdaptoru, "instagram": InstagramAdaptoru, "facebook": FacebookAdaptoru}


def etkin_adaptorler(platform_ayarlari):
    """Ayarda açık olan (YouTube dışı) platformların adaptörleri. Hepsi kapalıysa boş liste: hiçbir istek atılmaz.
    Açık bile olsa adaptörler henüz yazılmadığı için kullanılmaz; uyarı mesajı döner."""
    etkin, uyarilar = [], []
    for ad, sinif in ADAPTORLER.items():
        if platform_ayarlari.get(ad):
            uyarilar.append(f"{ad} ayarda açık ama {ETKIN_DEGIL}; atlanıyor.")
    return etkin, uyarilar
