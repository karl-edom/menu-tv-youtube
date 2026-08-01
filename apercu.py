#!/usr/bin/env python3
"""
Fabrique un aperçu autonome : une seule page HTML, CSS et JS embarqués.

    python3 apercu.py [sortie.html]

À n'utiliser que pour montrer le résultat hors ligne. Le site publié, lui,
charge les fichiers séparément — c'est plus léger et ça se met en cache.

POURQUOI CE FICHIER EXISTE
Un simple `replace()` sur les balises <link> est un piège : les feuilles de
style contiennent, dans leurs commentaires d'en-tête, des exemples d'usage du
genre `<link rel="stylesheet" href="theme.css">`. Un remplacement naïf frappe
aussi ces occurrences, imbrique un <style> dans un <style>, et le premier
</style> ferme les deux — la page s'affiche alors en texte brut.

On passe donc par des jalons uniques, posés AVANT toute insertion.
"""

import sys
from pathlib import Path

RACINE = Path(__file__).parent
SORTIE = RACINE / "public"

# (balise à remplacer, fichier, ouvrant, fermant)
PIECES = [
    ('<link rel="stylesheet" href="k-taisho.css">', "k-taisho.css", "<style>", "</style>"),
    ('<link rel="stylesheet" href="theme.css">',    "theme.css",    "<style>", "</style>"),
    ('<script src="app.js" defer></script>',        "app.js",       "<script>", "</script>"),
]


def construire() -> str:
    html = (SORTIE / "index.html").read_text(encoding="utf-8")

    # 1. Poser les jalons pendant que le document est encore propre. Une seule
    #    occurrence de chaque balise à ce stade : celle du <head>.
    for i, (balise, *_ ) in enumerate(PIECES):
        if html.count(balise) != 1:
            raise SystemExit(
                f"attendu 1 occurrence de {balise!r}, trouvé {html.count(balise)}"
            )
        html = html.replace(balise, f"@@PIECE{i}@@", 1)

    # 2. Puis seulement injecter. Les contenus peuvent contenir n'importe quoi,
    #    les jalons, eux, sont introuvables ailleurs.
    for i, (_, fichier, ouvrant, fermant) in enumerate(PIECES):
        contenu = (SORTIE / fichier).read_text(encoding="utf-8")
        if fermant in contenu:
            raise SystemExit(f"{fichier} contient {fermant} — inlining impossible")
        html = html.replace(f"@@PIECE{i}@@", f"{ouvrant}\n{contenu}\n{fermant}", 1)

    # 3. Vérifier avant de rendre la copie.
    for ouvrant, fermant, attendu in (("<style>", "</style>", 2), ("<script", "</script>", 3)):
        if html.count(fermant) != attendu:
            raise SystemExit(
                f"{html.count(fermant)} {fermant} au lieu de {attendu} — blocs imbriqués ?"
            )
    if "@@PIECE" in html:
        raise SystemExit("un jalon n'a pas été remplacé")
    return html


if __name__ == "__main__":
    cible = Path(sys.argv[1]) if len(sys.argv) > 1 else RACINE / "apercu.html"
    cible.write_text(construire(), encoding="utf-8")
    print(f"→ {cible}  ({cible.stat().st_size // 1024} Ko)")
