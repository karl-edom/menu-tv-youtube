#!/usr/bin/env python3
"""
Menu TV YouTube — génère chaque jour une grille fermée de propositions.

Principe : peu de propositions, jamais deux fois la même, choisies sur des
signaux objectifs et non sur un avis. La grille croise deux axes :
  - l'intention (apprendre, comprendre le monde, s'émerveiller, se cultiver,
    faire, se détendre)
  - la durée disponible (café, pause, soirée, long cours)

Architecture :
  1. Découverte via les flux RSS publics des chaînes — gratuit, sans quota.
  2. Enrichissement via l'API YouTube Data v3 — uniquement pour ce que le RSS
     ne donne pas (durée, vues, likes). 1 unité pour 50 vidéos, donc le quota
     de 10 000/jour est un non-sujet.
  3. Sélection par score, avec exclusion stricte du déjà-proposé.
  4. Rendu d'une page HTML autonome.

Usage :
    export YT_API_KEY="..."
    python3 menu_tv.py                 # run normal
    python3 menu_tv.py --demo          # données synthétiques, sans réseau
    python3 menu_tv.py --dry-run       # ne modifie pas l'historique
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

RACINE = Path(__file__).parent
ETAT = RACINE / "state"
SORTIE = RACINE / "public"

API = "https://www.googleapis.com/youtube/v3"
RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# --------------------------------------------------------------------------- #
# Configuration de la grille
# --------------------------------------------------------------------------- #

# L'identité visuelle vit entièrement dans theme/theme.css. Ici on ne manipule
# que des rôles : chaque intention reçoit un rang, qui devient une classe
# `af-section--N`. Aucune couleur, aucune taille dans ce fichier — pour changer
# l'apparence, on remplace la feuille, pas le programme.
#
# LES INTENTIONS SONT UNE BIBLIOTHÈQUE, PAS UNE GRILLE.
# Elles sont définies sur UN SEUL axe — « qu'est-ce qu'il te reste après ? » —
# pour que le classement soit reproductible : si un humain ne peut pas trancher
# de la même façon deux fois, un classeur automatique ne le pourra pas non plus.
# Chacun en active ensuite un sous-ensemble depuis la page ; la grille du jour
# reste courte même si la bibliothèque s'élargit.
#
# Le champ `creneaux` dit quelles durées cette intention peut réellement servir.
# Un entretien de 6 minutes n'existe pas, une vidéo drôle de deux heures non
# plus : déclarer ces cases plutôt que les laisser vides évite de faire passer
# une absence structurelle pour un manque de chaînes.
#
# (clé, libellé, description, créneaux servis)
INTENTIONS = [
    ("comprendre", "Comprendre", "Un mécanisme. Ça marche comment ?",
     ["cafe", "pause", "soiree", "long"]),
    ("monde", "Le monde", "Une situation. Pourquoi c'est comme ça ?",
     ["cafe", "pause", "soiree", "long"]),
    ("oeuvres", "Œuvres", "Une rencontre. Cinéma, musique, littérature, art.",
     ["cafe", "pause", "soiree", "long"]),
    ("recit", "Récit", "Une histoire. Début, milieu, fin.",
     ["pause", "soiree", "long"]),
    ("faire", "Faire", "Un savoir-faire. Un geste que tu peux reproduire.",
     ["cafe", "pause", "soiree"]),
    ("regarder", "Regarder", "Une image. La valeur est dans ce qu'on voit.",
     ["cafe", "pause", "soiree"]),
    ("ecouter", "Écouter penser", "Une conversation. Quelqu'un déroule une pensée.",
     ["soiree", "long"]),
    ("jouer", "Jouer", "Une partie. Jeu, compétition, univers.",
     ["pause", "soiree", "long"]),
    ("bouger", "Bouger", "Un corps en action. Sport, performance physique.",
     ["cafe", "pause", "soiree"]),
    ("rire", "Rire", "Un moment léger, assumé comme tel.",
     ["cafe", "pause"]),
]

# (clé, libellé, borne basse en minutes, borne haute)
CRENEAUX = [
    ("cafe", "Café", 3, 12),
    ("pause", "Pause", 12, 30),
    ("soiree", "Soirée", 30, 75),
    ("long", "Long cours", 75, 100_000),
]


def cellules() -> list[tuple[str, str]]:
    """Les cases qui existent réellement, intention par intention."""
    return [(i[0], c) for i in INTENTIONS for c in i[3]]

# Fenêtre de candidature : au-delà, une vidéo n'est plus "à l'affiche".
FENETRE_JOURS = 21
# Fenêtre de rattrapage, utilisée uniquement pour les cases qui resteraient
# vides. Mieux vaut une bonne vidéo d'il y a six semaines qu'une case morte —
# les créneaux longs et les intentions peu dotées publient lentement.
FENETRE_SECOURS = 75
# Une chaîne déjà proposée dans les N derniers jours est fortement pénalisée.
QUARANTAINE_CHAINE_JOURS = 10
# Durée de mémoire du déjà-proposé, en jours. Au-delà, une vidéo peut revenir.
MEMOIRE_JOURS = 240
# Une vidéo plus courte que ça est un Short : exclue.
DUREE_MIN_SECONDES = 90

POIDS = {
    "surperformance": 0.45,  # la vidéo marche-t-elle mieux que d'habitude sur SA chaîne
    "reception": 0.25,       # ratio likes / vues
    "fraicheur": 0.30,       # décroissance exponentielle avec l'âge
}
PENALITE_REDONDANCE = 0.60   # chaîne vue récemment
PENALITE_RACOLAGE = 0.50     # titre en capitales, emojis, ponctuation excessive

# Garde-fous de quota. Le quota YouTube est de 10 000 unités/jour ; on s'arrête
# avant, pour finir la journée avec une page plutôt qu'avec un 403.
PLAFOND_QUOTA = 9_000
# Le repli de résolution coûte 100 unités l'appel. Sans plafond, une liste de
# chaînes truffée de handles faux vide le quota en une passe et tue le run.
# Les handles non traités sont reportés au lendemain, pas marqués en échec.
BUDGET_RECHERCHE = 1_000     # soit 10 replis par run au maximum


# --------------------------------------------------------------------------- #
# Modèle
# --------------------------------------------------------------------------- #

@dataclass
class Video:
    id: str
    titre: str
    chaine_id: str
    chaine_nom: str
    publie_le: datetime
    duree_s: int = 0
    vues: int = 0
    likes: int = 0
    description: str = ""
    intention: str = ""
    langue: str = ""
    score: float = 0.0
    rattrapage: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def age_jours(self) -> float:
        return max(
            0.0, (datetime.now(timezone.utc) - self.publie_le).total_seconds() / 86400
        )

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def miniature(self) -> str:
        return f"https://i.ytimg.com/vi/{self.id}/hqdefault.jpg"

    @property
    def duree_lisible(self) -> str:
        h, reste = divmod(self.duree_s, 3600)
        m, s = divmod(reste, 60)
        return f"{h}h{m:02d}" if h else f"{m} min"


# --------------------------------------------------------------------------- #
# Persistance
# --------------------------------------------------------------------------- #

def charger_json(nom: str, defaut):
    chemin = ETAT / nom
    if chemin.exists():
        return json.loads(chemin.read_text(encoding="utf-8"))
    return defaut


def ecrire_json(nom: str, donnees) -> None:
    ETAT.mkdir(parents=True, exist_ok=True)
    (ETAT / nom).write_text(
        json.dumps(donnees, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Accès API
# --------------------------------------------------------------------------- #

class QuotaEpuise(RuntimeError):
    """Plus de budget. Ce n'est pas une erreur fatale : on publie ce qu'on a."""


class Api:
    def __init__(self, cle: str):
        self.cle = cle
        self.session = requests.Session()
        self.unites = 0
        self.recherches = 0     # unités dépensées en repli de résolution

    def budget_restant(self) -> int:
        return max(0, PLAFOND_QUOTA - self.unites)

    def get(self, ressource: str, cout: int = 1, **params):
        if self.unites + cout > PLAFOND_QUOTA:
            raise QuotaEpuise(
                f"plafond interne atteint ({PLAFOND_QUOTA} unités)"
            )
        params["key"] = self.cle
        r = self.session.get(f"{API}/{ressource}", params=params, timeout=30)
        self.unites += cout
        if ressource == "search":
            self.recherches += cout
        if r.status_code == 403:
            détail = r.text[:300]
            if "quota" in détail.lower():
                raise QuotaEpuise("403 renvoyé par YouTube : quota épuisé")
            raise SystemExit(
                "API refusée (403). L'API YouTube Data v3 n'est probablement "
                "pas activée sur le projet Google Cloud.\n" + détail
            )
        r.raise_for_status()
        return r.json()


def normaliser(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if c.isalnum())


def chercher_chaine(api: Api, handle: str) -> dict | None:
    """Repli quand le handle est faux : recherche par nom.

    Coûte 100 unités, mais une seule fois par handle grâce au cache. On exige
    une ressemblance forte entre le nom trouvé et le handle demandé, sinon on
    préfère ne rien lier — une mauvaise chaîne liée en silence serait pire
    qu'une chaîne manquante.
    """
    requete = re.sub(r"[-_]", " ", handle.lstrip("@"))
    requete = re.sub(r"(?i)\b(officiel|official|channel|tv)\b$", "", requete).strip()
    rep = api.get(
        "search", cout=100, part="snippet", type="channel", q=requete, maxResults=5
    )
    cible = normaliser(requete)
    meilleur, meilleur_score = None, 0.0
    for it in rep.get("items", []):
        sn = it["snippet"]
        nom = sn.get("title", "")
        cid = sn.get("channelId") or it.get("id", {}).get("channelId")
        if not cid:
            continue
        score = SequenceMatcher(None, cible, normaliser(nom)).ratio()
        if score > meilleur_score:
            meilleur, meilleur_score = {"id": cid, "nom": nom}, score
    if meilleur and meilleur_score >= 0.70:
        meilleur["resolu_par_recherche"] = True
        meilleur["ressemblance"] = round(meilleur_score, 2)
        return meilleur
    return None


def resoudre_handles(api: Api, chaines: list[dict]):
    """@handle -> channel_id. Résolu une fois, puis mis en cache.

    Les échecs sont eux aussi mis en cache, avec leur date : sans ça, chaque
    handle mort relancerait une recherche à 100 unités tous les jours.
    """
    cache = charger_json("channels.json", {})
    maintenant = datetime.now(timezone.utc)
    echecs, secours, reportes = [], [], []

    for c in chaines:
        h = c["handle"]
        entree = cache.get(h)

        if entree and entree.get("id"):
            continue
        if entree and entree.get("echec"):
            depuis = (maintenant - datetime.fromisoformat(entree["date"])).days
            if depuis < 30:          # on ne réessaie qu'une fois par mois
                echecs.append(h)
                continue

        rep = api.get("channels", part="snippet", forHandle=h.lstrip("@"))
        items = rep.get("items") or []
        if items:
            cache[h] = {"id": items[0]["id"], "nom": items[0]["snippet"]["title"]}
            continue

        # Le repli coûte 100 unités : on le rationne. Ce qui dépasse est
        # reporté au lendemain plutôt que marqué en échec — sinon un simple
        # manque de budget condamnerait la chaîne pour 30 jours.
        if api.recherches + 100 > BUDGET_RECHERCHE:
            reportes.append(h)
            continue
        try:
            trouve = chercher_chaine(api, h)
        except QuotaEpuise:
            reportes.append(h)
            continue
        if trouve:
            cache[h] = trouve
            secours.append((h, trouve["nom"], trouve["ressemblance"]))
        else:
            cache[h] = {"echec": True, "date": maintenant.isoformat()}
            echecs.append(h)

    ecrire_json("channels.json", cache)
    return cache, echecs, secours, reportes


# YouTube refuse les requêtes sans en-tête de navigateur, et se méfie des
# rafales venant d'une IP de centre de données — ce qui est exactement le cas
# d'un runner GitHub. Sans ça, les flux reviennent vides sans le moindre signe.
ENTETES_RSS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,en;q=0.8",
}
SESSION_RSS = requests.Session()


def lire_rss(chaine_id: str, essais: int = 3) -> tuple[list[dict], str | None]:
    """Flux public : pas de clé, pas de quota.

    Renvoie (vidéos, erreur). L'erreur n'est JAMAIS avalée : une collecte qui
    échoue en silence produit une grille vide sans rien dire, et on cherche le
    bug pendant des heures du mauvais côté.
    """
    derniere = None
    for tentative in range(essais):
        try:
            r = SESSION_RSS.get(
                RSS.format(chaine_id), headers=ENTETES_RSS, timeout=20
            )
        except Exception as e:
            derniere = f"réseau {type(e).__name__}"
            time.sleep(1.5 * (tentative + 1) + random.random())
            continue

        if r.status_code == 200:
            try:
                racine = ET.fromstring(r.content)
            except ET.ParseError:
                return [], "XML illisible"
            break

        # 429 = trop de requêtes, 5xx = incident passager : on réessaie.
        if r.status_code in (429, 500, 502, 503, 504):
            derniere = f"HTTP {r.status_code}"
            time.sleep(2.0 * (tentative + 1) + random.random() * 1.5)
            continue
        return [], f"HTTP {r.status_code}"
    else:
        return [], derniere or "épuisé"

    out = []
    for e in racine.findall("atom:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        titre = e.findtext("atom:title", namespaces=NS)
        publie = e.findtext("atom:published", namespaces=NS)
        if not (vid and publie):
            continue
        out.append(
            {
                "id": vid,
                "titre": titre or "",
                "publie_le": datetime.fromisoformat(publie.replace("Z", "+00:00")),
            }
        )
    return out, None


def parser_duree_iso(d: str) -> int:
    """PT1H2M3S -> secondes."""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    j, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return j * 86400 + h * 3600 + mi * 60 + s


def enrichir(api: Api, ids: list[str]) -> dict[str, dict]:
    """1 unité pour 50 vidéos. C'est ici qu'on récupère la durée."""
    infos = {}
    for i in range(0, len(ids), 50):
        lot = ids[i : i + 50]
        if api.budget_restant() < 1:
            print(f"⚠ budget épuisé : {len(ids) - i} vidéos non enrichies",
                  file=sys.stderr)
            break
        rep = api.get(
            "videos",
            part="contentDetails,statistics,snippet",
            id=",".join(lot),
            maxResults=50,
        )
        for it in rep.get("items", []):
            st, sn, cd = it.get("statistics", {}), it["snippet"], it["contentDetails"]
            infos[it["id"]] = {
                "duree_s": parser_duree_iso(cd.get("duration", "")),
                "vues": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "description": sn.get("description", "")[:600],
                "titre": sn.get("title", ""),
                "chaine_nom": sn.get("channelTitle", ""),
            }
    return infos


# --------------------------------------------------------------------------- #
# Scoring — uniquement des signaux objectifs
# --------------------------------------------------------------------------- #

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‼⁉]"
)


def taux_racolage(titre: str) -> float:
    """Mesure objective : capitales, ponctuation redoublée, emojis, superlatifs.

    Ce n'est pas un jugement de valeur sur le contenu, seulement sur la
    typographie du titre — un signal mesurable et reproductible.
    """
    if not titre:
        return 0.0
    signaux = []

    # Capitales : une majuscule en début de mot est normale. On ne compte que
    # l'excès au-delà de la moitié du titre, sinon tout titre bien écrit sonne
    # comme du racolage.
    lettres = [c for c in titre if c.isalpha()]
    if len(lettres) >= 12:
        part = sum(c.isupper() for c in lettres) / len(lettres)
        signaux.append(max(0.0, (part - 0.5) / 0.5) if part > 0.5 else 0.0)

    # Ponctuation : un seul « ? » est une question légitime. On compte à partir
    # de deux marques.
    marques = titre.count("!") + titre.count("?")
    signaux.append(min(1.0, (marques - 1) / 2) if marques >= 2 else 0.0)

    signaux.append(min(1.0, len(EMOJI.findall(titre))))

    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", titre.lower())
        if unicodedata.category(c) != "Mn"
    )
    motifs = (
        "vous ne croirez", "incroyable", "choquant", "shocking", "you won't believe",
        "gone wrong", "insane", "enfin la verite", "personne n'en parle",
    )
    signaux.append(1.0 if any(m in sans_accent for m in motifs) else 0.0)

    return min(1.0, sum(signaux) / 2.0)


def fraction_attendue(age_jours: float) -> float:
    """Part des vues de la 3e semaine déjà accumulée à un âge donné.

    Les vues sont très front-loaded : sans cette normalisation, une vidéo de
    la veille paraîtrait toujours en échec face à une vidéo de deux semaines.
    """
    return max(0.15, 1.0 - math.exp(-age_jours / 4.0))


def calculer_reference(videos: list[Video]) -> float:
    """Vues médianes d'une chaîne sur ses vidéos matures — sa 'vitesse de croisière'."""
    mures = [v.vues for v in videos if v.age_jours > 30 and v.vues > 0]
    if len(mures) < 3:
        mures = [v.vues for v in videos if v.vues > 0]
    return statistics.median(mures) if mures else 0.0


