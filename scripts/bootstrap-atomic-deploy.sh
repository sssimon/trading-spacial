#!/usr/bin/env bash
# bootstrap-atomic-deploy.sh — migración one-time del layout actual del
# server (todo en /var/www/trading/) al layout atómico con releases/<sha>
# + shared/ + symlink current.
#
# Ejecutar como usuario `ubuntu` en el server de producción.
# Requiere sudo para tocar systemd.
#
# Pre-condición:
#   /var/www/trading/                   contiene la app actual deployada
#   /var/www/trading/.env               existe (creado durante bootstrap original)
#   /var/www/trading/.venv/             existe (virtualenv del sistema)
#
# Post-condición:
#   /var/www/trading/current → releases/<initial-sha>
#   /var/www/trading/shared/.env, shared/.venv/, shared/data/
#   /var/www/trading/releases/<initial-sha>/  con copia del estado actual
#   systemd unit `trading-spacial` apuntando a /var/www/trading/current
#
# El script es idempotente: si /var/www/trading/current ya existe, sale
# limpio sin tocar nada.
#
# ── Flags ──────────────────────────────────────────────────────
#   --dry-run    Muestra todo lo que se haría sin ejecutar nada destructivo.
#                Calcula el diff del systemd unit pero NO lo escribe.
#                Implica --yes (no pide confirmación interactiva).
#   --yes        Skip la confirmación interactiva después del diff del unit.
#                Pensado para ejecución no-interactiva (CI). Con esto el
#                script aplica TODO sin pausas. Combinarlo con --dry-run
#                primero para revisar el plan.
#   --sha=<id>   ID custom para el release inicial (default: pre-atomic-<ts>).
#
# ── Ejemplos ───────────────────────────────────────────────────
#   Interactivo (default):       ./bootstrap-atomic-deploy.sh
#   Preview no-destructivo:      ./bootstrap-atomic-deploy.sh --dry-run
#   Aplicar sin prompts (CI):    ./bootstrap-atomic-deploy.sh --yes

set -euo pipefail

# ── Parse flags ──────────────────────────────────────────────

DRY_RUN=0
ASSUME_YES=0
INITIAL_SHA=""

for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1; ASSUME_YES=1 ;;
    --yes)      ASSUME_YES=1 ;;
    --sha=*)    INITIAL_SHA="${arg#--sha=}" ;;
    -h|--help)
      sed -n '1,/^set -e/p' "$0" | sed 's/^# \?//' | head -n -1
      exit 0
      ;;
    *)
      echo "::error::Flag desconocido: $arg"
      echo "Usage: $0 [--dry-run] [--yes] [--sha=<id>]"
      exit 2
      ;;
  esac
done

if [ -z "$INITIAL_SHA" ]; then
  INITIAL_SHA="pre-atomic-$(date +%Y%m%d-%H%M%S)"
fi

BASE=/var/www/trading
UNIT_FILE=/etc/systemd/system/trading-spacial.service

# ── Helpers ──────────────────────────────────────────────────

# `run` ejecuta el comando si no estamos en dry-run; si sí, lo imprime.
run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] would run: $*"
  else
    "$@"
  fi
}

# ── 0. Sanity checks (siempre se ejecutan) ───────────────────

echo "==> Bootstrap atómico"
echo "    BASE         = $BASE"
echo "    INITIAL_SHA  = $INITIAL_SHA"
echo "    DRY_RUN      = $DRY_RUN"
echo "    ASSUME_YES   = $ASSUME_YES"
echo

if [ ! -d "$BASE" ]; then
  echo "::error::$BASE no existe — ¿bootstrap original completado?"
  exit 1
fi

if [ -L "$BASE/current" ]; then
  echo "==> $BASE/current ya existe. Layout atómico ya está bootstrapped."
  echo "==> Release activo: $(readlink $BASE/current)"
  exit 0
fi

for f in .env .venv; do
  if [ ! -e "$BASE/$f" ]; then
    echo "::error::$BASE/$f no existe. Bootstrap original incompleto."
    exit 1
  fi
done

if [ ! -f "$UNIT_FILE" ]; then
  echo "::error::$UNIT_FILE no existe. Bootstrap original incompleto."
  exit 1
fi

echo "==> Sanity checks OK."
echo

# ── 1. Pausar service ────────────────────────────────────────

echo "==> Step 1: Pausar trading-spacial"
run sudo systemctl stop trading-spacial
echo

# ── 2. Crear shared/ y mover .env, .venv, data/ ──────────────

echo "==> Step 2: Crear $BASE/shared/ y mover .env, .venv, data/"
run sudo mkdir -p "$BASE/shared"
run sudo mv "$BASE/.env"  "$BASE/shared/.env"
run sudo mv "$BASE/.venv" "$BASE/shared/.venv"
if [ -d "$BASE/data" ]; then
  run sudo mv "$BASE/data" "$BASE/shared/data"
else
  run sudo mkdir -p "$BASE/shared/data"
fi
echo

# ── 3. Crear releases/<initial-sha>/ con el resto ────────────

