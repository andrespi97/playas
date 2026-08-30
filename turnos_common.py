"""Utilidades compartidas del cuadrante de turnos."""

from __future__ import annotations

import calendar
import csv
import shutil
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
CSV_PATH = ROOT / "turnos_jul_sep_2026.csv"
HTML_PATH = ROOT / "turnos.html"
VENDOR_DIR = ROOT / "vendor"
PAGES_DIR = ROOT / "docs"
PAGES_INDEX_PATH = PAGES_DIR / "index.html"
PAGES_AGOSTO_PATH = PAGES_DIR / "agosto.html"
PAGES_NOJEKYLL_PATH = PAGES_DIR / ".nojekyll"

# Jornada ordinaria de un día asignado sin marca de horas_extras
HORAS_JORNADA = 8.0

# 1 día extra → 1,5 días libres (Alex / Claudio). Editable en config.yaml.
FACTOR_COMPENSACION = 1.5
MARCA_COMPENSADO = "compensado"

# Socorristas cuyas horas siempre van a extras (nunca turno ordinario).
# Si están asignados sin horas_extras explícitas, cuentan 8 h como extra.
HORAS_SOLO_EXTRAS = frozenset({"Aaron", "Anxo", "Arturo", "Rober"})

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
# bloqueado: si está marcado, la fila no se toca al regenerar (1, x, sí…), jamás.
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