def noter(v: Video, reference: float) -> None:
    attendu = reference * fraction_attendue(v.age_jours)
    surperf = (v.vues / attendu) if attendu > 0 else 1.0
    s_surperf = max(0.0, min(1.0, math.log1p(surperf) / math.log(4)))

    ratio = (v.likes / v.vues) if v.vues else 0.0
    s_reception = max(0.0, min(1.0, ratio / 0.05))

    s_fraicheur = math.exp(-v.age_jours / 7.0)

    base = (
        POIDS["surperformance"] * s_surperf
        + POIDS["reception"] * s_reception
        + POIDS["fraicheur"] * s_fraicheur
    )
    racolage = taux_racolage(v.titre)
    v.score = base * (1 - PENALITE_RACOLAGE * racolage)
    v.detail = {
        "surperformance": round(surperf, 2),
        "reception_pct": round(ratio * 100, 2),
        "age_jours": round(v.age_jours, 1),
        "racolage": round(racolage, 2),
        "score_brut": round(base, 3),
    }


def creneau_de(duree_s: int) -> str | None:
    minutes = duree_s / 60
    for cle, _, bas, haut in CRENEAUX:
        if bas <= minutes < haut:
            return cle
    return None


# --------------------------------------------------------------------------- #
# Sélection
# --------------------------------------------------------------------------- #

