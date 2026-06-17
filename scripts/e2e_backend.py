"""
Arranca el backend en :8001 con el bypass de auth activo para E2E.
Uso: python scripts/e2e_backend.py  (desde la raiz del repo)
     o: python D:/Desktop/projects/trading-spacial/scripts/e2e_backend.py
"""
import os
import sys
import types
import pathlib

# Asegurar que el root del repo esté en el path
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Cargar .env si existe (para AUTH_JWT_SECRET y otras vars)
env_file = ROOT / '.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        os.environ.setdefault(key.strip(), val.strip())

# El middleware de bypass requiere que 'pytest' esté en sys.modules
# (triple guarda interna para que no sea activable en prod por accidente).
sys.modules.setdefault('pytest', types.ModuleType('pytest'))

os.environ['AUTH_TEST_BYPASS_ALLOWED'] = '1'
os.environ['AUTH_TEST_BYPASS_ROLE'] = 'admin'

import uvicorn
uvicorn.run('btc_api:app', host='127.0.0.1', port=8001, log_level='warning')