def filas_bloqueadas_por_fecha(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Copia literal de filas con bloqueado activo; el generador no las toca."""
    return {
        fecha: dict(fila)
        for fecha, fila in filas_csv_por_fecha(path).items()
        if celda_bloqueada(fila.get("bloqueado", ""))
    }


def fechas_bloqueadas_csv(path: Path | None = None) -> set[str]:
    return set(filas_bloqueadas_por_fecha(path))


def cargar_existentes_csv(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Carga el CSV; las filas bloqueadas se conservan sin normalizar."""
    existentes: dict[str, dict[str, str]] = {}
    for fecha, fila in filas_csv_por_fecha(path).items():
        if celda_bloqueada(fila.get("bloqueado", "")):
            existentes[fecha] = dict(fila)
        else:
            existentes[fecha] = normalizar_fila_csv(fila)
    return existentes


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


def nombres_en_cesantes(fila: dict[str, str]) -> list[str]:
    """Puestos en playa Cesantes ese día: patrón, abrir puesto y refuerzos."""
    nombres: list[str] = []
    vistos: set[str] = set()
    for campo in ("patron_cesantes", "llave_cesantes"):
        if valor := fila.get(campo, "").strip():
            sn = solo_nombre(valor)
            if sn and sn not in vistos:
                nombres.append(sn)
                vistos.add(sn)
    for nombre in nombres_cesantes_fila(fila):
        if nombre and nombre not in vistos:
            nombres.append(nombre)
            vistos.add(nombre)
    return nombres


def _en_puesto_chapela(fila: dict[str, str], nombre: str) -> bool:
    for campo in ("socorrista_chapela", "patron_chapela", "llave_chapela"):
        if solo_nombre(fila.get(campo, "").strip()) == nombre:
            return True
    return False


def contar_socorristas_cesantes(
    fila: dict[str, str],
    *,
    sustitutos: list[str] | None = None,
    patron_solo_zodiac: list[str] | None = None,  # legacy no-op
) -> int:
    """Socorristas reales en el lado Cesantes (únicos; las vacantes no cuentan).

    Cuenta nombres no-vacante en:
    - abrir puesto (llave) y refuerzos de Cesantes
    - Torre (abrir_torre) y Zodiac (socorrista_zodiac), que operan desde Cesantes

    El patrón Cesantes no cuenta (ni quien cubra esa vacante).

    Si una vacante de Cesantes (no la del patrón) está cubierta por un
    extra/sustituto que no figura ya como nombre real, se cuenta el cubridor
    una vez (sin duplicar ni sumar extras de Chapela).
    """
    del patron_solo_zodiac
    vistos: set[str] = set()
    resultado: list[str] = []
    patron = solo_nombre(fila.get("patron_cesantes", "").strip())

    def anadir(nombre: str) -> None:
        if not nombre or es_nombre_vacante(nombre) or nombre in vistos:
            return
        if nombre == patron:
            return
        resultado.append(nombre)
        vistos.add(nombre)

    for nombre in nombres_en_cesantes(fila):
        anadir(nombre)

    if torre := solo_nombre(fila.get("abrir_torre", "").strip()):
        anadir(torre)

    if zodiac := solo_nombre(fila.get("socorrista_zodiac", "").strip()):
        anadir(zodiac)

    # Cubridores de vacantes de Cesantes (excepto la del patrón).
    vacantes_ces = [n for n in nombres_en_cesantes(fila) if es_nombre_vacante(n)]
    if vacantes_ces:
        cubridores = cubridores_vacantes_fila(fila, sustitutos or [])
        for _vacante, cubridor in zip(vacantes_ces, cubridores):
            if _vacante == patron:
                continue
            if not cubridor or cubridor in vistos:
                continue
            if _en_puesto_chapela(fila, cubridor):
                continue
            resultado.append(cubridor)
            vistos.add(cubridor)

    return len(resultado)


def parse_horas_pendientes(cfg: dict | None) -> dict[str, float]:
    """Horas extras pendientes de pagar (config.yaml → horas_pendientes)."""
    if not cfg:
        return {}
    bruto = cfg.get("horas_pendientes") or {}
    if not isinstance(bruto, dict):
        return {}
    resultado: dict[str, float] = {}
    for clave, valor in bruto.items():
        nombre = solo_nombre(str(clave).strip())
        if not nombre or es_nombre_vacante(nombre):
            continue
        try:
            horas = float(valor)
        except (TypeError, ValueError):
            continue
        if horas <= 0:
            continue
        resultado[nombre] = horas
    return dict(sorted(resultado.items(), key=lambda p: (-p[1], p[0].casefold())))


def parse_compensacion(cfg: dict | None) -> tuple[float, frozenset[str]]:
    """Factor y nombres de pila que cobran extras en días libres."""
    if not cfg:
        return FACTOR_COMPENSACION, frozenset()
    bruto = cfg.get("compensacion") or {}
    if not isinstance(bruto, dict):
        return FACTOR_COMPENSACION, frozenset()
    try:
        factor = float(bruto.get("factor", FACTOR_COMPENSACION))
    except (TypeError, ValueError):
        factor = FACTOR_COMPENSACION
    if factor <= 0:
        factor = FACTOR_COMPENSACION
    personas = frozenset(
        solo_nombre(str(n).strip())
        for n in (bruto.get("personas") or [])
        if str(n).strip() and not es_nombre_vacante(str(n).strip())
    )
    return factor, personas


def contar_horas_compensadas(filas: list[dict[str, str]]) -> dict[str, float]:
    """Suma de horas marcadas Nombre:horas:compensado en el CSV."""
    usado: dict[str, float] = {}
    for fila in filas:
        try:
            compensado = parse_horas_compensadas(fila.get("horas_extras", ""))
        except ValueError:
            continue
        for nombre, horas in compensado.items():
            usado[nombre] = usado.get(nombre, 0.0) + float(horas)
    return usado


def saldos_compensacion(
    cfg: dict | None,
    filas: list[dict[str, str]],
) -> list[dict[str, float | str]]:
    """Crédito (pendientes × factor), gastado en el CSV y restante por persona."""
    factor, personas = parse_compensacion(cfg)
    if not personas:
        return []
    pendientes = parse_horas_pendientes(cfg)
    usado = contar_horas_compensadas(filas)
    resultado: list[dict[str, float | str]] = []
    for nombre in personas:
        pend = float(pendientes.get(nombre, 0.0))
        credito = pend * factor
        gastado = float(usado.get(nombre, 0.0))
        resultado.append(
            {
                "nombre": nombre,
                "pendientes": pend,
                "credito": credito,
                "gastado": gastado,
                "restante": credito - gastado,
            }
        )
    return sorted(
        resultado,
        key=lambda p: (-float(p["restante"]), str(p["nombre"]).casefold()),
    )


def errores_saldos_compensacion(
    cfg: dict | None,
    filas: list[dict[str, str]],
) -> list[str]:
    errores: list[str] = []
    for saldo in saldos_compensacion(cfg, filas):
        if float(saldo["restante"]) < -1e-9:
            errores.append(
                f"{saldo['nombre']}: compensación usada ({float(saldo['gastado']):g} h) "
                f"supera el crédito ({float(saldo['credito']):g} h)"
            )
    return errores


def nombres_ausentes_admin_fila(fila: dict[str, str]) -> set[str]:
    """Vacaciones y compensación (Nombre:horas:compensado) del día."""
    ausentes = set(parse_lista_nombres(fila.get("vacaciones", "")))
    try:
        ausentes.update(parse_horas_compensadas(fila.get("horas_extras", "")))
    except ValueError:
        pass
    return ausentes


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


def parse_anotaciones_horas(celda: str) -> tuple[dict[str, float], dict[str, float]]:
    """Formato: Nombre:horas o Nombre:horas:compensado, separados por ;.

    Devuelve (extras trabajadas, horas compensadas / días libres).
    """
    extras: dict[str, float] = {}
    compensado: dict[str, float] = {}
    if not celda or not celda.strip():
        return extras, compensado
    for parte in celda.replace(",", ";").split(";"):
        parte = parte.strip()
        if not parte:
            continue
        if ":" not in parte:
            raise ValueError(
                f"horas_extras inválido: «{parte}» "
                f"(use Nombre:horas o Nombre:horas:{MARCA_COMPENSADO})"
            )
        trozos = [t.strip() for t in parte.split(":")]
        if len(trozos) == 2:
            nombre_txt, horas_txt = trozos
            marca = ""
        elif len(trozos) == 3:
            nombre_txt, horas_txt, marca = trozos
        else:
            raise ValueError(
                f"horas_extras inválido: «{parte}» "
                f"(use Nombre:horas o Nombre:horas:{MARCA_COMPENSADO})"
            )
        nombre = solo_nombre(nombre_txt)
        if not nombre:
            raise ValueError(f"horas_extras inválido: «{parte}»")
        try:
            horas = float(horas_txt.replace(",", "."))
        except ValueError as e:
            raise ValueError(
                f"horas_extras inválido: «{parte}» "
                f"(use Nombre:horas o Nombre:horas:{MARCA_COMPENSADO})"
            ) from e
        if marca.casefold() == MARCA_COMPENSADO:
            compensado[nombre] = horas
        elif not marca:
            extras[nombre] = horas
        else:
            raise ValueError(f"horas_extras inválido: marca «{marca}» en «{parte}»")
    return extras, compensado


def parse_horas_extras(celda: str) -> dict[str, float]:
    """Extras trabajadas: Nombre:horas (ignora entradas :compensado)."""
    extras, _ = parse_anotaciones_horas(celda)
    return extras


def parse_horas_compensadas(celda: str) -> dict[str, float]:
    """Días libres por compensación: Nombre:horas:compensado."""
    _, compensado = parse_anotaciones_horas(celda)
    return compensado


def format_horas_extras(
    extras: dict[str, float],
    compensado: dict[str, float] | None = None,
) -> str:
    partes = [
        f"{nombre}:{horas:g}"
        for nombre, horas in sorted(extras.items(), key=lambda par: par[0].casefold())
    ]
    if compensado:
        partes.extend(
            f"{nombre}:{horas:g}:{MARCA_COMPENSADO}"
            for nombre, horas in sorted(
                compensado.items(), key=lambda par: par[0].casefold()
            )
        )
    return "; ".join(partes)


def es_nombre_vacante(nombre: str) -> bool:
    return bool(nombre) and nombre.startswith("Vacante")


def contar_horas_fila(
    fila: dict[str, str],
    *,
    horas_jornada: float = HORAS_JORNADA,
) -> dict[str, dict[str, float | int]]:
    """Horas de turno y extras por persona en un día.

    - Si figura en horas_extras: cuenta esas horas como extras (no se suma jornada).
    - Si está asignado y no tiene extras ese día: suma horas_jornada como turno.
    - HORAS_SOLO_EXTRAS: asignado sin entrada explícita → horas_jornada como extra.
    - Las vacantes no cuentan.
    """
    try:
        extras = parse_horas_extras(fila.get("horas_extras", ""))
    except ValueError:
        extras = {}
    asignados = [
        n for n in nombres_asignados_dia(fila) if not es_nombre_vacante(n)
    ]
    resultado: dict[str, dict[str, float | int]] = {}

    def fila_persona(nombre: str) -> dict[str, float | int]:
        if nombre not in resultado:
            resultado[nombre] = {
                "dias_turno": 0,
                "horas_turno": 0.0,
                "dias_extra": 0,
                "horas_extras": 0.0,
            }
        return resultado[nombre]

    for nombre, horas in extras.items():
        if es_nombre_vacante(nombre):
            continue
        p = fila_persona(nombre)
        p["dias_extra"] = int(p["dias_extra"]) + 1
        p["horas_extras"] = float(p["horas_extras"]) + float(horas)

    for nombre in asignados:
        if nombre in extras:
            continue
        p = fila_persona(nombre)
        if nombre in HORAS_SOLO_EXTRAS:
            p["dias_extra"] = int(p["dias_extra"]) + 1
            p["horas_extras"] = float(p["horas_extras"]) + float(horas_jornada)
            continue
        p["dias_turno"] = int(p["dias_turno"]) + 1
        p["horas_turno"] = float(p["horas_turno"]) + float(horas_jornada)

    return resultado


def contar_horas_por_mes(
    filas: list[dict[str, str]],
    *,
    horas_jornada: float = HORAS_JORNADA,
    plantilla: list[str] | None = None,
) -> dict[tuple[int, int], dict[str, dict[str, float | int]]]:
    """Totales por (año, mes) y persona: dias_turno, horas_turno, dias_extra, horas_extras, total."""
    por_mes: dict[tuple[int, int], dict[str, dict[str, float | int]]] = {}
    for fila in filas:
        d = parse_fecha(fila["fecha"])
        clave = (d.year, d.month)
        mes = por_mes.setdefault(clave, {})
        for nombre, parcial in contar_horas_fila(fila, horas_jornada=horas_jornada).items():
            dest = mes.setdefault(
                nombre,
                {
                    "dias_turno": 0,
                    "horas_turno": 0.0,
                    "dias_extra": 0,
                    "horas_extras": 0.0,
                    "total": 0.0,
                },
            )
            dest["dias_turno"] = int(dest["dias_turno"]) + int(parcial["dias_turno"])
            dest["horas_turno"] = float(dest["horas_turno"]) + float(parcial["horas_turno"])
            dest["dias_extra"] = int(dest["dias_extra"]) + int(parcial["dias_extra"])
            dest["horas_extras"] = float(dest["horas_extras"]) + float(parcial["horas_extras"])
            dest["total"] = float(dest["horas_turno"]) + float(dest["horas_extras"])

    if plantilla:
        for mes in por_mes.values():
            for nombre in plantilla:
                if es_nombre_vacante(nombre):
                    continue
                mes.setdefault(
                    nombre,
                    {
                        "dias_turno": 0,
                        "horas_turno": 0.0,
                        "dias_extra": 0,
                        "horas_extras": 0.0,
                        "total": 0.0,
                    },
                )
    return por_mes


def nombres_asignados_dia(fila: dict[str, str]) -> list[str]:
    """Nombres de pila asignados ese día (puestos + cesantes)."""
    nombres: list[str] = []
    vistos: set[str] = set()
    for campo in PUESTOS_ASIGNACION:
        if valor := fila.get(campo, "").strip():
            sn = solo_nombre(valor)
            if sn not in vistos:
                nombres.append(sn)
                vistos.add(sn)
    for nombre in parse_lista_nombres(fila.get("cesantes", "")):
        if nombre not in vistos:
            nombres.append(nombre)
            vistos.add(nombre)
    return nombres


def sustitutos_presentes_fila(fila: dict[str, str], sustitutos: list[str]) -> list[str]:
    """Sustitutos que trabajan ese día, en el orden del config."""
    if not sustitutos:
        return []
    ausentes = nombres_ausentes_admin_fila(fila)
    asignados = set(nombres_asignados_dia(fila))
    return [solo_nombre(nombre) for nombre in sustitutos if solo_nombre(nombre) in asignados and solo_nombre(nombre) not in ausentes]


def cubridores_vacantes_fila(fila: dict[str, str], sustitutos: list[str]) -> list[str]:
    """Personas que cubren vacantes: sustitutos asignados y trabajadores en horas_extras."""
    ausentes = nombres_ausentes_admin_fila(fila)
    asignados = set(nombres_asignados_dia(fila))
    cubridores: list[str] = []
    vistos: set[str] = set()

    for nombre in sustitutos:
        sn = solo_nombre(nombre)
        if sn in ausentes or sn in vistos:
            continue
        if sn in asignados:
            cubridores.append(sn)
            vistos.add(sn)

    try:
        extras = parse_horas_extras(fila.get("horas_extras", ""))
    except ValueError:
        extras = {}
    for nombre in extras:
        sn = solo_nombre(nombre)
        if sn in ausentes or sn in vistos:
            continue
        cubridores.append(sn)
        vistos.add(sn)

    return cubridores


def marcar_vacantes_cubiertas(
    puestos: list[dict[str, str | bool]],
    cubridores: list[str],
    no_patron: frozenset[str] | set[str] | None = None,
) -> None:
    """Etiqueta vacantes como cubiertas; respeta no_patron en puestos de patrón."""
    if not cubridores:
        return
    prohibidos = {solo_nombre(n) for n in (no_patron or ())}
    disponibles = list(cubridores)
    for idx, puesto in enumerate(puestos):
        if not str(puesto.get("persona", "")).startswith("Vacante"):
            continue
        es_patron = str(puesto.get("campo", "")) in ("patron_chapela", "patron_cesantes")
        cubridor: str | None = None
        for j, nombre in enumerate(disponibles):
            if es_patron and solo_nombre(nombre) in prohibidos:
                continue
            cubridor = disponibles.pop(j)
            break
        if cubridor:
            puestos[idx]["vacante_cubierta"] = True
            puestos[idx]["sustituto"] = cubridor


def fila_vacia_admin() -> dict[str, str]:
    return {col: "" for col in COLUMNAS_ADMIN}


def publicar_html_github_pages(origen: Path | None = None) -> Path:
    """Copia el HTML generado a docs/ (GitHub Pages)."""
    origen = origen or HTML_PATH
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_INDEX_PATH.write_text(origen.read_text(encoding="utf-8"), encoding="utf-8")
    if VENDOR_DIR.is_dir():
        shutil.copytree(VENDOR_DIR, PAGES_DIR / "vendor", dirs_exist_ok=True)
    # Evita que GitHub Pages ejecute Jekyll (sitio estático).
    PAGES_NOJEKYLL_PATH.touch(exist_ok=True)
    return PAGES_INDEX_PATH


def etiqueta_periodo(cfg: dict) -> str:
    periodo = cfg.get("periodo", {})
    ini = periodo.get("inicio", "")
    fin = periodo.get("vista_hasta") or periodo.get("fin", "")
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
    ultimo_dia = calendar.monthrange(d_fin.year, d_fin.month)[1]
    fin_txt = meses[d_fin.month]
    if d_fin.day != ultimo_dia:
        fin_txt = f"{d_fin.day} {meses[d_fin.month].lower()}"
    if d_ini.year == d_fin.year and d_ini.month == d_fin.month and d_fin.day == ultimo_dia:
        return f"{meses[d_ini.month]} {d_ini.year}"
    if d_ini.year == d_fin.year:
        return f"{meses[d_ini.month]} – {fin_txt} {d_ini.year}"
    return f"{d_ini.isoformat()} – {fin}"
