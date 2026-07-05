"""Utilidades compartidas del cuadrante de turnos."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
CSV_PATH = ROOT / "turnos_jul_sep_2026.csv"
HTML_PATH = ROOT / "turnos.html"
PAGES_DIR = ROOT / "docs"
PAGES_INDEX_PATH = PAGES_DIR / "index.html"
PAGES_NOJEKYLL_PATH = PAGES_DIR / ".nojekyll"

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
    "cesantes",
)

# Solo edición manual en el CSV. El generador nunca las rellena (salvo copiar bloqueado).
# vacaciones / horas_extras: se copian al regenerar días no bloqueados.
# bloqueado: si está marcado, la fila entera no se recalcula (1, x, sí…).
COLUMNAS_ADMIN = (
    "vacaciones",
    "horas_extras",
    "bloqueado",
)

CAMPOS_OBLIGATORIOS = (
    ("socorrista_chapela", "socorrista chapela"),
    ("llave_cesantes", "abrir puesto"),
)

ETIQUETAS_VISTA = {
    "socorrista_chapela": "Soc. Chapela",
    "patron_chapela": "Patrón Chapela",
    "patron_cesantes": "Patrón Cesantes",
    "llave_cesantes": "Abrir puesto",
    "socorrista_zodiac": "Zodiac",
    "abrir_torre": "Torre",
    "cesantes": "Cesantes",
}

CAMPOS_OCULTOS_HTML = frozenset({"llave_chapela"})


def parse_fecha(s: str | date) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


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
        limites.append(hoy)

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


def nombres_cesantes_fila(fila: dict[str, str]) -> list[str]:
    if celda := fila.get("cesantes", "").strip():
        return parse_lista_nombres(celda)
    nombres: list[str] = []
    for clave in sorted(fila.keys()):
        if clave.startswith("cesantes") and clave != "cesantes" and (valor := fila.get(clave, "").strip()):
            nombres.append(solo_nombre(valor))
    return nombres


def normalizar_fila_csv(fila: dict[str, str]) -> dict[str, str]:
    """Une cesantes2+ en cesantes y elimina columnas legacy."""
    fila = dict(fila)
    if not fila.get("cesantes", "").strip():
        legacy = nombres_cesantes_fila(fila)
        if legacy:
            fila["cesantes"] = format_lista_nombres(legacy)
    for clave in list(fila.keys()):
        if clave.startswith("cesantes") and clave != "cesantes":
            del fila[clave]
    fila.setdefault("cesantes", "")
    for col in COLUMNAS_ADMIN:
        fila.setdefault(col, "")
    return fila


def columnas_csv_completas() -> list[str]:
    return list(COLUMNAS_CSV) + list(COLUMNAS_ADMIN)


def parse_lista_nombres(celda: str) -> list[str]:
    """Nombres separados por ; o , (solo nombre de pila)."""
    if not celda or not celda.strip():
        return []
    return [
        solo_nombre(parte.strip())
        for parte in celda.replace(",", ";").split(";")
        if parte.strip()
    ]


def celda_bloqueada(celda: str) -> bool:
    """True si la celda bloqueado marca la fila como no editable al regenerar."""
    if not celda or not str(celda).strip():
        return False
    return str(celda).strip().casefold() in {"1", "x", "sí", "si", "yes", "y", "true", "bloqueado"}


def format_lista_nombres(nombres: list[str]) -> str:
    return "; ".join(sorted({n for n in nombres if n}, key=str.casefold))


def parse_horas_extras(celda: str) -> dict[str, float]:
    """Formato: Nombre:horas; Nombre:horas (horas decimales permitidas)."""
    if not celda or not celda.strip():
        return {}
    resultado: dict[str, float] = {}
    for parte in celda.replace(",", ";").split(";"):
        parte = parte.strip()
        if not parte:
            continue
        if ":" not in parte:
            raise ValueError(f"horas_extras inválido: «{parte}» (use Nombre:horas)")
        nombre, horas_txt = parte.split(":", 1)
        nombre = solo_nombre(nombre.strip())
        horas = float(horas_txt.strip().replace(",", "."))
        resultado[nombre] = horas
    return resultado


def format_horas_extras(extras: dict[str, float]) -> str:
    return "; ".join(
        f"{nombre}:{horas:g}"
        for nombre, horas in sorted(extras.items(), key=lambda par: par[0].casefold())
    )


def fila_vacia_admin() -> dict[str, str]:
    return {col: "" for col in COLUMNAS_ADMIN}


def publicar_html_github_pages(origen: Path | None = None) -> Path:
    """Copia el HTML generado a docs/index.html (GitHub Pages)."""
    origen = origen or HTML_PATH
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_INDEX_PATH.write_text(origen.read_text(encoding="utf-8"), encoding="utf-8")
    # Evita que GitHub Pages ejecute Jekyll (sitio estático).
    PAGES_NOJEKYLL_PATH.touch(exist_ok=True)
    return PAGES_INDEX_PATH


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
