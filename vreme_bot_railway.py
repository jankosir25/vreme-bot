"""
Vremenski AI Bot — Railway.app verzija (24/7)
=============================================
Enako kot vreme_bot.py, ampak prilagojen za Railway oblak.
Razlika: podatki so v okolijskih spremenljivkah (Environment Variables),
ne v kodi — tako so varni in jih ni treba vpisovati v datoteko.

Na Railway nastavljaš spremenljivke pod:
  Project → Service → Variables
"""

import requests
import anthropic
import smtplib
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
import time
import schedule   # pip install schedule

# ── Podatki iz Railway Environment Variables ──────────────
ANTHROPIC_API_KLJUC = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_POSILJATELJ   = os.environ.get("EMAIL_SENDER", "")
EMAIL_GESLO         = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_PREJEMNIK     = os.environ.get("EMAIL_RECIPIENT", "")
CAS_POSILJANJA      = os.environ.get("SEND_TIME", "06:30")   # format HH:MM

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
            "temperatura_C": v("t"), "vlaga_%": v("rh"),
            "padavine_mm": v("tp_1h") or v("tp"),
            "veter_kmh": v("ff_val"), "opis": v("wwsyn_shortText") or v("nn_shortText"),
        }
    except Exception as e:
        rezultat["napaka"] = str(e)
    return rezultat


def pridobi_open_meteo(ime, lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "daily": ["temperature_2m_max","temperature_2m_min","precipitation_sum",
                  "precipitation_probability_max","windspeed_10m_max","weathercode"],
        "timezone": "Europe/Belgrade", "forecast_days": 5,
    }
    try:
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        d = r.json().get("daily", {})
        return {
            "vir": "Open-Meteo (ECMWF)", "lokacija": ime,
            "danes": {
                "max_C": d.get("temperature_2m_max",[None])[0],
                "min_C": d.get("temperature_2m_min",[None])[0],
                "padavine_mm": d.get("precipitation_sum",[None])[0],
                "verjetnost_%": d.get("precipitation_probability_max",[None])[0],
                "veter_kmh": d.get("windspeed_10m_max",[None])[0],
                "stanje": WMO_OPISI.get(d.get("weathercode",[0])[0],"neznano"),
            },
            "jutri": {
                "max_C": d.get("temperature_2m_max",[None,None])[1],
                "min_C": d.get("temperature_2m_min",[None,None])[1],
                "stanje": WMO_OPISI.get((d.get("weathercode",[0,0])or[0,0])[1],"neznano"),
            },
            "obeti": [
                {"datum": d.get("time",[])[i],
                 "max_C": d.get("temperature_2m_max",[])[i] if i<len(d.get("temperature_2m_max",[])) else None,
                 "stanje": WMO_OPISI.get(d.get("weathercode",[])[i] if i<len(d.get("weathercode",[])) else 0,"?")}
                for i in range(2, min(5, len(d.get("time",[]))))
            ]
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
                "max_C": danes["maxtempC"], "min_C": danes["mintempC"],
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
    datum_str = f"{DNEVI_SLO[danes.weekday()]}, {danes.day}. {MESECI_SLO[danes.month]} {danes.year}"
    podatki_json = json.dumps(napovedi, ensure_ascii=False, indent=2)

    prompt = f"""Danes je {datum_str}.

Imaš vremenske podatke iz TREH virov (ARSO, Open-Meteo/ECMWF, wttr.in) za Novo Mesto in Ribnico.

PODATKI:
{podatki_json[:4000]}

Napiši vremensko poročilo v slovenščini kot izkušen meteorolog prijatelju.

Struktura:
**☀️ Danes — Novo Mesto**
(3-4 stavki: jutro → poldne → popoldne. Konkretno: temperature, % verjetnost dežja, kdaj.)

**🌿 Danes — Ribnica**
(2 stavka — razlike od NM. Ribnica je kotlina: hladnejša, bolj meglena.)

**📅 Jutri**
(1-2 stavka za obe lokaciji)

**🔭 Obeti 3-5 dni**
(2-3 stavki)

**🎯 Zanesljivost**
(1 stavek: strinjanje virov. ARSO ima prednost.)

Pravila: brez dolgih uvodov, konkretne številke, ne piši "možno" ko je nad 70%."""

    print("Analiziram z AI...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KLJUC)
    odgovor = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return odgovor.content[0].text


def pošlji_email(analiza):
    danes = datetime.now()
    dan   = DNEVI_SLO[danes.weekday()].capitalize()
    zadeva = f"🌤️ Vreme {dan}, {danes.day}. {MESECI_SLO[danes.month]} — NM & Ribnica"

    html_analiza = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', analiza)
    html_analiza = html_analiza.replace("\n", "<br>")

    html = f"""<html><body style="font-family:Georgia,serif;max-width:620px;
margin:0 auto;padding:24px;color:#222;background:#fafafa;">
<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:8px;">
🌤️ Vremenska napoved — {dan}, {danes.day}. {MESECI_SLO[danes.month]} {danes.year}
</h2>
<div style="line-height:1.8;font-size:15px;background:white;padding:20px;
border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
{html_analiza}
</div>
<p style="color:#aaa;font-size:11px;margin-top:20px;">
Viri: ARSO · Open-Meteo (ECMWF) · wttr.in · Analiza: Claude AI · 24/7 via Railway.app
</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = zadeva
    msg["From"]    = EMAIL_POSILJATELJ
    msg["To"]      = EMAIL_PREJEMNIK
    msg.attach(MIMEText(analiza, "plain", "utf-8"))
    msg.attach(MIMEText(html,    "html",  "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_POSILJATELJ, EMAIL_GESLO)
        s.sendmail(EMAIL_POSILJATELJ, EMAIL_PREJEMNIK, msg.as_string())
    print("✅ Email poslan!")


def dnevna_naloga():
    print(f"\n{'='*50}")
    print(f"  Vremenski Bot — {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"{'='*50}")
    try:
        napovedi = zberi_napovedi()
        analiza  = analiziraj_z_ai(napovedi)
        print("\n--- ANALIZA ---")
        print(analiza[:300] + "...")
        print("---------------")
        pošlji_email(analiza)
        print("Končano! 🎉")
    except Exception as e:
        print(f"❌ Napaka: {e}")


def main():
    if not ANTHROPIC_API_KLJUC:
        print("❌ Manjka ANTHROPIC_API_KEY v Environment Variables!"); return
    if not EMAIL_POSILJATELJ:
        print("❌ Manjka EMAIL_SENDER v Environment Variables!"); return

    print(f"🌤️ Vremenski Bot zagnan (Railway 24/7)")
    print(f"   Pošilja vsak dan ob {CAS_POSILJANJA}")

    # Nastavi urnik
    schedule.every().day.at(CAS_POSILJANJA).do(dnevna_naloga)

    # Pošlji takoj ob zagonu (za test)
    print("\nTestni email ob zagonu...")
    dnevna_naloga()

    # Teči v neskončnost
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
