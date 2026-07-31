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
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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

INTENTIONS = [
    ("apprendre", "Apprendre", "Comprendre un mécanisme, acquérir une notion."),
    ("monde", "Comprendre le monde", "Économie, géopolitique, société, enquête."),
    ("emerveiller", "S'émerveiller", "Nature, espace, exploration, beau geste."),
    ("culture", "Se cultiver", "Histoire, cinéma, musique, littérature, art."),
    ("faire", "Faire", "Technique, cuisine, artisanat, savoir-faire."),
    ("detente", "Se détendre", "Récit, humour, formats sans effort."),
]

# (clé, libellé, borne basse en minutes, borne haute)
CRENEAUX = [
    ("cafe", "Café", 4, 12),
    ("pause", "Pause", 12, 30),
    ("soiree", "Soirée", 30, 75),
    ("long", "Long cours", 75, 100_000),
]

# Fenêtre de candidature : au-delà, une vidéo n'est plus "à l'affiche".
FENETRE_JOURS = 21
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

class Api:
    def __init__(self, cle: str):
        self.cle = cle
        self.session = requests.Session()
        self.unites = 0

    def get(self, ressource: str, cout: int = 1, **params):
        params["key"] = self.cle
        r = self.session.get(f"{API}/{ressource}", params=params, timeout=30)
        self.unites += cout
        if r.status_code == 403:
            raise SystemExit(
                "API refusée (403). Quota épuisé, ou API YouTube Data v3 non "
                "activée sur le projet Google Cloud.\n" + r.text[:400]
            )
        r.raise_for_status()
        return r.json()


def resoudre_handles(api: Api, chaines: list[dict]) -> tuple[dict, list[str]]:
    """@handle -> channel_id. Résolu une seule fois, puis mis en cache."""
    cache = charger_json("channels.json", {})
    echecs = []
    for c in chaines:
        h = c["handle"]
        if h in cache:
            continue
        rep = api.get("channels", part="snippet", forHandle=h.lstrip("@"))
        items = rep.get("items") or []
        if not items:
            echecs.append(h)
            continue
        cache[h] = {"id": items[0]["id"], "nom": items[0]["snippet"]["title"]}
    ecrire_json("channels.json", cache)
    return cache, echecs


def lire_rss(chaine_id: str) -> list[dict]:
    """Flux public : pas de clé, pas de quota. Donne les ~15 dernières vidéos."""
    try:
        r = requests.get(RSS.format(chaine_id), timeout=20)
        if r.status_code != 200:
            return []
        racine = ET.fromstring(r.content)
    except Exception:
        return []

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
    return out


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
    cellules = [(i[0], c[0]) for i in INTENTIONS for c in CRENEAUX]

    # Les cases les plus contraintes d'abord : sinon les créneaux rares se
    # retrouvent vides parce qu'une case facile a raflé la seule chaîne dispo.
    def rarete(cellule):
        inten, cren = cellule
        return sum(
            1 for v in retenus if v.intention == inten and creneau_de(v.duree_s) == cren
        )

    for inten, cren in sorted(cellules, key=rarete):
        pool = [
            v
            for v in retenus
            if v.intention == inten
            and creneau_de(v.duree_s) == cren
            and v.chaine_id not in chaines_utilisees
        ]
        if pool:
            gagnant = max(pool, key=lambda v: v.score)
            grille[(inten, cren)] = gagnant
            chaines_utilisees.add(gagnant.chaine_id)

    return grille


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #

GABARIT = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Menu TV — {date}</title>
<style>
  :root {{
    --fond:#0d0f13; --carte:#161a21; --bord:#242a35;
    --texte:#e8eaee; --doux:#8b93a3; --accent:#d9a441;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--fond); color:var(--texte);
         font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
  header {{ padding:34px 28px 10px; border-bottom:1px solid var(--bord); }}
  h1 {{ margin:0; font-size:26px; letter-spacing:-.02em; font-weight:650; }}
  .date {{ color:var(--accent); font-size:13px; text-transform:uppercase;
           letter-spacing:.14em; margin-bottom:8px; }}
  .chapo {{ color:var(--doux); font-size:14px; margin:8px 0 0; max-width:64ch; }}
  main {{ padding:22px 28px 60px; }}
  .bloc {{ margin-bottom:34px; }}
  .titre-bloc {{ display:flex; align-items:baseline; gap:12px; margin-bottom:4px; }}
  .titre-bloc h2 {{ font-size:17px; margin:0; font-weight:620; }}
  .titre-bloc span {{ color:var(--doux); font-size:13px; }}
  .rangee {{ display:grid; gap:14px; margin-top:12px;
             grid-template-columns:repeat(auto-fill,minmax(255px,1fr)); }}
  .carte {{ background:var(--carte); border:1px solid var(--bord); border-radius:10px;
            overflow:hidden; text-decoration:none; color:inherit; display:flex;
            flex-direction:column; transition:border-color .15s,transform .15s; }}
  .carte:hover {{ border-color:var(--accent); transform:translateY(-2px); }}
  .vignette {{ position:relative; aspect-ratio:16/9; background:#000; }}
  .vignette img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .creneau {{ position:absolute; top:8px; left:8px; background:rgba(13,15,19,.9);
              color:var(--accent); font-size:11px; letter-spacing:.1em;
              text-transform:uppercase; padding:3px 8px; border-radius:4px; }}
  .duree {{ position:absolute; bottom:8px; right:8px; background:rgba(13,15,19,.9);
            font-size:12px; padding:2px 6px; border-radius:4px; }}
  .corps {{ padding:12px 13px 13px; display:flex; flex-direction:column; gap:6px;
            flex:1; }}
  .corps h3 {{ margin:0; font-size:14.5px; line-height:1.35; font-weight:580; }}
  .chaine {{ color:var(--doux); font-size:12.5px; }}
  .signaux {{ margin-top:auto; padding-top:9px; border-top:1px solid var(--bord);
              display:flex; flex-wrap:wrap; gap:5px; }}
  .puce {{ font-size:11px; color:var(--doux); background:#1d222b;
           padding:2px 7px; border-radius:99px; }}
  .puce.fort {{ color:var(--accent); }}
  .vide {{ background:transparent; border:1px dashed var(--bord); border-radius:10px;
           min-height:120px; display:flex; align-items:center; justify-content:center;
           color:#4a5261; font-size:12.5px; text-align:center; padding:16px; }}
  footer {{ padding:20px 28px 40px; color:#5a6272; font-size:12px;
            border-top:1px solid var(--bord); }}
</style></head><body>
<header>
  <div class="date">{date_longue}</div>
  <h1>Menu TV</h1>
  <p class="chapo">{nb} propositions pour aujourd'hui, une par case. Rien de plus.
     Ce qui a déjà été proposé ne reviendra pas avant {memoire} jours.</p>
</header>
<main>
{blocs}
</main>
<footer>
  {nb_candidats} vidéos examinées sur {nb_chaines} chaînes · {unites} unités de quota consommées ·
  sélection sur signaux objectifs (sur-performance relative à la chaîne, réception, fraîcheur),
  sans intervention éditoriale.
</footer>
</body></html>
"""


def rendre(grille: dict, stats: dict) -> str:
    maintenant = datetime.now()
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    date_longue = (
        f"{jours[maintenant.weekday()]} {maintenant.day} {mois[maintenant.month - 1]}"
    )

    libelle_creneau = {c[0]: c[1] for c in CRENEAUX}
    blocs = []
    for cle, libelle, desc in INTENTIONS:
        cartes = []
        for c_cle, c_lib, bas, haut in CRENEAUX:
            v = grille.get((cle, c_cle))
            if not v:
                cartes.append(
                    f'<div class="vide">{c_lib}<br>rien de neuf</div>'
                )
                continue
            d = v.detail
            puces = [
                f'<span class="puce{" fort" if d["surperformance"] >= 1.5 else ""}">'
                f'×{d["surperformance"]:.1f} vs sa moyenne</span>',
                f'<span class="puce">{v.vues:,}'.replace(",", " ") + " vues</span>",
                f'<span class="puce">{d["age_jours"]:.0f} j</span>',
            ]
            cartes.append(
                f"""<a class="carte" href="{v.url}" target="_blank" rel="noopener">
  <div class="vignette">
    <img src="{v.miniature}" alt="" loading="lazy">
    <span class="creneau">{c_lib}</span>
    <span class="duree">{v.duree_lisible}</span>
  </div>
  <div class="corps">
    <h3>{escape(v.titre)}</h3>
    <div class="chaine">{escape(v.chaine_nom)}</div>
    <div class="signaux">{''.join(puces)}</div>
  </div>
</a>"""
            )
        blocs.append(
            f'<section class="bloc"><div class="titre-bloc"><h2>{libelle}</h2>'
            f"<span>{desc}</span></div>"
            f'<div class="rangee">{"".join(cartes)}</div></section>'
        )

    return GABARIT.format(
        date=maintenant.strftime("%Y-%m-%d"),
        date_longue=date_longue,
        nb=len(grille),
        memoire=MEMOIRE_JOURS,
        blocs="\n".join(blocs),
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
        "apprendre": ["Pourquoi le cuivre conduit mieux que l'or", "La démonstration qui a mis 300 ans",
                      "Ce que révèle vraiment un spectre", "Le problème des trois corps, sans équations"],
        "monde": ["Qui contrôle vraiment le détroit d'Ormuz", "L'économie du café en quatre chiffres",
                  "Pourquoi les ports européens saturent", "Le vrai coût d'un barrage"],
        "emerveiller": ["Une éruption filmée à 10 000 images/seconde", "Six mois dans une forêt primaire",
                        "Le vol du martinet, image par image", "Fabriquer une hache à partir de rien"],
        "culture": ["Ce que Kubrick a coupé au montage", "Rome avant l'Empire : la république qui s'effondre",
                    "Pourquoi cet accord sonne triste", "Le plan-séquence qui a tout changé"],
        "faire": ["Réparer un moteur pas à pas", "La cuisson basse température, enfin claire",
                  "Construire son serveur en 40 minutes", "Affûter correctement un couteau"],
        "detente": ["J'ai visité le pays le plus étrange d'Europe", "L'histoire absurde d'un parc abandonné",
                    "Pourquoi les grille-pain sont mal conçus", "48 h dans un train de nuit"],
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

    if args.demo:
        toutes = fabriquer_demo(chaines)
        print(f"[démo] {len(toutes)} vidéos synthétiques sur {len(chaines)} chaînes")
    else:
        cle = os.environ.get("YT_API_KEY")
        if not cle:
            sys.exit("YT_API_KEY manquante. Voir le README.")
        api = Api(cle)

        cache, echecs = resoudre_handles(api, chaines)
        if echecs:
            print(f"⚠ handles non résolus, à corriger dans channels.yaml : {echecs}",
                  file=sys.stderr)

        par_chaine = {}
        with ThreadPoolExecutor(max_workers=12) as ex:
            futurs = {
                ex.submit(lire_rss, cache[c["handle"]]["id"]): c
                for c in chaines if c["handle"] in cache
            }
            for f, c in futurs.items():
                par_chaine[c["handle"]] = (c, f.result())

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

        infos = enrichir(api, a_enrichir)
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
        if v.age_jours <= FENETRE_JOURS
        and v.duree_s >= DUREE_MIN_SECONDES
        and creneau_de(v.duree_s)
    ]
    print(f"{len(candidats)} candidats dans la fenêtre de {FENETRE_JOURS} jours")

    grille = selectionner(candidats, histo_videos, histo_chaines)
    print(f"{len(grille)} cases remplies sur {len(INTENTIONS) * len(CRENEAUX)}")

    SORTIE.mkdir(parents=True, exist_ok=True)
    html = rendre(grille, {
        "nb_candidats": len(candidats),
        "nb_chaines": len(par_cid),
        "unites": unites,
    })
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
