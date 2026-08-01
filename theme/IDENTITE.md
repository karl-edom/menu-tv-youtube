# AFFICHE — système visuel

Identité autonome, réutilisable hors du Menu TV. Un seul fichier à emporter :
`theme.css`. Le HTML qui la consomme ne connaît que des classes `.af-*` ; il
n'y a pas une seule couleur ni une seule taille en dur dans le code applicatif.

## Le principe

Le registre est celui de **l'affichage** — l'affiche de cinéma, le panneau, la
signalétique. Trois partis pris en découlent, et ce sont eux qui font l'identité :

**L'échelle plutôt que l'ornement.** L'écart entre le plus grand et le plus petit
niveau typographique est d'un facteur 8. C'est ce rapport qui produit l'effet, pas
les couleurs. Le réduire tue l'identité plus sûrement que changer la palette.

**L'aplat plutôt que la bordure.** Une catégorie n'est pas signalée par un liseré,
elle est annoncée par un bandeau de couleur pleine sur toute la largeur. C'est
frontal, ça se lit à trois mètres.

**L'angle vif plutôt que l'arrondi.** Rayon zéro partout, ombres portées franches
et sans flou. C'est précisément ce qui sépare l'affiche du tableau de bord — le
coin arrondi et l'ombre floue sont la signature du second.

## Les familles

| Rôle | Famille | Usage |
|---|---|---|
| Display | **Anton** | Titre de page, noms de catégories, durées. Jamais de texte courant : cette graisse est faite pour être lue de loin, pas longtemps. |
| Texte | **Archivo** | Titres d'éléments, corps, étiquettes, métadonnées. |

Chargées depuis Google Fonts avec `display=swap` et une pile de repli système
(Haettenschweiler, Arial Narrow) qui conserve la proportion condensée si le
réseau tombe.

## L'échelle

| Token | Valeur | Rôle |
|---|---|---|
| `--af-t-geant` | 46 → 104 px | Titre de page |
| `--af-t-section` | 32 → 64 px | Nom de catégorie |
| `--af-t-duree` | 24 → 40 px | Durée — information de premier plan |
| `--af-t-titre` | 17 px | Titre d'élément |
| `--af-t-corps` | 14 px | Texte courant |
| `--af-t-label` | 12 px | Étiquettes capitales |
| `--af-t-micro` | 11 px | Signaux |

Les trois premiers sont en `clamp()` : ils respirent avec la largeur de fenêtre
sans média-query. La durée est volontairement traitée comme un chiffre d'affiche
et non comme une métadonnée — c'est par elle qu'on choisit.

## La palette

Dix teintes catégorielles : les huit de la palette de référence, plus deux
ajoutées, dans un ordre trouvé par recherche sous double contrainte.

| Token | Teinte | Encre posée dessus | Ratio |
|---|---|---|---|
| `--af-t1` | `#d95926` orange | noir | 5,09:1 |
| `--af-t2` | `#199e70` aqua | noir | 5,81:1 |
| `--af-t3` | `#a901e6` violet | **blanc** | 5,48:1 |
| `--af-t4` | `#c98500` jaune | noir | 6,44:1 |
| `--af-t5` | `#d55181` magenta | noir | 5,01:1 |
| `--af-t6` | `#8c9510` olive | noir | 6,05:1 |
| `--af-t7` | `#9085e9` lavande | noir | 6,33:1 |
| `--af-t8` | `#008300` vert | **blanc** | 4,95:1 |
| `--af-t9` | `#3987e5` bleu | noir | 5,44:1 |
| `--af-t10` | `#e66767` rouge | noir | 6,12:1 |

L'encre diffère pour deux teintes parce qu'elle est **calculée, pas choisie** :
sur le violet et le vert, le noir n'atteint pas le seuil du petit texte.

Vérifié sur les deux surfaces sombres (`#08080a` et `#101015`) : bande de clarté
OKLCH, plancher de chroma, séparation daltonisme, contraste. Sur les paires
adjacentes, le pire écart tient **9,4 en deutan** (cible 8) et **19,3 en vision
normale** (plancher 15).

Comment cet ordre a été obtenu : les 45 paires ont été mesurées une à une avec le
validateur, puis l'ordre cherché en maximisant la marge la plus faible sur les
deux portes simultanément. Un premier essai qui n'optimisait que le daltonisme
atteignait 11,5 en CVD mais faisait tomber la vision normale à 11,9 — sous le
plancher dur. Les deux contraintes se tiennent ensemble ou pas du tout.

## Les trois règles à ne pas casser

**1. L'ordre des teintes est le mécanisme de séparation daltonisme**, pas un
choix esthétique. Les orderings candidats ont été énumérés et seuls ceux
franchissant chaque seuil sur les paires adjacentes ont été retenus. Réordonner
ou substituer une teinte isolée casse la garantie — il faut revalider l'ensemble.

**2. Aucun petit texte ne porte la teinte.** À 11-12 px le seuil est de 4,5:1 sur
le fond, et la plupart des teintes échouent. Le texte porte un jeton d'encre ;
l'identité passe par un aplat ou un filet, qui sont des marques et non du texte.
Sur un aplat, c'est l'encre associée à la teinte qui s'applique.

**3. L'identité n'est jamais portée par la couleur seule.** Chaque catégorie a
son numéro, son nom écrit en toutes lettres et sa position. La couleur accélère
la lecture, elle ne la conditionne pas.

## Réutiliser ailleurs

Copier `theme.css`, ne modifier que le bloc `:root`. Les classes `.af-*` sont
génériques : `af-section`, `af-fiche`, `af-grille`, `af-puce`, `af-etiquette` ne
parlent pas de vidéos. Pour repartir d'une autre identité, réécrire le fichier en
conservant les mêmes noms de classes — le HTML n'a pas à bouger.

Si tu changes la palette, repasse-la au validateur avant de la déclarer bonne :
bande de clarté, plancher de chroma, ΔE sur paires adjacentes, contraste sur
chaque surface, et contraste de l'encre sur chaque aplat.

## Squelette HTML attendu

```html
<body class="af">
  <header class="af-entete">
    <div class="af-entete__date">…</div>
    <h1 class="af-entete__titre">…</h1>
    <p class="af-entete__chapo">…</p>
  </header>

  <section class="af-section af-section--1">
    <div class="af-section__bandeau">
      <span class="af-section__numero">01</span>
      <h2 class="af-section__nom">…</h2>
      <span class="af-section__sous">…</span>
    </div>
    <div class="af-section__corps">
      <div class="af-grille">
        <a class="af-fiche">
          <div class="af-fiche__image">
            <img …>
            <span class="af-fiche__creneau">…</span>
            <span class="af-fiche__duree">…</span>
          </div>
          <div class="af-fiche__corps">
            <h3 class="af-fiche__titre">…</h3>
            <div class="af-fiche__source">…</div>
            <div class="af-fiche__signaux">
              <span class="af-puce af-puce--fort">…</span>
            </div>
          </div>
        </a>
      </div>
    </div>
  </section>

  <footer class="af-pied">…</footer>
</body>
```
