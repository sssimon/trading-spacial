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

set -euo pipefail

BASE=/var/www/trading
INITIAL_SHA="${1:-pre-atomic-$(date +%Y%m%d-%H%M%S)}"
UNIT_FILE=/etc/systemd/system/trading-spacial.service

# ── 0. Sanity checks ─────────────────────────────────────────

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

echo "==> Bootstrap atómico: BASE=$BASE INITIAL_SHA=$INITIAL_SHA"
echo

# ── 1. Pausar service ────────────────────────────────────────

echo "==> Pausando trading-spacial..."
sudo systemctl stop trading-spacial

# ── 2. Crear shared/ y mover .env, .venv, data/ ──────────────

echo "==> Creando $BASE/shared/..."
sudo mkdir -p "$BASE/shared"

echo "==> Moviendo .env, .venv, data/ a shared/..."
sudo mv "$BASE/.env"  "$BASE/shared/.env"
sudo mv "$BASE/.venv" "$BASE/shared/.venv"
if [ -d "$BASE/data" ]; then
  sudo mv "$BASE/data" "$BASE/shared/data"
else
  sudo mkdir -p "$BASE/shared/data"
fi

# ── 3. Crear releases/<initial-sha>/ con el resto ────────────

echo "==> Creando $BASE/releases/$INITIAL_SHA/ con el estado actual..."
sudo mkdir -p "$BASE/releases/$INITIAL_SHA"

cd "$BASE"
# Mover todo lo que no sea releases/, shared/, current al release inicial.
for f in *; do
  case "$f" in
    releases|shared|current) ;;
    *) sudo mv "$f" "releases/$INITIAL_SHA/" ;;
  esac
done

# ── 4. Linkear shared/* dentro del release inicial ───────────

echo "==> Linkeando shared/* dentro del release inicial..."
sudo ln -sfn ../../shared/.env  "$BASE/releases/$INITIAL_SHA/.env"
sudo ln -sfn ../../shared/.venv "$BASE/releases/$INITIAL_SHA/.venv"
sudo ln -sfn ../../shared/data  "$BASE/releases/$INITIAL_SHA/data"

# ── 5. Symlink current → release inicial ─────────────────────

echo "==> Creando symlink $BASE/current → releases/$INITIAL_SHA..."
sudo ln -sfn "releases/$INITIAL_SHA" "$BASE/current"

# ── 6. Actualizar systemd unit ───────────────────────────────

echo "==> Backup del systemd unit en ${UNIT_FILE}.bak.$(date +%s)..."
sudo cp "$UNIT_FILE" "${UNIT_FILE}.bak.$(date +%s)"

echo "==> Reescribiendo paths del systemd unit (/var/www/trading → /var/www/trading/current)..."
# Reemplazo cuidadoso: solo paths que empiezan con /var/www/trading/ seguido
# de algo distinto a "current", "releases", "shared". Usa lookahead negativo
# vía dos pases (Perl) para evitar /var/www/trading/current/current/.
sudo perl -i -pe 's|/var/www/trading(?!/(?:current|releases|shared))(\b)|/var/www/trading/current\1|g' "$UNIT_FILE"

echo "==> Diff del unit (revisar antes de continuar):"
sudo diff "${UNIT_FILE}.bak."*[0-9] "$UNIT_FILE" || true
echo
echo "==> ¿OK? Presioná Enter para continuar, Ctrl+C para abortar."
read -r _

sudo systemctl daemon-reload

# ── 7. Restart + health check ────────────────────────────────

echo "==> Arrancando trading-spacial con el nuevo layout..."
sudo systemctl start trading-spacial

echo "==> Esperando 8s y verificando health..."
sleep 8

if curl -fsS http://localhost:8100/health > /dev/null; then
  echo
  echo "==> ✓ Bootstrap completado."
  echo "==> Release activo: $INITIAL_SHA"
  echo "==> Layout:"
  echo "      current     → $(readlink $BASE/current)"
  echo "      shared/.env → $(ls -la $BASE/shared/.env | awk '{print $NF}')"
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
  echo "  sudo mv $BASE/shared/.env $BASE/.env"
  echo "  sudo mv $BASE/shared/.venv $BASE/.venv"
  echo "  sudo mv $BASE/shared/data $BASE/data 2>/dev/null || true"
  echo "  sudo rm -rf $BASE/{current,releases,shared}"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl start trading-spacial"
  exit 1
fi
