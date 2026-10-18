# Fikir Kutusu — Kahve Stüdyo ile sözleşme

Bu belge, **Kahve Stüdyo**'yu (ve içindeki Claude ajanını) yazacak kişi/ajan içindir. Niş Avcısı'nın bulduğu
fırsatlar, kullanıcının YouTube kanalları için **fikir kartı** dosyaları olarak tek bir klasöre yazılır. Stüdyo
Niş Avcısı'nın koduna ya da veritabanına **hiç dokunmaz**; aradaki tek bağlantı bu klasördeki dosyalardır.

## 1. Klasör nerede?

| | |
|---|---|
| Kaynak (bulut) | Özel (gizli) GitHub deposu `adnanakyuz/fikir-kutusu`, klasör `fikir_kutusu/` |
| Stüdyonun okuyacağı yerel kopya | `C:\Users\adnan\KahveStudyo\fikir-kutusu\fikir_kutusu\` |
| Senkron | Aynı depodaki `senkronla.bat`: yeni kartları çeker, stüdyonun geri bildirimlerini gönderir |
| Klasör adı | `fikir_ayarlari.json` → `"klasor"` (varsayılan `fikir_kutusu`) |

Fikir Kutusu her saat GitHub'da çalışır (`.github/workflows/fikir.yml`); bilgisayar kapalıyken de çalışır.
Stüdyo çalışmaya başlarken `senkronla.bat`'ı çalıştırmalı (ya da `git pull`).

## 2. Klasör yapısı

```
fikir_kutusu/
  _bilgi.json                  # sürüm, son güncelleme, kanal listesi, kota durumu, sinyal özeti
  kanal1/
    fk_20261017_0001.json      # her fikir ayrı dosya; dosya adı = kartın id'si
    fk_20261017_0002.json
  kanal2/
  ...
  _yayin_saatleri/
    kanal1.json                # bölüm 5
  _geri_bildirim/              # STÜDYO yazar, Niş Avcısı sadece okur (bölüm 6)
oneriler/                      # deponun kökünde, fikir_kutusu/ dışında (bölüm 8)
  kanal1_takip_onerisi.json
  kanal1_takip_onerisi.md
