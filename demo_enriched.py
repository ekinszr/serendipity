#!/usr/bin/env python3
"""Merak-acici 'yem' versiyonunu API anahtari olmadan gostermek icin ornek fis.
Gercek, guncel makaleler + elle yazilmis Turkce yemler ile yeni tasarimi basar.
Calistir:  .venv/bin/python demo_enriched.py  ->  output/yem-ornek.html
"""
from datetime import datetime, timezone
from digest import render_html

selected = [
    {
        "title": "Time-Resolved X-ray Photoelectron Spectroscopy: Kimyasal Baglarin Isik Hizinda Yeniden Dizilisi",
        "link": "https://example.org/princeton-xps",
        "source": "Princeton", "category": "Seckin Kurumlardan Yeni Calismalar",
        "enriched": True,
        "summary": "Bir molekulun icinde kimyasal baglar femtosaniyeler icinde yeniden dizilir "
                   "— gozle gorulemeyecek kadar hizli. Princeton ekibi bu yeniden dizilisi ilk kez "
                   "anlik bir film gibi yakaladi. Peki bir baga 'sira' gelmesi ne anlama gelir, ve bu "
                   "neden gunes hucrelerinden gorusune kadar her seyi degistirebilir?",
    },
    {
        "title": "Odul Ogrenmesini Bozan Rakip Sinyaller: Dopamin ve Kesif Durtusu",
        "link": "https://example.org/plos-dopamine",
        "source": "PLOS Biology", "category": "Akademik Calismalar (Hakemli)",
        "enriched": True,
        "summary": "Beyniniz bir odulu beklerken iki ses ayni anda konusur: 'bildigimize sarilalim' "
                   "ve 'yeniyi deneyelim'. Bu calisma, dopaminin bu iki sesi nasil karistirdigini ve "
                   "kararlarimizi nasil bozdugunu gosteriyor. Kesfetme durtumuz aslinda bir hata mi, "
                   "yoksa bir ozellik mi?",
    },
    {
        "title": "2.000 Yil Once Insanlar Bugunkunun 10 Kati Dil Konusuyor Olabilir",
        "link": "https://example.org/sciam-languages",
        "source": "Scientific American", "category": "Genel Bilim",
        "enriched": True,
        "summary": "Bugun dunyada ~7.000 dil var. Yeni bir tahmin, iki bin yil once bu sayinin on "
                   "katina yakin olabilecegini soyluyor. Peki binlerce dil nereye gitti, ve her biri "
                   "kayboldugunda dunyayi gorme bicimimizden tam olarak ne eksildi?",
    },
    {
        "title": "Iyi Niyetli Ebeveynleri Otoriter Ebeveynlige Iten Nedir?",
        "link": "https://example.org/psyche-parenting",
        "source": "Psyche (Zihin & Anlam)", "category": "Tasarim ve Yaratici Dusunce",
        "enriched": True,
        "summary": "Cocuguna en iyisini isteyen bir ebeveyn, neden kati kurallara ve kontrole kayar? "
                   "Bu yazi, sevginin nasil sessizce korkuya donustugunu inceliyor. Belki de en sert "
                   "disiplin, en derin kaygidan besleniyor — peki bu dongu nasil kirilir?",
    },
    {
        "title": "Gozden Uzak Bir Av Taktigi: Kambur Balinalarin Gizli Stratejisi",
        "link": "https://example.org/sciencenews-whales",
        "source": "Science News", "category": "Karmasik Sistemler ve Bilim",
        "enriched": True,
        "summary": "Kambur balinalar, avlarini yakalamak icin bugune dek gorulmemis bir taktik "
                   "kullaniyor — ve bunu ancak yeni kamera teknolojisi ortaya cikarabildi. Dev bir "
                   "canli, denizin karanliginda tam olarak nasil 'tuzak' kurar?",
    },
    {
        "title": "Belirsizligin Cesareti: George Saunders Dunyayi Daha Cok Sevmek Uzerine",
        "link": "https://example.org/marginalian-saunders",
        "source": "The Marginalian", "category": "Tasarim ve Yaratici Dusunce",
        "enriched": True,
        "summary": "Emin olmamak bir zayiflik degil, belki de en buyuk cesaret olabilir. Saunders, "
                   "kesin cevaplara sarilmak yerine belirsizlikte durmayi bir sevgi bicimi olarak "
                   "anlatiyor. Bilmemekle baris icinde yasamak, dunyayi nasil daha genis acar?",
    },
    {
        "title": "Yoga'nin Uygulama Bilimi: Sagliga Butunsel Bir Yaklasim",
        "link": "https://example.org/mit-yoga",
        "source": "MIT", "category": "Seckin Kurumlardan Yeni Calismalar",
        "enriched": True,
        "summary": "Binlerce yillik bir pratik, modern saglik sistemine nasil olcekli bir mudahale "
                   "olarak sokulur? MIT baglantili bu calisma, yoga'yi bir 'uygulama bilimi' problemi "
                   "olarak ele aliyor. Iyi hissettiren bir sey, kanita dayali bir tedaviye nasil donusur?",
    },
]

run_date = datetime.now(timezone.utc).astimezone().strftime("%d %B %Y")
html = render_html(selected, run_date, accession_start=100)
out = "output/yem-ornek.html"
open(out, "w", encoding="utf-8").write(html)
print(f"Ornek yem'li fis yazildi: {out}")
