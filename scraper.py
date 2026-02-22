"""
Agent Immo - Scraper LeBonCoin
Recherche des immeubles de rapport autour de Narbonne avec renta brute >= 10%
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import time
import re

# ─────────────────────────────────────────────
# CONFIGURATION — À MODIFIER PAR L'UTILISATEUR
# ─────────────────────────────────────────────

VILLES = [
    "narbonne",
    "beziers",
    "carcassonne",
    "lezignan-corbieres",
    "port-la-nouvelle",
    "gruissan",
]

RENTABILITE_MIN = 3.0        # % brut minimum
PRIX_MAX = 600_000           # € prix maximum
PRIX_MIN = 50_000            # € prix minimum

GMAIL_EXPEDITEUR = os.environ.get("GMAIL_USER", "")       # Mis via GitHub Secrets
GMAIL_MOT_DE_PASSE = os.environ.get("GMAIL_PASSWORD", "") # Mis via GitHub Secrets
GMAIL_DESTINATAIRE = os.environ.get("GMAIL_USER", "")     # Tu recevras sur ta propre adresse

FICHIER_ANNONCES_VUES = "annonces_vues.json"

# ─────────────────────────────────────────────
# MOTS-CLÉS POUR DÉTECTER UN IMMEUBLE DE RAPPORT
# ─────────────────────────────────────────────

MOTS_CLES_IMMEUBLE = [
    "immeuble de rapport",
    "immeuble rapport",
    "immeuble locatif",
    "immeuble de rendement",
    "ensemble immobilier",
    "immeuble entier",
    "plusieurs logements",
    "plurifamilial",
    "logements",
    "appartements",
]

# ─────────────────────────────────────────────
# ESTIMATION DES LOYERS PAR VILLE (€/mois/m²)
# Source : estimations marché local 2024
# ─────────────────────────────────────────────

LOYER_M2_PAR_VILLE = {
    "narbonne": 9.5,
    "beziers": 8.5,
    "carcassonne": 8.0,
    "lezignan-corbieres": 7.5,
    "port-la-nouvelle": 9.0,
    "gruissan": 11.0,
    "default": 8.5,
}


def charger_annonces_vues():
    """Charge la liste des annonces déjà traitées pour éviter les doublons."""
    if os.path.exists(FICHIER_ANNONCES_VUES):
        with open(FICHIER_ANNONCES_VUES, "r") as f:
            return set(json.load(f))
    return set()


def sauvegarder_annonces_vues(vues):
    """Sauvegarde la liste des annonces déjà traitées."""
    with open(FICHIER_ANNONCES_VUES, "w") as f:
        json.dump(list(vues), f)


def extraire_prix(texte):
    """Extrait le prix en € depuis un texte."""
    texte = texte.replace(" ", "").replace("\xa0", "")
    match = re.search(r"(\d+[\d\s]*)\s*€", texte)
    if match:
        prix_str = re.sub(r"\D", "", match.group(1))
        return int(prix_str)
    return None


def extraire_surface(texte):
    """Extrait la surface en m² depuis un texte."""
    match = re.search(r"(\d+)\s*m²", texte, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def est_immeuble_de_rapport(titre, description=""):
    """Vérifie si l'annonce correspond à un immeuble de rapport."""
    texte = (titre + " " + description).lower()
    return any(mot in texte for mot in MOTS_CLES_IMMEUBLE)


def calculer_rentabilite(prix, surface, ville):
    """Calcule la rentabilité brute estimée."""
    loyer_m2 = LOYER_M2_PAR_VILLE.get(ville.lower(), LOYER_M2_PAR_VILLE["default"])
    loyer_mensuel = surface * loyer_m2
    loyer_annuel = loyer_mensuel * 12
    rentabilite = (loyer_annuel / prix) * 100
    return round(rentabilite, 2), round(loyer_mensuel, 0)


def scraper_leboncoin(ville):
    """Récupère les annonces LeBonCoin via le flux RSS officiel (non bloqué)."""
    annonces = []

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RSS reader)",
        "Accept": "application/rss+xml, application/xml, text/xml",
    }

    # Flux RSS officiel LeBonCoin — catégorie ventes immobilières
    url = f"https://www.leboncoin.fr/rss/ventes_immobilieres.htm?location={ville}&ros=1"

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "xml")

        items = soup.find_all("item")
        print(f"   → {len(items)} annonce(s) brute(s) dans le flux RSS")

        for item in items:
            try:
                titre = item.find("title").get_text(strip=True) if item.find("title") else ""
                url_annonce = item.find("link").get_text(strip=True) if item.find("link") else ""
                description_html = item.find("description").get_text(strip=True) if item.find("description") else ""

                # Extraire le texte brut de la description HTML
                desc_soup = BeautifulSoup(description_html, "html.parser")
                description = desc_soup.get_text(separator=" ", strip=True)

                # Extraire un ID unique depuis l'URL
                annonce_id = url_annonce.split("/")[-1].split(".")[0] if url_annonce else ""

                if not est_immeuble_de_rapport(titre, description):
                    continue

                prix = extraire_prix(description) or extraire_prix(titre)
                surface = extraire_surface(description) or extraire_surface(titre)

                if not prix or not surface:
                    continue
                if prix < PRIX_MIN or prix > PRIX_MAX:
                    continue

                annonces.append({
                    "id": annonce_id,
                    "titre": titre,
                    "prix": prix,
                    "surface": surface,
                    "ville": ville.capitalize(),
                    "url": url_annonce,
                    "description": description[:300],
                })

            except Exception as e:
                print(f"  [!] Erreur parsing annonce : {e}")
                continue

    except requests.RequestException as e:
        print(f"  [!] Erreur réseau pour {ville} : {e}")

    return annonces