def selectionner(candidats: list[Video], histo_videos: dict, histo_chaines: dict):
    """Une case = une vidéo. Une chaîne n'apparaît qu'une fois dans la grille."""
    maintenant = datetime.now(timezone.utc)

    def penalite_chaine(cid: str) -> float:
        vu = histo_chaines.get(cid)
        if not vu:
            return 0.0
        jours = (maintenant - datetime.fromisoformat(vu)).total_seconds() / 86400
        if jours >= QUARANTAINE_CHAINE_JOURS:
            return 0.0
        return PENALITE_REDONDANCE * (1 - jours / QUARANTAINE_CHAINE_JOURS)

    retenus = [v for v in candidats if v.id not in histo_videos]
    for v in retenus:
        v.score *= 1 - penalite_chaine(v.chaine_id)

    grille, chaines_utilisees = {}, set()
    cases = cellules()

    # Les cases les plus contraintes d'abord : sinon les créneaux rares se
    # retrouvent vides parce qu'une case facile a raflé la seule chaîne dispo.
    def rarete(cellule):
        inten, cren = cellule
        return sum(
            1
            for v in retenus
            if v.intention == inten
            and creneau_de(v.duree_s) == cren
            and v.age_jours <= FENETRE_JOURS
        )

    for inten, cren in sorted(cases, key=rarete):
        pool = [
            v
            for v in retenus
            if v.intention == inten
            and creneau_de(v.duree_s) == cren
            and v.chaine_id not in chaines_utilisees
        ]
        if not pool:
            continue
        # Priorité absolue à la fenêtre courte. On n'élargit que si la case
        # serait vide sans ça.
        frais = [v for v in pool if v.age_jours <= FENETRE_JOURS]
        choix = frais or pool
        gagnant = max(choix, key=lambda v: v.score)
        gagnant.rattrapage = not frais
        grille[(inten, cren)] = gagnant
        chaines_utilisees.add(gagnant.chaine_id)

    return grille


