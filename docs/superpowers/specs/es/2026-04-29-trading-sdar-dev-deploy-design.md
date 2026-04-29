# Deploy de trading-spacial a `https://trading.sdar.dev` — diseño

**Fecha**: 2026-04-29
**Branch**: `feat/deploy-trading-sdar-dev`
**Autor**: Samuel + Claude
**Estado**: Diseño aprobado, pendiente implementación

---

## 1. Contexto

`trading-spacial` corre hoy localmente (Mac/Windows). Queremos exponerlo en
`https://trading.sdar.dev` reusando el servidor EC2 que ya hospeda
`sdar.dev`, `n8n.sdar.dev`, `nido.sdar.dev`, `burgos.sdar.dev`,
`openclo.sdar.dev` y `api.samueldar.io`. La integración debe:

- No alterar ninguna de las apps existentes
- Seguir las convenciones de deploy ya establecidas (rsync + systemd, igual que `nido`)
- Auto-desplegarse en cada `merge` a `main`
- Cumplir el modelo de seguridad mínimo (TLS, rate limiting, fail2ban, headers)

## 2. Restricciones del entorno

Auditoría del servidor `13.48.46.19` (eu-north-1, Ubuntu 24.04, t3.micro-class):

| Recurso | Estado |
|---------|--------|
| RAM libre | ~160 MiB de 914 MiB total |
| Disco libre | ~8.9 GB de 30 GB |
| Puerto `8000` | **OCUPADO** por gunicorn (NewYorkMoves, proxied desde `sdar.dev/api/`) |
| Puerto `5678` | OCUPADO por contenedor `n8n` |
| Puerto `8005` | OCUPADO por `search-microservice` |
| Puerto `8080` | OCUPADO por backend de `api.samueldar.io` |
| Puerto `8615` | OCUPADO por `msg2jpg` |
| Python en sistema | `3.12.3` (no hay `3.11`) |
| `fail2ban` | NO instalado |
| Docker | Instalado y en uso (n8n, search-microservice) — pero **no lo usaremos en prod** por convención |
| Patrón existente de deploys | rsync + systemd (`nido`), o rsync de `dist` estático (`openclo`) |

Decisiones derivadas:

- **Puerto del backend**: `127.0.0.1:8100` (libre, sin exposición pública).
- **Python 3.11** vía `deadsnakes` PPA — paridad con `Dockerfile` y `ci.yml`.
- **Sin Docker en producción** — alineado con `nido`/`openclo`. El `docker-compose.yml`
  del repo sigue siendo válido para desarrollo local; no se toca.
- **Build en GitHub Actions**, rsync al servidor — RAM libre del servidor no
  permite construir imágenes ni hacer `npm run build` en sitio.

## 3. Topología

```
                        Internet
                           │
                  https://trading.sdar.dev
                           │
                ┌──────────▼──────────┐
                │   nginx host (443)   │   /etc/nginx/conf.d/trading.sdar.dev.conf
                │   TLS terminada      │   cert: /etc/letsencrypt/live/trading.sdar.dev/
                └─────┬────────┬───────┘
                      │        │
                  "/" │        │ "/api/"
                      ▼        ▼
       /var/www/trading/dist   127.0.0.1:8100
       (React build, servido     │
        por host nginx)          ▼
                             ┌──────────────────────────────┐
                             │ systemd: trading-spacial.svc │
                             │ User=ubuntu                  │
                             │ WorkingDirectory=            │
                             │   /var/www/trading           │
                             │ EnvironmentFile=             │
                             │   /var/www/trading/.env      │
                             │ ExecStart=                   │
                             │   .venv/bin/uvicorn          │
                             │     btc_api:app              │
                             │     --host 127.0.0.1         │
                             │     --port 8100              │
                             │ Restart=on-failure           │
                             └──────────────────────────────┘
                                Scanner thread vive dentro
                                del proceso uvicorn
```

### Layout en `/var/www/trading/`

