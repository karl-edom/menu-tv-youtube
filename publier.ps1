# ============================================================================
#  Menu TV — publier.ps1
#
#  Envoie ton travail sur GitHub. Rien d'autre a savoir.
#
#  UTILISATION : clic droit sur ce fichier > "Executer avec PowerShell".
#  Ou dans un terminal ouvert dans ce dossier :  .\publier.ps1
#
#  Le script fait tout dans le bon ordre, n'ouvre jamais d'editeur de texte,
#  et s'arrete en expliquant en francais si quelque chose cloche.
# ============================================================================

param(
  [string]$Message = ""
)

$ErrorActionPreference = "Continue"

function Titre($t)   { Write-Host ""; Write-Host "  $t" -ForegroundColor Cyan }
function Ok($t)      { Write-Host "  OK  $t" -ForegroundColor Green }
function Souci($t)   { Write-Host "  /!\ $t" -ForegroundColor Yellow }
function Stop2($t)   { Write-Host ""; Write-Host "  ARRET : $t" -ForegroundColor Red; Fin 1 }

function Fin($code) {
  Write-Host ""
  Write-Host "  Appuie sur une touche pour fermer..." -ForegroundColor DarkGray
  try { $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") } catch { Start-Sleep 5 }
  exit $code
}

# On se place dans le dossier du script, pour que le double-clic marche.
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "   MENU TV - PUBLICATION" -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan

# --- 0. Verifications de base -----------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Stop2 "git n'est pas installe. Telecharge-le sur https://git-scm.com/download/win"
}
if (-not (Test-Path ".git")) {
  Stop2 "Ce dossier n'est pas relie a GitHub. Le script doit etre a cote de menu_tv.py."
}

# --- 1. Nettoyage des verrous laisses par un plantage precedent --------------
Titre "1/5  Verification de l'etat du dossier"
$verrous = @(".git\index.lock", ".git\HEAD.lock")
foreach ($v in $verrous) {
  if (Test-Path $v) {
    Remove-Item -Force $v -ErrorAction SilentlyContinue
    Souci "verrou $v supprime (reste d'une commande interrompue)"
  }
}
if (Test-Path ".git\_verrous_a_supprimer") {
  Remove-Item -Recurse -Force ".git\_verrous_a_supprimer" -ErrorAction SilentlyContinue
}

# Fusion laissee en plan ?
if (Test-Path ".git\MERGE_HEAD") {
  Souci "une fusion etait restee en cours, je la termine"
  git commit --no-edit --quiet 2>&1 | Out-Null
}

# --- 2. Ce qui a change ------------------------------------------------------
Titre "2/5  Ce qui a change depuis la derniere publication"
$modifs = git status --porcelain
if ($modifs) {
  git status --short | ForEach-Object { Write-Host "      $_" }
} else {
  Write-Host "      (aucun fichier modifie)" -ForegroundColor DarkGray
}

# --- 3. Enregistrer ----------------------------------------------------------
Titre "3/5  Enregistrement"
if ($modifs) {
  if (-not $Message) {
    $Message = "Mise a jour du " + (Get-Date -Format "dd/MM/yyyy a HH:mm")
  }
  git add -A
  git commit -m $Message --quiet
  if ($LASTEXITCODE -ne 0) { Stop2 "l'enregistrement a echoue. Copie l'ecran et montre-le moi." }
  Ok "enregistre : $Message"
} else {
  Write-Host "      rien a enregistrer" -ForegroundColor DarkGray
}

# --- 4. Recuperer ce que GitHub a de son cote --------------------------------
# Le robot qui genere le menu chaque matin ecrit dans le dossier state/.
# --no-edit : n'ouvre jamais d'editeur.  -X ours : en cas de desaccord sur
# state/, garde la version locale. C'est un cache regenerable, sans risque.
Titre "4/5  Recuperation des modifications faites par le robot"
$sortie = git pull --no-rebase --no-edit -X ours 2>&1
$sortie | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
  Stop2 "la recuperation a echoue. Ne tape rien d'autre : copie tout cet ecran et montre-le moi."
}
Ok "a jour"

# --- 5. Envoyer --------------------------------------------------------------
Titre "5/5  Envoi vers GitHub"
$sortie = git push 2>&1
$sortie | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
  Stop2 "l'envoi a echoue. Souvent : pas de connexion, ou identifiants a re-saisir."
}
Ok "envoye"

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "   C'EST PUBLIE" -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Le code est sur GitHub. Pour regenerer le menu maintenant :"
Write-Host "  https://github.com/karl-edom/menu-tv-youtube/actions" -ForegroundColor White
Write-Host "  -> Menu TV quotidien -> Run workflow"
Write-Host ""
Write-Host "  Le menu en ligne :"
Write-Host "  https://karl-edom.github.io/menu-tv-youtube/" -ForegroundColor White

Fin 0