def construire_vivier(candidats: list[Video], profondeur: int = 12) -> dict:
    """Exporte plusieurs candidats par case, pas seulement le gagnant.

    C'est ce qui permet à la page de recomposer la grille selon les réglages de
    l'utilisateur — langues, chaînes masquées, historique local — sans refaire
    un seul appel à l'API. Le vivier n'est PAS filtré par l'historique serveur :
    sinon un utilisateur qui ne regarde que le français se verrait brûler la
    moitié du vivier par des choix qu'il n'a jamais vus.
    """
    vivier = {}
    for inten, cren in cellules():
            lot = sorted(
                (
                    v for v in candidats
                    if v.intention == inten and creneau_de(v.duree_s) == cren
                ),
                key=lambda v: v.score,
                reverse=True,
            )[:profondeur]
            if lot:
                vivier[f"{inten}|{cren}"] = [
                    {
                        "id": v.id,
                        "titre": v.titre,
                        "chaine_id": v.chaine_id,
                        "chaine_nom": v.chaine_nom,
                        "langue": v.langue,
                        "duree_s": v.duree_s,
                        "vues": v.vues,
                        "age": round(v.age_jours, 1),
                        "surperf": v.detail.get("surperformance", 1.0),
                        "rattrapage": v.age_jours > FENETRE_JOURS,
                    }
                    for v in lot
                ]
    return vivier


