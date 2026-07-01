"""Utilidades compartidas del cuadrante de turnos."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
CSV_PATH = ROOT / "turnos_jul_sep_2026.csv"
HTML_PATH = ROOT / "turnos.html"

# Puestos que cuentan como asignación (llave_chapela es metadato)
PUESTOS_ASIGNACION = (
    "socorrista_chapela",
    "patron_chapela",
    "patron_cesantes",
    "llave_cesantes",
    "socorrista_zodiac",
    "abrir_torre",
)

COLUMNAS_CSV = (
    "fecha",
    "socorrista_chapela",
    "patron_chapela",
    "llave_chapela",
    "patron_cesantes",
    "socorrista_zodiac",
    "llave_cesantes",
    "abrir_torre",
)

CAMPOS_OBLIGATORIOS = (
    ("socorrista_chapela", "socorrista chapela"),
    ("patron_chapela", "patrón chapela"),
    ("llave_cesantes", "abrir puesto"),
)

ETIQUETAS_VISTA = {
    "socorrista_chapela": "Soc. Chapela",
    "patron_chapela": "Patrón Chapela",
    "patron_cesantes": "Patrón Cesantes",
    "llave_cesantes": "Abrir puesto",
    "socorrista_zodiac": "Zodiac",
    "abrir_torre": "Torre",
}

CAMPOS_OCULTOS_HTML = frozenset({"llave_chapela"})


def parse_fecha(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def cargar_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_filas_csv(path: Path | None = None) -> list[dict[str, str]]:
    with open(path or CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def filas_csv_por_fecha(path: Path | None = None) -> dict[str, dict[str, str]]:
    return {f["fecha"]: dict(f) for f in cargar_filas_csv(path)}


def fecha_congelacion_limite(cfg: dict, hoy: date | None = None) -> date | None:
    """Última fecha (inclusive) que no se regenera; None = regenerar todo."""
    cong = cfg.get("congelado") or {}
    hoy = hoy or date.today()
    limites: list[date] = []

    if cong.get("pasado_automatico", True):
        limites.append(hoy - timedelta(days=1))

    if hasta := cong.get("hasta"):
        limites.append(parse_fecha(hasta))

    if not limites:
        return None
    return max(limites)


def solo_nombre(nombre: str) -> str:
    """Solo nombre de pila en el CSV (Vacante 1, 2… se mantiene entero)."""
    if not nombre:
        return ""
    if nombre.startswith("Vacante"):
        return nombre
    return nombre.split()[0]


def sin_vacantes_roster(roster: list[str]) -> list[str]:
    return [n for n in roster if not n.startswith("Vacante")]


def columnas_puesto(fila: dict[str, str]) -> list[str]:
    return list(PUESTOS_ASIGNACION) + sorted(k for k in fila if k.startswith("cesantes"))


def columnas_cesantes_extra(cantidad: int) -> list[str]:
    return [f"cesantes{i}" for i in range(2, 2 + cantidad)]


def columnas_csv_completas(max_cesantes: int) -> list[str]:
    return list(COLUMNAS_CSV) + columnas_cesantes_extra(max_cesantes)


def etiqueta_periodo(cfg: dict) -> str:
    ini = cfg.get("periodo", {}).get("inicio", "")
    fin = cfg.get("periodo", {}).get("fin", "")
    if not ini or not fin:
        return ""
    d_ini = parse_fecha(ini)
    d_fin = parse_fecha(fin)
    meses = (
        "",
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    )
    if d_ini.year == d_fin.year and d_ini.month == d_fin.month:
        return f"{meses[d_ini.month]} {d_ini.year}"
    if d_ini.year == d_fin.year:
        return f"{meses[d_ini.month]} – {meses[d_fin.month]} {d_ini.year}"
    return f"{d_ini.isoformat()} – {fin}"
