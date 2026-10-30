"""
Surveillance location voiture Tenerife - version cloud (GitHub Actions)
Prise en charge ET retour : aéroport Tenerife Sud (TFS). Retour fixe le 28/11.
Objectif : trouver le départ le PLUS TÔT possible avec un prix total < SEUIL_EUR.
"""

import asyncio
import base64
import datetime as dt
import json
import os
import re
import traceback

import requests
from playwright.async_api import async_playwright

# ============ CONFIGURATION ============

SEUIL_EUR = 200
RETOUR = dt.date(2026, 11, 28)
DEBUT_MIN = dt.date(2026, 10, 30)
DEBUT_MAX = dt.date(2026, 11, 10)     # départ le plus tardif acceptable
HEURE = "10"                          # heure de prise en charge et de retour
PAUSE_ENTRE_PAGES_S = 5
PRIX_PLANCHER = 60                    # ignore les montants trop petits (prix/jour, options)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

MOTS_CAPTCHA = ["captcha", "qu'un robot", "are you a robot", "êtes-vous un robot",
                "verify you are human", "vérifiez que vous êtes humain", "access denied"]
MOTS_INDISPO = ["rien de disponible", "aucun véhicule disponible", "no cars available",
                "no hay disponibilidad", "aucun résultat"]


# ============ CONSTRUCTION DES ADRESSES (une fonction par site) ============

def url_booking(d, f):
    return ("https://cars.booking.com/search-results?locationIata=TFS&dropLocationIata=TFS"
            f"&puDay={d.day}&puMonth={d.month}&puYear={d.year}"
            f"&doDay={f.day}&doMonth={f.month}&doYear={f.year}"
            f"&puHour={HEURE}&puMinute=00&doHour={HEURE}&doMinute=00"
            "&driversAge=30&cor=FR&locationName=TFS&dropLocationName=TFS"
            "&currency=EUR&preflang=fr&filterCriteria_sortBy=PRICE&filterCriteria_sortAscending=true")


def url_discovercars(d, f):
    recherche = {"PickupLocationId": 3865, "DropOffLocationId": 3865,      # 3865 = Tenerife Sud
                 "PickupDateTime": f"{d:%Y-%m-%d}T{HEURE}:00:00",
                 "DropOffDateTime": f"{f:%Y-%m-%d}T{HEURE}:00:00",
                 "ResidenceCountry": "FR", "DriverAge": 35, "Hash": ""}
    sq = base64.b64encode(json.dumps(recherche, separators=(",", ":")).encode()).decode()
    return ("https://www.discovercars.com/fr/search/c2a0fdad-bf0c-4b0f-9b7f-20fb222693a0"
            f"?sq={requests.utils.quote(sq)}&searchVersion=2")


def url_sixt(d, f):
    return ("https://www.sixt.fr/betafunnel/#/offerlist"
            f"?uda={d:%Y%m%d}&rda={f:%Y%m%d}&uti={HEURE}00&rti={HEURE}00"
            "&zen_pu_location=72116feb-f9aa-4be9-a06d-85a2cb14eeed"
            "&zen_do_location=72116feb-f9aa-4be9-a06d-85a2cb14eeed"
            f"&zen_pu_time={d:%Y-%m-%d}T{HEURE}%3A00&zen_do_time={f:%Y-%m-%d}T{HEURE}%3A00"
            "&zen_pu_title=Tenerife%20Sud%20A%C3%A9roport&zen_do_title=Tenerife%20Sud%20A%C3%A9roport"
            "&zen_pu_branch_id=BRANCH%3A42489&zen_do_branch_id=BRANCH%3A42489"
            "&zen_vehicle_type=car&zen_pickup_country_code=ES&zen_resident_country_required=false"
            "&zen_order_is_ascending=true&zen_order_by=price_total.gross.value&zen_point_of_sale=FR")


def url_skyscanner(d, f):
    return (f"https://www.skyscanner.fr/carhire/results/27547165/27547165/"
            f"{d:%Y-%m-%d}T{HEURE}:00/{f:%Y-%m-%d}T{HEURE}:00/30")