def construire_donnees(candidats: list[Video], chaines_actives: list[dict]) -> dict:
    return {
        "genere_le": datetime.now(timezone.utc).date().isoformat(),
        "intentions": [
            {"cle": c, "libelle": l, "desc": d, "creneaux": cr}
            for c, l, d, cr in INTENTIONS
        ],
        "creneaux": [
            {"cle": c, "libelle": l, "min": bas, "max": haut}
            for c, l, bas, haut in CRENEAUX
        ],
        "chaines": chaines_actives,
        "vivier": construire_vivier(candidats),
    }


def diagnostic(candidats: list[Video]) -> str:
    """Où sont les trous ? Sans ça, on corrige à l'aveugle."""
    lignes = [
        "  intention           " + "".join(f"{c[1]:>12}" for c in CRENEAUX),
    ]
    for cle, libelle, _desc, servis in INTENTIONS:
        cases = []
        for c_cle, *_ in CRENEAUX:
            if c_cle not in servis:
                cases.append("       .   ")
                continue
            recents = sum(
                1
                for v in candidats
                if v.intention == cle
                and creneau_de(v.duree_s) == c_cle
                and v.age_jours <= FENETRE_JOURS
            )
            total = sum(
                1
                for v in candidats
                if v.intention == cle and creneau_de(v.duree_s) == c_cle
            )
            cases.append(f"{recents:>7} /{total:>3}")
        lignes.append(f"  {libelle:<20}" + "".join(f"{c:>12}" for c in cases))
    lignes.append("  (récents dans la fenêtre / total avec rattrapage)")
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #

GABARIT = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Menu TV — {date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho+B1:wght@400;600;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&family=IBM+Plex+Mono:wght@400&display=swap">
<link rel="stylesheet" href="k-taisho.css">
<link rel="stylesheet" href="theme.css">
<script>
/* Appliqué AVANT le premier rendu : sinon la page s'affiche une fraction de
   seconde dans le mauvais mode. C'est la seule raison d'un script en tête. */
(function(){{try{{var t=localStorage.getItem("menu-tv:theme");
if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);}}catch(e){{}}}})();
</script>
</head><body class="af">
<header class="af-entete">
  <div class="af-entete__date">{date_longue}</div>
  <h1 class="af-entete__titre">Menu TV</h1>
  <p class="af-entete__chapo"><span data-zone="compte">{nb}</span> propositions pour
     aujourd'hui, une par case. Rien de plus. Ce qui a déjà été proposé ne reviendra
     pas avant {memoire} jours.</p>
  <div class="af-barre af-barre--temps" data-zone="temps"></div>
  <div class="af-barre">
    <button type="button" class="af-bouton af-bascule" data-role="basculer-theme"
            aria-live="polite"><span data-zone="theme-libelle">Auto</span></button>
    <button type="button" class="af-bouton" data-role="basculer-panneau"
            aria-expanded="false" aria-controls="panneau">Réglages</button>
    <span class="af-annonce" role="status" aria-live="polite" data-zone="annonce"></span>
  </div>
  <div class="af-panneau" id="panneau" data-zone="panneau" hidden></div>
</header>
<main data-zone="grille">
{sections}
</main>
<footer class="af-pied">
  {nb_candidats} vidéos examinées sur {nb_chaines} chaînes · {unites} unités de quota
  consommées · sélection sur signaux objectifs — sur-performance relative à la chaîne,
  réception, fraîcheur — sans intervention éditoriale.
  Les réglages et l'historique restent dans ce navigateur, rien n'est envoyé nulle part.
