# trading.sdar.dev Bootstrap Implementation Plan

> **For agentic workers:** This is an OPS runbook executed via SSH against
> a shared production EC2 host. It does not follow TDD — it follows the
> "every sudo printed first, every output printed after, STOP on any error"
> protocol mandated by the operator. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Bring up `https://trading.sdar.dev` on the existing EC2 host
without disturbing the 6+ production sites already running there.

**Architecture:** rsync + systemd + host nginx (no Docker in prod), aligned
with the existing `nido` deploy pattern. Backend uvicorn on `127.0.0.1:8100`,
frontend static dist served by host nginx. TLS via certbot webroot. Rate
limit + fail2ban + scoped sudoers + systemd hardening.

**Tech Stack:** Ubuntu 24.04, Python 3.11 (deadsnakes), uvicorn/FastAPI,
nginx 1.24, certbot 2.9, fail2ban, GitHub Actions (rsync over SSH).

**Reference spec:** `docs/superpowers/specs/es/2026-04-29-trading-sdar-dev-deploy-design.md`

---

## Pre-flight (operator + agent shared)

### Pre-flight 1: SSH connectivity check

- [ ] **Step 1: Verify SSH access works**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com 'echo OK'
```

Expected output: `OK`

If fails: STOP. SSH key or hostname wrong. Fix and re-run.

### Pre-flight 2: Confirm no `trading-spacial` artifacts already exist

- [ ] **Step 1: Check for stale install**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  echo "=== /var/www/trading ==="; ls -la /var/www/trading 2>&1 | head -3
  echo "=== systemd unit ==="; systemctl list-unit-files | grep -i trading 2>&1
  echo "=== nginx confs ==="; sudo ls -la /etc/nginx/conf.d/trading* /etc/nginx/conf.d/limits-trading* 2>&1
  echo "=== fail2ban jail ==="; sudo ls /etc/fail2ban/jail.d/trading* /etc/fail2ban/filter.d/trading* 2>&1
  echo "=== cert ==="; sudo ls /etc/letsencrypt/live/trading.sdar.dev 2>&1
'
```

Expected: every line shows "No such file or directory" or empty.

If any artifact exists: STOP. Clean install, not idempotent re-run. Operator must decide whether to wipe and redo, or skip the conflicting step.

---

## Task A: Install OS dependencies

**Files affected on server:**
- Adds: deadsnakes PPA to `/etc/apt/sources.list.d/deadsnakes-ubuntu-ppa-noble.sources` (or similar)
- Installs: `python3.11`, `python3.11-venv`, `python3.11-dev`, `fail2ban`, `libxml2-dev`, `libxslt-dev`

**Hard limit:** No other package installs, no apt upgrades, no apt full-upgrade.

- [ ] **Step A.1: Add deadsnakes PPA**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com \
  'sudo add-apt-repository -y ppa:deadsnakes/ppa'
```

Expected: ends with `Adding repository...` and an `apt update` summary or
`Repository: 'deb [...] noble main'`. No errors.

If fails: STOP. PPA add failure (network, gpg, etc.) — do not proceed without
operator decision.

- [ ] **Step A.2: Refresh apt cache**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com \
  'sudo apt update'
```

Expected: `Reading package lists... Done`. No `E:` errors.

If fails: STOP.

- [ ] **Step A.3: Install python3.11 + tooling + fail2ban + lxml deps**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com \
  'sudo apt install -y python3.11 python3.11-venv python3.11-dev fail2ban libxml2-dev libxslt-dev'
```

Expected: ends with `0 newly installed, 0 to remove` (if cached) OR a normal
install summary with no errors.

If fails: STOP.

- [ ] **Step A.4: Verify python3.11 + fail2ban**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  python3.11 --version
  fail2ban-client --version 2>&1 | head -1
'
```

Expected:
```
Python 3.11.x
Fail2Ban v1.x
```

If python3.11 not found OR fail2ban-client not found: STOP. Install incomplete.

- [ ] **Pause for operator confirmation before Task B.**

---

## Task B: Create `/var/www/trading/` structure

