# Diğer platformlar (TikTok, Instagram Reels, Facebook Reels) — ileride gerekecekler

Şu an **hepsi kapalı.** Kapalıyken Fikir Kutusu bu platformlara hiçbir istek atmaz ve hiçbir anahtar istemez.
Kodda sadece boş adaptör iskeletleri var (`fk_platformlar.py`); kartlar platform alanlarını şimdiden taşıyor
(`platformlar`, `kaynak_videolar[].platform`).

Açmak için ayar: `fikir_ayarlari.json` → `"platformlar": {"tiktok": true, ...}`. Ancak adaptörler yazılmadan açmak
bir işe yaramaz (sadece "henüz etkin değil" uyarısı verir).

## Ne gerekecek (kısa not — açmadan önce resmi dokümanla doğrulanmalı)
Bu platformların resmi API'leri genelde onay süreci ve iş/profesyonel hesap ister; başkalarının içeriğine erişim
YouTube'dakinden çok daha kısıtlıdır. **Kazıma (scraping) yapılmayacak.**

- **TikTok:** Resmi geliştirici hesabı ve uygulama onayı. Başkalarının videolarını analiz edebilen araştırma
  türü erişim genellikle sadece belirli kurumlara açıktır; ticari kullanım şartları ayrıca kontrol edilmeli.
  Kendi hesabının istatistikleri ve paylaşım için ayrı API'ler vardır.
- **Instagram Reels:** Profesyonel (işletme/içerik üreticisi) hesap + Meta geliştirici uygulaması + uygulama
  incelemesi. Kendi hesabının istatistikleri alınabilir; başkalarının içeriğine erişim (hashtag araması, diğer
  işletme hesapları) sınırlı ve kotalıdır.
- **Facebook Reels:** Bir Facebook Sayfası + Meta geliştirici uygulaması + gerekli izinlerin incelemeden geçmesi.
  Kendi sayfasının istatistikleri alınabilir.

## Açarken yapılacaklar
1. Platformun resmi API şartlarını ve kotalarını güncel dokümandan oku; ticari kullanım iznini doğrula.
2. Gerekli hesap ve uygulama onaylarını al; anahtarları GitHub'ın gizli kasasına koy (dosyaya değil).
3. `fk_platformlar.py` içindeki ilgili sınıfın `yeni_videolar`, `video_istatistikleri`, `ara` fonksiyonlarını resmi
   API ile doldur; kota sayacına bağla.
4. Kartlarda o platformun `uygun` alanını ve `kaynak_videolar[].platform` değerini doldur.
5. Testlere "kapalıyken istek yok" ve "açıkken doğru şema" testlerini ekle.
