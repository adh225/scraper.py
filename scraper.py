import os
import json
import smtplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────
# CONFIGURATION (à modifier avec vos infos)
# ─────────────────────────────────────────
UVCI_URL        = "https://elearning.uvci.edu.ci"   # URL du site UVCI (à vérifier)
UVCI_LOGIN_URL  = f"{UVCI_URL}/login/index.php"
UVCI_USERNAME   = os.environ.get("UVCI_USERNAME", "votre_email@uvci.ci")
UVCI_PASSWORD   = os.environ.get("UVCI_PASSWORD", "votre_mot_de_passe")

GMAIL_SENDER    = os.environ.get("GMAIL_SENDER", "votre_email@gmail.com")
GMAIL_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "xxxx xxxx xxxx xxxx")  # App Password Gmail
GMAIL_RECEIVER  = os.environ.get("GMAIL_RECEIVER", "votre_email@gmail.com")

DEVOIRS_FILE    = "devoirs_vus.json"   # Fichier pour mémoriser les devoirs déjà vus
CHECK_INTERVAL  = 300                  # Vérification toutes les 5 minutes (en secondes)

# ─────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────
# FONCTIONS UTILITAIRES
# ─────────────────────────────────────────

def charger_devoirs_vus():
    """Charge la liste des devoirs déjà notifiés depuis le fichier JSON."""
    if os.path.exists(DEVOIRS_FILE):
        with open(DEVOIRS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def sauvegarder_devoirs_vus(devoirs):
    """Sauvegarde la liste des devoirs déjà notifiés."""
    with open(DEVOIRS_FILE, "w", encoding="utf-8") as f:
        json.dump(devoirs, f, ensure_ascii=False, indent=2)


def envoyer_email(devoir):
    """Envoie un email de notification pour un nouveau devoir."""
    sujet = f"📚 Nouveau devoir UVCI : {devoir['titre']}"
    corps = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <div style="max-width:600px; margin:auto; border:1px solid #ddd; border-radius:8px; overflow:hidden;">
            <div style="background:#003399; padding:20px; color:white;">
                <h2 style="margin:0;">📚 Nouveau Devoir UVCI</h2>
            </div>
            <div style="padding:24px;">
                <h3 style="color:#003399;">{devoir['titre']}</h3>
                <table style="width:100%; border-collapse:collapse;">
                    <tr><td style="padding:8px; font-weight:bold; width:40%;">📅 Date limite :</td>
                        <td style="padding:8px;">{devoir.get('deadline', 'Non précisée')}</td></tr>
                    <tr style="background:#f9f9f9;">
                        <td style="padding:8px; font-weight:bold;">📖 Cours :</td>
                        <td style="padding:8px;">{devoir.get('cours', 'Non précisé')}</td></tr>
                    <tr><td style="padding:8px; font-weight:bold;">🔗 Lien :</td>
                        <td style="padding:8px;"><a href="{devoir.get('lien', UVCI_URL)}" style="color:#003399;">Voir le devoir</a></td></tr>
                </table>
                <p style="margin-top:20px; color:#666; font-size:13px;">
                    Détecté automatiquement le {datetime.now().strftime('%d/%m/%Y à %H:%M')}
                </p>
            </div>
        </div>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = sujet
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECEIVER
    msg.attach(MIMEText(corps, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as serveur:
            serveur.login(GMAIL_SENDER, GMAIL_PASSWORD)
            serveur.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())
        log.info(f"✅ Email envoyé pour : {devoir['titre']}")
        return True
    except Exception as e:
        log.error(f"❌ Erreur envoi email : {e}")
        return False


# ─────────────────────────────────────────
# CONNEXION & SCRAPING UVCI (Moodle)
# ─────────────────────────────────────────

def se_connecter(session):
    """Se connecte au site UVCI (basé sur Moodle)."""
    try:
        # Récupérer le token de connexion Moodle
        r = session.get(UVCI_LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        token_input = soup.find("input", {"name": "logintoken"})
        logintoken = token_input["value"] if token_input else ""

        payload = {
            "username":   UVCI_USERNAME,
            "password":   UVCI_PASSWORD,
            "logintoken": logintoken,
            "anchor":     ""
        }
        r2 = session.post(UVCI_LOGIN_URL, data=payload, timeout=15)

        if "Tableau de bord" in r2.text or "Dashboard" in r2.text or "Mon espace" in r2.text:
            log.info("✅ Connexion UVCI réussie")
            return True
        else:
            log.warning("⚠️ Connexion échouée - vérifiez vos identifiants")
            return False
    except Exception as e:
        log.error(f"❌ Erreur de connexion : {e}")
        return False


def recuperer_devoirs(session):
    """Récupère la liste des devoirs depuis le calendrier ou le tableau de bord UVCI."""
    devoirs = []
    try:
        # Page des devoirs à rendre (upcoming assignments sur Moodle)
        r = session.get(f"{UVCI_URL}/calendar/view.php?view=upcoming", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Chercher les événements de type "devoir" / "assign"
        events = soup.find_all("div", class_=lambda c: c and ("event" in c or "assign" in c))

        for ev in events:
            titre_tag = ev.find(["h3", "h4", "a"])
            if not titre_tag:
                continue
            titre = titre_tag.get_text(strip=True)
            lien  = titre_tag.get("href", UVCI_URL) if titre_tag.name == "a" else UVCI_URL

            # Deadline
            date_tag = ev.find(class_=lambda c: c and "date" in str(c))
            deadline = date_tag.get_text(strip=True) if date_tag else "Non précisée"

            # Cours associé
            cours_tag = ev.find(class_=lambda c: c and ("course" in str(c) or "module" in str(c)))
            cours = cours_tag.get_text(strip=True) if cours_tag else "Non précisé"

            if titre and "devoir" in titre.lower() or "assignment" in titre.lower() or lien:
                devoirs.append({
                    "id":       lien,        # L'URL sert d'identifiant unique
                    "titre":    titre,
                    "lien":     lien,
                    "deadline": deadline,
                    "cours":    cours,
                })

        log.info(f"📋 {len(devoirs)} devoir(s) trouvé(s)")
    except Exception as e:
        log.error(f"❌ Erreur lors du scraping : {e}")

    return devoirs


# ─────────────────────────────────────────
# BOUCLE PRINCIPALE
# ─────────────────────────────────────────

def main():
    log.info("🚀 Démarrage du notificateur UVCI...")
    devoirs_vus = charger_devoirs_vus()

    while True:
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            if se_connecter(session):
                devoirs = recuperer_devoirs(session)

                nouveaux = [d for d in devoirs if d["id"] not in devoirs_vus]

                if nouveaux:
                    log.info(f"🆕 {len(nouveaux)} nouveau(x) devoir(s) détecté(s) !")
                    for devoir in nouveaux:
                        if envoyer_email(devoir):
                            devoirs_vus.append(devoir["id"])
                    sauvegarder_devoirs_vus(devoirs_vus)
                else:
                    log.info("✔️ Aucun nouveau devoir.")
            else:
                log.warning("Connexion échouée, nouvel essai dans 5 minutes...")

        except Exception as e:
            log.error(f"Erreur inattendue : {e}")

        log.info(f"⏳ Prochaine vérification dans {CHECK_INTERVAL // 60} minutes...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