async def ouvrir_autoreisen(page, d, f):
    """AutoReisen : pas de dates dans l'adresse, on remplit le formulaire."""
    await page.goto("https://www.autoreisen.com/LocationDeVoitures/tarifs-flotte.php",
                    wait_until="domcontentloaded", timeout=60000)
    mois = ["Jan", "Fev", "Mar", "Avr", "Mai", "Juin", "Juil", "Aou", "Sep", "Oct", "Nov", "Dec"]
    resultat = await page.evaluate("""([agence, dj, dm, fj, fm, h]) => {
        const sels = [...document.querySelectorAll('select')];
        const has = (s, t) => [...s.options].some(o => o.text.trim() === t);
        const set = (s, t) => { const o = [...s.options].find(o => o.text.trim() === t);
                                s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true})); };
        const ag = sels.filter(s => has(s, agence));
        const jours = sels.filter(s => has(s, '01') && has(s, '31'));
        const moisS = sels.filter(s => has(s, dm) && has(s, fm));
        const heures = sels.filter(s => has(s, h));
        if (!ag.length || jours.length < 2 || moisS.length < 2 || heures.length < 2)
            return 'formulaire non reconnu ' + [ag.length, jours.length, moisS.length, heures.length];
        set(ag[0], agence);
        set(jours[0], dj); set(moisS[0], dm); set(heures[0], h);
        set(jours[1], fj); set(moisS[1], fm); set(heures[1], h);
        const form = jours[0].form;
        if (!form) return 'formulaire introuvable';
        setTimeout(() => form.requestSubmit ? form.requestSubmit() : form.submit(), 100);
        return 'ok';
    }""", ["Aéroport du Tenerife-Sud-TFS-Reina Sofia",
           f"{d.day:02d}", f"{mois[d.month - 1]}-{d.year}",
           f"{f.day:02d}", f"{mois[f.month - 1]}-{f.year}", f"{HEURE}:00"])
    if resultat != "ok":
        raise RuntimeError(f"AutoReisen : {resultat}")
    await page.wait_for_timeout(3000)
    await page.wait_for_load_state("domcontentloaded")


def url_carsrentals(d, f):
    return ("https://www.cars-rentals.com/frp/?lang=fr&vertical=cars"
            "&pickup_city=TFS&pickup_airport_code=TFS&pickup_code=mrvl_ap_42577&pickup_ctid=42577"
            "&pickup_country_name=Espagne&pickup_country_iso_code=ES"
            f"&pickup_date={d:%m}%2F{d:%d}%2F{d:%Y}&pickup_time={HEURE}"
            f"&dropoff_date={f:%m}%2F{f:%d}%2F{f:%Y}&dropoff_time={HEURE}"
            "&is_different_drop=0&locale=fr-FR")


def prix_autoreisen(texte):
    """Prix TOTAL = le montant affiché juste au-dessus du bouton « Reserver »."""
    totaux = re.findall(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s?€\s*R[ée]server", texte, re.I)
    return [p for p in (parse_prix(t) for t in totaux) if p]


# Les comparateurs (DiscoverCars, Booking.com, Sixt, Skyscanner, Kayak) bloquent
# les robots : ils sont retirés, on ne force pas leurs protections.
SITES = [
    {"nom": "AutoReisen", "ouvrir": ouvrir_autoreisen, "extraire": prix_autoreisen},
    {"nom": "Cars-rentals", "url": url_carsrentals},   # à l'essai : retiré s'il bloque
]


# ============ OUTILS ============

def combinaisons_dates():
    aujourd_hui = dt.date.today()
    combos, d = [], max(DEBUT_MIN, aujourd_hui + dt.timedelta(days=1))  # pas de départ le jour même
    while d <= DEBUT_MAX and d < RETOUR:
        combos.append((d, RETOUR, (RETOUR - d).days))
        d += dt.timedelta(days=1)
    return combos


def parse_prix(texte):
    t = re.sub(r"[^\d,.]", "", texte or "")
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) <= 2 else t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


