"""
Vremenski AI Bot — Railway.app verzija (24/7)
=============================================
- Pon–Pet: podrobna napoved za Novo Mesto, kratka za Ribnico
- Sob–Ned: podrobna napoved za Ribnico, kratka za Novo Mesto
- Priporočila za oblačila
- Pošilja vsak dan ob nastavljenem času

Environment Variables na Railway:
  ANTHROPIC_API_KEY, EMAIL_SENDER, EMAIL_PASSWORD (SendGrid),
  EMAIL_RECIPIENT, SENDGRID_API_KEY, SEND_TIME (npr. 06:30)
"""

import requests
import anthropic
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import time
import schedule
import sendgrid
from sendgrid.helpers.mail import Mail

# ── Environment Variables ─────────────────────────────────
ANTHROPIC_API_KLJUC = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_POSILJATELJ   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PREJEMNIK     = os.environ.get("EMAIL_RECIPIENT", "")
SENDGRID_API_KLJUC  = os.environ.get("SENDGRID_API_KEY", "")
CAS_POSILJANJA      = os.environ.get("SEND_TIME", "06:30")

# ─────────────────────────────────────────────────────────

LOKACIJE = {
    "Novo Mesto": {"lat": 45.8011, "lon": 15.1708, "arso_id": "NOVO-MES"},
    "Ribnica":    {"lat": 45.7373, "lon": 14.7267, "arso_id": "RIBNICA"},
}

DNEVI_SLO  = ["ponedeljek","torek","sreda","četrtek","petek","sobota","nedelja"]
MESECI_SLO = ["","januarja","februarja","marca","aprila","maja","junija",
               "julija","avgusta","septembra","oktobra","novembra","decembra"]

WMO_OPISI = {
    0:"jasno", 1:"pretežno jasno", 2:"delno oblačno", 3:"oblačno",
    45:"megla", 48:"ivje",
    51:"rahla rosica", 53:"zmerna rosica", 55:"gosta rosica",
    61:"rahel dež", 63:"zmeren dež", 65:"močan dež",
    71:"rahel sneg", 73:"zmeren sneg", 75:"močan sneg",
    80:"plohe", 81:"zmerne plohe", 82:"močne plohe",
    95:"nevihta", 96:"nevihta s točo", 99:"huda nevihta s točo",
}

HEADERS = {"User-Agent": "VremeBot/1.0"}


def je_vikend():
    """Vrne True če je danes sobota ali nedelja."""
    return datetime.now().weekday() >= 5


def primarna_lokacija():
    """Vrne ime primarne lokacije glede na dan v tednu."""
    return "Ribnica" if je_vikend() else "Novo Mesto"


def sekundarna_lokacija():
    return "Novo Mesto" if je_vikend() else "Ribnica"


def pridobi_arso(ime, arso_id):
    rezultat = {"vir": "ARSO", "lokacija": ime}
    try:
        url = (f"https://meteo.arso.gov.si/uploads/probase/www/observ/surface/text/sl/"
               f"observationAms_{arso_id}_latest.xml")
        r = requests.get(url, timeout=10, headers=HEADERS)
        root = ET.fromstring(r.content)
        def v(tag):
            el = root.find(f".//{tag}")
            return el.text.strip() if el is not None and el.text else None
        rezultat["trenutno"] = {
            "temperatura_C": v("t"),
            "vlaga_%": v("rh"),
            "padavine_mm": v("tp_1h") or v("tp"),
            "veter_kmh": v("ff_val"),
            "opis": v("wwsyn_shortText") or v("nn_shortText"),
        }
    except Exception as e:
        rezultat["napaka"] = str(e)
    return rezultat


