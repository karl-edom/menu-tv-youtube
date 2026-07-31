# Menu TV — YouTube

Une grille fermée de propositions quotidiennes, contre le scrolling infini.

Six intentions × quatre durées = vingt-quatre cases, **une vidéo par case**, jamais
deux fois la même. Pas de liste à parcourir, pas de « voir plus ».

|                        | Café (4-12 min) | Pause (12-30) | Soirée (30-75) | Long cours (75+) |
|------------------------|-----------------|---------------|----------------|------------------|
| Apprendre              | ● | ● | ● | ● |
| Comprendre le monde    | ● | ● | ● | ● |
| S'émerveiller          | ● | ● | ● | ● |
| Se cultiver            | ● | ● | ● | ● |
| Faire                  | ● | ● | ● | ● |
| Se détendre            | ● | ● | ● | ● |

## Comment la sélection fonctionne

Aucun avis éditorial, uniquement des signaux mesurables :

- **Sur-performance relative à la chaîne** (45 %) — la vidéo marche-t-elle mieux que
  ce que cette chaîne fait d'habitude ? C'est le signal central : il compare une
  chaîne à elle-même, donc une chaîne de 40 000 abonnés peut battre une chaîne de
  10 millions. Les vues sont normalisées par l'âge, parce qu'elles s'accumulent
  très vite au début puis stagnent.
- **Réception** (25 %) — ratio likes / vues.
- **Fraîcheur** (30 %) — décroissance exponentielle, demi-vie d'une semaine.

Puis deux pénalités :

- **Racolage** — mesuré sur la typographie du titre seulement : excès de capitales
  au-delà de la moitié du titre, ponctuation redoublée, emojis, formules types.
  C'est un critère objectif, pas un jugement sur le contenu.
- **Redondance** — une chaîne déjà proposée dans les 10 derniers jours est
  fortement pénalisée, et n'apparaît jamais deux fois dans la même grille.

Et une exclusion stricte : **une vidéo déjà proposée ne revient pas avant 240 jours.**
C'est la règle qui répond au « cerveau qui redit non ». L'historique vit dans
`state/history.json`, versionné par git.

## Architecture

```
flux RSS publics  ──►  enrichissement API  ──►  score  ──►  grille  ──►  page HTML
  (gratuit,             (durée, vues, likes)                            (statique)
   sans quota)          ~50 unités/jour
```

La découverte passe par les flux RSS publics de chaque chaîne
(`youtube.com/feeds/videos.xml?channel_id=…`) : pas de clé, pas de quota, pas de
limite. L'API officielle n'intervient que pour ce que le RSS ne donne pas — la
durée surtout, qui détermine le créneau. À 1 unité pour 50 vidéos, on consomme
environ 50 unités par jour sur les 10 000 disponibles. Le quota n'est jamais un
sujet, à condition de **ne jamais utiliser `search.list`** (100 unités l'appel).

Tout tourne sur GitHub Actions : pas de serveur, pas de coût.

## Installation

**1. La clé API** — [console.cloud.google.com](https://console.cloud.google.com/) →
nouveau projet → *APIs & Services* → activer **YouTube Data API v3** → *Credentials*
→ *Create credentials* → *API key*. Gratuit, aucune carte bancaire.

**2. Le dépôt** — pousse ce dossier sur GitHub, puis :

- *Settings → Secrets and variables → Actions* → nouveau secret `YT_API_KEY`
- *Settings → Pages* → Source : **GitHub Actions**
- *Actions* → « Menu TV quotidien » → **Run workflow** pour le premier tir

Le menu est ensuite publié chaque matin sur `https://<toi>.github.io/<dépôt>/`.

**3. En local**, pour essayer :

```bash
pip install requests pyyaml
export YT_API_KEY="..."
python menu_tv.py --dry-run      # génère public/index.html sans toucher la mémoire
python menu_tv.py --demo         # données synthétiques, sans réseau ni clé
```

## À régler

`channels.yaml` est le seul fichier à maintenir. Chaque ligne : un handle, une
intention, une langue. La liste fournie est **une proposition de départ**, pas une
recommandation — corrige-la, c'est elle qui détermine tout le reste.

Au premier run, les handles introuvables sont signalés dans le log : corrige-les
dans `channels.yaml`, jamais dans `state/channels.json` qui est un cache.

Les réglages se trouvent en haut de `menu_tv.py` : bornes des créneaux, fenêtre de
candidature, durée de quarantaine, poids du score.

## Ce qui n'est pas encore fait

- Le treemap de réglage (pondération fine des thématiques sous chaque intention).
- La détection de langue réelle de la vidéo — pour l'instant on se fie à la langue
  déclarée de la chaîne dans `channels.yaml`.
- Un bouton « pas ce soir » qui pousse une vidéo dans l'historique sans l'avoir vue.

## Conformité

L'ouverture d'une vidéo renvoie vers YouTube (`watch?v=…`), donc l'app YouTube si
elle est installée. Titres et miniatures sont affichés sans modification. Ces deux
points sont exigés par les *YouTube API Services Developer Policies*, qui
interdisent par ailleurs de cloner l'expérience YouTube sans valeur ajoutée
indépendante — ici, la contrainte de rareté et la sélection sur sur-performance
relative.
