"""
Surveillance location voiture Tenerife - version cloud (GitHub Actions)
Un passage complet par exécution ; GitHub relance le script toutes les heures.
Objectif : trouver le départ le PLUS TÔT possible sous le seuil,
retour fixe le 28/11 à Tenerife Sud (TFS).
"""

import asyncio
import datetime as dt
import os
import re

import requests
from playwright.async_api import async_playwright

# ============ CONFIGURATION ============

SEUIL_EUR = 200
RETOUR = dt.date(2026, 11, 28)
DEBUT_MIN = dt.date(2026, 10, 30)
DEBUT_MAX = dt.date(2026, 11, 10)     # départ le plus tardif acceptable
PAUSE_ENTRE_PAGES_S = 10

# Le nom du canal ntfy n'est PAS écrit ici : il est stocké en secret dans GitHub
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# url_modele : URL de résultats avec {debut} et {fin} à la place des dates
# format_date : "%Y-%m-%d" -> 2026-11-03 | "%d/%m/%Y" -> 03/11/2026
# selecteur_prix = None -> détection automatique des montants en € sur la page
# (on garde le plus bas au-dessus de PRIX_PLANCHER, pour ignorer prix/jour et options)
PRIX_PLANCHER = 60

SITES = [
    {
        "nom": "Kayak",
        "url_modele": "https://www.kayak.fr/cars/TFS/{debut}/{fin}?sort=price_a",
        "format_date": "%Y-%m-%d",
        "selecteur_prix": None,
        "prix_par_jour": False,
    },
    # Les sites ci-dessous seront activés dès que Manon aura envoyé un lien de résultats :
    # DiscoverCars, Cicar, AutoReisen
]

MOTS_CAPTCHA = ["captcha", "are you a robot", "êtes-vous un robot",
                "verify you are human", "vérifiez que vous êtes humain"]

# ============ FONCTIONS ============


def combinaisons_dates():
    aujourd_hui = dt.date.today()
    combos, d = [], max(DEBUT_MIN, aujourd_hui)
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


async def scanner(page, url, site, duree):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        print(f"   page ouverte : {await page.title()!r}")
        contenu = (await page.content()).lower()
        if any(m in contenu for m in MOTS_CAPTCHA):
            return None, "captcha"
        if site.get("selecteur_prix"):
            textes = await page.locator(site["selecteur_prix"]).all_inner_texts()
            prix = [p for p in (parse_prix(t) for t in textes) if p and p > 0]
        else:
            # Détection automatique : tous les montants en € visibles sur la page
            texte = await page.inner_text("body")
            montants = re.findall(
                r"(\d{1,3}(?:[ .\u202f\u00a0]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s?€"
                r"|€\s?(\d{1,3}(?:[ ,.\u202f\u00a0]\d{3})*(?:[.,]\d{1,2})?)", texte)
            prix = [parse_prix(a or b) for a, b in montants]
            prix = [p for p in prix if p and PRIX_PLANCHER <= p < 10000]
        if not prix:
            return None, "aucun prix trouvé (sélecteur ou URL à vérifier ?)"
        prix_min = min(prix) * (duree if site.get("prix_par_jour") else 1)
        return prix_min, "ok"
    except Exception as e:
        import traceback
        ligne = traceback.extract_tb(e.__traceback__)[-1]
        return None, f"erreur : {e.__class__.__name__} : {e} (ligne {ligne.lineno})"


async def main():
    combos = combinaisons_dates()
    if not combos:
        print("Plus aucune date de départ à tester. Désactive le workflow sur GitHub.")
        return

    nb_ok, nb_captcha = 0, 0
    async with async_playwright() as p:
        navigateur = await p.chromium.launch(headless=True)
        contexte = await navigateur.new_context(locale="fr-FR")

        # Dates testées dans l'ordre : on s'arrête à la première qui passe sous le seuil
        for debut, fin, duree in combos:
            offres = []
            for site in SITES:
                url = site["url_modele"].format(
                    debut=debut.strftime(site["format_date"]),
                    fin=fin.strftime(site["format_date"]))
                page = await contexte.new_page()
                prix, statut = await scanner(page, url, site, duree)
                await page.close()

                if statut == "captcha":
                    nb_captcha += 1
                    print(f"{site['nom']} {debut:%d/%m} : CAPTCHA")
                elif prix is None:
                    print(f"{site['nom']} {debut:%d/%m} : {statut}")
                else:
                    nb_ok += 1
                    print(f"{site['nom']} {debut:%d/%m} -> {fin:%d/%m} ({duree} j) : {prix:.2f} €")
                    if prix < SEUIL_EUR:
                        offres.append((prix, site["nom"], url))
                await asyncio.sleep(PAUSE_ENTRE_PAGES_S)

            if offres:
                prix, nom, url = min(offres)
                print(f"==> OFFRE : {nom}, {prix:.2f} €, départ {debut:%d/%m}")
                notifier("Offre sous 200 EUR !",
                         f"{nom} : {prix:.2f} EUR, depart {debut:%d/%m}, "
                         f"retour {fin:%d/%m} TFS ({duree} j)", url)
                break
        else:
            print("Aucune offre sous le seuil pour l'instant.")

        await navigateur.close()

    if nb_ok == 0 and nb_captcha > 0:
        notifier("Sites bloques", "Tous les sites ont renvoyé un CAPTCHA à ce passage.")


if __name__ == "__main__":
    asyncio.run(main())