**Files created on server:**
- `/var/www/trading/` (dir, ubuntu:ubuntu 755)
- `/var/www/trading/dist/` (dir)
- `/var/www/trading/data/` (dir)
- `/var/www/trading/logs/` (dir)
- `/var/www/trading/.venv/` (Python 3.11 venv)
- `/var/www/trading/.env` (mode 600, contains JWT secret)
- `/var/www/trading/dist/index.html` (neutral placeholder)

**Hard limit:** Only `/var/www/trading/`. Don't touch sibling dirs.

- [ ] **Step B.1: Create the dir + chown to ubuntu**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo mkdir -p /var/www/trading &&
  sudo chown ubuntu:ubuntu /var/www/trading
'
```

Expected: no output, exit 0.

- [ ] **Step B.2: Create subdirs as ubuntu**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  cd /var/www/trading &&
  mkdir -p dist data logs &&
  ls -la
'
```

Expected: 4 dirs (dist, data, logs, .) all `ubuntu ubuntu`.

- [ ] **Step B.3: Create Python 3.11 venv**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  cd /var/www/trading &&
  python3.11 -m venv .venv &&
  .venv/bin/pip install --upgrade pip wheel
'
```

Expected: `Successfully installed pip-XX wheel-XX`. `.venv/` dir created.

If fails: STOP. Could be missing python3.11-venv (already installed in A.3).

- [ ] **Step B.4: Verify venv works**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  /var/www/trading/.venv/bin/python --version
'
```

Expected: `Python 3.11.x`

- [ ] **Step B.5: Generate JWT secret AND write .env in a single SSH command**

> **Critical:** the JWT secret is generated server-side and never leaves the
> server. The agent does NOT see the value. The `.env` file is the sole
> location.

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  cd /var/www/trading
  JWT_SECRET=$(/var/www/trading/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))")
  cat > .env <<EOF
AUTH_JWT_SECRET=$JWT_SECRET
AUTH_CORS_ORIGINS=https://trading.sdar.dev
AUTH_COOKIE_SECURE=1
AUTH_DISABLE_WEB_SETUP=1
AUTH_API_PREFIX=/api
EOF
  chmod 600 .env
  echo "=== .env permissions ==="
  ls -la .env
  echo "=== .env keys (values masked) ==="
  awk -F= "{print \$1}" .env
'
```

Expected output:
```
=== .env permissions ===
-rw------- 1 ubuntu ubuntu ... .env
=== .env keys (values masked) ===
AUTH_JWT_SECRET
AUTH_CORS_ORIGINS
AUTH_COOKIE_SECURE
AUTH_DISABLE_WEB_SETUP
AUTH_API_PREFIX
```

Verification: `.env` is mode `600`, owned by ubuntu. Five expected keys.
JWT secret value never printed.

If fails: STOP. Don't retry — the failed command may have written a partial
.env that contains secrets but with wrong perms.

- [ ] **Step B.6: Write neutral placeholder index.html**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  cat > /var/www/trading/dist/index.html <<EOF
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trading Spacial</title></head>
<body></body></html>
EOF
  cat /var/www/trading/dist/index.html
'
```

Expected: the HTML printed back verbatim.

- [ ] **Pause for operator confirmation before Task C.**

---

## Task C: systemd unit

**Files created on server:**
- `/etc/systemd/system/trading-spacial.service`

**Hard limit:** No edits to other systemd unit files.

- [ ] **Step C.1: Verify no existing unit collision**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  systemctl list-unit-files | grep -i trading 2>&1
'
```

Expected: empty output (no existing unit named trading-spacial).

If output non-empty: STOP. Pre-flight 2 should have caught this — operator must investigate.

- [ ] **Step C.2: Write the unit file**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/systemd/system/trading-spacial.service > /dev/null <<'UNIT_EOF'
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
UNIT_EOF

  echo '=== file contents ==='
  sudo cat /etc/systemd/system/trading-spacial.service
"
```

Expected: the unit file contents printed back verbatim, matching exactly what
was written.