```
.env                          ← bootstrap once (secrets)
.venv/                        ← bootstrap once (Python 3.11 venv)
btc_api.py, btc_scanner.py    ← rsync por GH Actions
auth/, db/, scanner/, cli/    ← rsync por GH Actions
api/, notifier/, strategy/    ← rsync por GH Actions
data/, observability.py       ← rsync por GH Actions
requirements.txt              ← rsync por GH Actions
config.json,
config.defaults.json          ← rsync por GH Actions
scripts/create_user.py, ...   ← rsync por GH Actions
dist/                         ← rsync por GH Actions (frontend build)
data/                         ← NO rsync (persistente, ohlcv.db, regime_cache)
logs/                         ← NO rsync (persistente)
signals.db                    ← NO rsync (persistente, creado en primer arranque)
```

Pertenencia: `ubuntu:ubuntu`, modo `755` para dirs, `644` para archivos, `600` para `.env`.

## 4. Modelo de procesos

Solo **dos** procesos por trading-spacial en el servidor:

1. **`trading-spacial.service`** — uvicorn corriendo `btc_api:app`. El scanner es
   un hilo daemon dentro de ese mismo proceso (`scanner.runtime.start_scanner_thread`),
   junto con el health monitor y el kill-switch v2 calibrator.

2. **nginx host** (ya corriendo, compartido con todos los sitios).

**No** desplegamos:

- `trading_webhook.py` — Telegram es one-way en este proyecto (memoria del usuario).
- `watchdog.py` — Windows-only.

## 5. Modelo de seguridad

### 5.1 TLS

- Certbot 2.9 ya está instalado y con timer de auto-renovación.
- Cert para `trading.sdar.dev` se emite vía `certbot certonly --webroot -w /var/www/trading/dist`.
  Webroot evita que certbot toque la conf de nginx — el archivo lo controlamos nosotros 100%.
- HSTS con `max-age=31536000; includeSubDomains` (1 año).

### 5.2 Cookies

`AUTH_COOKIE_SECURE=1` en el `.env` del servidor — cookies marcadas `Secure`,
solo viajan sobre HTTPS. CORS restringido a `https://trading.sdar.dev`
(`AUTH_CORS_ORIGINS`).

**`AUTH_API_PREFIX=/api`** — el cookie `refresh_token` se emite con
`Path=/api/auth/refresh` en vez del default `/auth/refresh`. Sin esto, el
browser no manda la cookie de vuelta cuando llama a `/api/auth/refresh`
(el path del cookie no matchea el path del request) y el refresh flow
falla a los 15 min — usuario es expulsado. Ver fix en commit 9b4b023 +
`api/auth.py:_api_prefix()`. Routers siguen montados en `/auth/*`; nginx
strippea `/api` antes de que el request llegue a FastAPI, así que CSRF
middleware y handlers ven paths sin prefix (sin cambios en su lógica).

### 5.3 Rate limiting (nginx)

Dos zonas declaradas a nivel `http{}` en `/etc/nginx/conf.d/limits-trading.conf`:

```nginx
limit_req_zone $binary_remote_addr zone=trading_login:10m   rate=5r/m;
limit_req_zone $binary_remote_addr zone=trading_general:10m rate=60r/m;
```

Aplicación:

| Path público | Zona | Burst | Razón |
|--------------|------|-------|-------|
| `/api/auth/login` | `trading_login` | `3 nodelay` | anti brute-force |
| `/api/*` | `trading_general` | `20 nodelay` | proteger thread pool del backend |
| `/` (estático) | — | — | servido por nginx desde disco, no hace falta |

**Por qué nginx y no solo el rate limiter de la app**: la app limita por proceso
en memoria — un flood satura el thread pool antes de que el limiter actúe. nginx
rechaza con `503` sin entrar a la app.

**Status code de rechazo**: nginx usa `limit_req_status 503` por defecto. El
status `429` lo emite la **app**, no nginx — relevante para el regex de fail2ban.

### 5.4 Headers de seguridad (nginx)