```

- Dosyalar **UTF-8** JSON'dur. Yazım güvenlidir: önce `*.json.tmp`'ye yazılır, sonra yeniden adlandırılır.
  **`.tmp` ile biten dosyaları okuma.**
- Kanal klasörleri `kanallar.json`'daki `id`'lerdir. Yeni kanal eklenince klasörü otomatik açılır.
- Tüm zamanlar **UTC** ve ISO 8601'dir (`2026-10-17T09:30:00Z`). Yayın saati önerileri Europe/Istanbul'dur.

## 3. Fikir kartı şeması

```json
{
  "surum": 1,
  "id": "fk_20261017_0001",
  "kanal_id": "kanal1",
  "olusturma": "2026-10-17T09:30:00Z",
  "guncelleme": "2026-10-17T12:30:00Z",
  "durum": "yeni",
  "konu": "Kısa ve net konu başlığı",
  "ozet": "Neden tuttuğunu anlatan 2-3 cümle",
  "nis": "finans/altın",
  "format": "shorts",
  "hedef_dil": "tr",
  "onerilen_aci": null,
  "kanca_onerileri": null,
  "baslik_kaliplari": ["Tutan başlıklardan çıkarılan kalıplar"],
  "kapak_gozlemleri": null,
  "izleyici_sorulari": ["Yorumlardan çıkan sorular"],
  "puanlar": {
    "toplam": 0, "patlama": 0, "hiz": 0, "etkilesim": 0,
    "rekabet_boslugu": 0, "turkce_boslugu": 0,
    "omur": "haber", "kazanc_potansiyeli": "orta",
    "uretilebilirlik": 0, "kanal_uyumu": 0
  },
  "puan_aciklamasi": "...",
  "uretim": {
    "tahmini_yontem": "stok+seslendirme | yapay_zeka_video | avatar | karma",
    "gerekli_gorseller": null,
    "yapay_zeka_icerik_beyani_gerekli": false
  },
  "riskler": ["telif", "yeniden_kullanilmis_icerik", "yatirim_tavsiyesi", "saglik", "marka_guvenligi"],
  "risk_aciklamasi": "...",
  "platformlar": {
    "youtube": {"uygun": true},
    "tiktok": {"uygun": null},
    "instagram_reels": {"uygun": null},
    "facebook_reels": {"uygun": null}
  },
  "kaynak_videolar": [
    {"platform": "youtube", "id": "...", "baslik": "...", "kanal": "...", "abone": 0,
     "izlenme": 0, "kanal_medyan_izlenme": 0, "yayin": "ISO8601", "sure_sn": 0,
     "dil": "en", "patlama_orani": 0.0}
  ],
  "basari_nedeni": null
}
```

`basari_nedeni` **isteğe bağlıdır** (18 Ekim 2026'dan önceki kartlarda hiç yoktur; yeni kartlarda çoğunlukla
`null`'dur). Alan yoksa `null` gibi ele al. Yapısı bölüm 9'da.

### Alanların anlamı
- **`null` = veri yok / ölçülmedi.** Değer asla uydurulmaz. Stüdyo `null`'u "bilinmiyor" diye ele almalı.
- **`durum`**: Niş Avcısı sadece `"yeni"` yazar. **Stüdyo durumu kendi tarafında tutar**; kart dosyasını
  değiştirmemeli. Niş Avcısı sonradan sadece istatistikleri (izlenme, patlama oranı) ve `guncelleme`'yi yeniler,
  diğer alanlara (durum dahil) dokunmaz.
- **`puanlar`**: sayısal puanlar 0-100. `toplam` = ağırlıklı ortalama (null olanlar hariç; ağırlıklar
  `fikir_ayarlari.json` → `agirliklar`). `omur`: `haber` | `kalici` | null. `kazanc_potansiyeli`: `dusuk` | `orta` | `yuksek` | null.
  - `patlama`: video izlenmesi ÷ kanalın son ~30 videosunun medyanı (yeni kanalda abone sayısıyla desteklenir), log ölçek. **En önemli sinyal.**
  - `hiz`: yayından sonra 1, 3, 6, 12, 24, 48 saat ve 7. günde ölçülen saatlik izlenme; aynı yaştaki nişteki videolarla karşılaştırma + ivme.
  - `etkilesim`: (beğeni + 2×yorum) ÷ izlenme; nişin ortalamasına göre.
  - `rekabet_boslugu`: son 30 günde konudaki en çok izlenen 25 videonun izlenmelerinin ne kadarı yeni/küçük kanallara gidiyor (arz zayıflığı) × toplam izlenme (talep).
  - `turkce_boslugu`: konu başka dilde tutuyorsa Türkçe aramada (TR bölgesi, Türkçe) az/zayıf içerik varsa yüksek. Kaynak zaten Türkçeyse null.
    **Kartın kanalı Türkçe değilse de null** (`puan_aciklamasi`'nda "kanal İngilizce, Türkçe boşluğu hesaba katılmadı").
  - `uretilebilirlik`: stok görüntü + seslendirmeyle ucuza yapılabiliyorsa yüksek.
  - `kanal_uyumu`: kanal profiline (niş, anahtar kelimeler, format, kaçınılacaklar) uygunluk. Profil boşsa null.
    Kelimeler **tam kelime** olarak eşleşir: "kumar" "Suryakumar"ı, "earn" "learn"ü, "tax" "taxi"yi yakalamaz;
    gerçek ekler sayılır ("maaş" → "maaşı, maaşla"; "invest" → "investing"). Çok kelimeli ifadeler ("credit card") ve `$` de çalışır.
- **`puan_aciklamasi`**: her puanın nedenini anlatan sade Türkçe metin (her zaman dolu).
- **Yaratıcı alanlar Niş Avcısı tarafından `null` bırakılır, stüdyo doldurur** (kendi verisinde):
  `onerilen_aci`, `kanca_onerileri`, `kapak_gozlemleri`, `uretim.gerekli_gorseller`.
  Veriye dayalı alanlar doludur: `ozet` (ölçümlerden kurulan cümleler), `baslik_kaliplari`, `izleyici_sorulari`
  (kaynak videonun yorumlarından; yorumlar kapalıysa null), puanlar, riskler, kaynak videolar.
- **`riskler`**: sadece tespit edilenler listelenir (boş liste = belirgin risk yok). Açıklaması `risk_aciklamasi`'nda.
  Risk kelimeleri de kelime başından eşleşir ("war" "software"i, "cover" "recovering"i yakalamaz).
- **`uretim.yapay_zeka_icerik_beyani_gerekli`**: yöntem `yapay_zeka_video` ya da `avatar` ise true
  (YouTube'un gerçekçi yapay içerik beyanı).
- **`kaynak_videolar`**: ilk eleman ana kaynak; aynı konudaki en fazla 2 video daha. Videolar **sadece fikir ve
  sinyal kaynağıdır: indirilmez, kopyalanmaz, yeniden yüklenmez.**

## 4. Diğer platformlar
Kartlar platform bilgisini taşır (`platformlar`, `kaynak_videolar[].platform`); şimdilik YouTube dışındakiler `null`.
Ayar: `fikir_ayarlari.json` → `"platformlar": {"youtube": true, "tiktok": false, "instagram": false, "facebook": false}`.
Kapalıyken hiçbir istek atılmaz. Ayrıntılar: `PLATFORMLAR.md`.

## 5. Yayın saatleri (`_yayin_saatleri/<kanal_id>.json`)

```json
{
  "surum": 1, "kanal_id": "kanal1", "guncelleme": "ISO8601", "saat_dilimi": "Europe/Istanbul",
  "yetersiz_veri": true, "ornek_sayisi": 0, "kapsam": "kanalın nişleri | tüm nişler (kanal profili boş)",
  "hiz_birimi": "saat başına izlenme",
  "gruplar": [
    {"format": "shorts|uzun", "gun_tipi": "hafta_ici|hafta_sonu", "omur": "haber|kalici|bilinmiyor",
     "ornek_sayisi": 0, "yetersiz_veri": true,
     "oneriler": [{"saat_araligi": "18:00-21:00", "ornek_sayisi": 0, "medyan_hiz_3s": null,
                   "medyan_hiz_6s": null, "medyan_hiz_24s": null, "guven": "dusuk|orta|yuksek"}]}
  ],
  "not_": "Değerler sadece Fikir Kutusu'nun kendi ölçümlerinden hesaplanır."
}
```
- `oneriler` en hızlıdan yavaşa sıralıdır. `guven`: <5 örnek düşük, 5-14 orta, ≥15 yüksek.
- `yetersiz_veri: true` ise öneriye güvenme. İnternetteki genel "en iyi saat" listelerinden değer alınmaz.

## 6. Geri bildirim (`_geri_bildirim/`) — stüdyo yazar
Stüdyo yayınladığı her video için bir dosya yazar (dosya adı serbest, ör. `<youtube_id>.json`):
```json
{"fikir_id": "fk_20261017_0001", "kanal_id": "kanal1", "youtube_id": "dQw4w9WgXcQ", "yayin_zamani": "2026-10-18T15:00:00Z", "format": "shorts"}
```
Niş Avcısı bu dosyaları **sadece okur** (değiştirmez/silmez). Videoyu aynı takvimle ölçer; haftada bir kartın
tahmini puanlarıyla gerçek sonucu (7. gün izlenmesi ÷ kanalın medyanı) karşılaştırır ve ağırlıkları küçük, sınırlı
adımlarla günceller. Her değişiklik özel depodaki `agirlik_gecmisi.json`'a yazılır (geri alınabilir).
`_bilgi.json` → `sinyal_ozeti`'nde "bu kanalda en çok işe yarayan sinyaller" özeti bulunur.
Gönderim: `senkronla.bat` `_geri_bildirim/` klasörünü depoya gönderir.

## 7. `_bilgi.json`
```json
{
  "surum": 1, "son_guncelleme": "ISO8601",
  "kanallar": [{"id": "kanal1", "ad": "...", "aktif": true, "klasor": "kanal1", "kart_sayisi": 0}],
  "kota": {"gun_pt": "2026-10-18", "arama": 0, "arama_butcesi": 20, "birim": 0, "birim_butcesi": 3000,
           "oran": 0.0, "onemli_mod": false, "aciklama": "..."},
  "platformlar": {"youtube": true, "tiktok": false, "instagram": false, "facebook": false},
  "agirliklar": {"patlama": 0.30, "...": 0},
  "sinyal_ozeti": {"kanal1": "..."},
  "uyarilar": [],
  "sozlesme": "FIKIR_KUTUSU.md (Niş Avcısı deposu)"
}
```

## 8. Takip edilecek kanal önerisi (`oneriler/<kanal_id>_takip_onerisi.json`)
Elle başlatılır: GitHub → fikir-kutusu → Actions → Fikir Kutusu → Run workflow → `takip_onerisi` kutusuna `kanal1`.
Profilin nişleri ve anahtar kelimeleriyle, kanalın dilinde en fazla 3 arama yapılır; adaylar ucuz isteklerle ölçülür.
```json
{
  "surum": 1, "kanal_id": "kanal1", "kanal_adi": "...", "olusturma": "ISO8601", "dil": "en",
  "sorgular": ["personal finance|salary|budget|..."], "kota": {"arama": 3, "birim": 64},
  "oneriler": [{"ad": "...", "kanal_id": "UC...", "link": "https://www.youtube.com/channel/UC...",
                "abone": 0, "son30_video_sayisi": 0, "son30_medyan_izlenme": 0,
                "izlenme_abone_orani": 0.0, "dil": "en", "neden": "son 30 günde 18 video; ..."}],
  "nasil_eklenir": "...", "not_": "..."
}
```
- 10-15 kanal; son 30 günde en az 2 video atmış ve dili kanalla aynı olanlar. Aynı bilgiler `.md` dosyasında tablo olarak da var.
- **`kanallar.json`'a otomatik eklenmez.** Kullanıcı seçer, `takip_edilen_kanallar`'a kendisi (ya da birlikte) ekler.
- 30 günden eski öneri dosyaları silinir (YouTube 30 gün kuralı). Stüdyonun bu dosyaları okuması gerekmez.

## 9. Konu mu, format mı? (`basari_nedeni`)
Günün en yüksek puanlı **3 konusunun** kartlarında doldurulur (aynı kaynak video birden fazla kanala gittiyse
hepsine aynı sonuç yazılır); diğer kartlarda `null`. Elle de başlatılabilir: Run workflow → `basari_analizi` → `kanal1`
(son 3 günün analiz edilmemiş en iyi kartları).
```json
"basari_nedeni": {
  "etiket": "konu_tutuyor | format_tutuyor | ikisi_de | null",
  "aciklama": "Sade Türkçe açıklama (her zaman dolu)",
  "benzer_kanal_sayisi": 7, "benzer_medyan_oran": 1.8, "kaynak_oran": 12.4, "olcum": "ISO8601",
  "benzer_kanallar": [{"ad": "...", "kanal_id": "UC...", "abone": 0, "medyan_izlenme": 0,
                       "konu_videosu_izlenme": 0, "oran": 0.0, "benzerlik_nedeni": "aynı konuda Shorts video, İngilizce, 12 gün önce: \"...\""}]
}
```
- Yöntem: aynı konuda, aynı dilde, aynı formatta (Shorts/uzun) son 90 günde video yapmış **5-10 başka kanal**
  bulunur (alaka sırasıyla; izlenme sırası kullanılmaz, yoksa hep tutan videolar gelirdi). Her birinin konu videosu,
  kendi kanalının normaline (son 30 videonun medyan izlenmesi) bölünür → `oran`. Bu oranların medyanı `benzer_medyan_oran`.
- Karar: `benzer_medyan_oran` ≥ 1,5 → **konu_tutuyor**; ayrıca kaynak video (`kaynak_oran`) bunun ≥ 3 katıysa
  **ikisi_de**. `benzer_medyan_oran` ≤ 1,2 ve kaynak ≥ 1,5 → **format_tutuyor** (konu başkalarında normal kalıyor,
  başarı sunuştan). 5'ten az benzer kanal ya da arada kalan oranlar → **null** (açıklamada nedeni yazar). Uydurma yok.
- Arama: niş konusu başlıkta geçiyorsa o; geçmiyorsa (ör. konu bir kanal adıysa) başlığın ilk anlamlı kelimeleri.
- 30 gün sonra `benzer_kanallar` içindeki `abone`, `medyan_izlenme`, `konu_videosu_izlenme`, `oran` null yapılır.

## 10. Sözleşmeden sapmalar (ve nedenleri)
1. **Konum:** Klasör Niş Avcısı'nın açık deposunda değil, **özel depoda**. Kanal profilleri ve fikirler gizli
   kalsın diye. Yerel kopya `senkronla.bat` ile güncellenir.
2. **Anahtarlar** ayar dosyasında değil, GitHub'ın gizli kasasında (`YT_API_KEY`). Hiçbir dosyaya ya da log'a yazılmaz.
3. **Yaratıcı alanlar `null`**: `onerilen_aci`, `kanca_onerileri`, `kapak_gozlemleri`, `uretim.gerekli_gorseller`.
   Stüdyodaki Claude doldurur (kullanıcının kararı; Niş Avcısı tarafında ücretli yapay zeka yok).
4. **YouTube 30 gün kuralı:** API verisi 30 günden uzun saklanmaz. 30 günü geçen kartlarda `kaynak_videolar`
   içindeki `abone`, `izlenme`, `kanal_medyan_izlenme`, `patlama_orani` null yapılır; fikir metni kalır.
5. **Kota:** 15 Ekim 2026'dan beri YouTube'da arama ayrı kotada (günde 100 çağrı, Niş Avcısı'yla ortak).
   Fikir Kutusu günde en fazla 20 arama ve 3.000 birim kullanır; %80'de yeni aramalar durur, ölçümler sürer.
   Bu yüzden rekabet ve Türkçe boşluğu ölçümleri günde sadece en iyi ~10 konu için yapılır; diğerlerinde null.
6. **`hiz`**: Niş Avcısı'nın bulduğu adaylar genelde birkaç günlükken bulunur; bu videolarda 1/3/6. saat ölçümü
   olamaz (ilk ölçüm gerçek yaşıyla kaydedilir). Erken saat ölçümleri, profillerdeki `takip_edilen_kanallar`'ın yeni
   videolarında ve stüdyonun geri bildirim videolarında yapılır.
7. **`kanal_uyumu`** profil boşsa null; o durumda fikirler profili boş kanallara sırayla dağıtılır.
8. **`omur`** 7 günlük eğri yoksa ve başka işaret yoksa null olabilir.
9. **Fikir id sayacı** gün içinde tüm kanallar için ortaktır (id'ler kanallar arasında da tekildir).
10. **vidIQ** bağlantısı yok (kullanıcının hesabı yok); sadece resmi YouTube Data API.
11. Adaylar Niş Avcısı'nın günlük taramasından (`site/adaylar.json`) gelir; bu dosya yoksa son taramanın
    veritabanı kaydından kurulur (arama harcamadan).
12. **Tam kelime eşleşmesi (18 Ekim 2026):** `kanal_uyumu`, kaçınılacaklar, yayın saatleri, riskler, kazanç ve
    üretilebilirlik kuralları artık kelimeleri tam kelime olarak arar (önceden metin içinde parça olarak arıyordu:
    "kumar" "Suryakumar"da, "cover" "recovering"de bulunuyordu). Alanlar ve tipleri aynı; sadece değerler daha doğru.
    Eski kartlar yeniden hesaplanmaz.
13. **`turkce_boslugu` Türkçe olmayan kanalda null (18 Ekim 2026):** önceden İngilizce kanalın kartında da
    hesaplanıyordu ve toplamı düşürüyordu. Alan ve tipi aynı (zaten null olabiliyordu). Ayrıca aktif Türkçe kanal
    yoksa Türkçe boşluğu araması hiç yapılmaz (arama kotası boşa gitmesin). Kart başlıklarındaki görünmez
    karakterler (sıfır genişlikli boşluk vb.) temizlenir.
14. **Yeni isteğe bağlı alan `basari_nedeni` (18 Ekim 2026)**, bölüm 9. Mevcut alanlar değişmedi; eski kartlarda
    alan yoktur ve doğrulayıcı onları da geçerli sayar.
15. **Yeni dosyalar `oneriler/` (18 Ekim 2026)**, bölüm 8. `fikir_kutusu/` klasörünün dışında; kartları etkilemez.
16. **Kota paylaşımı (18 Ekim 2026):** günde 20 arama / 3.000 birim / %80'de durma aynı. İçinden: rekabet ve
    Türkçe boşluğu ölçümleri 13. aramada durur, 3 arama "konu mu format mı"ya kalır (16'ya kadar). Kalan %20 (4 arama)
    sadece elle başlatılan takip önerisi ve elle analiz içindir; 20 asla aşılmaz.

## 11. Doğrulama
```
python fikir_kutusu_dogrula.py <fikir_kutusu klasörü>
```
Tüm kartları şemaya göre denetler; hata varsa çıkış kodu 1.

## 12. Örnek gerçek kart
<!-- ORNEK_KART -->
İlk gerçek çalıştırmadan (18 Ekim 2026) alınmış bir kart, olduğu gibi. Profiller henüz boş olduğu için `kanal_uyumu` null; 7 günlük ölçüm henüz olmadığı için `omur` null.

```json
{
  "surum": 1,
  "id": "fk_20261017_0011",
  "kanal_id": "kanal1",
  "olusturma": "2026-10-17T22:32:42Z",
  "guncelleme": "2026-10-17T22:36:25Z",
  "durum": "yeni",
  "konu": "Polis arabayı kurtardığının farkına varmadı!",
  "ozet": "\"GymTP\" kanalının bu videosu 9 günde 12.3M izlendi; bu, kanalın normalinin 341.0 katı.",
  "nis": "Kişiler & Bloglar/camera",
  "format": "shorts",
  "hedef_dil": "tr",
  "onerilen_aci": null,
  "kanca_onerileri": null,
  "baslik_kaliplari": [
    "Tek kelimeyi BÜYÜK HARFLE vurguluyor",
    "Emoji kullanıyor",
    "Ortalama uzunluk 55 karakter"
  ],
  "kapak_gozlemleri": null,
  "izleyici_sorulari": [
    "“DONT DRIVE ON THE HARD SHOULDER” “okay what i want you to do is stay on the hard shoulder”",
    "Traffic is stopped for a broken-down car. A recovery truck arrives. \"Why are you on the hard shoulder?\"",
    "How do you recover a car if you're stuck in the teaffic",
    "That's what the hard shoulder is for breakdown emergency or emergency services in gridlock traffic",
    "What a terrible attitude the so called traffic officer has."
  ],
  "puanlar": {
    "toplam": 73,
    "patlama": 100,
    "hiz": 35,
    "etkilesim": 79,
    "rekabet_boslugu": null,
    "turkce_boslugu": null,
    "omur": null,
    "kazanc_potansiyeli": "dusuk",
    "uretilebilirlik": 60,
    "kanal_uyumu": null
  },
  "puan_aciklamasi": "Toplam 73: ağırlıklı ortalama (null olanlar hariç). Patlama 100: video kanalın son videolarının tipik izlenmesinin 341.0 katı izlendi. Hız 35: 224. saatte saatte 55K izlenme alıyor; aynı yaştaki nişteki videoların tipik hızı 84.4K. Etkileşim 79: (beğeni + 2×yorum) / izlenme = %2.2; nişin ortalaması %1.0. Rekabet boşluğu: bu konu için arama yapılmadı (günlük arama bütçesi). Türkçe boşluğu: Türkçe arama yapılmadı (günlük arama bütçesi). Ömür: henüz belirlenemedi (7 günlük izlenme eğrisi bekleniyor). Kazanç potansiyeli: düşük (eğlence/oyun/çocuk alanında reklam gelirleri düşüktür). Üretilebilirlik 60: belirgin bir işaret yok. Kanal uyumu: 'Kanal 1 (adını yaz)' profilinde niş/anahtar kelime yok. Profil boş olduğu için fikir bu kanala sırayla atandı.",
  "uretim": {
    "tahmini_yontem": "karma",
    "gerekli_gorseller": null,
    "yapay_zeka_icerik_beyani_gerekli": false
  },
  "riskler": [
    "telif"
  ],
  "risk_aciklamasi": "telif: başkasına ait film, müzik, maç ya da dizi görüntüsü kullanılırsa telif ihlali olur; kendi görüntünü üret",
  "platformlar": {
    "youtube": {
      "uygun": true
    },
    "tiktok": {
      "uygun": null
    },
    "instagram_reels": {
      "uygun": null
    },
    "facebook_reels": {
      "uygun": null
    }
  },
  "kaynak_videolar": [
    {
      "platform": "youtube",
      "id": "fxz5rkOdbKg",
      "baslik": "The police DIDN'T REALISE she was recovering the car! 😯",
      "kanal": "GymTP",
      "abone": 3230,
      "izlenme": 12328069,
      "kanal_medyan_izlenme": 36156.0,
      "yayin": "2026-10-08T14:14:35Z",
      "sure_sn": 33,
      "dil": "en",
      "patlama_orani": 340.97
    }
  ]
}
```