- [ ] **Step C.3: Reload systemd + enable (NOT start)**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl daemon-reload &&
  sudo systemctl enable trading-spacial &&
  echo "=== status ===" &&
  sudo systemctl status trading-spacial --no-pager 2>&1 | head -10
'
```

Expected:
```
Created symlink /etc/systemd/system/multi-user.target.wants/trading-spacial.service → /etc/systemd/system/trading-spacial.service.
=== status ===
○ trading-spacial.service - Trading Spacial — BTC/USDT signal scanner + FastAPI
     Loaded: loaded (/etc/systemd/system/trading-spacial.service; enabled; ...)
     Active: inactive (dead)
```

Verification: `Loaded: ...; enabled` AND `Active: inactive (dead)`. We
intentionally do NOT start the service yet — the code isn't deployed.

If `systemctl enable` fails: STOP. Unit file likely has syntax error.

- [ ] **Pause for operator confirmation before Task D.**

---

## Task D: Scoped sudoers

**Files created on server:**
- `/etc/sudoers.d/trading-spacial`

**Hard limit:** Only this file. NEVER edit `/etc/sudoers` directly. NEVER
write to other files in `/etc/sudoers.d/`.

> **Risk note:** Bad sudoers syntax can lock the operator out of sudo if it
> gets included before the syntax error is caught. We use `visudo -c -f` to
> validate the new file BEFORE it can affect sudo behavior.

- [ ] **Step D.1: Write the file**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  echo 'ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart trading-spacial, /bin/systemctl status trading-spacial, /usr/bin/systemctl restart trading-spacial, /usr/bin/systemctl status trading-spacial' \
    | sudo tee /etc/sudoers.d/trading-spacial > /dev/null
  sudo chmod 440 /etc/sudoers.d/trading-spacial
  echo '=== file contents ==='
  sudo cat /etc/sudoers.d/trading-spacial
  echo '=== file permissions ==='
  sudo ls -la /etc/sudoers.d/trading-spacial
"
```

Expected:
```
=== file contents ===
ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart trading-spacial, /bin/systemctl status trading-spacial, /usr/bin/systemctl restart trading-spacial, /usr/bin/systemctl status trading-spacial
=== file permissions ===
-r--r----- 1 root root ... /etc/sudoers.d/trading-spacial
```

Verification: Mode `440`, owner `root:root`.

- [ ] **Step D.2: Validate sudoers syntax**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo visudo -c -f /etc/sudoers.d/trading-spacial
'
```

Expected: `/etc/sudoers.d/trading-spacial: parsed OK`

If fails: **STOP IMMEDIATELY** and remove the file:

```bash
# RECOVERY (only if syntax check failed):
ssh ... 'sudo rm /etc/sudoers.d/trading-spacial'
```

Then report the syntax error to the operator.

- [ ] **Step D.3: Verify ubuntu can run the allowed commands without password**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo -n -l 2>&1 | grep -A2 -i trading || echo "(no match)"
'
```

Expected: lists the 4 allowed `systemctl restart/status` paths.

- [ ] **Step D.4: Verify a real `sudo systemctl status trading-spacial` works without password**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo -n systemctl status trading-spacial --no-pager 2>&1 | head -3
'
```

Expected: status output (the service is enabled but inactive). NO password
prompt, NO "sudo: a password is required" error.

If password prompt or sudo error: STOP. Sudoers entry is wrong.

- [ ] **Pause for operator confirmation before Task E.**

---

## Task E: nginx config + Let's Encrypt cert

**This is the riskiest task.** The host nginx serves 6+ production sites
(sdar.dev, n8n.sdar.dev, nido.sdar.dev, burgos.sdar.dev, openclo.sdar.dev,
api.samueldar.io). A bad `nginx -t` after `reload` will tank all of them.

**Files created/modified on server:**
- Create: `/etc/nginx/conf.d/limits-trading.conf` (rate-limit zones)
- Create: `/etc/nginx/conf.d/trading.sdar.dev.conf` (server block, evolves
  through HTTP-only → HTTPS)
- Create (by certbot): `/etc/letsencrypt/live/trading.sdar.dev/`
- Modified by certbot: `/etc/letsencrypt/renewal/trading.sdar.dev.conf`

**Hard limit:** ZERO edits to other files in `/etc/nginx/`. ZERO edits to
other certs in `/etc/letsencrypt/`. If `nginx -t` fails at any step, STOP.

### E.1: Rate-limit zones

- [ ] **Step E.1.1: Write `/etc/nginx/conf.d/limits-trading.conf`**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/nginx/conf.d/limits-trading.conf > /dev/null <<'LIMITS_EOF'
# Trading rate-limit zones — declared at http{} via conf.d/* include.
# 5 req/min strict para /api/auth/login (anti-brute-force).
# 60 req/min general para resto del backend.
limit_req_zone \$binary_remote_addr zone=trading_login:10m   rate=5r/m;
limit_req_zone \$binary_remote_addr zone=trading_general:10m rate=60r/m;
LIMITS_EOF

  echo '=== file contents ==='
  sudo cat /etc/nginx/conf.d/limits-trading.conf
"
```

