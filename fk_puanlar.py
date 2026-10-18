"""Fikir Kutusu — puan fonksiyonları.

Her puan ayrı, saf (dış bağlantısız) bir fonksiyondur; birim testleri tests/test_fikir_kutusu.py içinde.
Sayısal puanlar 0-100 arasıdır. Veri yoksa None döner (JSON'da null): değer asla uydurulmaz.
Her fonksiyon (puan, açıklama) döndürür; açıklama karttaki "puan_aciklamasi" için sade Türkçedir.
"""
import math
import re

KAZANC_SAYI = {"dusuk": 30, "orta": 60, "yuksek": 90}


# ---------- kelime eşleşmesi ----------
# Kelimeler metnin İÇİNDE parça olarak değil, TAM KELİME olarak eşleşir: kelime başında başlamalı, sonuna sadece
# gerçek bir ek gelebilir. Böylece "kumar" → "Suryakumar", "earn" → "learn", "tax" → "taxi", "rent" → "parent"
# eşleşmez; "maaş" → "maaşı/maaşla", "borç" → "borcu", "earn" → "earnings", "politic" → "politics" eşleşir.
_EN_EK = r"(?:s|es|'s|’s|ed|d|ing|ings|er|ers|al|ally|ian|ians|ism|ist|ists|ment|ments|ly)"
_E_DUSEN_EK = r"(?:ing|ings|ed|er|ers|al|ation|ations)"  # "price" → "pricing", "retire" → "retiring"
# Türkçe ekler ünlü uyumuna göre: kelimenin son ünlüsü kalınsa (a ı o u) kalın, inceyse (e i ö ü) ince ekler.
# Bu sayede "tax" (kalın a) + "i" (ince) = "taxi" eşleşmez, ama "faiz" (ince i) + "i" = "faizi" eşleşir.
_TR_KALIN = (r"(?:ı|u|a|ın|un|nın|nun|da|ta|dan|tan|la|yla|ya|lar|ları|ların|sı|su|ım|um|ımız|umuz|dı|du|tı|tu|"
             r"cı|cu|çı|çu|lı|lu|sız|suz|lık|luk|daki|taki|ki|nı|nu|na|nda|ndan|yı|yu)")  # kartı-nı, araba-yı
_TR_INCE = (r"(?:i|ü|e|in|ün|nin|nün|de|te|den|ten|le|yle|ye|ler|leri|lerin|si|sü|im|üm|imiz|ümüz|di|dü|ti|tü|"
            r"ci|cü|çi|çü|li|lü|siz|süz|lik|lük|deki|teki|ki|ni|nü|ne|nde|nden|yi|yü)")
_YUMUSAMA = {"p": "b", "ç": "c", "t": "d", "k": "ğ"}  # "borç" → "borcu", "kitap" → "kitabı"
_desen_onbellegi = {}


_TR_HARF = re.compile("[çğıöşüÇĞİÖŞÜ]")


def normallestir(metin):
    """Küçük harf; Türkçe "İ" doğru küçülsün (yoksa "i̇" olup kelimeyi böler). Metinde Türkçe harf varsa büyük "I"
    Türkçedeki gibi "ı" olur ("MAAŞLARIN" → "maaşların"); İngilizce "TAXI" ise "taxi" kalır."""
    metin = metin or ""
    if _TR_HARF.search(metin):
        metin = metin.replace("I", "ı")
    return metin.replace("İ", "i").lower().replace("̇", "")