Todos con `always`, replicados en cada `location` anidado (limitación de nginx
vanilla — `add_header` en `location` anidado anula los del padre):

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options    "nosniff"                              always;
add_header X-Frame-Options           "DENY"                                 always;
add_header Referrer-Policy           "strict-origin-when-cross-origin"     always;
```

### 5.5 fail2ban

Jail dedicado para `/api/auth/login`. Lee un access log **por dominio** para
no sufrir falsos positivos por hits a `n8n.sdar.dev` u otros.

`/etc/fail2ban/filter.d/trading-login.conf`:

```ini
[Definition]
failregex = ^<HOST> .* "POST /api/auth/login HTTP/[\d\.]+" (401|503) .*$
ignoreregex =
```

**Por qué `(401|503)` y no `(401|403|429)`**:
- `401` — credenciales rechazadas por la app (lo más común en brute-force).
- `503` — nginx rechazó por rate-limit excedido (default de `limit_req_status`).
- `429` — emitido por la **app**, NO aparece en nginx access log si nginx ya
  bloqueó con `503`. Y si la app sí lo emite, indica saturación interna —
  igualmente queremos banear.
- `403` — no aplica: `/api/auth/login` está exento de CSRF (es el endpoint
  que **establece** los tokens), por lo que no devuelve 403.

> **Validación post-deploy obligatoria**: hacer 11 requests fallidos a
> `https://trading.sdar.dev/api/auth/login` con credenciales inválidas y
> verificar que (a) los códigos en `/var/log/nginx/trading.sdar.dev.access.log`
> son los esperados (401 → 503 después del burst), (b) `fail2ban-client status
> trading-login` muestra la IP baneada.

`/etc/fail2ban/jail.d/trading.conf`:

```ini
[trading-login]
enabled  = true
port     = http,https
filter   = trading-login
logpath  = /var/log/nginx/trading.sdar.dev.access.log
maxretry = 10
findtime = 600    ; 10 min
bantime  = 3600   ; 1 hora
```

### 5.6 systemd hardening

```ini
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/www/trading
```

Razones:
- `ProtectSystem=strict` (en vez de `full`): hace **todo** el FS read-only,
  no solo `/usr` y `/boot`. Más explícito y resistente a defaults futuros.
- `ProtectHome=true` (en vez de `read-only`): oculta `/home`, `/root`, `/run/user`
  por completo. La app no necesita leerlos.
- `ReadWritePaths=/var/www/trading`: necesario porque `strict` cierra `/var`.
  Permite escribir `signals.db`, `data/`, `logs/`.

### 5.7 Archivo nginx completo: `/etc/nginx/conf.d/trading.sdar.dev.conf`

Versión final post-cert. Combina las secciones 5.1 (TLS), 5.3 (rate limits),
5.4 (headers), y la separación frontend estático / backend `/api/`:

```nginx
# ── HTTPS ─────────────────────────────────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name trading.sdar.dev;

    ssl_certificate     /etc/letsencrypt/live/trading.sdar.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/trading.sdar.dev/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Per-domain access log → fail2ban watcher only sees trading hits.
    access_log /var/log/nginx/trading.sdar.dev.access.log;
    error_log  /var/log/nginx/trading.sdar.dev.error.log;

    # Security headers (apply to all responses)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff"                              always;
    add_header X-Frame-Options           "DENY"                                 always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"     always;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    # /api/auth/login → strict rate limit (5 r/m, burst 3 nodelay)
    location = /api/auth/login {
        limit_req zone=trading_login burst=3 nodelay;
        proxy_pass         http://127.0.0.1:8100/auth/login;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # /api/* → backend FastAPI (general rate limit)
    location /api/ {
        limit_req zone=trading_general burst=20 nodelay;
        proxy_pass         http://127.0.0.1:8100/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 5s;
    }

    # Frontend SPA (React build)
    root  /var/www/trading/dist;
    index index.html;

    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        # Re-aplicar headers (add_header en location anidado override headers padres)
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Content-Type-Options    "nosniff"                              always;
        add_header X-Frame-Options           "DENY"                                 always;
        add_header Referrer-Policy           "strict-origin-when-cross-origin"     always;
    }

    location / {
        try_files $uri $uri/ /index.html;
        location = /index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate" always;
            add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
            add_header X-Content-Type-Options    "nosniff"                              always;
            add_header X-Frame-Options           "DENY"                                 always;
            add_header Referrer-Policy           "strict-origin-when-cross-origin"     always;
        }
    }
}

# ── HTTP → HTTPS redirect + ACME challenge ────────────────────────────────
server {
    listen 80;
    server_name trading.sdar.dev;

    location /.well-known/acme-challenge/ {
        root /var/www/trading/dist;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}
```

