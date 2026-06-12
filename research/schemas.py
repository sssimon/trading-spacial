"""Schema del dossier de due-diligence (estricto, sin campo de opinión).

Cada hecho lleva su `fuente` (URL). `extra='forbid'` en todos los modelos:
un campo de opinión que el LLM intente meter → output rechazado (frontera de
Voronov por construcción). Spec §2."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class MiembroEquipo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str
    rol: str | None = None
    enlaces: list[str] = []
    fuente: str


class Canal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = None
    activo: Literal["si", "no", "desconocido"] = "desconocido"
    fuente: str | None = None


class Cita(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valor: str
    fuente: str


class Hito(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descripcion: str
    fecha: str | None = None
    fuente: str


class Dossier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    equipo: list[MiembroEquipo] = []
    equipo_identificado: bool = False
    # presencia keys esperadas: sitio_web, github, twitter, telegram_discord, whitepaper.
    presencia: dict[str, Canal] = {}
    # actividad keys esperadas: ultimo_commit_github, ultimo_release, ultimo_post_anuncio.
    actividad: dict[str, Cita] = {}
    financiacion: list[Hito] = []
    hitos: list[Hito] = []
    estado_general: Literal["rastreable", "opaco", "no_disponible"]
    # Qué se buscó y NO apareció (la ausencia es información — spec §2).
    no_encontrado_en: list[str] = []
    generated_at: str | None = None