def kelime_deseni(kelime):
    """Bir anahtar kelime/ifade için tam kelime deseni (önbellekli)."""
    if kelime in _desen_onbellegi:
        return _desen_onbellegi[kelime]
    k = normallestir(kelime).strip()
    if not re.search(r"\w", k):  # "$" gibi işaretler: olduğu gibi aranır ("$5,000" içinde de bulunur)
        desen = re.compile(re.escape(k))
    else:
        parcalar = k.split()
        son = parcalar[-1]
        once = "".join(re.escape(p) + r"\s+" for p in parcalar[:-1])
        unluler = [c for c in son if c in "aıoueiöü"]
        tr = _TR_KALIN if (unluler and unluler[-1] in "aıou") else _TR_INCE
        tr_ek = rf"(?:['’]?{tr}{{1,3}})"
        secenekler = [rf"{re.escape(son)}(?:{_EN_EK}|{tr_ek})?"]
        if len(son) > 2 and son.endswith("y") and son[-2] not in "aeiou":
            secenekler.append(re.escape(son[:-1]) + r"ies")                      # salary → salaries
        if len(son) > 3 and son.endswith("e"):
            secenekler.append(re.escape(son[:-1]) + _E_DUSEN_EK)                # price → pricing
        if son[-1] in _YUMUSAMA and unluler:
            secenekler.append(re.escape(son[:-1] + _YUMUSAMA[son[-1]]) + rf"['’]?{tr}{{1,3}}")  # borç → borcu
        desen = re.compile(rf"(?<!\w){once}(?:{'|'.join(secenekler)})(?!\w)")
    _desen_onbellegi[kelime] = desen
    return desen


def eslesir(kelime, metin):
    """Kelime/ifade metinde tam kelime olarak geçiyor mu? (metin normalleştirilmiş olmalı ya da ham verilebilir)"""
    return bool(kelime and kelime.strip()) and bool(kelime_deseni(kelime).search(normallestir(metin)))


def eslesenler(kelimeler, metin):
    m = normallestir(metin)
    return [k for k in kelimeler if k and k.strip() and kelime_deseni(k).search(m)]


def _kelime_basi(desen):
    """Mevcut kural listelerini (risk, kazanç, üretilebilirlik) kelime başına bağlar: "war" artık "software"
    içinde, "para" artık "kampara" içinde eşleşmez. Kelimenin sonuna ek gelebilir ("wars", "paralar")."""
    return re.compile(rf"(?<!\w)(?:{desen})", re.I)


def _sinirla(x):
    return max(0, min(100, round(x)))