### 5.8 sudoers scoped

```
ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart trading-spacial, /bin/systemctl status trading-spacial, /usr/bin/systemctl restart trading-spacial, /usr/bin/systemctl status trading-spacial
```

NOPASSWD limitado a esas dos unidades concretas. ubuntu **no** gana sudo general.
Los dos paths (`/bin/` y `/usr/bin/`) cubren ambas resoluciones de symlink según
versión de sudo.

## 6. Bootstrap one-time (manual, vía SSH)

Estos pasos se corren **una sola vez** antes del primer deploy de GitHub Actions.
No son parte del workflow.

### Paso A — Dependencias del SO

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
                    fail2ban \
                    libxml2-dev libxslt-dev    # build deps for lxml
```

### Paso B — Estructura `/var/www/trading/`

```bash
sudo mkdir -p /var/www/trading
sudo chown ubuntu:ubuntu /var/www/trading
cd /var/www/trading

mkdir -p dist data logs
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip wheel

# Generate JWT secret + write .env (este secret vive solo aquí, jamás en GH)
JWT_SECRET=$(python3.11 -c 'import secrets; print(secrets.token_urlsafe(64))')
cat > .env <<EOF
AUTH_JWT_SECRET=$JWT_SECRET
AUTH_CORS_ORIGINS=https://trading.sdar.dev
AUTH_COOKIE_SECURE=1
AUTH_DISABLE_WEB_SETUP=1
AUTH_API_PREFIX=/api
EOF
chmod 600 .env

# Placeholder neutro para que nginx no 404 durante ACME
cat > dist/index.html <<'EOF'
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trading Spacial</title></head>
<body></body></html>
EOF
```

### Paso C — systemd unit

```bash
sudo tee /etc/systemd/system/trading-spacial.service > /dev/null <<'EOF'
[Unit]
Description=Trading Spacial — BTC/USDT signal scanner + FastAPI
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/var/www/trading
EnvironmentFile=/var/www/trading/.env
ExecStart=/var/www/trading/.venv/bin/uvicorn btc_api:app --host 127.0.0.1 --port 8100
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/www/trading

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable trading-spacial
# NO iniciar todavía — el código aún no está en /var/www/trading
```

### Paso D — sudoers scoped

```bash
echo 'ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart trading-spacial, /bin/systemctl status trading-spacial, /usr/bin/systemctl restart trading-spacial, /usr/bin/systemctl status trading-spacial' \
  | sudo tee /etc/sudoers.d/trading-spacial > /dev/null
sudo chmod 440 /etc/sudoers.d/trading-spacial
sudo visudo -c -f /etc/sudoers.d/trading-spacial    # validate syntax
```

### Paso E — nginx + cert (orden cuidadoso)

```bash
# 1. Rate-limit zones primero (independiente del server block)
sudo tee /etc/nginx/conf.d/limits-trading.conf > /dev/null <<'EOF'
limit_req_zone $binary_remote_addr zone=trading_login:10m   rate=5r/m;
limit_req_zone $binary_remote_addr zone=trading_general:10m rate=60r/m;
EOF
sudo nginx -t && sudo systemctl reload nginx

