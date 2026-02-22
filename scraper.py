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

RENTABILITE_MIN = 10.0       # % brut minimum
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
    """Scrape les annonces LeBonCoin pour une ville donnée."""
    annonces = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
    
    # URL de recherche LeBonCoin - catégorie immobilier, section ventes
    url = f"https://www.leboncoin.fr/recherche?category=9&locations={ville}&real_estate_type=6&price=50000-600000"
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # LeBonCoin utilise du JSON embarqué dans la page
        script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script_tag:
            print(f"  [!] Structure LeBonCoin non trouvée pour {ville}")
            return annonces

        data = json.loads(script_tag.string)
        
        try:
            listings = data["props"]["pageProps"]["searchData"]["ads"]
        except (KeyError, TypeError):
            print(f"  [!] Pas d'annonces trouvées pour {ville}")
            return annonces

        for item in listings:
            try:
                titre = item.get("subject", "")
                prix = item.get("price", [None])
                if isinstance(prix, list):
                    prix = prix[0] if prix else None
                url_annonce = "https://www.leboncoin.fr/ad/" + str(item.get("list_id", ""))
                
                # Attributs
                attributs = {a["key"]: a.get("value_label", a.get("values", [""])[0]) 
                             for a in item.get("attributes", [])}
                surface = None
                surface_str = attributs.get("square", "")
                if surface_str:
                    surface = extraire_surface(str(surface_str) + " m²")
                
                description = item.get("body", "")
                
                if not est_immeuble_de_rapport(titre, description):
                    continue
                if not prix or not surface:
                    continue
                if prix < PRIX_MIN or prix > PRIX_MAX:
                    continue
                    
                annonces.append({
                    "id": str(item.get("list_id")),
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
    except json.JSONDecodeError as e:
        print(f"  [!] Erreur JSON pour {ville} : {e}")
    
    return annonces


def envoyer_email(annonces_qualifiees):
    """Envoie un email récapitulatif des annonces qualifiées."""
    if not annonces_qualifiees:
        return
    
    if not GMAIL_EXPEDITEUR or not GMAIL_MOT_DE_PASSE:
        print("  [!] Gmail non configuré, affichage console uniquement")
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
    
    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto;">
    <h2 style="color: #2c3e50;">🏢 Agent Immo — {len(annonces_qualifiees)} bien(s) trouvé(s)</h2>
    <p style="color: #7f8c8d;">Scan du {date_str} | Critère : Renta brute ≥ {RENTABILITE_MIN}%</p>
    <hr>
    """
    
    for a in annonces_qualifiees:
        couleur = "#27ae60" if a["rentabilite"] >= 12 else "#f39c12"
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
    
    if annonces_qualifiees:
        envoyer_email(annonces_qualifiees)
    else:
        print("Aucun bien ne correspond aux critères aujourd'hui.")
    
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
