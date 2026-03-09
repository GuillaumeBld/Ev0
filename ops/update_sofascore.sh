#!/usr/bin/env bash
# ops/update_sofascore.sh
#
# MAJ Sofascore → VPS en une commande.
# Usage : ./ops/update_sofascore.sh
#
# Prérequis : venv /tmp/ss-env avec playwright + chromium installés.
# Si absent, le script le crée automatiquement.

set -euo pipefail

VPS="root@213.130.144.204"
CONTAINER="ev0-compose-z5hvqt-backend-1"
VENV="/tmp/ss-env"
DATA="/tmp/sofascore_data.json"
IMPORT_SCRIPT="$(dirname "$0")/../ops/import_sofascore.py"

# ── 1. Venv + dépendances ─────────────────────────────────────────
if [[ ! -x "$VENV/bin/python3" ]]; then
  echo "→ Création du venv..."
  python3.13 -m venv "$VENV"
  "$VENV/bin/pip" install httpx playwright -q
  "$VENV/bin/playwright" install chromium
fi

# ── 2. Fetch Sofascore (local, via Playwright) ────────────────────
echo "→ Fetch Sofascore..."
"$VENV/bin/python3" "$(dirname "$0")/fetch_sofascore.py" "$DATA"

# ── 3. Copier vers le VPS ─────────────────────────────────────────
echo "→ Upload vers VPS..."
scp -q "$DATA" "$VPS:/tmp/sofascore_data.json"
scp -q "$IMPORT_SCRIPT" "$VPS:/tmp/import_sofascore.py"

# ── 4. Copier dans le container et importer ───────────────────────
echo "→ Import en base..."
ssh "$VPS" "
  docker cp /tmp/sofascore_data.json $CONTAINER:/tmp/sofascore_data.json
  docker cp /tmp/import_sofascore.py $CONTAINER:/tmp/import_sofascore.py
  docker exec -e PYTHONPATH=/app $CONTAINER python /tmp/import_sofascore.py
"

echo "✓ MAJ terminée."