Expected: file contents printed, with `$binary_remote_addr` (NOT escaped).

> Note: heredoc uses `\$` to escape `$` from local shell, but bash
> de-escapes once before sending. The remote shell receives `$binary_remote_addr`.

- [ ] **Step E.1.2: nginx -t**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo nginx -t 2>&1
'
```

Expected:
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test passed
```

If fails: **STOP**. Remove `/etc/nginx/conf.d/limits-trading.conf` if and
only if the error specifically mentions that file:

```bash
# RECOVERY (only if error mentions limits-trading.conf):
ssh ... 'sudo rm /etc/nginx/conf.d/limits-trading.conf && sudo nginx -t'
```

- [ ] **Step E.1.3: Reload nginx**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl reload nginx &&
  sudo systemctl status nginx --no-pager 2>&1 | head -5
'
```

Expected: `Active: active (running)`. No error logs.

If fails: STOP. nginx didn't reload — production sites may still be running
the previous config (which doesn't include our new zones, harmless), but
investigate before continuing.

### E.2: HTTP-only conf for ACME challenge

- [ ] **Step E.2.1: Write minimal HTTP-only conf**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/nginx/conf.d/trading.sdar.dev.conf > /dev/null <<'NGINX_HTTP_EOF'
server {
    listen 80;
    server_name trading.sdar.dev;
    location /.well-known/acme-challenge/ { root /var/www/trading/dist; }
    location / { return 200 \"ok\\n\"; }
}
NGINX_HTTP_EOF

  echo '=== file contents ==='
  sudo cat /etc/nginx/conf.d/trading.sdar.dev.conf
"
```

Expected: file contents printed back.

- [ ] **Step E.2.2: nginx -t**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo nginx -t 2>&1
'
```

Expected: `syntax is ok` + `test passed`.

If fails: STOP. Remove `/etc/nginx/conf.d/trading.sdar.dev.conf`.

- [ ] **Step E.2.3: Reload nginx + verify HTTP responds**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl reload nginx &&
  curl -s -o - -w "HTTP %{http_code}\n" http://localhost/ -H "Host: trading.sdar.dev"
'
```

Expected: `ok` + `HTTP 200`.

### E.3: Issue Let's Encrypt cert

- [ ] **Step E.3.1: Verify DNS resolves to this server (third-party check)**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  dig +short trading.sdar.dev @8.8.8.8
'
```

Expected: `13.48.46.19`

If wrong IP or empty: STOP. DNS not propagated — Let's Encrypt validation
will fail.

- [ ] **Step E.3.2: Issue cert via webroot**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo certbot certonly --webroot -w /var/www/trading/dist \
                       -d trading.sdar.dev \
                       --non-interactive --agree-tos \
                       -m samueldarioballesteros@gmail.com 2>&1
'
```

Expected:
```
Successfully received certificate.
Certificate is saved at: /etc/letsencrypt/live/trading.sdar.dev/fullchain.pem
Key is saved at:         /etc/letsencrypt/live/trading.sdar.dev/privkey.pem
This certificate expires on YYYY-MM-DD.
```