def pridobi_open_meteo(ime, lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "daily": [
            "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "precipitation_probability_max",
            "windspeed_10m_max", "weathercode",
        ],
        "hourly": [
            "temperature_2m", "precipitation_probability",
            "weathercode", "apparent_temperature",
        ],
        "timezone": "Europe/Belgrade",
        "forecast_days": 5,
    }
    try:
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        data = r.json()
        d = data.get("daily", {})
        h = data.get("hourly", {})

        # Izvleci urne podatke za danes (prvih 24 ur)
        urni = []
        for i in range(24):
            if i < len(h.get("time", [])):
                urni.append({
                    "ura": h["time"][i][11:16] if h.get("time") else "",
                    "temp": h.get("temperature_2m", [])[i] if i < len(h.get("temperature_2m", [])) else None,
                    "obcutena": h.get("apparent_temperature", [])[i] if i < len(h.get("apparent_temperature", [])) else None,
                    "dez_%": h.get("precipitation_probability", [])[i] if i < len(h.get("precipitation_probability", [])) else None,
                    "stanje": WMO_OPISI.get(h.get("weathercode", [])[i] if i < len(h.get("weathercode", [])) else 0, "?"),
                })

        return {
            "vir": "Open-Meteo (ECMWF)", "lokacija": ime,
            "danes": {
                "max_C": d.get("temperature_2m_max", [None])[0],
                "min_C": d.get("temperature_2m_min", [None])[0],
                "padavine_mm": d.get("precipitation_sum", [None])[0],
                "verjetnost_%": d.get("precipitation_probability_max", [None])[0],
                "veter_kmh": d.get("windspeed_10m_max", [None])[0],
                "stanje": WMO_OPISI.get(d.get("weathercode", [0])[0], "neznano"),
            },
            "jutri": {
                "max_C": d.get("temperature_2m_max", [None, None])[1],
                "min_C": d.get("temperature_2m_min", [None, None])[1],
                "padavine_mm": d.get("precipitation_sum", [None, None])[1],
                "verjetnost_%": d.get("precipitation_probability_max", [None, None])[1],
                "stanje": WMO_OPISI.get((d.get("weathercode", [0, 0]) or [0, 0])[1], "neznano"),
            },
            "obeti": [
                {
                    "datum": d.get("time", [])[i],
                    "max_C": d.get("temperature_2m_max", [])[i] if i < len(d.get("temperature_2m_max", [])) else None,
                    "min_C": d.get("temperature_2m_min", [])[i] if i < len(d.get("temperature_2m_min", [])) else None,
                    "stanje": WMO_OPISI.get(d.get("weathercode", [])[i] if i < len(d.get("weathercode", [])) else 0, "?"),
                    "padavine_mm": d.get("precipitation_sum", [])[i] if i < len(d.get("precipitation_sum", [])) else None,
                }
                for i in range(2, min(5, len(d.get("time", []))))
            ],
            "urni_danes": urni,
        }
    except Exception as e:
        return {"vir": "Open-Meteo", "lokacija": ime, "napaka": str(e)}


def pridobi_wttr(ime, lat, lon):
    try:
        r = requests.get(f"https://wttr.in/{lat},{lon}?format=j1",
                         timeout=10, headers=HEADERS)
        data = r.json()
        danes = data["weather"][0]
        return {
            "vir": "wttr.in", "lokacija": ime,
            "danes": {
                "max_C": danes["maxtempC"],
                "min_C": danes["mintempC"],
                "opis": danes["hourly"][4]["weatherDesc"][0]["value"],
                "padavine_mm": danes["hourly"][4]["precipMM"],
            }
        }
    except Exception as e:
        return {"vir": "wttr.in", "lokacija": ime, "napaka": str(e)}


def zberi_napovedi():
    print("Zbiram napovedi...")
    napovedi = {}
    for ime, k in LOKACIJE.items():
        print(f"  → {ime}")
        napovedi[ime] = {
            "arso":       pridobi_arso(ime, k["arso_id"]),
            "open_meteo": pridobi_open_meteo(ime, k["lat"], k["lon"]),
            "wttr":       pridobi_wttr(ime, k["lat"], k["lon"]),
        }
    return napovedi


