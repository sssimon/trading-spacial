# Migración al deploy atómico — guía operativa

Esta es una migración **one-time** del layout actual del server
(`/var/www/trading/` plano) al layout con `releases/<sha>` + symlink
`current` que permite deploys atómicos con rollback.

## Por qué

El `deploy.yml` previo hacía rsync directo a `/var/www/trading/`. Trade-off
conocido:
- Si rsync del backend pasaba pero rsync del frontend fallaba, el sistema
  quedaba con backend nuevo + frontend viejo hasta el próximo deploy.
- Si el `systemctl restart` no levantaba la app, no había rollback fácil
  — había que re-rsync de un commit anterior o restaurar a mano.

El nuevo flujo:
1. Cada deploy va a `releases/<sha>/` (sin tocar el activo).
2. Pip install corre dentro del release nuevo.
3. **Cutover atómico**: `mv -Tf current.new current` (rename(2) es atómico
   en Linux). Antes del swap el sistema está 100% en el release anterior;
   después del swap está 100% en el nuevo. No hay estado intermedio.
4. Health check → si falla, **rollback automático** al release anterior.
5. Se mantienen los últimos 5 releases para rollback manual si hace falta.

## Layout objetivo

```
/var/www/trading/
├── current → releases/abc123def4    (symlink, el target apunta al release activo)
├── releases/
│   ├── abc123def4/                   (release activo)
│   │   ├── btc_api.py / ...          (código copiado por rsync)
│   │   ├── dist/                     (frontend build)
│   │   ├── .env    → ../../shared/.env
│   │   ├── .venv   → ../../shared/.venv
│   │   └── data    → ../../shared/data
│   ├── 789ghi456789/                 (release anterior)
│   └── ...                           (hasta 5 releases)
└── shared/                           (persiste entre deploys)
    ├── .env                          (config — NO se commitea, NO se rsync)
    ├── .venv/                        (virtualenv compartido)
    └── data/                         (signals.db, logs, etc)
```

Por qué linkear `.env`, `.venv`, `data/` en vez de tenerlos por-release:
- **`.env`**: secrets / config. Tiene que sobrevivir al deploy.
- **`.venv`**: tarda 30-60s en recrearse. Compartirlo evita esa latencia
  por deploy. El `pip install --upgrade -r requirements.txt` se ejecuta
  pero solo aplica deltas.
- **`data/`**: la DB y los logs. Obvio que no podemos tirarlos cada deploy.

## Pasos del bootstrap (una vez, en el server)

### 0. Pre-requisitos

- Acceso SSH al server como `ubuntu`.
- Permisos `sudo` (para `systemctl` y escribir en `/etc/systemd/system/`).
- El service `trading-spacial` está corriendo en el layout viejo
  (`/var/www/trading/btc_api.py`, etc).

### 1. Subir el script al server

Desde tu máquina (la que tiene SSH key):

```bash
scp scripts/bootstrap-atomic-deploy.sh ubuntu@<DEPLOY_HOST>:/tmp/
ssh ubuntu@<DEPLOY_HOST>
```

### 2. Ejecutar el bootstrap

```bash
cd /tmp
chmod +x bootstrap-atomic-deploy.sh
./bootstrap-atomic-deploy.sh
```

El script:

1. **Sanity checks** — chequea que `/var/www/trading/.env`, `/var/www/trading/.venv/`
   y el systemd unit existen. Si `current` ya existe, sale sin tocar nada.
2. `systemctl stop trading-spacial` — pausa la app durante la migración.
3. Crea `shared/` y mueve `.env`, `.venv`, `data/` ahí.
4. Crea `releases/<initial-sha>/` y mueve todo lo demás (código backend,
   `dist/`, etc) ahí.
5. Crea los symlinks `.env`, `.venv`, `data` dentro del release inicial
   apuntando a `../../shared/*`.
6. Crea el symlink `current → releases/<initial-sha>`.
7. **Reescribe el systemd unit** con `perl -i` reemplazando `/var/www/trading`
   por `/var/www/trading/current` (con lookahead para no doble-prefijar
   paths que ya empiezan con `releases/`, `shared/` o `current`).
   Hace un `.bak.<timestamp>` antes.
8. Muestra el diff del unit y **pide confirmación interactiva**.
9. `systemctl daemon-reload && systemctl start trading-spacial`.
10. `curl -fsS http://localhost:8100/health` — si falla, imprime los
    comandos para revertir manualmente.

### 3. Verificación post-bootstrap

```bash
# El service tiene que estar activo
sudo systemctl status trading-spacial

# El symlink apunta al release inicial
ls -la /var/www/trading/current

# .env y .venv son symlinks a shared/
ls -la /var/www/trading/current/.env
ls -la /var/www/trading/current/.venv

# El health check responde
curl http://localhost:8100/health

# Logs limpios sin tracebacks
sudo journalctl -u trading-spacial -n 50 --no-pager
```

### 4. Mergear el PR del deploy atómico

Con el server ya migrado, mergear el PR de
`.github/workflows/deploy.yml` (deploy atómico).

El primer deploy con el flujo nuevo:
- Tomará el SHA del commit que mergeó el PR.
- Creará `releases/<ese-sha>/` con la copia.
- Cutover atómico al nuevo release.
- Health check.

Si todo OK, vas a ver dos releases en `releases/`: el inicial del bootstrap
+ el primer release auto-generado.

## Rollback manual (después de bootstrapped)

Si un deploy auto-completa pero el comportamiento de la app es malo (no
es un health check failure, sino un bug funcional), podés rollback a mano:

```bash
ssh ubuntu@<DEPLOY_HOST>
cd /var/www/trading
ls -1t releases/ | head -5            # ver últimos 5 releases
ln -sfn releases/<sha-anterior> current.new
mv -Tf current.new current
sudo systemctl restart trading-spacial
```

El rollback es atómico también.

## Rollback del bootstrap (volver al layout viejo)

Si el bootstrap falla y querés volver al layout plano, el script imprime
los comandos exactos al final del error. Resumen:

```bash
sudo systemctl stop trading-spacial
sudo cp /etc/systemd/system/trading-spacial.service.bak.<timestamp> \
        /etc/systemd/system/trading-spacial.service
sudo mv /var/www/trading/releases/<initial-sha>/* /var/www/trading/
sudo mv /var/www/trading/shared/.env  /var/www/trading/.env
sudo mv /var/www/trading/shared/.venv /var/www/trading/.venv
sudo mv /var/www/trading/shared/data  /var/www/trading/data
sudo rm -rf /var/www/trading/{current,releases,shared}
sudo systemctl daemon-reload
sudo systemctl start trading-spacial
```

## Notas y limitaciones

- **Rolling release window**: el flujo de CI mantiene los últimos 5 releases.
  Si querés más historia, ajustar `tail -n +6` en el step "Cleanup old
  releases" del `deploy.yml`.
- **Concurrent deploys**: el `concurrency.group: deploy-production` con
  `cancel-in-progress: true` cancela cualquier deploy en curso si llega
  uno nuevo. Si el nuevo cancela uno que ya rsync-eó pero no hizo cutover,
  el release queda en `releases/` pero no activo. Cleanup lo barre tras
  el próximo deploy exitoso.
- **DB migrations**: este flujo NO contempla migraciones de schema. Si
  agregás SQLAlchemy + Alembic o similar, hay que insertar un step
  `.venv/bin/alembic upgrade head` ANTES del cutover (dentro del release
  nuevo, antes del swap).