# 2. HTTP-only minimal config para ACME challenge
sudo tee /etc/nginx/conf.d/trading.sdar.dev.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name trading.sdar.dev;
    location /.well-known/acme-challenge/ { root /var/www/trading/dist; }
    location / { return 200 "ok\n"; }
}
EOF
sudo nginx -t && sudo systemctl reload nginx

# 3. Validar SINTAXIS de la conf HTTPS final ANTES de issue cert.
#    nginx -t va a quejarse de cert files faltantes (esperado), pero
#    parsea el resto del archivo primero — si hay typo en una directiva
#    o llave desbalanceada, falla en el parser y lo veremos.
#
#    El archivo /tmp/trading.sdar.dev.conf con el contenido final HTTPS
#    (ver Sección 5.7) se prepara primero — vía heredoc local o scp desde
#    la máquina del operador.
# (Pegar el contenido completo de Sección 5.7 en /tmp/trading.sdar.dev.conf
#  vía heredoc o scp antes de continuar.)

# Copiar temporalmente al directorio de nginx para que -t valide en contexto
sudo cp /tmp/trading.sdar.dev.conf /etc/nginx/conf.d/trading.sdar.dev.conf

# Validación: capturar el output, aceptar solo errores cert-related
NGINX_OUT=$(sudo nginx -t 2>&1 || true)
if echo "$NGINX_OUT" | grep -qE "BIO_new_file|cannot load certificate|fullchain.pem.*No such file"; then
    echo "Sintaxis OK (cert files se emiten en el siguiente paso)."
elif echo "$NGINX_OUT" | grep -q "syntax is ok"; then
    echo "Sintaxis OK."
else
    echo "ERROR: nginx -t falló con errores no relacionados al cert:"
    echo "$NGINX_OUT"
    exit 1
fi

# 4. Revertir al HTTP-only minimal para que nginx pueda recargar mientras
#    se emite el cert (el HTTPS conf todavía referencia archivos inexistentes
#    y nginx no puede arrancar/recargar con esos errores).
sudo tee /etc/nginx/conf.d/trading.sdar.dev.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name trading.sdar.dev;
    location /.well-known/acme-challenge/ { root /var/www/trading/dist; }
    location / { return 200 "ok\n"; }
}
EOF
sudo nginx -t && sudo systemctl reload nginx

# 5. Issue cert via webroot challenge
sudo certbot certonly --webroot -w /var/www/trading/dist \
                     -d trading.sdar.dev \
                     --non-interactive --agree-tos \
                     -m samueldarioballesteros@gmail.com

# 6. Reemplazar conf con la versión final HTTPS (cert ya existe → nginx -t pasa limpio)
sudo cp /tmp/trading.sdar.dev.conf /etc/nginx/conf.d/trading.sdar.dev.conf
sudo nginx -t && sudo systemctl reload nginx
```

### Paso F — fail2ban

```bash
sudo tee /etc/fail2ban/filter.d/trading-login.conf > /dev/null <<'EOF'
[Definition]
failregex = ^<HOST> .* "POST /api/auth/login HTTP/[\d\.]+" (401|503) .*$
ignoreregex =
EOF

sudo tee /etc/fail2ban/jail.d/trading.conf > /dev/null <<'EOF'
[trading-login]
enabled  = true
port     = http,https
filter   = trading-login
logpath  = /var/log/nginx/trading.sdar.dev.access.log
maxretry = 10
findtime = 600
bantime  = 3600
EOF

