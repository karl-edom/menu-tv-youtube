# Menu TV — YouTube

Une grille fermée de propositions quotidiennes, contre le scrolling infini.

Six intentions × quatre durées = vingt-quatre cases, **une vidéo par case**, jamais
deux fois la même. Pas de liste à parcourir, pas de « voir plus ».

En ligne : **https://karl-edom.github.io/menu-tv-youtube/**

|                        | Café (3-12 min) | Pause (12-30) | Soirée (30-75) | Long cours (75+) |
|------------------------|-----------------|---------------|----------------|------------------|
| Apprendre              | ● | ● | ● | ● |
| Comprendre le monde    | ● | ● | ● | ● |
| S'émerveiller          | ● | ● | ● | ● |
| Se cultiver            | ● | ● | ● | ● |
| Faire                  | ● | ● | ● | ● |
| Se détendre            | ● | ● | ● | ● |

---

## Publier une modification

**Clic droit sur `publier.ps1` → Exécuter avec PowerShell.**

C'est tout. Le script enregistre tes modifications, récupère ce que le robot a
écrit de son côté, envoie sur GitHub, et t'affiche les liens utiles. Il n'ouvre
jamais d'éditeur de texte et s'arrête en français si quelque chose cloche.

Ensuite, pour régénérer le menu tout de suite sans attendre demain matin :
[Actions](https://github.com/karl-edom/menu-tv-youtube/actions) → *Menu TV
quotidien* → **Run workflow**.

> Les trois mots de git, en clair : **commit** = enregistrer une photo du dossier
> sur ta machine · **push** = l'envoyer sur GitHub · **pull** = récupérer ce que
> GitHub a et que tu n'as pas. Le robot écrit lui aussi dans le dépôt, d'où la
> nécessité du troisième. `publier.ps1` fait les trois dans le bon ordre.

---

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
  Critère objectif, pas un jugement sur le contenu.
- **Redondance** — une chaîne déjà proposée récemment est fortement pénalisée, et
  n'apparaît jamais deux fois dans la même grille.

**Rattrapage** : une case ne se remplit qu'avec une vidéo de moins de 21 jours. Si
aucune n'existe — créneau long, intention peu dotée — la fenêtre s'élargit à 75
jours **pour cette case seulement**, et la vignette porte la mention.

---

## Architecture

```
flux RSS publics  ──►  enrichissement API  ──►  vivier classé  ──►  page
  (gratuit,             (durée, vues, likes)     (9-12 par case)      │
   sans quota)          ~20 unités/jour                              │
                                                                     ▼
                                              le navigateur compose TA grille
                                              (langues, chaînes, historique)
```

La découverte passe par les flux RSS publics de chaque chaîne
(`youtube.com/feeds/videos.xml?channel_id=…`) : pas de clé, pas de quota. L'API
officielle n'intervient que pour ce que le RSS ne donne pas — la durée surtout,
qui détermine le créneau. Mesuré en conditions réelles : **95 unités pour 919
vidéos sur 65 chaînes**, sur les 10 000 disponibles par jour.

**Règle absolue : ne jamais appeler `search.list` dans le job quotidien** — 100
unités l'appel, soit une centaine de requêtes par jour maximum. Seule exception,
le repli de résolution des handles, plafonné par un cache d'échecs de 30 jours.

Le build n'exporte pas une grille figée mais un **vivier** : les 9 à 12 meilleurs
candidats de chaque case, embarqués en JSON dans la page. C'est le navigateur qui
tire la grille selon tes réglages. Conséquence : le serveur ne sait rien de toi, et
le jour où plusieurs personnes utilisent le site, elles partagent un seul index
sans multiplier les appels à l'API.

Tout tourne sur GitHub Actions : pas de serveur, pas de coût.

---

## Les réglages, dans la page

Bouton **Réglages** en haut. Tout reste dans ton navigateur, rien n'est envoyé
nulle part — et donc ton téléphone aura ses propres réglages.

**Langues** — français, anglais, ou les deux. Décocher tout revient à tout
afficher plutôt qu'à vider l'écran.

**Retirer un créateur** — immédiat et réversible. La chaîne disparaît de la grille,
la case se recompose avec le candidat suivant. Rien n'est supprimé du dépôt.

**Ajouter un créateur** — ne peut pas être instantané : les vidéos d'une nouvelle
chaîne doivent être collectées par le build. Le panneau constitue une file
d'attente et génère les lignes exactes à coller dans `channels.yaml`, avec un
bouton pour les copier.

**Demain →** sur chaque fiche — la vidéo quitte la grille du jour, la case se
remplit avec le suivant, et elle revient **à sa place** demain avec la mention
« reportée ». Ce n'est pas une liste où les choses s'empilent et meurent.

---

## `channels.yaml` — le seul fichier à maintenir

Une ligne par chaîne : un handle, une intention, une langue. C'est lui qui
détermine toute la qualité de la grille.

```yaml
  - {handle: "@ScienceEtonnante", intention: apprendre, langue: fr}
```

Intentions possibles : `apprendre`, `monde`, `emerveiller`, `culture`, `faire`,
`detente`. Langues : `fr`, `en`.

### Résolution des handles

Un handle est d'abord cherché tel quel (1 unité). S'il n'existe pas, un repli par
recherche se déclenche (100 unités) et ne lie la chaîne trouvée que si son nom
ressemble à plus de 70 % au handle demandé — une mauvaise chaîne liée en silence
serait pire qu'une chaîne manquante. Ces liaisons apparaissent dans le log sous
**« résolus par recherche — À VÉRIFIER »** : c'est le seul endroit où le programme
devine.

Corrige toujours dans `channels.yaml`, jamais dans `state/channels.json` qui est un
cache. Pour forcer une nouvelle résolution, supprime la ligne correspondante du
cache.

### Lire le log

Chaque run affiche un tableau `intention × créneau` avec le nombre de candidats
récents et le total. C'est là qu'on voit quelles intentions manquent de chaînes —
une case vide n'est pas un bug, c'est un signal.

---

## Installation, depuis zéro

**1. La clé API** — [console.cloud.google.com](https://console.cloud.google.com/) →
nouveau projet → *APIs & Services* → activer **YouTube Data API v3** → *Credentials*
→ *Create credentials* → *API key*. Gratuit, aucune carte bancaire.

**2. Le dépôt** — pousse le dossier sur GitHub, puis :

- *Settings → Secrets and variables → Actions* → nouveau secret `YT_API_KEY`
- *Settings → Pages* → Source : **GitHub Actions**
- *Actions* → « Menu TV quotidien » → **Run workflow** pour le premier tir

**3. En local**, pour essayer sans rien casser :

```bash
pip install requests pyyaml
python menu_tv.py --demo       # données synthétiques, sans réseau ni clé
python menu_tv.py --dry-run    # vrai run, mais n'écrit pas l'historique
```

---

## Où vivent les choses

| Fichier | Rôle |
|---|---|
| `channels.yaml` | La liste des chaînes. Le seul fichier éditorial. |
| `menu_tv.py` | Collecte, score, sélection, rendu. Aucune couleur, aucune taille. |
| `app.js` | La sélection côté navigateur et le panneau de réglages. |
| `theme/theme.css` | **Toute** l'identité visuelle. Autonome, réutilisable ailleurs. |
| `theme/IDENTITE.md` | Ce que signifie chaque token et les règles à ne pas casser. |
| `publier.ps1` | Publier sans taper de git. |
| `state/` | Caches régénérables. Écrits par le robot, pas par toi. |
| `public/` | **N'existe pas dans le dépôt.** Généré à chaque run, envoyé directement à Pages. |

Ce dernier point surprend souvent : le dépôt contient la recette, pas le plat. Le
HTML est recuisiné chaque matin sur une machine jetable louée par GitHub, puis
servi à Pages. Pour le voir tel qu'il est publié : *Actions* → un run → section
**Artifacts** → `github-pages`.

---

## Conformité

L'ouverture d'une vidéo renvoie vers YouTube, donc vers l'application YouTube si
elle est installée. Titres et miniatures sont affichés sans modification — c'est
pourquoi les titres de vidéos ne sont jamais mis en capitales, contrairement aux
libellés du site. Ces points sont exigés par les *YouTube API Services Developer
Policies*, qui interdisent par ailleurs de cloner l'expérience YouTube sans valeur
ajoutée indépendante : ici, la contrainte de rareté et la sélection sur
sur-performance relative.

Aucun lecteur n'est embarqué dans la page. C'est un choix de conformité, qui a
aussi une conséquence économique : la règle interdisant de faire payer l'accès à
un lecteur embarqué ne s'applique pas.

---

## Ce qui n'est pas encore fait

- **Rédac chef automatique** : un job hebdomadaire qui découvre des chaînes, les
  classe et les propose à valider. Prérequis pour des bouquets prêts à l'emploi.
- **Treemap de réglage** : pondération fine des thématiques sous chaque intention.
- **Langue réelle de la vidéo** : `defaultAudioLanguage` arrive dans l'appel qu'on
  fait déjà, donc à coût nul — pour l'instant on se fie à la langue de la chaîne.
- **Synchronisation entre appareils** : demanderait un compte, donc un serveur.
- **Ajout de chaînes par un visiteur** : voir la piste des issues GitHub.
- **Polices auto-hébergées** : aujourd'hui chargées depuis Google Fonts.

## Limites connues

- Les réglages sont liés à un navigateur. Changer d'appareil = repartir de zéro.
- Le vivier pèse ~67 Ko en clair dans la page. Si la liste de chaînes triple, il
  faudra le sortir dans un fichier séparé plutôt que l'embarquer.
- La liste de chaînes fournie au départ n'a pas été validée : certains handles
  sont faux et sont signalés au premier run.
