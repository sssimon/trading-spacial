"""
Configuración compartida de pytest para el proyecto BTC Scanner.
"""
import sys
import os

# Asegurar que el directorio raíz esté en el path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