sudo systemctl enable fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status trading-login
```

### Paso G — GitHub Secrets

En `github.com/sssimon/trading-spacial/settings/secrets/actions`:

| Secret | Valor |
|--------|-------|
| `DEPLOY_SSH_KEY` | mismo valor que en `nido` (private key ed25519) |
| `DEPLOY_HOST` | `ec2-13-48-46-19.eu-north-1.compute.amazonaws.com` |

> **No** se añade `AUTH_JWT_SECRET` ni nada relacionado con el admin.
> El secret JWT vive **solo** en `/var/www/trading/.env` y nunca pasa por
> GitHub Actions. El workflow verificará que `.env` existe en el server
> antes de proceder; si no existe, falla con error claro.

## 7. Workflow `.github/workflows/deploy.yml`

Estructura completa:

```yaml
# Deploy in-place. Trade-off conocido: si el rsync de frontend falla
# después del rsync de backend, el sistema queda con backend nuevo y
# frontend viejo hasta el siguiente deploy. Aceptable mientras el sistema
# sea single-user sin capital real. Para capital real, migrar a deploy
# atómico con symlink swap (releases/<sha> → /var/www/trading current symlink).
name: Deploy to Production

on:
  push:
    branches: [main]

concurrency:
  group: deploy-production
  cancel-in-progress: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Frontend build
        working-directory: frontend
        run: |
          npm ci
          npm run build

      - name: Setup SSH
        env:
          SSH_KEY:  ${{ secrets.DEPLOY_SSH_KEY }}
          HOST:     ${{ secrets.DEPLOY_HOST }}
        run: |
          mkdir -p ~/.ssh
          echo "$SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          ssh-keyscan -H "$HOST" >> ~/.ssh/known_hosts 2>/dev/null

      - name: Verify .env exists on server (fail-fast if bootstrap missing)
        env:
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          if ! ssh -i ~/.ssh/deploy_key ubuntu@${HOST} "test -f /var/www/trading/.env"; then
            echo "::error::/var/www/trading/.env no existe en el server."
            echo "::error::Ejecutar bootstrap manual primero (ver docs/superpowers/specs/es/2026-04-29-trading-sdar-dev-deploy-design.md §6)."
            exit 1
          fi

      - name: rsync backend source
        env:
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          rsync -az --delete \
            -e "ssh -i ~/.ssh/deploy_key" \
            --exclude='tests/' --exclude='data/' --exclude='logs/' \
            --exclude='*.db' --exclude='.venv/' --exclude='__pycache__/' \
            --exclude='frontend/' --exclude='.git/' --exclude='.github/' \
            --exclude='.coverage' --exclude='.pytest_cache/' \
            --exclude='.worktrees/' --exclude='Backtesting_BTCUSDT/' \
            --exclude='Dashboard/' --exclude='docs/' --exclude='*.bat' \
            --exclude='*.ps1' --exclude='*.env' \
            ./ ubuntu@${HOST}:/var/www/trading/

      - name: rsync frontend dist
        env:
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          rsync -az --delete \
            -e "ssh -i ~/.ssh/deploy_key" \
            frontend/dist/ ubuntu@${HOST}:/var/www/trading/dist/

      - name: pip install + restart service
        env:
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          ssh -i ~/.ssh/deploy_key ubuntu@${HOST} "
            cd /var/www/trading &&
            .venv/bin/pip install --upgrade -r requirements.txt &&
            sudo systemctl restart trading-spacial
          "

      - name: Health check
        env:
          HOST: ${{ secrets.DEPLOY_HOST }}
        run: |
          sleep 5
          if ! ssh -i ~/.ssh/deploy_key ubuntu@${HOST} "curl -fsS http://localhost:8100/health"; then
            echo "::error::Health check falló. Logs del servicio:"
            ssh -i ~/.ssh/deploy_key ubuntu@${HOST} "sudo journalctl -u trading-spacial -n 80 --no-pager"
            exit 1
          fi

      - name: Cleanup
        if: always()
        run: rm -f ~/.ssh/deploy_key