def envoyer_email(annonces_qualifiees):
    """Envoie un email récapitulatif des annonces qualifiées (ou un rapport vide)."""
    
    if not GMAIL_EXPEDITEUR or not GMAIL_MOT_DE_PASSE:
        print("  [!] Gmail non configuré, affichage console uniquement")
        if not annonces_qualifiees:
            print("  Aucun bien trouvé aujourd'hui.")
        for a in annonces_qualifiees:
            print(f"\n✅ {a['titre']}")
            print(f"   Prix : {a['prix']:,} € | Surface : {a['surface']} m²")
            print(f"   Renta brute estimée : {a['rentabilite']}%")
            print(f"   Loyer estimé : {a['loyer_mensuel']:.0f} €/mois")
            print(f"   Ville : {a['ville']}")
            print(f"   Lien : {a['url']}")
        return

    # Construction de l'email HTML
    date_str = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    nb = len(annonces_qualifiees)
    
    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h2 style="color: #2c3e50;">🏢 Agent Immo — {nb} bien(s) trouvé(s)</h2>
    <p style="color: #7f8c8d;">Scan du {date_str} | Critère : Renta brute ≥ {RENTABILITE_MIN}%</p>
    <hr>
    """

    if not annonces_qualifiees:
        html += """
        <div style="text-align:center; padding: 40px; color: #95a5a6;">
            <p style="font-size: 2em;">😴</p>
            <p style="font-size: 1.1em;">Aucun immeuble de rapport ne correspond aux critères aujourd'hui.</p>
            <p>Le scan reprendra demain matin à 8h.</p>
        </div>
        """
    
    for a in annonces_qualifiees:
        couleur = "#27ae60" if a["rentabilite"] >= 8 else "#f39c12" if a["rentabilite"] >= 5 else "#e67e22"
        html += f"""
        <div style="border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 16px 0;">
            <h3 style="margin: 0 0 8px 0; color: #2c3e50;">{a['titre']}</h3>
            <p style="margin: 4px 0;">📍 <strong>{a['ville']}</strong></p>
            <p style="margin: 4px 0;">💰 Prix : <strong>{a['prix']:,} €</strong></p>
            <p style="margin: 4px 0;">📐 Surface : <strong>{a['surface']} m²</strong></p>
            <p style="margin: 4px 0;">🏠 Loyer estimé : <strong>{a['loyer_mensuel']:.0f} €/mois</strong></p>
            <p style="margin: 8px 0;">
                <span style="background:{couleur}; color:white; padding: 4px 12px; border-radius: 20px; font-weight: bold;">
                    Renta brute : {a['rentabilite']}%
                </span>
            </p>
            {'<p style="color:#888; font-size:0.9em;">' + a['description'][:200] + '...</p>' if a['description'] else ''}
            <a href="{a['url']}" style="display:inline-block; margin-top:8px; background:#3498db; color:white; padding:8px 16px; border-radius:4px; text-decoration:none;">
                Voir l'annonce →
            </a>
        </div>
        """
    
    html += """
    <hr>
    <p style="color: #bdc3c7; font-size: 0.8em;">
        ⚠️ Les loyers sont estimés selon le marché local. Vérifiez toujours les loyers réels avant d'investir.<br>
        Agent Immo — GitHub Actions
    </p>
    </body></html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏢 Agent Immo — {len(annonces_qualifiees)} immeuble(s) à +{RENTABILITE_MIN}% autour de Narbonne"
    msg["From"] = GMAIL_EXPEDITEUR
    msg["To"] = GMAIL_DESTINATAIRE
    msg.attach(MIMEText(html, "html"))
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_EXPEDITEUR, GMAIL_MOT_DE_PASSE)
            server.sendmail(GMAIL_EXPEDITEUR, GMAIL_DESTINATAIRE, msg.as_string())
        print(f"  ✅ Email envoyé : {len(annonces_qualifiees)} annonce(s)")
    except Exception as e:
        print(f"  [!] Erreur envoi email : {e}")


def main():
    print(f"\n{'='*50}")
    print(f"Agent Immo — Démarrage {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"Zone : {', '.join(VILLES)}")
    print(f"Critère : renta brute ≥ {RENTABILITE_MIN}%")
    print(f"{'='*50}\n")

    annonces_vues = charger_annonces_vues()
    annonces_qualifiees = []
    total_scrapees = 0

    for ville in VILLES:
        print(f"🔍 Scan de {ville.capitalize()}...")
        annonces = scraper_leboncoin(ville)
        total_scrapees += len(annonces)
        print(f"   → {len(annonces)} immeuble(s) de rapport trouvé(s)")

        for annonce in annonces:
            if annonce["id"] in annonces_vues:
                continue  # Déjà traitée

            rentabilite, loyer_mensuel = calculer_rentabilite(
                annonce["prix"], annonce["surface"], ville
            )
            
            if rentabilite >= RENTABILITE_MIN:
                annonce["rentabilite"] = rentabilite
                annonce["loyer_mensuel"] = loyer_mensuel
                annonces_qualifiees.append(annonce)
                annonces_vues.add(annonce["id"])
                print(f"   ✅ MATCH : {annonce['titre'][:50]} — {rentabilite}%")
            else:
                annonces_vues.add(annonce["id"])

        time.sleep(2)  # Pause entre les villes pour ne pas surcharger le serveur

    sauvegarder_annonces_vues(annonces_vues)

    print(f"\n{'='*50}")
    print(f"Résultat : {len(annonces_qualifiees)} bien(s) qualifié(s) sur {total_scrapees} scrapé(s)")
    
    envoyer_email(annonces_qualifiees)
    
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