If fails: STOP. Common causes:
- DNS not pointing at this server (verified in E.3.1, but maybe stale)
- Port 80 blocked by AWS security group (would have failed earlier for other domains, unlikely)
- Rate limit hit on Let's Encrypt (5 certs/week per registered domain — sdar.dev already has 6 subdomains issued, getting close)

Recovery: do NOT retry without diagnosing. Ask operator.

- [ ] **Step E.3.3: Verify cert files exist**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo ls -la /etc/letsencrypt/live/trading.sdar.dev/
'
```

Expected: `cert.pem`, `chain.pem`, `fullchain.pem`, `privkey.pem` (all
symlinks to `../../archive/trading.sdar.dev/`).

### E.4: Switch to final HTTPS conf

- [ ] **Step E.4.1: Write the full HTTPS conf**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/nginx/conf.d/trading.sdar.dev.conf > /dev/null <<'NGINX_HTTPS_EOF'
# ── HTTPS ─────────────────────────────────────────────────────────────────
server {
    listen 443 ssl http2;
    server_name trading.sdar.dev;

    ssl_certificate     /etc/letsencrypt/live/trading.sdar.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/trading.sdar.dev/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    access_log /var/log/nginx/trading.sdar.dev.access.log;
    error_log  /var/log/nginx/trading.sdar.dev.error.log;

    add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;
    add_header X-Content-Type-Options    \"nosniff\"                              always;
    add_header X-Frame-Options           \"DENY\"                                 always;
    add_header Referrer-Policy           \"strict-origin-when-cross-origin\"     always;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    location = /api/auth/login {
        limit_req zone=trading_login burst=3 nodelay;
        proxy_pass         http://127.0.0.1:8100/auth/login;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
    }

    location /api/ {
        limit_req zone=trading_general burst=20 nodelay;
        proxy_pass         http://127.0.0.1:8100/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 5s;
    }

    root  /var/www/trading/dist;
    index index.html;

    location /assets/ {
        add_header Cache-Control \"public, max-age=31536000, immutable\";
        add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;
        add_header X-Content-Type-Options    \"nosniff\"                              always;
        add_header X-Frame-Options           \"DENY\"                                 always;
        add_header Referrer-Policy           \"strict-origin-when-cross-origin\"     always;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
        location = /index.html {
            add_header Cache-Control \"no-cache, no-store, must-revalidate\" always;
            add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;
            add_header X-Content-Type-Options    \"nosniff\"                              always;
            add_header X-Frame-Options           \"DENY\"                                 always;
            add_header Referrer-Policy           \"strict-origin-when-cross-origin\"     always;
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
        return 301 https://\$host\$request_uri;
    }
}
NGINX_HTTPS_EOF

  echo '=== file size ==='
  sudo wc -l /etc/nginx/conf.d/trading.sdar.dev.conf
"
```

Expected: `~75 etc/nginx/conf.d/trading.sdar.dev.conf` (around 75 lines).

- [ ] **Step E.4.2: nginx -t**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo nginx -t 2>&1
'
```

Expected: `syntax is ok` + `test passed`. Cert files exist now (issued in
E.3.2), so no cert-related errors.

If fails: **STOP**. Recovery — replace the file with the HTTP-only minimal
content from E.2.1 to keep nginx serving the existing 6 sites:

```bash
# RECOVERY (only if Step E.4.2 failed):
ssh ... "
  sudo tee /etc/nginx/conf.d/trading.sdar.dev.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name trading.sdar.dev;
    location /.well-known/acme-challenge/ { root /var/www/trading/dist; }
    location / { return 200 \"ok\\n\"; }
}
EOF
  sudo nginx -t
"
```

- [ ] **Step E.4.3: Reload nginx + verify HTTPS responds**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl reload nginx &&
  curl -s -o - -w "HTTP %{http_code}\n" -k https://localhost/ -H "Host: trading.sdar.dev" | head -10
'
```

Expected: HTML body of placeholder index.html + `HTTP 200`.

- [ ] **Step E.4.4: Verify HTTP → HTTPS redirect**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://localhost/ -H "Host: trading.sdar.dev"
'
```

Expected: `301 https://trading.sdar.dev/`.