```

### Notas del workflow

- **Concurrency group `deploy-production`** con `cancel-in-progress` —
  dos commits seguidos solo despliegan el último.
- **Verifica `.env` antes de cualquier rsync** — si falta, sabemos que el
  bootstrap no se hizo y abortamos sin tocar nada.
- **rsync `--delete`** en backend + frontend — limpia archivos eliminados
  entre commits. Las exclusiones protegen `data/`, `logs/`, `*.db`, `.venv/`.
- **No depende de `ci.yml`** explícitamente — GitHub branch protection
  (a configurar manualmente en repo settings) gating: ningún merge a `main`
  sin que `ci.yml` haya pasado. Recomendación: añadir esa regla.
- **Deploy in-place — trade-off conocido**: si el rsync de frontend falla
  después del rsync de backend, el sistema queda con backend nuevo + frontend
  viejo hasta el siguiente deploy. Aceptable mientras el sistema sea
  single-user y no esté operando capital real. Para capital real, migrar a
  deploy atómico con symlink swap (`/var/www/trading` → `releases/<sha>`,
  swap atómico tras validar todo el bundle). El comentario equivalente debe
  vivir en el header del `deploy.yml` para que cualquier futuro lector lo
  vea sin tener que abrir este spec.

## 8. Primer deploy + post-deploy

### 8.1 Bootstrap → primer deploy

1. Correr Pasos A–G del bootstrap (sección 6).
2. Push a `main` (o `gh workflow run deploy.yml`). El workflow rsync-eará
   código, hará `pip install`, y `systemctl restart`.
3. systemd arranca el servicio con `.env` ya presente. `curl localhost:8100/health` debe responder OK.
4. Browser: `https://trading.sdar.dev` debe mostrar el SPA con login form
   (sin admin todavía).

> ⚠ **Ventana sin admin**: entre 8.1.4 (primer deploy exitoso) y 8.2 (crear
> admin via SSH), el sitio muestra login form pero ningún login funciona.
> No hay usuario en la DB y `AUTH_DISABLE_WEB_SETUP=1` deshabilita la página
> de setup. Esta ventana es esperada y debería ser **<5 minutos**. Completar
> 8.2 inmediatamente después de 8.1, no dejar para después. Mientras tanto,
> cualquier visitante ve un form que no responde — preferible a dejar la
> página de setup expuesta.

### 8.2 Crear admin (manual, una sola vez)

```bash
ssh ubuntu@<host>
cd /var/www/trading
set -a && source .env && set +a   # exporta AUTH_JWT_SECRET al shell
.venv/bin/python scripts/create_user.py --role admin
# (interactivo: email + password con getpass × 2)
```

### 8.3 Validar fail2ban con tráfico real

```bash
# Generar 11 fails contra /api/auth/login (uno más que maxretry)
for i in $(seq 1 11); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://trading.sdar.dev/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"x@x.x","password":"badpass"}'
done

# Inspeccionar el access log del dominio
sudo tail -20 /var/log/nginx/trading.sdar.dev.access.log

# Verificar bans en fail2ban
sudo fail2ban-client status trading-login
```

Esperado:
- Primeros 5–8 hits → `401` (la app rechaza credenciales).
- Hits 9–11 → `503` (nginx rechaza por rate-limit excedido).
- `fail2ban-client status trading-login` muestra 1 IP baneada.

Si los códigos no coinciden con `(401|503)`, **ajustar el regex** del filtro
y reload `fail2ban`. La idea de este paso es exactamente confirmar el regex
contra logs reales antes de declarar el sistema operativo.

## 9. Operación

### 9.1 Logs

| Tipo | Path |
|------|------|
| nginx access (este dominio) | `/var/log/nginx/trading.sdar.dev.access.log` |
| nginx error (este dominio)  | `/var/log/nginx/trading.sdar.dev.error.log` |
| Backend (uvicorn + scanner) | `journalctl -u trading-spacial -f` |
| Backend signal log textual  | `/var/www/trading/logs/signals_log.txt` |
| fail2ban                    | `/var/log/fail2ban.log` |

### 9.2 Operaciones comunes

```bash
# Restart manual
sudo systemctl restart trading-spacial

# Ver últimos 200 logs del backend
journalctl -u trading-spacial -n 200 --no-pager

# Forzar un scan desde el server (vía API local, sin pasar por nginx)
curl -X POST http://localhost:8100/scan

# Backup manual de signals.db
cp /var/www/trading/signals.db /var/www/trading/signals.db.$(date +%Y%m%d-%H%M%S).bak

# Editar config.json
sudo -u ubuntu nano /var/www/trading/config.json
sudo systemctl restart trading-spacial   # config se relee al arrancar
```