def analiziraj_z_ai(napovedi):
    danes = datetime.now()
    dan_ime = DNEVI_SLO[danes.weekday()]
    datum_str = f"{dan_ime}, {danes.day}. {MESECI_SLO[danes.month]} {danes.year}"
    vikend = je_vikend()
    primarna = primarna_lokacija()
    sekundarna = sekundarna_lokacija()

    podatki_json = json.dumps(napovedi, ensure_ascii=False, indent=2)

    prompt = f"""Danes je {datum_str}.

Uporabnik je dijak ki {'je doma v Ribnici (vikend)' if vikend else 'je v Novem Mestu (šolski teden)'}.
Primarna lokacija danes: {primarna}
Sekundarna lokacija: {sekundarna}

Imaš podatke iz ARSO, Open-Meteo (ECMWF) in wttr.in za obe lokaciji.

PODATKI:
{podatki_json[:4500]}

Napiši vremensko poročilo v slovenščini. Ton: prijazen, direkten, kot da pišeš sošolcu.

OBVEZNA STRUKTURA:

**{'🏡' if vikend else '🏫'} Danes — {primarna}** ← GLAVNA NAPOVED
(4-5 stavkov: jutro → dopoldne → popoldne → zvečer. Konkretne temperature, % dežja, kdaj točno.)

**👕 Kaj obleči danes**
Priporoči konkretno:
- Zgornji del: kratka majica / majica z dolgimi rokavi / tanek pulover / debel pulover / jakna
- Spodnji del: kratke hlače / dolge hlače / (po potrebi tudi kaj bolj vodoodpornega)
- Obutev: lahki čevlji / normalni čevlji / vodoodporni čevlji / škornji
- Dodatki: dežnik DA/NE, kaj drugega če relevantno
Utemelji v 1 stavku zakaj.

**📍 {sekundarna} danes**
(1-2 stavka — samo ključne razlike. Če je podobno, povej "podobno kot {primarna}".)

**📅 Jutri — {primarna}**
(2 stavka: kaj pričakovati, ali se vreme spreminja)

**🔭 Obeti do konca tedna**
(2-3 stavki za {primarna})

**🎯 Zanesljivost**
(1 stavek: strinjanje virov. ARSO ima prednost.)

Pravila:
- Ne piši "možno" ko je verjetnost nad 70%
- Brez dolgih uvodov ali zaključkov
- Oblačila: bodi konkreten, ne "morda pulover" ampak "pulover bo dovolj" ali "jakna nujna"
- Ribnica: vedno omeni da je kotlina in pogosto 2-3°C hladnejša ter bolj meglena"""

    print("Analiziram z AI...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KLJUC)
    odgovor = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )
    return odgovor.content[0].text


def pošlji_email(analiza):
    danes = datetime.now()
    dan   = DNEVI_SLO[danes.weekday()].capitalize()
    primarna = primarna_lokacija()
    ikona = "🏡" if je_vikend() else "🏫"
    zadeva = f"🌤️ Vreme {dan}, {danes.day}. {MESECI_SLO[danes.month]} — {ikona} {primarna}"

    html_analiza = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', analiza)
    html_analiza = html_analiza.replace("\n", "<br>")

    html = f"""<html><body style="font-family:Georgia,serif;max-width:620px;
margin:0 auto;padding:24px;color:#222;background:#fafafa;">
<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:8px;font-size:18px;">
🌤️ Vremenska napoved — {dan}, {danes.day}. {MESECI_SLO[danes.month]} {danes.year}
</h2>
<div style="background:#e8f4fd;padding:10px 16px;border-radius:6px;margin-bottom:16px;font-size:14px;color:#1a5276;">
{ikona} Danes si v: <strong>{primarna}</strong>
</div>
<div style="line-height:1.9;font-size:15px;background:white;padding:20px;
border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
{html_analiza}
</div>
<p style="color:#aaa;font-size:11px;margin-top:20px;">
Viri: ARSO · Open-Meteo (ECMWF) · wttr.in · Analiza: Claude AI · 24/7 via Railway.app
</p>
</body></html>"""

    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KLJUC)
    message = Mail(
        from_email=EMAIL_POSILJATELJ,
        to_emails=EMAIL_PREJEMNIK,
        subject=zadeva,
        html_content=html
    )
    sg.send(message)
    print("✅ Email poslan!")


def dnevna_naloga():
    print(f"\n{'='*55}")
    print(f"  Vremenski Bot — {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"  Primarna lokacija danes: {primarna_lokacija()}")
    print(f"{'='*55}")
    try:
        napovedi = zberi_napovedi()
        analiza  = analiziraj_z_ai(napovedi)
        print("\n--- ANALIZA (prvih 300 znakov) ---")
        print(analiza[:300] + "...")
        print("-----------------------------------")
        pošlji_email(analiza)
        print("Končano! 🎉")
    except Exception as e:
        print(f"❌ Napaka: {e}")


def main():
    if not ANTHROPIC_API_KLJUC:
        print("❌ Manjka ANTHROPIC_API_KEY!"); return
    if not SENDGRID_API_KLJUC:
        print("❌ Manjka SENDGRID_API_KEY!"); return
    if not EMAIL_POSILJATELJ:
        print("❌ Manjka EMAIL_SENDER!"); return

    print(f"🌤️ Vremenski Bot zagnan (Railway 24/7)")
    print(f"   Pošilja vsak dan ob {CAS_POSILJANJA}")
    print(f"   Pon–Pet → Novo Mesto | Sob–Ned → Ribnica")

    schedule.every().day.at(CAS_POSILJANJA).do(dnevna_naloga)

    print("\nTestni email ob zagonu...")
    dnevna_naloga()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