</footer>
<script id="mt-donnees" type="application/json">{donnees}</script>
<script src="app.js" defer></script>
</body></html>
"""


def decouper_duree(v: "Video") -> str:
    """Sépare le nombre de son unité : l'unité est composée plus petite."""
    h, reste = divmod(v.duree_s, 3600)
    m, _ = divmod(reste, 60)
    if h:
        return f'{h}<small>H</small>{m:02d}'
    return f'{m}<small>MIN</small>'


def rendre(grille: dict, stats: dict, donnees: dict) -> str:
    maintenant = datetime.now()
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    date_longue = (
        f"{jours[maintenant.weekday()]} {maintenant.day} {mois[maintenant.month - 1]}"
    )

    sections = []
    for rang, (cle, libelle, desc, servis) in enumerate(INTENTIONS, start=1):
        fiches = []
        for c_cle, c_lib, bas, haut in CRENEAUX:
            if c_cle not in servis:
                continue
            v = grille.get((cle, c_cle))
            if not v:
                fiches.append(
                    f'<div class="af-vide">'
                    f'<span class="af-vide__creneau">{c_lib}</span>'
                    f"<span>rien de neuf</span></div>"
                )
                continue

            d = v.detail
            puces = [
                f'<span class="af-puce'
                f'{" af-puce--fort" if d["surperformance"] >= 1.5 else ""}">'
                f'×{d["surperformance"]:.1f} vs sa moyenne</span>',
                '<span class="af-puce">'
                + f'{v.vues:,}'.replace(",", " ")
                + " vues</span>",
                f'<span class="af-puce">{d["age_jours"]:.0f} j</span>',
            ]
            if v.rattrapage:
                puces.append('<span class="af-puce af-puce--faible">rattrapage</span>')

            # Le titre garde sa casse d'origine : les métadonnées YouTube doivent
            # être affichées non modifiées. Les capitales sont réservées à nos
            # propres libellés.
            fiches.append(
                f'<article class="af-fiche" data-video="{v.id}">'
                f'<a class="af-fiche__lien" href="{v.url}" target="_blank"'
                f' rel="noopener" data-role="ouvrir">'
                f'<div class="af-fiche__image">'
                f'<img src="{v.miniature}" alt="" loading="lazy">'
                f'<span class="af-fiche__creneau">{c_lib}</span>'
                f'<span class="af-fiche__duree">{decouper_duree(v)}</span>'
                f"</div>"
                f'<div class="af-fiche__corps">'
                f'<h3 class="af-fiche__titre">{escape(v.titre)}</h3>'
                f'<div class="af-fiche__source">{escape(v.chaine_nom)}</div>'
                f"</div></a>"
                f'<div class="af-fiche__pied">'
                f'<div class="af-fiche__signaux">{"".join(puces)}</div>'
                f'<button type="button" class="af-bouton" data-role="reporter">'
                f"Demain →</button>"
                f"</div></article>"
            )

        sections.append(
            f'<section class="af-section af-section--{rang}">'
            f'<div class="af-section__bandeau">'
            f'<span class="af-section__numero">{rang:02d}</span>'
            f'<h2 class="af-section__nom">{libelle}</h2>'
            f'<span class="af-section__sous">{desc}</span>'
            f"</div>"
            f'<div class="af-section__corps">'
            f'<div class="af-grille">{"".join(fiches)}</div>'
            f"</div></section>"
        )

    return GABARIT.format(
        date=maintenant.strftime("%Y-%m-%d"),
        date_longue=date_longue,
        nb=len(grille),
        memoire=MEMOIRE_JOURS,
        sections="\n".join(sections),
        donnees=json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
                   .replace("</", "<\\/"),
        **stats,
    )