echo "==> Step 3: Crear $BASE/releases/$INITIAL_SHA/ con el estado actual"
run sudo mkdir -p "$BASE/releases/$INITIAL_SHA"

cd "$BASE"
# En dry-run mostramos la lista de archivos que se moverían.
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] would move these entries from $BASE to releases/$INITIAL_SHA/:"
  for f in *; do
    case "$f" in
      releases|shared|current) ;;
      *) echo "    - $f" ;;
    esac
  done
else
  for f in *; do
    case "$f" in
      releases|shared|current) ;;
      *) sudo mv "$f" "releases/$INITIAL_SHA/" ;;
    esac
  done
fi
echo

# ── 4. Linkear shared/* dentro del release inicial ───────────

echo "==> Step 4: Linkear shared/.env, shared/.venv, shared/data en el release"
run sudo ln -sfn ../../shared/.env  "$BASE/releases/$INITIAL_SHA/.env"
run sudo ln -sfn ../../shared/.venv "$BASE/releases/$INITIAL_SHA/.venv"
run sudo ln -sfn ../../shared/data  "$BASE/releases/$INITIAL_SHA/data"
echo

# ── 5. Symlink current → release inicial ─────────────────────

echo "==> Step 5: Crear symlink $BASE/current → releases/$INITIAL_SHA"
run sudo ln -sfn "releases/$INITIAL_SHA" "$BASE/current"
echo

# ── 6. Actualizar systemd unit ───────────────────────────────

echo "==> Step 6: Reescribir paths del systemd unit"
echo "    /var/www/trading  →  /var/www/trading/current"
echo

# Calculamos el nuevo contenido del unit usando perl con lookahead negativo
# para no doble-prefijar paths que ya empiezan con current/, releases/, o shared/.
ORIGINAL_UNIT_CONTENT=$(sudo cat "$UNIT_FILE")
NEW_UNIT_CONTENT=$(echo "$ORIGINAL_UNIT_CONTENT" | perl -pe 's|/var/www/trading(?!/(?:current|releases|shared))(\b)|/var/www/trading/current\1|g')

echo "    Diff del systemd unit:"
echo "    ──────────────────────"
diff <(echo "$ORIGINAL_UNIT_CONTENT") <(echo "$NEW_UNIT_CONTENT") | sed 's/^/    /' || true
echo "    ──────────────────────"
echo

if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] would back up $UNIT_FILE → ${UNIT_FILE}.bak.<timestamp>"
  echo "  [dry-run] would write the diff above to $UNIT_FILE"
  echo "  [dry-run] would run: sudo systemctl daemon-reload"
else
  BAK="${UNIT_FILE}.bak.$(date +%s)"
  echo "==> Backup del unit en $BAK"
  sudo cp "$UNIT_FILE" "$BAK"

  if [ "$ASSUME_YES" = "0" ]; then
    echo "==> ¿OK el diff de arriba?"
    echo "    Enter para aplicar · Ctrl+C para abortar."
    read -r _
  fi

  echo "==> Reescribiendo $UNIT_FILE"
  echo "$NEW_UNIT_CONTENT" | sudo tee "$UNIT_FILE" > /dev/null

  echo "==> systemctl daemon-reload"
  sudo systemctl daemon-reload
fi
echo

# ── 7. Restart + health check ────────────────────────────────

echo "==> Step 7: Arrancar trading-spacial con el nuevo layout"
run sudo systemctl start trading-spacial

if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] would wait 8s then curl http://localhost:8100/health"
  echo
  echo "==> Dry-run completado. Para aplicar:"
  echo "       $0 --yes"
  exit 0
fi

echo "==> Esperando 8s y verificando health..."
sleep 8

if curl -fsS http://localhost:8100/health > /dev/null; then
  echo
  echo "==> ✓ Bootstrap completado."
  echo "==> Release activo: $INITIAL_SHA"
  echo "==> Layout final:"
  echo "      current       → $(readlink $BASE/current)"
  echo "      shared/.env   → $(ls -la $BASE/shared/.env 2>/dev/null | awk '{print $NF}')"
  echo "      shared/.venv  → $(ls -la $BASE/shared/.venv 2>/dev/null | awk '{print $NF}')"
  echo
  echo "==> Próximos deploys vía CI usarán el flujo atómico de .github/workflows/deploy.yml"
else
  echo "::error::Health check falló. El service no levantó con el nuevo layout."
  echo "::error::Logs:"
  sudo journalctl -u trading-spacial -n 60 --no-pager
  echo
  echo "::error::Para revertir manualmente:"
  echo "  sudo systemctl stop trading-spacial"
  echo "  sudo cp ${UNIT_FILE}.bak.* ${UNIT_FILE}"
  echo "  sudo mv $BASE/releases/$INITIAL_SHA/* $BASE/"
  echo "  sudo mv $BASE/shared/.env  $BASE/.env"
  echo "  sudo mv $BASE/shared/.venv $BASE/.venv"
  echo "  sudo mv $BASE/shared/data  $BASE/data 2>/dev/null || true"
  echo "  sudo rm -rf $BASE/{current,releases,shared}"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl start trading-spacial"
  exit 1
fi