### 9.3 Rotación del JWT secret

Si hay sospecha de fuga:

```bash
ssh ubuntu@<host>
cd /var/www/trading
NEW_SECRET=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))')
sed -i "s|^AUTH_JWT_SECRET=.*|AUTH_JWT_SECRET=$NEW_SECRET|" .env
sudo systemctl restart trading-spacial
# Todos los tokens emitidos antes quedan inválidos — usuarios deben re-loguear.
```

### 9.4 Rollback

`rsync --delete` no preserva versión anterior. Para rollback:

```bash
# Re-run el GH Actions deploy apuntando a un commit/tag previo desde tu máquina:
gh workflow run deploy.yml --ref <previous-sha-or-tag> \
  --repo sssimon/trading-spacial
```

Esto fuerza un deploy con código viejo (rsync sobreescribe `/var/www/trading/`)
y restart del servicio. Los datos persistentes (`signals.db`, `data/`, `logs/`)
no se ven afectados.

Para un kill-switch inmediato sin redeploy:

```bash
sudo systemctl stop trading-spacial   # frontend estático sigue sirviendo,
                                      # /api/* devuelve 502 hasta nuevo restart
```

## 10. Limitaciones conocidas

- **Sin runner self-hosted**: cada deploy bootstrappea SSH desde cero. Coste:
  ~5s extra por deploy. Beneficio: cero proceso permanente en el server.
- **`rsync --delete` sin staging**: si un deploy se interrumpe a la mitad,
  archivos parciales pueden coexistir. Mitigación: el `restart` final reinicia
  un proceso limpio que carga código consistente del FS.
- **Single point of failure**: el server EC2 hospeda múltiples sitios. Caída
  del host afecta todos. Fuera de alcance de esta migración.
- **Sin alta disponibilidad / sin replica**: trading-spacial es single-instance.
  El kill-switch v2 y el scanner son in-process, no distribuidos. Suficiente
  para el caso de uso (uso personal del usuario).

## 11. Referencias

- Patrón base: `sssamuelll/nido` `.github/workflows/deploy.yml` (mismo
  modelo de SSH key + rsync + systemd).
- nginx host `/etc/nginx/conf.d/openclo.sdar.dev.conf` y `n8n.sdar.dev.conf`
  como ejemplos de cert + redirect 80→443 ya en producción.
- Issue #242 — `tech-debt: audit unmarked network-touching tests` (no
  bloquea este deploy, pero relevante para confiabilidad de CI).
- Bug fix de cookie path bajo proxy con `/api/` strip: commit **9b4b023**
  (`fix(auth): make refresh_token cookie path respect AUTH_API_PREFIX`).
  Detectado durante review de PR #243 — el path del cookie estaba hardcoded
  a `/auth/refresh`, inválido bajo el proxy. Default empty del env var
  preserva compat con setups sin prefix; producción set `AUTH_API_PREFIX=/api`.

## 12. Checklist de implementación

Esta sección la convertimos en plan ejecutable después de aprobar el spec.

- [ ] Bootstrap manual del server (Sección 6, Pasos A–F)
- [ ] Configurar GitHub Secrets `DEPLOY_SSH_KEY` y `DEPLOY_HOST` (Paso G)
- [ ] Crear `.github/workflows/deploy.yml` en el repo (Sección 7)
- [ ] PR + merge → primer deploy automatizado
- [ ] Crear admin con `scripts/create_user.py` (§8.2)
- [ ] Validar fail2ban con 11 requests fallidos (§8.3)
- [ ] Documentar credenciales admin en password manager personal
- [ ] (Opcional) configurar branch protection en GitHub para que `ci.yml`
      sea required check antes de merge a `main`