- [ ] **Step E.4.5: Verify other sites still work (smoke test)**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  for host in n8n.sdar.dev nido.sdar.dev openclo.sdar.dev sdar.dev burgos.sdar.dev api.samueldar.io; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -k "https://localhost/" -H "Host: $host")
    echo "$host → $code"
  done
'
```

Expected: each one returns a non-5xx status (200, 301, 302, 401 are all
acceptable — they were responding the same way before our changes).

If any returns 5xx: STOP. Our changes broke a sibling site somehow
(unlikely with our isolation, but verify).

- [ ] **Pause for operator confirmation before Task F.**

---

## Task F: fail2ban jail

**Files created on server:**
- `/etc/fail2ban/filter.d/trading-login.conf`
- `/etc/fail2ban/jail.d/trading.conf`

**Hard limit:** Don't touch other jails (we verified there are none — but
still). Don't reload existing jails — `restart` is OK because we verified
fail2ban is currently inactive (per audit).

- [ ] **Step F.1: Confirm fail2ban not running yet**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl is-active fail2ban 2>&1
'
```

Expected: `inactive` (per pre-flight; we just installed it in A.3, didn't enable).

If `active`: STOP. Operator must verify what's running.

- [ ] **Step F.2: Write filter**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/fail2ban/filter.d/trading-login.conf > /dev/null <<'FILTER_EOF'
[Definition]
failregex = ^<HOST> .* \"POST /api/auth/login HTTP/[\\d\\.]+\" (401|503) .*\$
ignoreregex =
FILTER_EOF

  echo '=== filter ==='
  sudo cat /etc/fail2ban/filter.d/trading-login.conf
"
```

Expected: file contents back. Note `\$` becomes `$` after one level of bash
escaping.

- [ ] **Step F.3: Write jail**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com "
  sudo tee /etc/fail2ban/jail.d/trading.conf > /dev/null <<'JAIL_EOF'
[trading-login]
enabled  = true
port     = http,https
filter   = trading-login
logpath  = /var/log/nginx/trading.sdar.dev.access.log
maxretry = 10
findtime = 600
bantime  = 3600
JAIL_EOF

  echo '=== jail ==='
  sudo cat /etc/fail2ban/jail.d/trading.conf
"
```

Expected: file contents back.

- [ ] **Step F.4: Touch the log file so fail2ban doesn't error on missing path**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo touch /var/log/nginx/trading.sdar.dev.access.log &&
  sudo chown www-data:adm /var/log/nginx/trading.sdar.dev.access.log
'
```

Expected: no output, exit 0. The log will be populated by nginx as soon as
real traffic hits.

- [ ] **Step F.5: Enable + start fail2ban**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo systemctl enable fail2ban &&
  sudo systemctl start fail2ban &&
  sleep 2 &&
  sudo systemctl status fail2ban --no-pager 2>&1 | head -8
'
```

Expected: `Active: active (running)`. No error in log.

If fails: STOP. Common cause: bad regex in filter or missing log path.
Check `journalctl -u fail2ban -n 30 --no-pager`.

- [ ] **Step F.6: Verify our jail is loaded**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com '
  sudo fail2ban-client status 2>&1 &&
  echo "---" &&
  sudo fail2ban-client status trading-login 2>&1
'
```

Expected:
```
Status
|- Number of jail:	1
`- Jail list:	trading-login
---
Status for the jail: trading-login
|- Filter
|  |- Currently failed:	0
|  |- Total failed:	0
|  `- File list:	/var/log/nginx/trading.sdar.dev.access.log
`- Actions
   |- Currently banned:	0
   |- Total banned:	0
   `- Banned IP list:
```

If `Number of jail: 0` or jail not listed: STOP. Filter or jail conf wrong.

- [ ] **Pause for operator confirmation. Task F complete.**

---

## HARD STOP — Operator action required for Task G

> Task G is **NOT** automated. Operator does this in GitHub UI manually.

### Task G: GitHub Secrets (operator manual)

- [ ] **Operator: open `github.com/sssimon/trading-spacial/settings/secrets/actions`**

- [ ] **Add secret `DEPLOY_SSH_KEY`** with the same value used in `nido` and `openclo` (the private key whose public part is the `github-actions-portfolio-deploy` line in `~/.ssh/authorized_keys` on the server).

- [ ] **Add secret `DEPLOY_HOST`** with value `ec2-13-48-46-19.eu-north-1.compute.amazonaws.com`.

- [ ] **Verify** both secrets show up under `Repository secrets`. The values are write-only after creation — there's no read-back in the UI, which is correct.

> **Do NOT add `AUTH_JWT_SECRET` to GitHub Secrets.** It lives only in
> `/var/www/trading/.env` on the server (set by Task B.5).

- [ ] **Operator: signal agent to continue** (the agent waits for explicit "continue" before proceeding to write the deploy.yml workflow file).

---

## Task H — operator-triggered first deploy

> Task H is **NOT** automated. Operator pushes to main from their machine.

The agent will write `.github/workflows/deploy.yml` to the repo (separate
from this bootstrap; can be a follow-up PR or directly on main per operator's
preference). Operator merges the workflow change. The push itself is what
triggers the first deploy.

- [ ] **Agent: write `.github/workflows/deploy.yml`** matching Section 7
  of the spec. Open as a PR to `main`.

- [ ] **Operator: review the workflow PR + merge to main.**

- [ ] **Operator: watch GitHub Actions** at
  `github.com/sssimon/trading-spacial/actions`. The deploy job should:
  1. Build frontend (npm run build → frontend/dist)
  2. Verify .env exists on server (fail-fast)
  3. rsync backend
  4. rsync frontend dist
  5. pip install + sudo systemctl restart trading-spacial
  6. Health check `curl localhost:8100/health`

- [ ] **Operator: smoke test** `https://trading.sdar.dev` in a real browser.
  Expected: SPA loads, login form renders, no console errors.

- [ ] **Operator: signal agent if anything is broken** so we can debug
  together.

---

## Task I — operator-triggered admin user creation

> Task I is **NOT** automated. Operator does this manually via SSH.

- [ ] **Operator: SSH to server and run create_user.py**

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@ec2-13-48-46-19.eu-north-1.compute.amazonaws.com
# Now in the remote shell:
cd /var/www/trading
set -a && source .env && set +a   # exports AUTH_JWT_SECRET to current shell
.venv/bin/python scripts/create_user.py --role admin
# Interactive: prompts for email, then password (twice via getpass).
exit
```

- [ ] **Operator: log in at `https://trading.sdar.dev`** with the new admin
  credentials. Verify access to all dashboard sections.

- [ ] **Operator: validate fail2ban with 11 failed login attempts**
  (per spec §8.3). From any client, run:

```bash
for i in $(seq 1 11); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://trading.sdar.dev/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"x@x.x","password":"badpass"}'
done
```

Expected: a mix of `401` (backend rejection) and `503` (nginx rate limit).
Then verify ban:

```bash
ssh -i ~/.ssh/webserverkeypair.pem ubuntu@... 'sudo fail2ban-client status trading-login'
```

Expected: `Currently banned: 1`.

If the regex doesn't catch the codes that actually appear: edit
`/etc/fail2ban/filter.d/trading-login.conf` and `sudo systemctl restart fail2ban`.

---

## Self-review (this plan, before execution)

- [x] **Spec coverage**: every Paso A–I from spec §6/§8 is a Task here
- [x] **Placeholder scan**: no TBD/TODO/"add appropriate X"
- [x] **Type/path consistency**: paths all match spec (`/var/www/trading/`,
  `127.0.0.1:8100`, `AUTH_API_PREFIX=/api`, `(401|503)`, `ProtectSystem=strict`)
- [x] **Pause points**: 5 explicit pauses (after A, B, C, D, E, F), plus
  HARD STOP before G + H + I (operator-only)
- [x] **Failure handling**: every step has a "If fails: STOP" + recovery
  command where reversible
- [x] **Hard limits documented**: each task's "Hard limit" section names
  what NOT to touch
