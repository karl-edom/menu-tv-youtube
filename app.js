/* ==========================================================================
   Menu TV — sélection côté navigateur
   --------------------------------------------------------------------------
   Le build quotidien produit un VIVIER classé (plusieurs candidats par case) ;
   c'est cette page qui en tire la grille du jour selon les réglages locaux.

   Conséquence architecturale : le serveur est sans état vis-à-vis de
   l'utilisateur. Langues, chaînes masquées, historique et reports vivent dans
   le navigateur. C'est ce qui permettra plus tard à N utilisateurs de partager
   un seul index sans multiplier les appels à l'API.
   ========================================================================== */

(() => {
  "use strict";

  const CLES = {
    reglages: "menu-tv:reglages",
    historique: "menu-tv:historique",
    reports: "menu-tv:reports",
    souhaits: "menu-tv:souhaits",
    theme: "menu-tv:theme",
    temps: "menu-tv:temps",
    montrees: "menu-tv:montrees",
  };

  /* Une vidéo affichée et non ouverte, c'est un refus. La remontrer demain,
     c'est reproduire exactement ce qu'on reproche aux plateformes.

     Mais l'EXCLURE serait pire : à ~26 vidéos affichées par jour pour un
     vivier de quelques centaines, on viderait la grille en deux semaines.
     On la RÉTROGRADE donc : pendant cette durée elle n'est retenue que s'il
     n'existe aucun candidat jamais montré pour cette case. Avec un vivier
     riche on ne la revoit jamais ; avec un vivier maigre on la revoit plutôt
     que de voir un trou. Le système s'autorégule. */
  const QUARANTAINE_MONTREE_JOURS = 21;

  /* Le temps disponible n'est PAS un réglage : il change à chaque fois qu'on
     ouvre la page. Il est donc mémorisé avec la date du jour et remis à zéro
     le lendemain — sinon on retrouverait « 20 minutes » un dimanche soir. */
  const TEMPS = [
    { minutes: 10,   libelle: "10 min" },
    { minutes: 20,   libelle: "20 min" },
    { minutes: 45,   libelle: "45 min" },
    { minutes: 90,   libelle: "1 h 30" },
    { minutes: null, libelle: "Tout le temps" },
  ];

  /* Trois états, pas deux : « auto » suit le système et doit rester
     joignable, sinon quelqu'un qui bascule une fois ne retrouve jamais le
     comportement par défaut. */
  const THEMES = [
    { cle: "auto",  libelle: "Auto" },
    { cle: "light", libelle: "Clair" },
    { cle: "dark",  libelle: "Sombre" },
  ];

  function appliquerTheme(cle) {
    const html = document.documentElement;
    if (cle === "auto") html.removeAttribute("data-theme");
    else html.setAttribute("data-theme", cle);
    const t = THEMES.find((x) => x.cle === cle) || THEMES[0];
    const zone = document.querySelector("[data-zone=theme-libelle]");
    if (zone) zone.textContent = t.libelle;
    const bouton = document.querySelector("[data-role=basculer-theme]");
    if (bouton) {
      const suivant = THEMES[(THEMES.indexOf(t) + 1) % THEMES.length];
      bouton.setAttribute(
        "aria-label",
        `Thème : ${t.libelle}. Cliquer pour passer à ${suivant.libelle}.`
      );
    }
  }

  function brancherTheme() {
    let courant = lire(CLES.theme, "auto");
    if (!THEMES.some((t) => t.cle === courant)) courant = "auto";
    appliquerTheme(courant);
    const bouton = document.querySelector("[data-role=basculer-theme]");
    if (!bouton) return;
    bouton.addEventListener("click", () => {
      const i = THEMES.findIndex((t) => t.cle === courant);
      courant = THEMES[(i + 1) % THEMES.length].cle;
      ecrire(CLES.theme, courant);
      appliquerTheme(courant);
    });
  }

  const MEMOIRE_JOURS = 240;

  // ---------------------------------------------------------------- stockage

  function lire(cle, defaut) {
    try {
      const brut = localStorage.getItem(cle);
      return brut ? JSON.parse(brut) : defaut;
    } catch {
      return defaut; // navigation privée, stockage plein, JSON corrompu
    }
  }

  function ecrire(cle, valeur) {
    try {
      localStorage.setItem(cle, JSON.stringify(valeur));
      return true;
    } catch {
      return false;
    }
  }

  const aujourdhui = () => new Date().toISOString().slice(0, 10);

  function tempsDispo() {
    const t = lire(CLES.temps, null);
    if (!t || t.date !== aujourdhui()) return null;   // périmé : tout le temps
    return typeof t.minutes === "number" ? t.minutes : null;
  }

  function demain() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return d.toISOString().slice(0, 10);
  }

  function reglages() {
    const r = lire(CLES.reglages, {});
    return {
      langues: Array.isArray(r.langues) && r.langues.length ? r.langues : ["fr", "en"],
      chainesMasquees: r.chainesMasquees || [],
      // null = toutes actives. Un tableau vide serait un écran blanc, donc on
      // distingue « pas encore réglé » de « tout décoché ».
      intentions: Array.isArray(r.intentions) ? r.intentions : null,
    };
  }

  // ------------------------------------------------------------- historique

  function purger() {
    const h = lire(CLES.historique, {});
    const limite = new Date();
    limite.setDate(limite.getDate() - MEMOIRE_JOURS);
    const iso = limite.toISOString().slice(0, 10);
    let change = false;
    for (const [id, date] of Object.entries(h)) {
      if (date < iso) {
        delete h[id];
        change = true;
      }
    }
    if (change) ecrire(CLES.historique, h);
    return h;
  }

  // ---------------------------------------------------------------- sélection

  /* Mêmes règles que la sélection serveur : une case = une vidéo, une chaîne
     n'apparaît qu'une fois dans la grille, et les cases les plus pauvres sont
     servies en premier pour ne pas se faire rafler leur seul candidat. */
  function construireGrille(donnees, reg, histo, reportsDus, differes, budget, montrees) {
    const grille = new Map();
    const chainesPrises = new Set();

    // 1. Les reports arrivés à échéance sont posés d'office : l'utilisateur a
    //    demandé cette vidéo pour aujourd'hui, elle passe avant le classement.
    for (const r of reportsDus) {
      if (grille.has(r.cellule)) continue;
      if (budget !== null && r.video.duree_s > budget) continue;  // ne rentre pas
      grille.set(r.cellule, { ...r.video, reporte: true });
      chainesPrises.add(r.video.chaine_id);
    }

    // `differes` : reportée à une date encore à venir. Sans ce filtre, la
    // vidéo qu'on vient de repousser réapparaîtrait immédiatement — reporter
    // ne servirait à rien.
    // `budget` en secondes, ou null. C'est un critère d'ÉLIGIBILITÉ, pas un
    // masquage après coup : sinon une case se viderait alors qu'il existait un
    // candidat plus court, un cran plus bas dans le vivier.
    const tientDansLeTemps = (v) => budget === null || v.duree_s <= budget;

    const eligibles = (cellule) =>
      (donnees.vivier[cellule] || []).filter(
        (v) =>
          reg.langues.includes(v.langue) &&
          !reg.chainesMasquees.includes(v.chaine_id) &&
          !histo[v.id] &&
          !differes.has(v.id) &&
          tientDansLeTemps(v) &&
          !chainesPrises.has(v.chaine_id)
      );

    const cellules = [];
    for (const i of donnees.intentions)
      for (const c of i.creneaux) cellules.push(`${i.cle}|${c}`);

    // Rareté calculée avant toute attribution, sinon l'ordre dépend de lui-même.
    const rarete = new Map(
      cellules.map((c) => [
        c,
        (donnees.vivier[c] || []).filter(
          (v) =>
            reg.langues.includes(v.langue) &&
            !reg.chainesMasquees.includes(v.chaine_id) &&
            tientDansLeTemps(v)
        ).length,
      ])
    );

    for (const cellule of [...cellules].sort((a, b) => rarete.get(a) - rarete.get(b))) {
      if (grille.has(cellule)) continue;
      const pool = eligibles(cellule);
      if (!pool.length) continue;
      // Deux étages : on sert d'abord ce qui n'a jamais été affiché. Le
      // déjà-montré n'est qu'un filet de sécurité contre la case vide.
      const inedits = pool.filter((v) => !montrees[v.id]);
      const gagnant = (inedits.length ? inedits : pool)[0];
      if (!inedits.length) gagnant.revu = true;
      grille.set(cellule, gagnant);
      chainesPrises.add(gagnant.chaine_id);
    }
    return grille;
  }

  // -------------------------------------------------------------------- rendu

  const echapper = (t) =>
    String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function dureeHtml(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h ? `${h}<small>H</small>${String(m).padStart(2, "0")}` : `${m}<small>MIN</small>`;
  }

  function fiche(v, creneau) {
    const puces = [
      `<span class="af-puce${v.surperf >= 1.5 ? " af-puce--fort" : ""}">×${v.surperf.toFixed(1)} vs sa moyenne</span>`,
      `<span class="af-puce">${v.vues.toLocaleString("fr-FR")} vues</span>`,
      `<span class="af-puce">${Math.round(v.age)} j</span>`,
    ];
    if (v.rattrapage) puces.push('<span class="af-puce af-puce--faible">rattrapage</span>');
    if (v.reporte) puces.push('<span class="af-puce af-puce--fort">reportée</span>');
    if (v.revu) puces.push('<span class="af-puce af-puce--faible">déjà proposée</span>');

    // Titre et miniature affichés tels quels : les métadonnées YouTube ne
    // doivent pas être modifiées.
    return `<article class="af-fiche" data-video="${echapper(v.id)}">
  <a class="af-fiche__lien" href="https://www.youtube.com/watch?v=${echapper(v.id)}"
     target="_blank" rel="noopener" data-role="ouvrir">
    <div class="af-fiche__image">
      <img src="https://i.ytimg.com/vi/${echapper(v.id)}/hqdefault.jpg" alt="" loading="lazy">
      <span class="af-fiche__creneau">${echapper(creneau)}</span>
      <span class="af-fiche__duree">${dureeHtml(v.duree_s)}</span>
    </div>
    <div class="af-fiche__corps">
      <h3 class="af-fiche__titre">${echapper(v.titre)}</h3>
      <div class="af-fiche__source">${echapper(v.chaine_nom)}</div>
    </div>
  </a>
  <div class="af-fiche__pied">
    <div class="af-fiche__signaux">${puces.join("")}</div>
    <button type="button" class="af-bouton" data-role="reporter"
            aria-label="Reporter « ${echapper(v.titre)} » à demain">Demain →</button>
  </div>
</article>`;
  }

  function rendre(donnees) {
    const reg = reglages();
    const histo = purger();

    const reports = lire(CLES.reports, {});
    const jour = aujourdhui();
    const dus = Object.values(reports).filter((r) => r.date <= jour);
    const differes = new Set(
      Object.entries(reports).filter(([, r]) => r.date > jour).map(([id]) => id)
    );
    const minutes = tempsDispo();
    const budget = minutes === null ? null : minutes * 60;
    const montrees = lire(CLES.montrees, {});
    const grille = construireGrille(donnees, reg, histo, dus, differes, budget, montrees);

    const actives = reg.intentions;
    const sections = donnees.intentions.map((intention, i) => {
      if (actives && !actives.includes(intention.cle)) return "";
      const servis = donnees.creneaux.filter(
        (c) => intention.creneaux.includes(c.cle) && (minutes === null || c.min <= minutes)
      );
      if (!servis.length) return "";
      const cases = donnees.creneaux
        .filter((c) => intention.creneaux.includes(c.cle))
        // Un créneau dont la borne basse dépasse le temps disponible n'est pas
        // « vide », il est hors sujet : on le retire au lieu d'afficher un
        // trou qu'on prendrait pour un manque de chaînes.
        .filter((c) => minutes === null || c.min <= minutes)
        .map((c) => {
          const v = grille.get(`${intention.cle}|${c.cle}`);
          if (v) return fiche(v, c.libelle);
          const raison = minutes === null ? "rien de neuf" : "rien qui rentre";
          return `<div class="af-vide"><span class="af-vide__creneau">${c.libelle}</span><span>${raison}</span></div>`;
        })
        .join("");
      return `<section class="af-section af-section--${i + 1}">
  <div class="af-section__bandeau">
    <span class="af-section__numero">${String(i + 1).padStart(2, "0")}</span>
    <h2 class="af-section__nom">${intention.libelle}</h2>
    <span class="af-section__sous">${intention.desc}</span>
  </div>
  <div class="af-section__corps"><div class="af-grille">${cases}</div></div>
</section>`;
    });

    memoriserAffichage(grille);
    document.querySelector("[data-zone=grille]").innerHTML = sections.join("");
    const compte = document.querySelector("[data-zone=compte]");
    if (compte) {
      const visibles = [...grille.keys()].filter(
        (k) => !actives || actives.includes(k.split("|")[0])
      );
      compte.textContent = visibles.length;
    }
  }

  // ------------------------------------------------------- temps disponible

  function peindreTemps() {
    const hote = document.querySelector("[data-zone=temps]");
    if (!hote) return;
    const courant = tempsDispo();
    const puces = TEMPS.map((t) => {
      const actif = t.minutes === courant;
      return `<button type="button" class="af-chip${actif ? " af-chip--actif" : ""}"
        data-role="temps" data-minutes="${t.minutes === null ? "" : t.minutes}"
        aria-pressed="${actif}">${t.libelle}</button>`;
    }).join("");
    hote.innerHTML = `<span class="af-barre__intitule">J'ai</span>${puces}`;
  }

  function brancherTemps(donnees) {
    peindreTemps();
    const hote = document.querySelector("[data-zone=temps]");
    if (!hote) return;
    hote.addEventListener("click", (e) => {
      const b = e.target.closest("[data-role=temps]");
      if (!b) return;
      const brut = b.dataset.minutes;
      ecrire(CLES.temps, {
        date: aujourdhui(),
        minutes: brut === "" ? null : Number(brut),
      });
      peindreTemps();
      rendre(donnees);
    });
  }

  // ------------------------------------------------------------------ actions

  function brancherActions(donnees) {
    document.addEventListener("click", (e) => {
      const bouton = e.target.closest("[data-role=reporter]");
      if (bouton) {
        e.preventDefault();
        const art = bouton.closest(".af-fiche");
        reporter(donnees, art.dataset.video);
        return;
      }
      const lien = e.target.closest("[data-role=ouvrir]");
      if (lien) {
        const art = lien.closest(".af-fiche");
        marquerVue(art.dataset.video);
      }
    });
  }

  /* Reporter, ce n'est pas mettre de côté : la vidéo quitte la grille
     d'aujourd'hui et revient occuper SA case demain. On stocke la fiche
     complète, pour ne pas dépendre du vivier de demain — une vidéo peut
     sortir de la fenêtre entre-temps. */
  function reporter(donnees, id) {
    let trouve = null, cellule = null;
    for (const [cle, liste] of Object.entries(donnees.vivier)) {
      const v = liste.find((x) => x.id === id);
      if (v) { trouve = v; cellule = cle; break; }
    }
    if (!trouve) {
      const reports = lire(CLES.reports, {});
      if (reports[id]) { // déjà reportée : on repousse d'un jour de plus
        reports[id].date = demain();
        ecrire(CLES.reports, reports);
        rendre(donnees);
      }
      return;
    }
    const reports = lire(CLES.reports, {});
    reports[id] = { date: demain(), cellule, video: trouve };
    ecrire(CLES.reports, reports);
    rendre(donnees);
    annoncer("Reportée à demain.");
  }

  function memoriserAffichage(grille) {
    const m = lire(CLES.montrees, {});
    const jour = aujourdhui();
    const limite = new Date();
    limite.setDate(limite.getDate() - QUARANTAINE_MONTREE_JOURS);
    const iso = limite.toISOString().slice(0, 10);
    for (const [id, d] of Object.entries(m)) if (d < iso) delete m[id];
    for (const v of grille.values()) if (!m[v.id]) m[v.id] = jour;
    ecrire(CLES.montrees, m);
  }

  function marquerVue(id) {
    const h = lire(CLES.historique, {});
    h[id] = aujourdhui();
    ecrire(CLES.historique, h);
    const reports = lire(CLES.reports, {});
    if (reports[id]) { delete reports[id]; ecrire(CLES.reports, reports); }
  }

  function annoncer(message) {
    const zone = document.querySelector("[data-zone=annonce]");
    if (zone) { zone.textContent = message; setTimeout(() => (zone.textContent = ""), 4000); }
  }

  // ------------------------------------------------------------------ réglages

  function panneau(donnees) {
    const reg = reglages();
    const souhaits = lire(CLES.souhaits, []);
    const masquees = new Set(reg.chainesMasquees);

    const actives = reg.intentions;
    const categories = donnees.intentions
      .map((i) => {
        const n = (donnees.chaines || []).filter((c) => c.intention === i.cle).length;
        const coche = !actives || actives.includes(i.cle) ? "checked" : "";
        return `<label class="af-choix">
          <input type="checkbox" data-intention="${i.cle}" ${coche}>
          <span>${i.libelle} <em>${n} chaînes</em></span></label>`;
      })
      .join("");

    const langues = [["fr", "Français"], ["en", "Anglais"]]
      .map(([code, nom]) => `<label class="af-choix">
        <input type="checkbox" data-langue="${code}" ${reg.langues.includes(code) ? "checked" : ""}>
        <span>${nom}</span></label>`)
      .join("");

    const parIntention = donnees.intentions.map((i) => {
      const lignes = donnees.chaines
        .filter((c) => c.intention === i.cle)
        .sort((a, b) => a.nom.localeCompare(b.nom, "fr"))
        .map((c) => `<label class="af-choix">
            <input type="checkbox" data-chaine="${echapper(c.id)}" ${masquees.has(c.id) ? "" : "checked"}>
            <span>${echapper(c.nom)} <em>${c.langue}</em></span></label>`)
        .join("");
      return lignes ? `<div class="af-groupe"><h4>${i.libelle}</h4>${lignes}</div>` : "";
    }).join("");

    const listeSouhaits = souhaits.length
      ? `<pre class="af-code" data-zone="yaml">${souhaits
          .map((s) => `  - {handle: "${s.handle}", intention: ${s.intention}, langue: ${s.langue}}`)
          .join("\n")}</pre>
         <button type="button" class="af-bouton" data-role="copier">Copier ces lignes</button>
         <button type="button" class="af-bouton af-bouton--discret" data-role="vider-souhaits">Vider</button>`
      : '<p class="af-note">Aucune chaîne en attente.</p>';

    return `<div class="af-panneau__contenu">
  <section>
    <h3>Catégories</h3>
    <p class="af-note">La bibliothèque est large, ta grille reste courte.
      Active ce que tu veux voir tous les jours.</p>
    <div class="af-colonnes-2">${categories}</div>
  </section>

  <section>
    <h3>Langues</h3>
    <p class="af-note">Décocher tout revient à tout afficher.</p>
    <div class="af-choix-ligne">${langues}</div>
  </section>

  <section>
    <h3>Ajouter un créateur</h3>
    <p class="af-note">Une chaîne ne peut pas être ajoutée à la volée : ses vidéos
      doivent être collectées par le build quotidien. Ces lignes sont à coller dans
      <code>channels.yaml</code>, puis à pousser.</p>
    <div class="af-formulaire">
      <input type="text" data-champ="handle" placeholder="@identifiant" aria-label="Handle de la chaîne">
      <select data-champ="intention" aria-label="Intention">
        ${donnees.intentions.map((i) => `<option value="${i.cle}">${i.libelle}</option>`).join("")}
      </select>
      <select data-champ="langue" aria-label="Langue">
        <option value="fr">fr</option><option value="en">en</option>
      </select>
      <button type="button" class="af-bouton" data-role="ajouter-souhait">Ajouter</button>
    </div>
    ${listeSouhaits}
  </section>

  <section class="af-panneau__large">
    <h3>Créateurs suivis</h3>
    <p class="af-note">Décocher retire la chaîne de la grille immédiatement. Rien
      n'est supprimé du dépôt — c'est réversible.</p>
    <div class="af-colonnes">${parIntention}</div>
  </section>

  <section>
    <button type="button" class="af-bouton af-bouton--discret" data-role="oublier">
      Oublier l'historique de visionnage
    </button>
  </section>
</div>`;
  }

  function brancherPanneau(donnees) {
    const hote = document.querySelector("[data-zone=panneau]");
    const bascule = document.querySelector("[data-role=basculer-panneau]");
    if (!hote || !bascule) return;

    const peindre = () => (hote.innerHTML = panneau(donnees));
    peindre();

    bascule.addEventListener("click", () => {
      const ouvert = hote.hasAttribute("hidden");
      if (ouvert) hote.removeAttribute("hidden");
      else hote.setAttribute("hidden", "");
      bascule.setAttribute("aria-expanded", String(ouvert));
    });

    hote.addEventListener("change", (e) => {
      const reg = reglages();
      const l = e.target.dataset.langue;
      if (l) {
        reg.langues = [...hote.querySelectorAll("[data-langue]:checked")].map((i) => i.dataset.langue);
        if (!reg.langues.length) reg.langues = ["fr", "en"];
      }
      const it = e.target.dataset.intention;
      if (it) {
        reg.intentions = [...hote.querySelectorAll("[data-intention]:checked")]
          .map((i) => i.dataset.intention);
        if (!reg.intentions.length) reg.intentions = null; // tout décoché = tout
      }
      const c = e.target.dataset.chaine;
      if (c) {
        const masquees = new Set(reg.chainesMasquees);
        e.target.checked ? masquees.delete(c) : masquees.add(c);
        reg.chainesMasquees = [...masquees];
      }
      ecrire(CLES.reglages, reg);
      rendre(donnees);
      if (l || it) peindre(); // les cases se re-normalisent
    });

    hote.addEventListener("click", (e) => {
      const role = e.target.dataset.role;
      if (role === "ajouter-souhait") {
        const handle = hote.querySelector("[data-champ=handle]").value.trim();
        if (!handle) return;
        const propre = handle.startsWith("@") ? handle : "@" + handle.replace(/^.*\/@?/, "");
        const souhaits = lire(CLES.souhaits, []);
        if (!souhaits.some((s) => s.handle.toLowerCase() === propre.toLowerCase())) {
          souhaits.push({
            handle: propre,
            intention: hote.querySelector("[data-champ=intention]").value,
            langue: hote.querySelector("[data-champ=langue]").value,
          });
          ecrire(CLES.souhaits, souhaits);
        }
        peindre();
        annoncer("Ajoutée à la liste à coller dans channels.yaml.");
      }
      if (role === "copier") {
        const texte = hote.querySelector("[data-zone=yaml]").textContent;
        navigator.clipboard?.writeText(texte).then(
          () => annoncer("Lignes copiées."),
          () => annoncer("Copie impossible — sélectionne le texte à la main.")
        );
      }
      if (role === "vider-souhaits") { ecrire(CLES.souhaits, []); peindre(); }
      if (role === "oublier") {
        ecrire(CLES.historique, {});
        ecrire(CLES.reports, {});
        ecrire(CLES.montrees, {});
        rendre(donnees);
        annoncer("Historique effacé.");
      }
    });
  }

  // ---------------------------------------------------------------- démarrage

  const balise = document.getElementById("mt-donnees");
  if (!balise) return;
  let donnees;
  try {
    donnees = JSON.parse(balise.textContent);
  } catch {
    return; // on laisse la grille rendue par le serveur
  }

  brancherTheme();
  brancherTemps(donnees);
  rendre(donnees);
  brancherActions(donnees);
  brancherPanneau(donnees);
})();
