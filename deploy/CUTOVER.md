# Cutover a blue-green — runbook (one-time, manual, zero-downtime)

Migra el server (`ubuntu@trading` / alias `atrium-aws`, `/var/www/trading`) del
modelo single-process (`trading-spacial`, scanner in-process) al blue-green
(`trading-scanner` + `trading-api@8100/8101`). **El cliente nunca queda sin
upstream.** Rollback en cada punto. Requiere PR1 ya en prod (gates + scanner_main
+ /health/live) — ✅ ya está.

> Verificá antes: `curl -fsS http://localhost:8100/health/live` da `{"ready":true}`
> y `ls /var/www/trading/scanner_main.py` existe (lo trae el rsync de PR1).

## 0. Pre-flight (sin downtime)
```bash
ssh atrium-aws
cd /var/www/trading
sudo cp /etc/nginx/conf.d/trading.sdar.dev.conf ~/trading.sdar.dev.conf.bak.$(date +%s)   # backup nginx
ls deploy/   # confirmá que los artefactos llegaron por rsync
```

## 1. Instalar los 3 units + sudoers (sin activar)
```bash
sudo cp deploy/trading-scanner.service /etc/systemd/system/trading-scanner.service
sudo cp deploy/trading-api@.service     /etc/systemd/system/trading-api@.service
sudo install -m 0440 deploy/sudoers-trading /etc/sudoers.d/trading-deploy
sudo visudo -c                          # valida sudoers; si falla, rm el archivo
sudo systemctl daemon-reload
```

## 2. Instalar el include de nginx + chown (para que el deploy lo reescriba sin sudo)
```bash
sudo cp deploy/trading-upstream.conf /etc/nginx/conf.d/trading-upstream.conf
sudo chown ubuntu:ubuntu /etc/nginx/conf.d/trading-upstream.conf
# El include arranca apuntando a :8100 (el proceso viejo) → cero impacto.
```

## 3. Apuntar el server block al upstream (UNA vez)
Editar `/etc/nginx/conf.d/trading.sdar.dev.conf`: en `location /api/` y en
`location = /api/auth/login`, cambiar `proxy_pass http://127.0.0.1:8100/...`
por `proxy_pass http://trading_api/...` (mismo path-rewrite). Aislar el SSE:
```nginx
location /api/agent/ {
    proxy_pass http://trading_api/agent/;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_set_header Host $host;
    # (replicar los proxy_set_header del location /api/ existente)
}
```
```bash
sudo nginx -t && sudo nginx -s reload    # aún apunta a :8100 (proceso viejo) → cero impacto
```

## 4. CUTOVER sin hueco (minimizar 4a→4d)
```bash
# 4a — arrancar el scanner-service (dueño del schema). Esperá READY + scans.
sudo systemctl start trading-scanner
sudo systemctl status trading-scanner --no-pager -n 5   # active, "READY"
sudo journalctl -u trading-scanner -n 20 --no-pager     # 5 threads arrancan, escribe scans

# 4b — arrancar la API web-only en el puerto LIBRE (verificá que :8101 esté libre)
ss -ltnp | grep 8101 || echo "8101 libre"
sudo systemctl start trading-api@8101
for i in $(seq 1 15); do curl -fsS http://localhost:8101/health/live && break; sleep 1; done   # 200 ~2s

# 4c — swap atómico del include → :8101 + reload (cliente pasa a la API sin scanner)
printf 'upstream trading_api {\n    server 127.0.0.1:8101;\n}\n' > /tmp/tu.conf
mv -f /tmp/tu.conf /etc/nginx/conf.d/trading-upstream.conf
sudo nginx -t && sudo nginx -s reload

# 4d — INMEDIATO: parar el viejo (mata el 2º scanner in-process, libera :8100)
sudo systemctl stop trading-spacial     # desde aquí: UN solo scanner

# 4e — arrancar el standby en :8100
sudo systemctl start trading-api@8100
for i in $(seq 1 15); do curl -fsS http://localhost:8100/health/live && break; sleep 1; done

# 4f — deshabilitar el viejo (cierra el riesgo de doble-scanner por reboot)
sudo systemctl disable trading-spacial
systemctl is-active trading-spacial     # debe decir "inactive"
sudo systemctl enable trading-scanner trading-api@8100 trading-api@8101   # arrancan en boot
```

## 5. Verificación
```bash
curl -fsS http://localhost:8101/health/live   # {"ready":true}
curl -s   http://localhost:8101/health        # {"healthy":true,...,"scanner":"fresco"}
sudo journalctl -u trading-scanner -n 5 --no-pager   # SOLO acá debe loguear scans (un escritor)
curl -k -s -o /dev/null -w "%{http_code}\n" --resolve trading.sdar.dev:443:127.0.0.1 https://trading.sdar.dev/api/auth/me   # 401 (sano)
```

## 6. Mergear el PR2 (deploy.yml blue-green)
Solo DESPUÉS de que el cutover quedó verde. El primer deploy nuevo corre la
rutina blue-green contra el server ya migrado. Deploy de prueba (commit no-op)
con un monitor en paralelo:
```bash
while true; do curl -k -s -o /dev/null -w '%{http_code}\n' --resolve trading.sdar.dev:443:127.0.0.1 https://trading.sdar.dev/api/health/live; sleep 0.5; done
# Confirmá CERO 502/503 en la ventana del swap.
```

## Rollback (en cualquier punto)
```bash
# Reapuntar nginx al color sano + reload:
printf 'upstream trading_api {\n    server 127.0.0.1:<color-sano>;\n}\n' > /tmp/tu.conf
mv -f /tmp/tu.conf /etc/nginx/conf.d/trading-upstream.conf && sudo nginx -s reload
# O volver al modelo viejo entero:
sudo systemctl stop trading-api@8100 trading-api@8101 trading-scanner
sudo systemctl enable --now trading-spacial          # el unit viejo sigue en disco
# restaurar el server block: sudo cp ~/trading.sdar.dev.conf.bak.<ts> /etc/nginx/conf.d/trading.sdar.dev.conf && sudo nginx -s reload
```
El código es backward-compatible (RUN_SCANNER default '1', SKIP_DB_INIT ausente → el viejo migra + escanea como hoy).