def escape(t: str) -> str:
    return (
        t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------- #
# Mode démo — données synthétiques, pour valider l'algo sans réseau
# --------------------------------------------------------------------------- #

def fabriquer_demo(chaines: list[dict]) -> list[Video]:
    rng = random.Random(20260731)
    exemples = {
        "comprendre": ["Pourquoi le cuivre conduit mieux que l'or",
                       "La démonstration qui a mis 300 ans",
                       "Ce que révèle vraiment un spectre",
                       "Le problème des trois corps, sans équations"],
        "monde": ["Qui contrôle vraiment le détroit d'Ormuz",
                  "L'économie du café en quatre chiffres",
                  "Pourquoi les ports européens saturent",
                  "Le vrai coût d'un barrage"],
        "oeuvres": ["Ce que Kubrick a coupé au montage",
                    "Pourquoi cet accord sonne triste",
                    "Le plan-séquence qui a tout changé",
                    "Relire Duras à trente ans"],
        "recit": ["Rome avant l'Empire : la république qui s'effondre",
                  "L'histoire absurde d'un parc abandonné",
                  "Le naufrage dont personne ne parle",
                  "Six mois pour retrouver un tableau"],
        "faire": ["Réparer un moteur pas à pas",
                  "La cuisson basse température, enfin claire",
                  "Construire son serveur en 40 minutes",
                  "Affûter correctement un couteau"],
        "regarder": ["Une éruption filmée à 10 000 images/seconde",
                     "Six mois dans une forêt primaire",
                     "Le vol du martinet, image par image",
                     "Fabriquer une hache à partir de rien"],
        "ecouter": ["Trois heures avec un climatologue",
                    "Ce que quarante ans de terrain lui ont appris",
                    "Entretien : sortir du récit dominant",
                    "La conversation qu'on n'attendait pas"],
        "jouer": ["Le jeu qui a inventé un genre",
                  "Pourquoi ce niveau est mal conçu",
                  "Vingt ans après, on y rejoue",
                  "La partie la plus longue de ma vie"],
        "bouger": ["Ce que ton dos essaie de te dire",
                   "Courir sans se blesser, vraiment",
                   "La descente filmée en une prise",
                   "Trente minutes, sans matériel"],
        "rire": ["J'ai visité le pays le plus étrange d'Europe",
                 "Pourquoi les grille-pain sont mal conçus",
                 "On a refait la pub la plus nulle de 2003",
                 "48 h dans un train de nuit"],
    }
    videos = []
    for c in chaines:
        base = rng.randint(30_000, 900_000)
        for k in range(rng.randint(4, 9)):
            age = rng.uniform(0.3, 40)
            duree = rng.choice([rng.randint(300, 700), rng.randint(750, 1750),
                                rng.randint(1850, 4400), rng.randint(4600, 9000)])
            perf = rng.choice([0.5, 0.7, 0.9, 1.0, 1.1, 1.4, 2.2, 3.1])
            vues = int(base * perf * fraction_attendue(age))
            titre = rng.choice(exemples[c["intention"]])
            if rng.random() < 0.12:
                titre = titre.upper() + " !!"
            videos.append(
                Video(
                    id=f"demo{abs(hash(c['handle'] + str(k))) % 10**8:08d}",
                    titre=titre,
                    chaine_id=c["handle"],
                    chaine_nom=c["handle"].lstrip("@"),
                    publie_le=datetime.now(timezone.utc) - timedelta(days=age),
                    duree_s=duree,
                    vues=vues,
                    likes=int(vues * rng.uniform(0.01, 0.06)),
                    intention=c["intention"],
                    langue=c["langue"],
                )
            )
    return videos


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="données synthétiques, sans réseau")
    ap.add_argument("--dry-run", action="store_true", help="n'écrit pas l'historique")
    ap.add_argument("--langues", default="fr,en")
    args = ap.parse_args()

    config = yaml.safe_load((RACINE / "channels.yaml").read_text(encoding="utf-8"))
    chaines = config["chaines"]
    langues = set(args.langues.split(","))
    chaines = [c for c in chaines if c["langue"] in langues]

    histo_videos = charger_json("history.json", {})
    histo_chaines = charger_json("channels_seen.json", {})
    unites = 0

    # Annuaire exporté vers la page : c'est lui qui alimente la liste des
    # créateurs dans le panneau de réglages.
    chaines_exportees = []

    if args.demo:
        toutes = fabriquer_demo(chaines)
        chaines_exportees = [
            {"id": c["handle"], "nom": c["handle"].lstrip("@"),
             "handle": c["handle"], "intention": c["intention"], "langue": c["langue"]}
            for c in chaines
        ]
        print(f"[démo] {len(toutes)} vidéos synthétiques sur {len(chaines)} chaînes")
    else:
        cle = os.environ.get("YT_API_KEY")
        if not cle:
            sys.exit("YT_API_KEY manquante. Voir le README.")
        api = Api(cle)

        try:
            cache, echecs, secours, reportes = resoudre_handles(api, chaines)
        except QuotaEpuise as e:
            print(f"⚠ {e} pendant la résolution — on continue avec le cache",
                  file=sys.stderr)
            cache = charger_json("channels.json", {})
            echecs, secours, reportes = [], [], []
        if secours:
            print("↻ résolus par recherche — À VÉRIFIER :")
            for h, nom, r in secours:
                print(f"    {h:<28} → « {nom} »   (ressemblance {r})")
        if echecs:
            print(f"✗ introuvables, à corriger dans channels.yaml ({len(echecs)}) :",
                  file=sys.stderr)
            for h in echecs:
                print(f"    {h}", file=sys.stderr)
        if reportes:
            print(f"⏳ {len(reportes)} handles n'existent pas tels quels. Le repli "
                  f"par recherche est rationné à {BUDGET_RECHERCHE // 100} par run, "
                  f"ils seront retentés demain.")
            print("   Le plus rapide reste de les corriger à la main dans "
                  "channels.yaml :")
            for h in reportes[:15]:
                print(f"    {h}")
            if len(reportes) > 15:
                print(f"    … et {len(reportes) - 15} autres")

        actives = [
            c for c in chaines if cache.get(c["handle"], {}).get("id")
        ]
        print(f"— {len(actives)}/{len(chaines)} chaînes résolues, "
              f"{api.unites} unités consommées à ce stade")
        if len(actives) < len(chaines) * 0.5:
            print("⚠ moins de la moitié des chaînes sont résolues : la grille "
                  "sera très creuse. Corrige channels.yaml avant tout.",
                  file=sys.stderr)
        chaines_exportees = [
            {"id": cache[c["handle"]]["id"], "nom": cache[c["handle"]]["nom"],
             "handle": c["handle"], "intention": c["intention"], "langue": c["langue"]}
            for c in actives
        ]
        # 6 au lieu de 12 : au-delà, YouTube commence à refuser les flux.
        par_chaine, flux_rates = {}, []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futurs = {
                ex.submit(lire_rss, cache[c["handle"]]["id"]): c for c in actives
            }
            for f, c in futurs.items():
                entrees, erreur = f.result()
                if erreur:
                    flux_rates.append((c["handle"], erreur))
                par_chaine[c["handle"]] = (c, entrees)

        lus = len(actives) - len(flux_rates)
        print(f"— flux RSS : {lus}/{len(actives)} lus")
        if flux_rates:
            from collections import Counter
            motifs = Counter(e for _, e in flux_rates)
            print(f"  {len(flux_rates)} échecs : "
                  + ", ".join(f"{m} ×{n}" for m, n in motifs.most_common()),
                  file=sys.stderr)
            for h, e in flux_rates[:8]:
                print(f"    {h} — {e}", file=sys.stderr)
        if lus < len(actives) * 0.5:
            print("⚠ plus de la moitié des flux ont échoué : la grille sera "
                  "creuse, et ce n'est PAS un problème de channels.yaml.",
                  file=sys.stderr)

        toutes, a_enrichir = [], []
        for handle, (c, entrees) in par_chaine.items():
            for e in entrees:
                v = Video(
                    id=e["id"], titre=e["titre"],
                    chaine_id=cache[handle]["id"], chaine_nom=cache[handle]["nom"],
                    publie_le=e["publie_le"],
                    intention=c["intention"], langue=c["langue"],
                )
                toutes.append(v)
                a_enrichir.append(v.id)

        try:
            infos = enrichir(api, a_enrichir)
        except QuotaEpuise as e:
            print(f"⚠ {e} pendant l'enrichissement — page partielle",
                  file=sys.stderr)
            infos = {}
        for v in toutes:
            i = infos.get(v.id)
            if i:
                v.duree_s, v.vues, v.likes = i["duree_s"], i["vues"], i["likes"]
                v.description = i["description"]
                v.titre = i["titre"] or v.titre
        unites = api.unites
        print(f"{len(toutes)} vidéos collectées · {unites} unités de quota")

    # Référence par chaîne, puis notation
    par_cid = {}
    for v in toutes:
        par_cid.setdefault(v.chaine_id, []).append(v)
    for cid, lot in par_cid.items():
        ref = calculer_reference(lot)
        for v in lot:
            noter(v, ref)

    candidats = [
        v for v in toutes
        if v.age_jours <= FENETRE_SECOURS
        and v.duree_s >= DUREE_MIN_SECONDES
        and creneau_de(v.duree_s)
    ]
    recents = sum(1 for v in candidats if v.age_jours <= FENETRE_JOURS)
    ecartees = len(toutes) - len(candidats)
    print(
        f"{len(candidats)} candidats retenus ({recents} dans la fenêtre de "
        f"{FENETRE_JOURS} j, le reste en rattrapage jusqu'à {FENETRE_SECOURS} j) · "
        f"{ecartees} vidéos écartées (trop vieilles, trop courtes ou Shorts)"
    )
    print(diagnostic(candidats))

    grille = selectionner(candidats, histo_videos, histo_chaines)
    nb_rattrapage = sum(1 for v in grille.values() if v.rattrapage)
    print(
        f"{len(grille)} cases remplies sur {len(cellules())}"
        + (f" (dont {nb_rattrapage} en rattrapage)" if nb_rattrapage else "")
    )

    SORTIE.mkdir(parents=True, exist_ok=True)
    # L'identité est un fichier autonome : on le copie tel quel à côté de la
    # page. Le remplacer suffit à changer l'apparence, sans retoucher ce code.
    for nom in ("k-taisho.css", "theme.css"):
        feuille = RACINE / "theme" / nom
        if feuille.exists():
            (SORTIE / nom).write_text(
                feuille.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:
            print(f"⚠ theme/{nom} introuvable — page sans mise en forme",
                  file=sys.stderr)

    script = RACINE / "app.js"
    if script.exists():
        (SORTIE / "app.js").write_text(
            script.read_text(encoding="utf-8"), encoding="utf-8"
        )

    donnees = construire_donnees(candidats, chaines_exportees)
    html = rendre(grille, {
        "nb_candidats": len(candidats),
        "nb_chaines": len(par_cid),
        "unites": unites,
    }, donnees)
    (SORTIE / "index.html").write_text(html, encoding="utf-8")
    print(f"→ {SORTIE / 'index.html'}")

    if not args.dry_run and not args.demo:
        aujourdhui = datetime.now(timezone.utc).isoformat()
        for v in grille.values():
            histo_videos[v.id] = aujourdhui
            histo_chaines[v.chaine_id] = aujourdhui
        limite = datetime.now(timezone.utc) - timedelta(days=MEMOIRE_JOURS)
        histo_videos = {
            k: d for k, d in histo_videos.items() if datetime.fromisoformat(d) > limite
        }
        ecrire_json("history.json", histo_videos)
        ecrire_json("channels_seen.json", histo_chaines)
        print(f"mémoire : {len(histo_videos)} vidéos déjà proposées")


if __name__ == "__main__":
    main()