MOTIF_PRIX = re.compile(
    r"(\d{1,3}(?:[ .\u202f\u00a0]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s?€"
    r"|€\s?(\d{1,3}(?:[ ,.\u202f\u00a0]\d{3})*(?:[.,]\d{1,2})?)")


def extraire_prix(texte):
    prix = [parse_prix(a or b) for a, b in MOTIF_PRIX.findall(texte)]
    return [p for p in prix if p and PRIX_PLANCHER <= p < 10000]


def notifier(titre, message, lien=None):
    if not NTFY_TOPIC:
        print("[!] NTFY_TOPIC absent : notification non envoyée")
        return
    headers = {"Title": titre, "Priority": "high", "Tags": "red_car"}
    if lien:
        headers["Click"] = lien
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"), headers=headers, timeout=15)
    except requests.RequestException as e:
        print(f"[!] Notification non envoyée : {e}")


async def scanner(page, site, d, f):
    """Retourne (prix_min_total, statut, lien)."""
    lien = site["url"](d, f) if "url" in site else page.url
    try:
        if "ouvrir" in site:
            await site["ouvrir"](page, d, f)
            lien = "https://www.autoreisen.com/LocationDeVoitures/tarifs-flotte.php"
        else:
            await page.goto(lien, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)   # laisse charger les résultats

        titre = await page.title()
        texte = (await page.inner_text("body")).lower()
        if any(m in texte or m in titre.lower() for m in MOTS_CAPTCHA):
            return None, f"BLOQUÉ (anti-robot) - page : {titre!r}", lien
        if any(m in texte for m in MOTS_INDISPO):
            return None, "rien de disponible", lien
        corps = await page.inner_text("body")
        prix = site["extraire"](corps) if "extraire" in site else extraire_prix(corps)
        if not prix:
            return None, f"aucun prix trouvé - page : {titre!r}", lien
        return min(prix), "ok", lien
    except Exception as e:
        ligne = traceback.extract_tb(e.__traceback__)[-1].lineno
        return None, f"erreur : {e.__class__.__name__} : {str(e)[:120]} (ligne {ligne})", lien


async def main():
    combos = combinaisons_dates()
    if not combos:
        print("Plus aucune date de départ à tester. Désactive le workflow sur GitHub.")
        return

    sites_bloques = set()
    async with async_playwright() as p:
        navigateur = await p.chromium.launch(headless=True)
        contexte = await navigateur.new_context(
            locale="fr-FR", viewport={"width": 1366, "height": 900})

        # Dates dans l'ordre : on s'arrête à la première qui passe sous le seuil
        for d, f, duree in combos:
            print(f"\n--- Départ {d:%d/%m} -> retour {f:%d/%m} TFS ({duree} jours) ---")
            offres = []
            for site in SITES:
                if site["nom"] in sites_bloques:
                    continue
                page = await contexte.new_page()
                prix, statut, lien = await scanner(page, site, d, f)
                await page.close()

                if prix is None:
                    print(f"  {site['nom']:<13} : {statut}")
                    if statut.startswith("BLOQUÉ"):
                        sites_bloques.add(site["nom"])   # inutile d'insister ce tour-ci
                else:
                    marque = "  <== SOUS LE SEUIL" if prix < SEUIL_EUR else ""
                    print(f"  {site['nom']:<13} : {prix:.2f} €{marque}")
                    if prix < SEUIL_EUR:
                        offres.append((prix, site["nom"], lien))
                await asyncio.sleep(PAUSE_ENTRE_PAGES_S)

            if offres:
                prix, nom, lien = min(offres)
                print(f"\n==> OFFRE : {nom}, {prix:.2f} €, départ {d:%d/%m}")
                notifier("Offre sous 200 EUR !",
                         f"{nom} : {prix:.2f} EUR, depart {d:%d/%m}, retour {f:%d/%m} TFS "
                         f"({duree} j). Verifie caution et assurance avant de reserver.", lien)
                break
        else:
            print("\nAucune offre sous le seuil pour l'instant.")

        await navigateur.close()

    if sites_bloques:
        print(f"Sites bloqués à ce passage : {', '.join(sorted(sites_bloques))}")


if __name__ == "__main__":
    asyncio.run(main())