def _orta(liste):
    l = sorted(x for x in liste if x is not None)
    if not l:
        return None
    n = len(l)
    return l[n // 2] if n % 2 else (l[n // 2 - 1] + l[n // 2]) / 2


def _kisa(n):
    n = float(n or 0)
    for esik, harf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= esik:
            return f"{n / esik:.1f}".rstrip("0").rstrip(".") + harf
    return str(int(n))


# ---------- patlama (en önemli sinyal) ----------
def patlama_orani(izlenme, kanal_medyan, abone, video_sayisi):
    """Video izlenmesi ÷ kanalın son videolarının medyan izlenmesi. Kanal yeni ise (5'ten az video) abone sayısıyla
    desteklenir: iki oranın geometrik ortalaması. Hiç referans yoksa None."""
    if not izlenme:
        return None
    oran_medyan = izlenme / kanal_medyan if kanal_medyan else None
    oran_abone = izlenme / max(abone, 100) if abone is not None else None
    if oran_medyan is not None and (video_sayisi or 0) >= 5:
        return oran_medyan
    if oran_medyan is not None and oran_abone is not None:
        return math.sqrt(oran_medyan * oran_abone)
    return oran_medyan if oran_medyan is not None else oran_abone


def patlama(izlenme, kanal_medyan, abone, video_sayisi):
    """Logaritmik ölçek: kanalının normalini tutturmak 0, 50 katı 100. Küçük kanalın sıra dışı videosu yüksek,
    büyük kanalın normal videosu düşük puan alır."""
    oran = patlama_orani(izlenme, kanal_medyan, abone, video_sayisi)
    if oran is None:
        return None, "Patlama: kanalın normal izlenmesi bilinmediği için hesaplanamadı."
    puan = _sinirla(math.log10(max(oran, 1e-9)) / math.log10(50) * 100)
    temel = "kanalın son videolarının tipik izlenmesinin" if kanal_medyan and (video_sayisi or 0) >= 5 \
        else "kanal yeni olduğu için tipik izlenme ve abone sayısına göre normalinin"
    return puan, f"Patlama {puan}: video {temel} {oran:.1f} katı izlendi."


# ---------- hız ----------
def hiz(olcumler, referans_hizlar):
    """olcumler: [(yas_saat, izlenme), ...] artan sırada. referans_hizlar: aynı yaştaki nişteki videoların saatlik
    izlenmeleri. Saatlik izlenme referans medyanına göre log ölçekte puanlanır, ivme ±10 puan ekler."""
    olcumler = [o for o in olcumler if o[0] and o[0] > 0 and o[1] is not None]
    if not olcumler:
        return None, "Hız: henüz ölçüm yok."
    ref = _orta(referans_hizlar or [])
    if not ref:
        return None, "Hız: karşılaştıracak aynı yaşta yeterli video yok."
    yas, izl = olcumler[-1]
    saatlik = izl / yas
    puan = 50 + 25 * math.log2(max(saatlik, .01) / ref)
    ivme_metni = ""
    if len(olcumler) >= 3:
        (y1, i1), (y2, i2), (y3, i3) = olcumler[-3:]
        once, son = (i2 - i1) / max(y2 - y1, .01), (i3 - i2) / max(y3 - y2, .01)
        if once > 0:
            ivme = son / once - 1
            puan += max(-10, min(10, 10 * ivme))
            ivme_metni = " Hızlanıyor." if ivme > .1 else (" Yavaşlıyor." if ivme < -.1 else " Hızı sabit.")
    puan = _sinirla(puan)
    return puan, (f"Hız {puan}: {yas:.0f}. saatte saatte {_kisa(saatlik)} izlenme alıyor; aynı yaştaki nişteki "
                  f"videoların tipik hızı {_kisa(ref)}.{ivme_metni}")


# ---------- etkileşim ----------
def etkilesim_orani(begeni, yorum, izlenme):
    if not izlenme or begeni is None:
        return None
    return (begeni + 2 * (yorum or 0)) / izlenme


def etkilesim(begeni, yorum, izlenme, nis_ortalamasi):
    oran = etkilesim_orani(begeni, yorum, izlenme)
    if oran is None or not nis_ortalamasi:
        return None, "Etkileşim: beğeni/yorum verisi ya da niş ortalaması yok."
    puan = _sinirla(50 + 25 * math.log2(max(oran, 1e-6) / nis_ortalamasi))
    return puan, (f"Etkileşim {puan}: (beğeni + 2×yorum) / izlenme = %{oran * 100:.1f}; nişin ortalaması "
                  f"%{nis_ortalamasi * 100:.1f}.")


# ---------- rekabet boşluğu ----------
def rekabet_boslugu(yeni_pay, toplam_izlenme, video_sayisi):
    """Son 30 günde aynı konuda en çok izlenen videolar (en fazla 25): izlenmelerin ne kadarı yeni/küçük kanallara
    gidiyor (arz zayıf) ve toplam izlenme (talep). Talep düşükse boşluk değerli değildir."""
    if yeni_pay is None or toplam_izlenme is None:
        return None, "Rekabet boşluğu: bu konu için arama yapılmadı (günlük arama bütçesi)."
    talep = min(1, math.log10(max(toplam_izlenme, 1)) / 7)  # 10M izlenme ≈ tam talep
    puan = _sinirla(100 * yeni_pay * (0.4 + 0.6 * talep))
    return puan, (f"Rekabet boşluğu {puan}: son 30 günde bu konudaki en çok izlenen {video_sayisi} videonun "
                  f"izlenmelerinin %{yeni_pay * 100:.0f}'si yeni/küçük kanallara gidiyor; toplam talep "
                  f"{_kisa(toplam_izlenme)} izlenme.")


# ---------- Türkçe boşluğu ----------
def turkce_boslugu(kaynak_dil, tr_sonuc_sayisi, tr_medyan_izlenme):
    """Konu başka dilde tutuyorsa: Türkçe aramada az ya da zayıf içerik varsa puan yüksek."""
    if (kaynak_dil or "").lower().startswith("tr"):
        return None, "Türkçe boşluğu: kaynak zaten Türkçe."
    if tr_sonuc_sayisi is None:
        return None, "Türkçe boşluğu: Türkçe arama yapılmadı (günlük arama bütçesi)."
    azlik = 1 - min(tr_sonuc_sayisi, 25) / 25
    zayiflik = 1 - min(math.log10((tr_medyan_izlenme or 0) + 1) / 6, 1)  # 1M+ izlenme = güçlü
    puan = _sinirla(100 * (0.4 * azlik + 0.6 * zayiflik))
    return puan, (f"Türkçe boşluğu {puan}: Türkçe aramada {tr_sonuc_sayisi} video çıktı, tipik izlenmeleri "
                  f"{_kisa(tr_medyan_izlenme or 0)}.")


# ---------- ömür ----------
HABER_KELIMELERI = re.compile(r"\b(son dakika|breaking|today|bugün|açıklandı|announced|leaked|update|güncelleme|"
                              r"hafta|this week|yesterday|dün|news|haber|202\d)\b", re.I)


def omur(olcumler, metin, evergreen_mi=False):
    """haber (hızlı söner) ya da kalici (uzun süre izlenir). Önce izlenme eğrisi (48. saat → 7. gün arası büyüme),
    yoksa evergreen listesi ve konu kelimeleri. Hiçbiri yoksa None."""
    d = dict((round(y), i) for y, i in olcumler if i)
    i48 = next((d[h] for h in sorted(d) if 40 <= h <= 60), None)
    i168 = next((d[h] for h in sorted(d) if h >= 150), None)
    if i48 and i168 and i168 > 0:
        kalan = (i168 - i48) / i168
        sonuc = "kalici" if kalan >= .3 else "haber"
        return sonuc, (f"Ömür: {sonuc} (izlenmelerin %{kalan * 100:.0f}'si 2. günden sonra geldi).")
    if evergreen_mi:
        return "kalici", "Ömür: kalici (aylar sonra hâlâ izlenen evergreen listesinde)."
    if metin and HABER_KELIMELERI.search(metin):
        return "haber", "Ömür: haber (başlıkta güncel olay/tarih ifadesi var; izlenme eğrisi henüz yok)."
    return None, "Ömür: henüz belirlenemedi (7 günlük izlenme eğrisi bekleniyor)."


# ---------- kazanç potansiyeli ----------
KAZANC_YUKSEK = _kelime_basi(r"finans|borsa|hisse|kripto|bitcoin|yatırım|invest|stock|crypto|money|para\b|paralar|parası|"
                           r"sigorta|insurance|emlak|real estate|kredi|loan|vergi|tax(?:es|ed|ing)?\b|yazılım|software|"
                           r"programming|salary|budget|debt|mortgage|wealth|income|maaş|bütçe|borç|faiz|enflasyon|"
                           r"\bai\b|yapay zeka|business|iş kurma|girişim|marketing|pazarlama")
KAZANC_DUSUK = _kelime_basi(r"funny|komik|prank|meme|oyun|game|gaming|minecraft|roblox|çocuk|kids|cartoon|"
                          r"music|müzik|song|dans|dance|challenge|asmr|satisfying")


def kazanc_potansiyeli(nis_metni):
    if not nis_metni:
        return None, "Kazanç potansiyeli: konu bilinmiyor."
    if KAZANC_YUKSEK.search(nis_metni):
        return "yuksek", "Kazanç potansiyeli: yüksek (reklamverenlerin çok para ödediği bir alan)."
    if KAZANC_DUSUK.search(nis_metni):
        return "dusuk", "Kazanç potansiyeli: düşük (eğlence/oyun/çocuk alanında reklam gelirleri düşüktür)."
    return "orta", "Kazanç potansiyeli: orta (kaba tahmin)."


# ---------- üretilebilirlik ----------
URETIM_KOLAY = _kelime_basi(r"facts|gerçek|bilgi|lists?\b|listed|top \d|en iyi|tarih|history|belgesel|documentary|explained|"
                          r"açıklam|nedir|why|neden|how|nasıl|finans|haber|news|teknoloji|tech|space|uzay|bilim|"
                          r"science|psikoloji|psychology|mystery|gizem|ranking|sıralama|tips|ipucu")
URETIM_ZOR = _kelime_basi(r"vlog|\bben\b|\bmy\b|\bi\b|challenge|prank|react|tepki|yüz\b|yüzü\b|yüzün\b|faces?\b|cooking|"
                        r"tarif|recipe|rescue|kurtar|puppy|yavru|unboxing|kutu açılış|travel|seyahat|gezi\b|gezisi|gezimiz|"
                        r"workout|antrenman|"
                        r"dance|dans|interview|röportaj|sokak|street|family|aile|baby|bebek")


def uretilebilirlik(metin, format_):
    """Stok görüntü + seslendirme + kartlarla ucuza yapılabiliyorsa yüksek; gerçek çekim, kişi, yapay zeka video
    ya da avatar gerekiyorsa düşük. Kaba bir kelime kuralı; açıklamasıyla birlikte."""
    if not metin:
        return None, "Üretilebilirlik: konu bilinmiyor.", None
    puan, nedenler = 55, []
    if URETIM_KOLAY.search(metin):
        puan += 30
        nedenler.append("bilgi/anlatım türü stok görüntü ve seslendirmeyle yapılabilir")
    if URETIM_ZOR.search(metin):
        puan -= 35
        nedenler.append("gerçek çekim, kişi ya da olay görüntüsü gerektiriyor gibi")
    if format_ == "shorts":
        puan += 5
    puan = _sinirla(puan)
    yontem = "stok+seslendirme" if puan >= 70 else ("karma" if puan >= 40 else "yapay_zeka_video")
    return puan, f"Üretilebilirlik {puan}: " + ("; ".join(nedenler) or "belirgin bir işaret yok") + ".", yontem


# ---------- kanal uyumu ----------
def kanal_uyumu(profil, metin, format_, dil):
    """Fırsatın kanal profiline uygunluğu: niş ve anahtar kelime eşleşmesi, format; kaçınılacaklar varsa 0.
    Profilin nişi ve anahtar kelimeleri boşsa None (uydurulmaz)."""
    nisler = [n.lower() for n in profil.get("nisler", []) if n.strip()]
    kelimeler = [k.lower() for k in profil.get("anahtar_kelimeler", []) if k.strip()]
    if not nisler and not kelimeler:
        return None, f"Kanal uyumu: '{profil.get('ad', profil.get('id'))}' profilinde niş/anahtar kelime yok."
    m = normallestir(metin)
    for k in eslesenler(profil.get("kacinilacak", []), m):  # tam kelime: "kumar" → "Suryakumar" eşleşmez
        return 0, f"Kanal uyumu 0: kanalın kaçındığı '{k}' konusu geçiyor."
    puan, nedenler = 0, []
    nis_eslesen = eslesenler(nisler, m)
    if nis_eslesen:
        puan += 50
        nedenler.append("niş: " + ", ".join(nis_eslesen))
    kel_eslesen = eslesenler(kelimeler, m)
    if kel_eslesen:  # tek bir anahtar kelime de güçlü bir işarettir (ör. finans kanalı için "altın")
        puan += min(55, 35 + 10 * (len(kel_eslesen) - 1))
        nedenler.append("anahtar kelime: " + ", ".join(kel_eslesen))
    formatlar = profil.get("formatlar") or []
    if not formatlar or format_ in formatlar:
        puan += 10
    else:
        nedenler.append(f"kanal {format_} formatı yapmıyor")
    puan = _sinirla(puan)
    return puan, f"Kanal uyumu {puan}: " + ("; ".join(nedenler) or "eşleşme yok") + "."


# ---------- toplam ----------
def toplam(puanlar, agirliklar):
    """Ağırlıklı ortalama. null olan puanlar hesaba katılmaz, kalan ağırlıklar yeniden ölçeklenir.
    kazanc_potansiyeli sayıya çevrilir (düşük 30, orta 60, yüksek 90). ömür toplamı etkilemez."""
    pay = payda = 0
    for ad, w in agirliklar.items():
        p = puanlar.get(ad)
        if ad == "kazanc_potansiyeli":
            p = KAZANC_SAYI.get(p)
        if p is None or not w:
            continue
        pay += w * p
        payda += w
    if not payda:
        return None
    return _sinirla(pay / payda)
