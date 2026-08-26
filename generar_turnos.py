#!/usr/bin/env python3
"""
Genera CSV de turnos para socorristas y patrones.
Lee config.yaml en tiempo real — edita el YAML y vuelve a ejecutar.

Rotaciones en bloques de 4 días (alineados al ciclo 4/2), sin intercalar día a día.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from turnos_common import (
    CAMPOS_OBLIGATORIOS,
    COLUMNAS_ADMIN,
    CONFIG_PATH,
    CSV_PATH,
    PUESTOS_ASIGNACION,
    columnas_csv_completas,
    cargar_config,
    celda_bloqueada,
    cargar_existentes_csv,
    fecha_congelacion_limite,
    filas_bloqueadas_por_fecha,
    format_lista_nombres,
    nombres_asignados_dia,
    normalizar_fila_csv,
    parse_fecha,
    parse_horas_extras,
    parse_horas_compensadas,
    parse_anotaciones_horas,
    parse_compensacion,
    parse_lista_nombres,
    errores_saldos_compensacion,
    sin_vacantes_roster,
    solo_nombre,
    es_nombre_vacante,
    _en_puesto_chapela,
)


class ErrorConfig(Exception):
    """Configuración inválida."""


class ErrorGeneracion(Exception):
    """Cuadrante inválido; no se escribe CSV."""

    def __init__(self, errores: list[str]) -> None:
        self.errores = errores
        super().__init__(f"{len(errores)} error(es) de generación")


@dataclass(frozen=True)
class Persona:
    nombre: str
    grupo: int
    rol: str  # "socorrista" | "patron"


EQUILIBRIO_ZODIAC_DESDE = date(2026, 8, 18)


def cargar_config_validada() -> dict:
    cfg = cargar_config()
    if errores := validar_config(cfg):
        raise ErrorConfig("\n".join(errores))
    return cfg


def validar_config(cfg: dict) -> list[str]:
    errores: list[str] = []

    periodo = cfg.get("periodo", {})
    for clave in ("inicio", "fin"):
        if clave not in periodo:
            errores.append(f"Falta periodo.{clave}")
    if "inicio" in periodo and "fin" in periodo:
        try:
            inicio = parse_fecha(periodo["inicio"])
            fin = parse_fecha(periodo["fin"])
            if fin < inicio:
                errores.append("periodo.fin anterior a periodo.inicio")
        except ValueError:
            errores.append("periodo con fechas inválidas (use YYYY-MM-DD)")

    rot = cfg.get("rotacion", {})
    for clave in ("dias_trabajo", "dias_libres", "desfase_grupos"):
        if clave not in rot:
            errores.append(f"Falta rotacion.{clave}")
    if rot.get("desfase_grupos") and len(rot["desfase_grupos"]) != 3:
        errores.append("rotacion.desfase_grupos debe tener 3 entradas (grupos 1–3)")
    if rot.get("dias_trabajo", 0) < 1 or rot.get("dias_libres", 0) < 1:
        errores.append("rotacion.dias_trabajo y dias_libres deben ser >= 1")

    nombres_registrados: set[str] = set()
    for lista, rol in (("socorristas", "socorrista"), ("patrones", "patron")):
        if lista not in cfg:
            errores.append(f"Falta sección {lista}")
            continue
        for entrada in cfg[lista]:
            if "nombre" not in entrada or "grupo" not in entrada:
                errores.append(f"Entrada incompleta en {lista}: {entrada}")
                continue
            if entrada["grupo"] not in (1, 2, 3):
                errores.append(f"Grupo inválido en {lista}: {entrada['nombre']}")
            nombres_registrados.add(entrada["nombre"])

    prefs = cfg.get("preferencias", {})
    listas_nombre = (
        "patron_chapela",
        "socorrista_chapela",
        "socorrista_zodiac",
        "socorrista_zodiac_reserva",
        "llave_chapela_patrones",
        "llave_cesantes_pool_g2",
        "llave_cesantes_pool_g3",
        "abrir_torre_pool",
        "prefieren_zodiac",
        "abren_zodiac",
        "prioridad_invertida",
        "solo_socorrista",
        "solo_patron",
        "no_abrir_puesto",
    )
    for clave in listas_nombre:
        for nombre in prefs.get(clave, []):
            if not nombre.startswith("Vacante") and nombre not in nombres_registrados:
                errores.append(f"preferencias.{clave}: nombre desconocido «{nombre}»")

    for nombre in cfg.get("sustitutos", []):
        if not nombre.startswith("Vacante") and nombre not in nombres_registrados:
            sn = solo_nombre(nombre)
            if not any(solo_nombre(n) == sn for n in nombres_registrados):
                errores.append(f"sustitutos: nombre desconocido «{nombre}»")

    for patron, pareja in prefs.get("pareja_chapela", {}).items():
        if patron not in nombres_registrados:
            errores.append(f"pareja_chapela: patrón desconocido «{patron}»")
        if pareja not in nombres_registrados:
            errores.append(f"pareja_chapela: socorrista desconocido «{pareja}»")

    for entrada in cfg.get("baja", []):
        nombre = entrada.get("nombre", "")
        if nombre and nombre in nombres_registrados:
            errores.append(f"baja: «{nombre}» no puede estar también en socorristas/patrones")

    indice_pila = {solo_nombre(n): n for n in nombres_registrados}
    for clave, datos in (cfg.get("disponibilidad") or {}).items():
        nombre_cfg = indice_pila.get(solo_nombre(clave)) or (
            clave if clave in nombres_registrados else None
        )
        if not nombre_cfg:
            errores.append(f"disponibilidad: nombre desconocido «{clave}»")
            continue
        if isinstance(datos, dict):
            for fecha in datos.get("excepto") or []:
                try:
                    parse_fecha(fecha)
                except ValueError:
                    errores.append(f"disponibilidad.{clave}: fecha inválida en excepto «{fecha}»")
        if isinstance(datos, dict) and (
            datos.get("fines_de_semana") or datos.get("laborables")
        ):
            continue
        fechas = datos.get("fechas", []) if isinstance(datos, dict) else datos
        if not fechas:
            errores.append(
                f"disponibilidad.{clave}: indica fechas, fines_de_semana o laborables"
            )
            continue
        for fecha in fechas:
            try:
                parse_fecha(fecha)
            except ValueError:
                errores.append(f"disponibilidad.{clave}: fecha inválida «{fecha}»")

    for sub_nombre, datos in (cfg.get("patron_sustituto") or {}).items():
        sn = solo_nombre(sub_nombre)
        if not any(solo_nombre(n) == sn for n in nombres_registrados):
            errores.append(f"patron_sustituto: nombre desconocido «{sub_nombre}»")
            continue
        if not isinstance(datos, dict):
            errores.append(f"patron_sustituto.{sub_nombre}: formato inválido")
            continue
        cubre = datos.get("cubre", [])
        puesto = datos.get("puesto", "")
        if isinstance(cubre, list):
            if puesto not in ("patron_chapela", "patron_cesantes"):
                errores.append(f"patron_sustituto.{sub_nombre}: indica puesto patron_chapela o patron_cesantes")
            for titular in cubre:
                if titular not in nombres_registrados:
                    errores.append(f"patron_sustituto.{sub_nombre}: titular desconocido «{titular}»")
        elif isinstance(cubre, dict):
            for titular, puesto_map in cubre.items():
                if titular not in nombres_registrados:
                    errores.append(f"patron_sustituto.{sub_nombre}: titular desconocido «{titular}»")
                if puesto_map not in ("patron_chapela", "patron_cesantes"):
                    errores.append(f"patron_sustituto.{sub_nombre}: puesto inválido «{puesto_map}»")
        else:
            errores.append(f"patron_sustituto.{sub_nombre}: cubre debe ser lista o mapa")

    cong = cfg.get("congelado") or {}
    if hasta := cong.get("hasta"):
        try:
            parse_fecha(hasta)
        except ValueError:
            errores.append("congelado.hasta inválida (use YYYY-MM-DD)")

    comp = cfg.get("compensacion") or {}
    if comp:
        if not isinstance(comp, dict):
            errores.append("compensacion: formato inválido")
        else:
            if "factor" in comp:
                try:
                    if float(comp["factor"]) <= 0:
                        errores.append("compensacion.factor debe ser > 0")
                except (TypeError, ValueError):
                    errores.append("compensacion.factor inválido")
            for nombre in comp.get("personas") or []:
                sn = solo_nombre(str(nombre).strip())
                if not sn:
                    continue
                if sn not in indice_pila and str(nombre).strip() not in nombres_registrados:
                    errores.append(f"compensacion.personas: nombre desconocido «{nombre}»")

    return errores


def rango_fechas(inicio: date, fin: date) -> list[date]:
    dias: list[date] = []
    actual = inicio
    while actual <= fin:
        dias.append(actual)
        actual += timedelta(days=1)
    return dias


def posicion_ciclo(dia_idx: int, grupo: int, rotacion: dict) -> int:
    ciclo = rotacion["dias_trabajo"] + rotacion["dias_libres"]
    desfase = rotacion["desfase_grupos"][grupo - 1]
    return (dia_idx + desfase) % ciclo


def trabaja_en_dia(dia_idx: int, grupo: int, rotacion: dict) -> bool:
    return posicion_ciclo(dia_idx, grupo, rotacion) < rotacion["dias_trabajo"]


def indice_bloque_grupo(dia_idx: int, grupo: int, rotacion: dict) -> int:
    """Cuántos bloques de trabajo ha empezado el grupo hasta dia_idx (incluido)."""
    ciclo = rotacion["dias_trabajo"] + rotacion["dias_libres"]
    desfase = rotacion["desfase_grupos"][grupo - 1]
    inicio = (-desfase) % ciclo
    if dia_idx < inicio:
        return -1
    return (dia_idx - inicio) // ciclo


def bloque_calendario(dia_idx: int, rotacion: dict) -> int:
    return dia_idx // rotacion["dias_trabajo"]


def clave_bloque_g2(dia_idx: int, bloque: int, rotacion: dict) -> int:
    """Clave en mapas de rol por bloque G2 (positiva) o reserva G2 libre (negativa)."""
    return bloque if g2_trabaja(dia_idx, rotacion) else -bloque - 1


def elegir_fijo_por_bloque(
    bloque: int,
    roster: list[str],
    candidatos: list[Persona],
    excluidos: set[str],
) -> Persona | None:
    if not roster:
        return None
    inicio = bloque % len(roster)
    for i in range(len(roster)):
        nombre = roster[(inicio + i) % len(roster)]
        if nombre in excluidos:
            continue
        p = buscar_por_nombre(candidatos, nombre)
        if p:
            return p
    return elegir_preferido(candidatos, roster, excluidos)


def hay_patron_cesantes(fila: dict[str, str]) -> bool:
    """Hay patrón real en Cesantes (no vacío ni vacante). Sin él no se abre Zodiac."""
    nombre = fila.get("patron_cesantes", "").strip()
    return bool(nombre) and not es_nombre_vacante(nombre)


def reubicar_zodiac_sin_patron(fila: dict[str, str]) -> None:
    """Si no hay patrón Cesantes, Zodiac no abre: esa persona pasa a torre o Cesantes."""
    actual = fila.get("socorrista_zodiac", "").strip()
    if not actual:
        return
    fila["socorrista_zodiac"] = ""
    if not fila.get("abrir_torre", "").strip():
        fila["abrir_torre"] = actual
        return
    cesantes = parse_lista_nombres(fila.get("cesantes", ""))
    for i, n in enumerate(cesantes):
        if es_nombre_vacante(n):
            cesantes[i] = actual
            fila["cesantes"] = format_lista_nombres(cesantes)
            return
    cesantes.append(actual)
    fila["cesantes"] = format_lista_nombres(cesantes)


def elegir_menos_asignado(
    candidatos: list[Persona],
    roster: list[str],
    contador: dict[str, int],
) -> Persona | None:
    """Elige al candidato del roster con menos días ya asignados (desempate: orden del roster)."""
    if not candidatos:
        return None
    orden = {solo_nombre(n): i for i, n in enumerate(roster)}
    return min(
        candidatos,
        key=lambda p: (contador.get(solo_nombre(p.nombre), 0), orden.get(solo_nombre(p.nombre), 99)),
    )


def colocar_socorrista_zodiac(fila: dict[str, str], nuevo: str, no_abrir: set[str]) -> bool:
    """Pone a `nuevo` en Zodiac intercambiando con el titular. False si no hay intercambio válido."""
    actual = fila.get("socorrista_zodiac", "").strip()
    if not nuevo or nuevo == actual:
        return True
    if fila.get("abrir_torre", "").strip() == nuevo:
        fila["abrir_torre"] = actual
        fila["socorrista_zodiac"] = nuevo
        return True
    cesantes = parse_lista_nombres(fila.get("cesantes", ""))
    if nuevo in cesantes:
        cesantes[cesantes.index(nuevo)] = actual
        fila["cesantes"] = format_lista_nombres([n for n in cesantes if n])
        fila["socorrista_zodiac"] = nuevo
        return True
    if fila.get("llave_cesantes", "").strip() == nuevo:
        if actual and solo_nombre(actual) not in no_abrir:
            fila["llave_cesantes"] = actual
            fila["socorrista_zodiac"] = nuevo
            return True
        for i, n in enumerate(cesantes):
            if n and solo_nombre(n) not in no_abrir and not n.startswith("Vacante"):
                fila["llave_cesantes"] = n
                cesantes[i] = actual
                fila["cesantes"] = format_lista_nombres([x for x in cesantes if x])
                fila["socorrista_zodiac"] = nuevo
                return True
        return False
    return False


def _prioridad_swap_zodiac(fila: dict[str, str], nombre: str) -> int:
    """Prefiere no mover abrir puesto: titular Zodiac, torre, cesantes, llave."""
    if fila.get("socorrista_zodiac", "").strip() == nombre:
        return 0
    if fila.get("abrir_torre", "").strip() == nombre:
        return 1
    if nombre in parse_lista_nombres(fila.get("cesantes", "")):
        return 2
    if fila.get("llave_cesantes", "").strip() == nombre:
        return 3
    return 9


def repartir_zodiac_desde(
    filas: list[dict[str, str]],
    pools: PoolsPreferencias,
    desde: date,
) -> None:
    """Reparte Zodiac entre el roster desde `desde`, salvo Arturo/Rober."""
    abren = {solo_nombre(n) for n in pools.abren_zodiac}
    roster = [solo_nombre(n) for n in (pools.pref_zodiac or pools.pref_zodiac_reserva)]
    no_abrir = {solo_nombre(n) for n in pools.no_abrir_puesto}
    contador: dict[str, int] = {}
    for fila in filas:
        if parse_fecha(fila["fecha"]) < desde:
            continue
        if not hay_patron_cesantes(fila):
            reubicar_zodiac_sin_patron(fila)
            continue
        presentes = set(nombres_asignados_dia(fila))
        if presentes & abren:
            continue
        chapela = {
            solo_nombre(fila.get("socorrista_chapela", "")),
            solo_nombre(fila.get("patron_chapela", "")),
        }
        candidatos = [n for n in roster if n in presentes and n not in chapela]
        if not candidatos:
            continue
        ordenados = sorted(
            candidatos,
            key=lambda n: (
                contador.get(n, 0),
                _prioridad_swap_zodiac(fila, n),
                roster.index(n),
            ),
        )
        for pick in ordenados:
            if colocar_socorrista_zodiac(fila, pick, no_abrir):
                contador[pick] = contador.get(pick, 0) + 1
                break


def dias_bloque_g2(bloque: int, rotacion: dict, total: int) -> tuple[list[int], list[int]]:
    ciclo = rotacion["dias_trabajo"] + rotacion["dias_libres"]
    dt = rotacion["dias_trabajo"]
    desfase = rotacion["desfase_grupos"][1]
    n = -1
    for d in range(total):
        if (d + desfase) % ciclo == 0:
            n += 1
            if n == bloque:
                trabajo = list(range(d, min(d + dt, total)))
                libres = list(range(d + dt, min(d + dt + (ciclo - dt), total)))
                return trabajo, libres
    return [], []


@dataclass(frozen=True)
class PoolsPreferencias:
    pref_zodiac: list[str]
    pref_zodiac_reserva: list[str]
    prefieren_zodiac: set[str]
    abren_zodiac: list[str]
    llave_patrones: list[str]
    pool_llave_g2: list[str]
    pool_llave_g3: list[str]
    pool_torre: list[str]
    prioridad_invertida: set[str]
    solo_socorrista: set[str]
    solo_patron: set[str]
    no_abrir_puesto: set[str]
    no_patron: set[str]
    patron_sustituto_chapela: list[str]
    patron_sustituto_cesantes: list[str]
    pref_patron_chapela: list[str]
    pref_soc_chapela: list[str]
    pareja_chapela: dict[str, str]
    sustitutos: tuple[str, ...]


def listas_patron_sustituto(cfg: dict) -> tuple[list[str], list[str]]:
    chapela: list[str] = []
    cesantes: list[str] = []
    for sub_nombre, datos in (cfg.get("patron_sustituto") or {}).items():
        if not isinstance(datos, dict):
            continue
        puesto = datos.get("puesto", "")
        cubre = datos.get("cubre", {})
        if isinstance(cubre, list) and puesto == "patron_cesantes" and sub_nombre not in cesantes:
            cesantes.append(sub_nombre)
        elif isinstance(cubre, list) and puesto == "patron_chapela" and sub_nombre not in chapela:
            chapela.append(sub_nombre)
        elif isinstance(cubre, dict):
            for _titular, puesto_map in cubre.items():
                if puesto_map == "patron_chapela" and sub_nombre not in chapela:
                    chapela.append(sub_nombre)
                elif puesto_map == "patron_cesantes" and sub_nombre not in cesantes:
                    cesantes.append(sub_nombre)
    return chapela, cesantes


def extraer_pools(cfg: dict) -> PoolsPreferencias:
    prefs = cfg.get("preferencias", {})
    pref_zodiac = sin_vacantes_roster(prefs.get("socorrista_zodiac", []))
    sub_chapela, sub_cesantes = listas_patron_sustituto(cfg)
    return PoolsPreferencias(
        pref_zodiac=pref_zodiac,
        pref_zodiac_reserva=sin_vacantes_roster(prefs.get("socorrista_zodiac_reserva", [])),
        prefieren_zodiac=set(
            sin_vacantes_roster(prefs.get("prefieren_zodiac", prefs.get("solo_zodiac", pref_zodiac)))
        ),
        abren_zodiac=sin_vacantes_roster(prefs.get("abren_zodiac", [])),
        llave_patrones=prefs.get("llave_chapela_patrones", []),
        pool_llave_g2=sin_vacantes_roster(prefs.get("llave_cesantes_pool_g2", [])),
        pool_llave_g3=sin_vacantes_roster(prefs.get("llave_cesantes_pool_g3", [])),
        pool_torre=sin_vacantes_roster(prefs.get("abrir_torre_pool", [])),
        prioridad_invertida=set(sin_vacantes_roster(prefs.get("prioridad_invertida", []))),
        solo_socorrista=set(sin_vacantes_roster(prefs.get("solo_socorrista", []))),
        solo_patron=set(sin_vacantes_roster(prefs.get("solo_patron", []))),
        no_abrir_puesto=set(sin_vacantes_roster(prefs.get("no_abrir_puesto", []))),
        no_patron=set(sin_vacantes_roster(prefs.get("no_patron", []))),
        patron_sustituto_chapela=sub_chapela,
        patron_sustituto_cesantes=sub_cesantes,
        pref_patron_chapela=prefs.get("patron_chapela", []),
        pref_soc_chapela=prefs.get("socorrista_chapela", []),
        pareja_chapela=prefs.get("pareja_chapela", {}),
        sustitutos=tuple(solo_nombre(n) for n in cfg.get("sustitutos", [])),
    )


def precomputar_roles_bloque(
    rotacion: dict,
    total: int,
    pools: PoolsPreferencias,
) -> tuple[dict[int, str], dict[int, str]]:
    zodiac: dict[int, str] = {}
    llave: dict[int, str] = {}

    bloque = 0
    while True:
        trabajo, _ = dias_bloque_g2(bloque, rotacion, total)
        if not trabajo:
            break

        if pools.pref_zodiac:
            zodiac[bloque] = pools.pref_zodiac[bloque % len(pools.pref_zodiac)]
        if pools.pref_zodiac_reserva:
            zodiac[-bloque - 1] = pools.pref_zodiac_reserva[bloque % len(pools.pref_zodiac_reserva)]

        pool_g2 = _pool_abrir_puesto(pools.pool_llave_g2, pools.prefieren_zodiac)
        if pool_g2:
            llave[bloque] = pool_g2[(bloque + 1) % len(pool_g2)]

        pool_g3 = _pool_abrir_puesto(pools.pool_llave_g3, pools.prefieren_zodiac)
        if pool_g3:
            llave[-bloque - 1] = pool_g3[bloque % len(pool_g3)]

        bloque += 1

    return zodiac, llave


def _pool_abrir_puesto(pool: list[str], prefieren_zodiac: set[str]) -> list[str]:
    """Pool de abrir puesto priorizando quien no prefiere zodiac."""
    filtrado = [n for n in pool if n not in prefieren_zodiac]
    return filtrado or list(pool)


def _sin_prioridad_invertida(candidatos: list[Persona], invertida: set[str]) -> list[Persona]:
    return [p for p in candidatos if p.nombre not in invertida]


def _colocar_prioridad_invertida(
    persona: Persona,
    llave_cesantes: Persona | None,
    socorrista_zodiac: Persona | None,
    abrir_torre: Persona | None,
    *,
    permitir_zodiac: bool = True,
) -> tuple[Persona | None, Persona | None, Persona | None]:
    """zodiac → torre → puesto (inverso del orden normal de asignación)."""
    if permitir_zodiac and not socorrista_zodiac:
        return llave_cesantes, persona, abrir_torre
    if not abrir_torre:
        return llave_cesantes, socorrista_zodiac, persona
    if not llave_cesantes:
        return persona, socorrista_zodiac, abrir_torre
    return llave_cesantes, socorrista_zodiac, abrir_torre


def _persona_en_puesto(
    persona: Persona,
    llave_cesantes: Persona | None,
    socorrista_zodiac: Persona | None,
    abrir_torre: Persona | None,
) -> bool:
    return any(
        p is not None and p.nombre == persona.nombre
        for p in (llave_cesantes, socorrista_zodiac, abrir_torre)
    )


def _ajustar_cesantes_sustitutos(nombres: list[str], sustitutos: tuple[str, ...]) -> list[str]:
    """Cada sustituto en refuerzos sustituye una vacante (cubre el hueco)."""
    resultado = list(nombres)
    for nombre in resultado:
        if nombre not in sustitutos:
            continue
        for i, extra in enumerate(resultado):
            if extra.startswith("Vacante"):
                resultado.pop(i)
                break
    return resultado


def bloque_g2_activo(dia_idx: int, rotacion: dict) -> int:
    return indice_bloque_grupo(dia_idx, 2, rotacion)


def g2_trabaja(dia_idx: int, rotacion: dict) -> bool:
    return trabaja_en_dia(dia_idx, 2, rotacion)


def es_vacante(p: Persona) -> bool:
    return p.nombre.startswith("Vacante")


def confirmados(candidatos: list[Persona]) -> list[Persona]:
    return [p for p in candidatos if not es_vacante(p)]


def nombres_completos_ausentes(
    vacaciones_csv: str,
    personas: list[Persona],
    horas_extras_csv: str = "",
) -> set[str]:
    indice = indice_por_nombre_pila(personas)
    nombres = set(parse_lista_nombres(vacaciones_csv))
    try:
        nombres.update(parse_horas_compensadas(horas_extras_csv))
    except ValueError:
        pass
    return {
        indice[n].nombre
        for n in nombres
        if n in indice
    }


def _nombre_en_conjunto(nombre: str, conjunto: set[str]) -> bool:
    if not nombre or not conjunto:
        return False
    sn = solo_nombre(nombre)
    return nombre in conjunto or sn in conjunto or sn in {solo_nombre(x) for x in conjunto}


def _fechas_disponibilidad_persona(cfg: dict, datos: dict | list) -> set[str]:
    periodo = cfg.get("periodo", {})
    inicio = parse_fecha(periodo["inicio"])
    fin = parse_fecha(periodo["fin"])
    excepto: set[str] = set()
    if isinstance(datos, dict):
        excepto = {parse_fecha(f).isoformat() for f in (datos.get("excepto") or [])}
    if isinstance(datos, dict) and datos.get("fines_de_semana"):
        return {d.isoformat() for d in rango_fechas(inicio, fin) if d.weekday() >= 5} - excepto
    if isinstance(datos, dict) and datos.get("laborables"):
        return {d.isoformat() for d in rango_fechas(inicio, fin) if d.weekday() < 5} - excepto
    fechas = datos.get("fechas", []) if isinstance(datos, dict) else datos
    return {parse_fecha(f).isoformat() for f in fechas} - excepto


def _nombres_disponibilidad_limitada(cfg: dict) -> set[str]:
    nombres: set[str] = set()
    for nombre in mapa_disponibilidad(cfg):
        nombres.add(nombre)
        nombres.add(solo_nombre(nombre))
    return nombres


def nombres_refuerzo_disponibilidad(cfg: dict) -> set[str]:
    return _nombres_disponibilidad_limitada(cfg)


def socorrista_por_nombre(personas: list[Persona], nombre: str) -> Persona | None:
    sn = solo_nombre(nombre)
    for p in personas:
        if p.rol == "socorrista" and solo_nombre(p.nombre) == sn:
            return p
    return None


def patron_por_nombre(personas: list[Persona], nombre: str) -> Persona | None:
    sn = solo_nombre(nombre)
    for p in personas:
        if p.rol == "patron" and solo_nombre(p.nombre) == sn:
            return p
    return None


def persona_por_nombre_completo(personas: list[Persona], nombre: str) -> Persona | None:
    sn = solo_nombre(nombre)
    for p in personas:
        if p.nombre == nombre or solo_nombre(p.nombre) == sn:
            return p
    return None


def nombres_patron_sustituto(cfg: dict) -> set[str]:
    return {solo_nombre(n) for n in (cfg.get("patron_sustituto") or {})}


def patrones_para_roles(patrones: list[Persona], pools: PoolsPreferencias) -> list[Persona]:
    """Patrones asignables a chapela/cesantes (todos los patrones activos)."""
    return [p for p in patrones if not _nombre_en_conjunto(p.nombre, pools.no_patron)]


def patrones_para_chapela(patrones: list[Persona], pools: PoolsPreferencias) -> list[Persona]:
    """Candidatos a patrón Chapela (excluye sustitutos de solo cesantes)."""
    excluir = {solo_nombre(n) for n in pools.patron_sustituto_cesantes}
    excluir.update(pools.patron_sustituto_cesantes)
    return [p for p in patrones_para_roles(patrones, pools) if not _nombre_en_conjunto(p.nombre, excluir)]


def agregar_patrones_sustituto(
    cfg: dict,
    personas: list[Persona],
    trabajando: list[Persona],
    nombres_trabajando: set[str],
    dia_idx: int,
    rotacion: dict,
    fecha_str: str,
) -> None:
    disponibles = mapa_disponibilidad(cfg)
    for sub_clave, datos in (cfg.get("patron_sustituto") or {}).items():
        pat = patron_por_nombre(personas, sub_clave)
        if not pat or pat.nombre in nombres_trabajando:
            continue
        if fecha_str not in disponibles.get(pat.nombre, set()):
            continue
        cubre = datos.get("cubre", []) if isinstance(datos, dict) else []
        titulares = cubre if isinstance(cubre, list) else list(cubre.keys()) if isinstance(cubre, dict) else []
        if not any(
            (t := persona_por_nombre_completo(personas, str(titular_nombre)))
            and not trabaja_en_dia(dia_idx, t.grupo, rotacion)
            for titular_nombre in titulares
        ):
            continue
        trabajando.append(pat)
        nombres_trabajando.add(pat.nombre)


def mapa_disponibilidad(cfg: dict) -> dict[str, set[str]]:
    """Nombre completo -> fechas ISO en que puede trabajar. Sin entrada = sin restricción."""
    resultado: dict[str, set[str]] = {}
    personas = construir_personas(cfg)
    indice = indice_por_nombre_pila(personas)
    for clave, datos in (cfg.get("disponibilidad") or {}).items():
        soc = socorrista_por_nombre(personas, clave)
        if soc:
            nombre = soc.nombre
        elif pat := patron_por_nombre(personas, clave):
            nombre = pat.nombre
        elif persona := indice.get(solo_nombre(clave)):
            nombre = persona.nombre
        else:
            nombre = clave
        resultado[nombre] = _fechas_disponibilidad_persona(cfg, datos)
    return resultado


def ausentes_por_disponibilidad(
    cfg: dict,
    fecha_str: str,
    personas: list[Persona],
) -> set[str]:
    ausentes: set[str] = set()
    for nombre, fechas_ok in mapa_disponibilidad(cfg).items():
        if fecha_str not in fechas_ok:
            ausentes.add(nombre)
    return ausentes


def admin_desde_existente(existentes: dict[str, dict[str, str]], fecha_str: str) -> dict[str, str]:
    """Copia vacaciones/horas_extras del CSV previo. Nunca las inventa."""
    prev = existentes.get(fecha_str, {})
    return {col: prev.get(col, "") for col in COLUMNAS_ADMIN}


def validar_administracion(
    fila: dict[str, str],
    personas: list[Persona],
    cfg: dict | None = None,
) -> str | None:
    indice = indice_por_nombre_pila(personas)
    vacaciones = parse_lista_nombres(fila.get("vacaciones", ""))
    try:
        extras, compensado = parse_anotaciones_horas(fila.get("horas_extras", ""))
    except ValueError as e:
        return str(e)

    for nombre in vacaciones:
        if nombre not in indice:
            return f"Vacaciones: nombre desconocido «{nombre}»"
    for nombre, horas in extras.items():
        if nombre not in indice:
            return f"Horas extras: nombre desconocido «{nombre}»"
        if horas <= 0:
            return f"Horas extras: «{nombre}» debe ser > 0"
    for nombre, horas in compensado.items():
        if nombre not in indice:
            return f"Horas extras: nombre desconocido «{nombre}»"
        if horas <= 0:
            return f"Horas extras: «{nombre}» debe ser > 0"

    if cfg is not None:
        _, permitidos = parse_compensacion(cfg)
        for nombre in compensado:
            if nombre not in permitidos:
                return f"Horas extras: «{nombre}» no puede marcar compensado"

    asignados = nombres_asignados_fila(fila)
    for nombre in vacaciones:
        if nombre in asignados:
            return f"{nombre} en vacaciones y asignado el mismo día"
    for nombre in compensado:
        if nombre in asignados:
            return f"{nombre} en compensación y asignado el mismo día"

    if solapados := set(vacaciones) & set(extras):
        return f"{sorted(solapados)[0]} en vacaciones y horas extras"
    if solapados := set(vacaciones) & set(compensado):
        return f"{sorted(solapados)[0]} en vacaciones y compensación"
    if solapados := set(extras) & set(compensado):
        return f"{sorted(solapados)[0]} en horas extras y compensación"

    return None


def validar_sin_duplicados(fila: dict[str, str]) -> str | None:
    vistos: dict[str, str] = {}
    for col in PUESTOS_ASIGNACION:
        nombre = fila.get(col, "").strip()
        if not nombre:
            continue
        if nombre in vistos:
            return f"{nombre} repetido ({vistos[nombre]} y {col})"
        vistos[nombre] = col
    for nombre in parse_lista_nombres(fila.get("cesantes", "")):
        if nombre in vistos:
            return f"{nombre} repetido ({vistos[nombre]} y cesantes)"
        vistos[nombre] = "cesantes"
    return None


def validar_cobertura_obligatoria(fila: dict[str, str]) -> str | None:
    for col, etiqueta in CAMPOS_OBLIGATORIOS:
        if not fila.get(col, "").strip():
            return f"Falta {etiqueta}"
    return None


def validar_zodiac_solo_socorrista(fila: dict[str, str], personas: list[Persona]) -> str | None:
    """Zodiac es puesto de socorrista: un patrón puro no puede figurar ahí."""
    zodiac = fila.get("socorrista_zodiac", "").strip()
    if not zodiac:
        return None
    sn = solo_nombre(zodiac)
    es_patron = any(p.rol == "patron" and solo_nombre(p.nombre) == sn for p in personas)
    es_socorrista = any(p.rol == "socorrista" and solo_nombre(p.nombre) == sn for p in personas)
    if es_patron and not es_socorrista:
        return f"Zodiac no puede ser el patrón «{sn}» (solo socorristas)"
    return None


def validar_no_abrir_puesto(fila: dict[str, str], cfg: dict) -> str | None:
    """Algunos socorristas no pueden figurar en abrir puesto."""
    prohibidos = {
        solo_nombre(n)
        for n in (cfg.get("preferencias") or {}).get("no_abrir_puesto", [])
    }
    if not prohibidos:
        return None
    llave = solo_nombre(fila.get("llave_cesantes", "").strip())
    if llave and llave in prohibidos:
        return f"{llave} no puede abrir puesto"
    return None


def validar_no_patron(fila: dict[str, str], cfg: dict) -> str | None:
    """Algunos socorristas no pueden figurar como patrón."""
    prohibidos = {
        solo_nombre(n)
        for n in (cfg.get("preferencias") or {}).get("no_patron", [])
    }
    if not prohibidos:
        return None
    for col, etiqueta in (("patron_chapela", "patrón Chapela"), ("patron_cesantes", "patrón Cesantes")):
        nombre = solo_nombre(fila.get(col, "").strip())
        if nombre and nombre in prohibidos:
            return f"{nombre} no puede ser {etiqueta}"
    return None


def validar_abren_zodiac(fila: dict[str, str], cfg: dict) -> str | None:
    """Si Arturo o Rober trabajan y no están en Chapela, deben abrir Zodiac (si hay patrón)."""
    if not hay_patron_cesantes(fila):
        return None
    abren = [
        solo_nombre(n)
        for n in (cfg.get("preferencias") or {}).get("abren_zodiac", [])
    ]
    if not abren:
        return None
    presentes = {solo_nombre(n) for n in nombres_asignados_fila(fila)}
    presentes_abren = [
        n for n in abren if n in presentes and not _en_puesto_chapela(fila, n)
    ]
    if not presentes_abren:
        return None
    z = solo_nombre(fila.get("socorrista_zodiac", "").strip())
    if z not in set(abren):
        quien = " o ".join(presentes_abren)
        return f"{quien} está de turno: debe abrir Zodiac"
    return None


def validar_cobertura_extendida(fila: dict[str, str], socorristas_trabajando: int) -> str | None:
    """Zodiac y torre cuando hay personal suficiente ese día."""
    if fila.get("socorrista_zodiac", "").strip() and not hay_patron_cesantes(fila):
        return "Zodiac sin patrón Cesantes"
    libres = socorristas_trabajando - 1  # excluye socorrista chapela
    if hay_patron_cesantes(fila) and libres >= 1 and not fila.get("socorrista_zodiac", "").strip():
        return "Falta zodiac"
    if libres >= 3 and not fila.get("abrir_torre", "").strip():
        return "Falta torre"
    return None


def nombres_asignados_fila(fila: dict[str, str]) -> set[str]:
    nombres = {fila.get(col, "").strip() for col in PUESTOS_ASIGNACION if fila.get(col, "").strip()}
    nombres.update(parse_lista_nombres(fila.get("cesantes", "")))
    return nombres


def indice_por_nombre_pila(personas: list[Persona]) -> dict[str, Persona]:
    return {solo_nombre(p.nombre): p for p in personas if not es_vacante(p)}


def max_racha_dias(indices: list[int]) -> int:
    if not indices:
        return 0
    ordenados = sorted(set(indices))
    mejor = actual = 1
    for anterior, siguiente in zip(ordenados, ordenados[1:]):
        if siguiente == anterior + 1:
            actual += 1
            mejor = max(mejor, actual)
        else:
            actual = 1
    return mejor


def validar_rotacion_4_2(
    filas: list[dict[str, str]],
    personas: list[Persona],
    rotacion: dict,
    inicio: date | None = None,
    fechas_congeladas: set[str] | None = None,
    refuerzos_disponibilidad: set[str] | None = None,
) -> str | None:
    if not filas:
        return None

    congeladas = fechas_congeladas or set()
    max_trabajo = rotacion["dias_trabajo"]
    indice = indice_por_nombre_pila(personas)
    refuerzos = refuerzos_disponibilidad or set()
    if inicio is None:
        inicio = parse_fecha(filas[0]["fecha"])

    for fila in filas:
        if fila["fecha"] in congeladas:
            continue
        dia_idx = (parse_fecha(fila["fecha"]) - inicio).days
        try:
            extras_dia = set(parse_horas_extras(fila.get("horas_extras", "")).keys())
        except ValueError as e:
            return f"{fila['fecha']}: {e}"
        for nombre in nombres_asignados_fila(fila):
            persona = indice.get(nombre)
            if not persona:
                continue
            if (
                not trabaja_en_dia(dia_idx, persona.grupo, rotacion)
                and nombre not in extras_dia
                and not _nombre_en_conjunto(nombre, refuerzos)
            ):
                return (
                    f"{nombre} asignado el {fila['fecha']} en día libre "
                    f"(grupo {persona.grupo}); añádelo a horas_extras si es extra"
                )

    for nombre in indice:
        persona = indice[nombre]
        dias = [
            (parse_fecha(fila["fecha"]) - inicio).days
            for fila in filas
            if fila["fecha"] not in congeladas and nombre in nombres_asignados_fila(fila)
        ]
        dias = [d for d in dias if trabaja_en_dia(d, persona.grupo, rotacion)]
        racha = max_racha_dias(dias)
        if racha > max_trabajo:
            return f"{nombre}: {racha} días seguidos asignados (máximo {max_trabajo})"

    return None


def personal_del_dia(
    personas: list[Persona],
    dia_idx: int,
    rotacion: dict,
    cfg: dict | None = None,
    fecha_str: str | None = None,
    solo_socorrista: set[str] | None = None,
    solo_patron: set[str] | None = None,
) -> tuple[list[Persona], list[Persona]]:
    trabajando = [
        p
        for p in personas
        if trabaja_en_dia(dia_idx, p.grupo, rotacion)
        and not (p.rol == "patron" and solo_nombre(p.nombre) in nombres_patron_sustituto(cfg or {}))
    ]
    nombres_trabajando = {p.nombre for p in trabajando}

    if cfg and fecha_str:
        for nombre, fechas_ok in mapa_disponibilidad(cfg).items():
            if fecha_str not in fechas_ok:
                continue
            soc = socorrista_por_nombre(personas, nombre)
            if soc and soc.nombre not in nombres_trabajando:
                trabajando.append(soc)
                nombres_trabajando.add(soc.nombre)
        if cfg and fecha_str:
            agregar_patrones_sustituto(
                cfg, personas, trabajando, nombres_trabajando, dia_idx, rotacion, fecha_str
            )

    excluir_patron = solo_socorrista or set()
    excluir_soc = solo_patron or set()
    patrones = [
        p for p in trabajando if p.rol == "patron" and not _nombre_en_conjunto(p.nombre, excluir_patron)
    ]
    socorristas = [
        p for p in trabajando if p.rol == "socorrista" and not _nombre_en_conjunto(p.nombre, excluir_soc)
    ]
    return patrones, socorristas


def contar_socorristas_trabajando(
    personas: list[Persona],
    dia_idx: int,
    rotacion: dict,
    ausentes: set[str] | None = None,
    cfg: dict | None = None,
    fecha_str: str | None = None,
) -> int:
    ausentes = ausentes or set()
    contados: set[str] = set()
    total = 0
    for p in personas:
        if p.rol != "socorrista" or es_vacante(p) or p.nombre in ausentes:
            continue
        if trabaja_en_dia(dia_idx, p.grupo, rotacion):
            total += 1
            contados.add(solo_nombre(p.nombre))

    if cfg and fecha_str:
        for nombre, fechas_ok in mapa_disponibilidad(cfg).items():
            if fecha_str not in fechas_ok or nombre in ausentes:
                continue
            sn = solo_nombre(nombre)
            if sn in contados:
                continue
            soc = socorrista_por_nombre(personas, nombre)
            if soc and not es_vacante(soc):
                total += 1
                contados.add(sn)

    return total


def buscar_por_nombre(lista: list[Persona], nombre: str) -> Persona | None:
    for p in lista:
        if p.nombre == nombre:
            return p
    sn = solo_nombre(nombre)
    for p in lista:
        if solo_nombre(p.nombre) == sn:
            return p
    return None


def elegir_preferido(
    candidatos: list[Persona],
    preferidos: list[str],
    excluidos: set[str],
) -> Persona | None:
    for nombre in preferidos:
        if nombre in excluidos:
            continue
        p = buscar_por_nombre(candidatos, nombre)
        if p:
            return p
    for p in candidatos:
        if p.nombre not in excluidos:
            return p
    return None


def resolver_socorrista(
    nombre_objetivo: str,
    roster: list[str],
    candidatos: list[Persona],
    bloque: int,
    excluidos: set[str],
) -> Persona | None:
    if nombre_objetivo:
        if p := buscar_por_nombre(candidatos, nombre_objetivo):
            return p
    if p := elegir_fijo_por_bloque(bloque, roster, candidatos, excluidos):
        return p
    return candidatos[0] if candidatos else None


def asignar_puestos(
    patrones: list[Persona],
    socorristas: list[Persona],
    dia_idx: int,
    rotacion: dict,
    pools: PoolsPreferencias,
    roles_bloque: tuple[dict[int, str], dict[int, str]],
    ausentes: set[str] | None = None,
    zodiac_hechos: dict[str, int] | None = None,
) -> dict[str, str]:
    fila: dict[str, str] = {}
    ausentes = ausentes or set()
    patrones = [p for p in patrones if p.nombre not in ausentes]
    socorristas = [p for p in socorristas if p.nombre not in ausentes]

    if len(socorristas) < 2:
        fila["_error"] = f"Faltan socorristas ({len(socorristas)}/2 mínimo)"
        return fila

    _, llave_por_bloque = roles_bloque
    bloque = bloque_g2_activo(dia_idx, rotacion)
    bloque_cal = bloque_calendario(dia_idx, rotacion)
    clave = clave_bloque_g2(dia_idx, bloque, rotacion)
    soc_confirmados = confirmados(socorristas)

    patron_chapela: Persona | None = None
    patron_cesantes: Persona | None = None
    patrones_roles = patrones_para_roles(patrones, pools)
    patrones_chapela = patrones_para_chapela(patrones, pools)

    if patrones_roles:
        pref_patron = pools.pref_patron_chapela
        patron_pref = buscar_por_nombre(patrones_chapela, pref_patron[0]) if pref_patron else None
        if patron_pref:
            patron_chapela = patron_pref
        else:
            patron_chapela = None
            for nombre_sub in pools.patron_sustituto_chapela:
                if p := buscar_por_nombre(patrones_chapela, nombre_sub):
                    patron_chapela = p
                    break
            if not patron_chapela:
                confirmados_pat = confirmados(patrones_chapela) or patrones_chapela
                otros = [p for p in confirmados_pat if p.nombre in pools.llave_patrones] or confirmados_pat
                patron_chapela = elegir_fijo_por_bloque(bloque_cal, pools.llave_patrones, otros, set()) or (
                    otros[0] if otros else None
                )

        if patron_chapela:
            otros_patrones = [p for p in patrones_roles if p.nombre != patron_chapela.nombre]
            patron_cesantes = None
            for nombre_sub in pools.patron_sustituto_cesantes:
                if p := buscar_por_nombre(otros_patrones, nombre_sub):
                    patron_cesantes = p
                    break
            if not patron_cesantes:
                confirmados_otros = confirmados(otros_patrones)
                patron_cesantes = confirmados_otros[0] if confirmados_otros else (
                    otros_patrones[0] if otros_patrones else None
                )

    pareja = None
    if patron_chapela and not es_vacante(patron_chapela):
        pareja = pools.pareja_chapela.get(patron_chapela.nombre)
    if pareja and (soc := buscar_por_nombre(soc_confirmados, pareja)):
        socorrista_chapela = soc
    else:
        socorrista_chapela = elegir_preferido(soc_confirmados, pools.pref_soc_chapela, set())

    if not socorrista_chapela:
        fila["_error"] = "Sin socorrista chapela"
        return fila

    excluidos: set[str] = {socorrista_chapela.nombre}
    invertida = pools.prioridad_invertida
    abre_zodiac = patron_cesantes is not None and not es_vacante(patron_cesantes)

    # 1. Zodiac preferente: Arturo / Rober si están de turno (solo si hay patrón Cesantes)
    socorrista_zodiac: Persona | None = None
    if abre_zodiac:
        for nombre in pools.abren_zodiac:
            if _nombre_en_conjunto(nombre, excluidos):
                continue
            if p := buscar_por_nombre(soc_confirmados, nombre):
                socorrista_zodiac = p
                break
        if socorrista_zodiac:
            excluidos.add(socorrista_zodiac.nombre)

    # 2. Abrir puesto: primero quien no prefiere zodiac ni tiene prioridad invertida
    pool_llave = _pool_abrir_puesto(pools.pool_llave_g2 + pools.pool_llave_g3, pools.prefieren_zodiac)
    candidatos_llave = _sin_prioridad_invertida(
        [
            s
            for s in soc_confirmados
            if s.nombre not in excluidos
            and not _nombre_en_conjunto(s.nombre, pools.prefieren_zodiac)
            and not _nombre_en_conjunto(s.nombre, pools.no_abrir_puesto)
        ],
        invertida,
    )
    nombre_lc = llave_por_bloque.get(
        clave,
        (pools.pool_llave_g2 or pools.pool_llave_g3 or [""])[0],
    )
    llave_cesantes = resolver_socorrista(nombre_lc, pool_llave, candidatos_llave, bloque + 1, set())

    if not llave_cesantes:
        emergencia = _sin_prioridad_invertida(
            [
                s
                for s in soc_confirmados
                if s.nombre not in excluidos
                and _nombre_en_conjunto(s.nombre, pools.prefieren_zodiac)
                and not _nombre_en_conjunto(s.nombre, pools.no_abrir_puesto)
            ],
            invertida,
        )
        llave_cesantes = resolver_socorrista(
            "",
            list(pools.prefieren_zodiac),
            emergencia,
            bloque + 1,
            set(),
        )

    # Si aún falta, cualquier socorrista confirmado salvo no_abrir_puesto / invertida
    if not llave_cesantes:
        emergencia_libre = _sin_prioridad_invertida(
            [
                s
                for s in soc_confirmados
                if s.nombre not in excluidos and not _nombre_en_conjunto(s.nombre, pools.no_abrir_puesto)
            ],
            invertida,
        )
        llave_cesantes = emergencia_libre[0] if emergencia_libre else None

    if llave_cesantes:
        excluidos.add(llave_cesantes.nombre)

    # 3. Zodiac de reserva: reparte Robinson / Alejandro / Sergio / Rodrigo / Claudio
    if abre_zodiac and not socorrista_zodiac:
        roster_z = pools.pref_zodiac or pools.pref_zodiac_reserva
        candidatos_pref = _sin_prioridad_invertida(
            [
                s
                for s in soc_confirmados
                if s.nombre not in excluidos and _nombre_en_conjunto(s.nombre, set(roster_z))
            ],
            invertida,
        )
        socorrista_zodiac = elegir_menos_asignado(candidatos_pref, roster_z, zodiac_hechos or {})
        if not socorrista_zodiac:
            candidatos_zodiac = _sin_prioridad_invertida(
                [s for s in soc_confirmados if s.nombre not in excluidos],
                invertida,
            )
            socorrista_zodiac = candidatos_zodiac[0] if candidatos_zodiac else None

    if socorrista_zodiac:
        excluidos.add(socorrista_zodiac.nombre)

    # 4. Torre
    candidatos_torre = _sin_prioridad_invertida(
        [s for s in soc_confirmados if s.nombre not in excluidos],
        invertida,
    )
    abrir_torre = elegir_fijo_por_bloque(bloque_cal, pools.pool_torre, candidatos_torre, set())
    if not abrir_torre and candidatos_torre:
        abrir_torre = candidatos_torre[0]
    if abrir_torre:
        excluidos.add(abrir_torre.nombre)

    # 5. Prioridad invertida: zodiac → torre → puesto (último hueco libre)
    for persona in [s for s in soc_confirmados if s.nombre in invertida and s.nombre not in excluidos]:
        llave_cesantes, socorrista_zodiac, abrir_torre = _colocar_prioridad_invertida(
            persona, llave_cesantes, socorrista_zodiac, abrir_torre, permitir_zodiac=abre_zodiac
        )
        if _persona_en_puesto(persona, llave_cesantes, socorrista_zodiac, abrir_torre):
            excluidos.add(persona.nombre)

    fila["socorrista_chapela"] = solo_nombre(socorrista_chapela.nombre)
    fila["patron_chapela"] = solo_nombre(patron_chapela.nombre) if patron_chapela else ""
    patrones_con_llave = confirmados(
        [p for p in patrones_chapela if p.nombre in pools.llave_patrones]
    ) or confirmados(patrones_chapela)
    titular = elegir_fijo_por_bloque(bloque_cal, pools.llave_patrones, patrones_con_llave, set()) if patrones else None
    fila["llave_chapela"] = solo_nombre(
        titular.nombre if titular else (patron_chapela.nombre if patron_chapela and not es_vacante(patron_chapela) else "")
    )
    fila["patron_cesantes"] = solo_nombre(patron_cesantes.nombre) if patron_cesantes else ""
    fila["llave_cesantes"] = solo_nombre(llave_cesantes.nombre) if llave_cesantes else ""
    fila["socorrista_zodiac"] = solo_nombre(socorrista_zodiac.nombre) if socorrista_zodiac else ""
    fila["abrir_torre"] = solo_nombre(abrir_torre.nombre) if abrir_torre else ""

    libres = len(soc_confirmados) - 1
    faltan: list[str] = []
    if abre_zodiac and libres >= 1 and not socorrista_zodiac:
        faltan.append("zodiac")
    if not llave_cesantes:
        faltan.append("abrir puesto")
    if libres >= 3 and not abrir_torre:
        faltan.append("torre")
    if faltan:
        fila["_error"] = f"Sin cubrir: {', '.join(faltan)}"
        return fila

    asignados = excluidos.copy()
    if patron_chapela:
        asignados.add(patron_chapela.nombre)
    if patron_cesantes:
        asignados.add(patron_cesantes.nombre)

    extras_pat: list[Persona] = []
    for p in patrones:
        if p.nombre in asignados:
            continue
        extras_pat.append(p)
        asignados.add(p.nombre)

    extras_soc = [s for s in socorristas if s.nombre not in asignados]
    nombres_extra = [solo_nombre(p.nombre) for p in extras_pat + extras_soc]
    nombres_extra = _ajustar_cesantes_sustitutos(nombres_extra, pools.sustitutos)
    fila["cesantes"] = format_lista_nombres(nombres_extra)

    return fila


def construir_personas(cfg: dict) -> list[Persona]:
    personas: list[Persona] = []
    for s in cfg["socorristas"]:
        personas.append(Persona(s["nombre"], s["grupo"], "socorrista"))
    for p in cfg["patrones"]:
        personas.append(Persona(p["nombre"], p["grupo"], "patron"))
    return personas


def nombres_plantilla(cfg: dict) -> list[str]:
    return sorted(
        {solo_nombre(p.nombre) for p in construir_personas(cfg) if not es_vacante(p)},
        key=str.casefold,
    )


def libran_por_fecha(cfg: dict, fechas_iso: list[str]) -> dict[str, list[str]]:
    """Quién está de descanso (rotación 4/2) cada día."""
    personas = construir_personas(cfg)
    rotacion = cfg["rotacion"]
    inicio = parse_fecha(cfg["periodo"]["inicio"])
    libres: dict[str, list[str]] = {}
    for fecha_str in fechas_iso:
        dia_idx = (parse_fecha(fecha_str) - inicio).days
        libres[fecha_str] = sorted(
            {
                solo_nombre(p.nombre)
                for p in personas
                if not es_vacante(p) and not trabaja_en_dia(dia_idx, p.grupo, rotacion)
            },
            key=str.casefold,
        )
    return libres


def generar_csv(
    cfg: dict,
    *,
    congelar: bool = True,
    hoy: date | None = None,
    congelar_hasta: date | None = None,
) -> tuple[Path, int, int, date | None]:
    inicio = parse_fecha(cfg["periodo"]["inicio"])
    fin = parse_fecha(cfg["periodo"]["fin"])
    fechas = rango_fechas(inicio, fin)
    personas = construir_personas(cfg)
    rotacion = cfg["rotacion"]
    pools = extraer_pools(cfg)
    roles_bloque = precomputar_roles_bloque(rotacion, len(fechas), pools)

    limite: date | None = None
    existentes: dict[str, dict[str, str]] = {}
    bloqueados_originales: dict[str, dict[str, str]] = {}
    if CSV_PATH.exists():
        bloqueados_originales = filas_bloqueadas_por_fecha()
        existentes = cargar_existentes_csv()
    if congelar:
        limites: list[date] = []
        if auto := fecha_congelacion_limite(cfg, hoy):
            limites.append(auto)
        if congelar_hasta:
            limites.append(congelar_hasta)
        if limites:
            limite = max(limites)

    filas: list[dict[str, str]] = []
    errores: list[str] = []
    n_congelados = 0
    fechas_congeladas: set[str] = set()
    zodiac_hechos: dict[str, int] = {}
    roster_zodiac = {solo_nombre(n) for n in (pools.pref_zodiac or pools.pref_zodiac_reserva)}

    for dia_idx, fecha in enumerate(fechas):
        fecha_str = fecha.isoformat()
        admin = admin_desde_existente(existentes, fecha_str)
        ausentes = nombres_completos_ausentes(
            admin["vacaciones"], personas, admin.get("horas_extras", "")
        ) | ausentes_por_disponibilidad(
            cfg, fecha_str, personas
        )

        previa = existentes.get(fecha_str)
        bloqueada = fecha_str in bloqueados_originales
        congelada_rango = (
            congelar
            and limite is not None
            and fecha <= limite
            and previa is not None
            and not bloqueada
        )

        if bloqueada:
            # Copia literal del snapshot inicial: bloqueado=1 no se regenera nunca.
            fila = dict(bloqueados_originales[fecha_str])
            fila["fecha"] = fecha_str
            n_congelados += 1
            fechas_congeladas.add(fecha_str)
        elif congelada_rango:
            fila = normalizar_fila_csv(previa)
            fila["fecha"] = fecha_str
            n_congelados += 1
            fechas_congeladas.add(fecha_str)
        else:
            patrones, socorristas = personal_del_dia(
                personas,
                dia_idx,
                rotacion,
                cfg,
                fecha_str,
                pools.solo_socorrista,
                pools.solo_patron,
            )
            asignacion = asignar_puestos(
                patrones,
                socorristas,
                dia_idx,
                rotacion,
                pools,
                roles_bloque,
                ausentes,
                zodiac_hechos,
            )

            if "_error" in asignacion:
                errores.append(f"{fecha_str}: {asignacion['_error']}")
                asignacion = {k: v for k, v in asignacion.items() if k != "_error"}

            fila = normalizar_fila_csv({"fecha": fecha_str, **asignacion, **admin})

        filas.append(fila)
        if fecha >= EQUILIBRIO_ZODIAC_DESDE and hay_patron_cesantes(fila):
            z = solo_nombre(fila.get("socorrista_zodiac", ""))
            if z in roster_zodiac:
                zodiac_hechos[z] = zodiac_hechos.get(z, 0) + 1

    for idx, fila in enumerate(filas):
        fecha_str = fila["fecha"]
        if fecha_str in bloqueados_originales:
            restaurada = dict(bloqueados_originales[fecha_str])
            restaurada["fecha"] = fecha_str
            filas[idx] = restaurada

    repartir_zodiac_desde(filas, pools, EQUILIBRIO_ZODIAC_DESDE)

    if rot_err := validar_rotacion_4_2(
        filas,
        personas,
        rotacion,
        inicio,
        fechas_congeladas,
        _nombres_disponibilidad_limitada(cfg),
    ):
        errores.append(rot_err)

    for dia_idx, fila in enumerate(filas):
        fecha_str = fila["fecha"]
        congelada = fecha_str in fechas_congeladas
        bloqueada = celda_bloqueada(fila.get("bloqueado", ""))
        if congelada and bloqueada:
            prefijo = f"{fecha_str} (bloqueado)"
        elif congelada:
            prefijo = f"{fecha_str} (congelado)"
        else:
            prefijo = fecha_str

        if bloqueada:
            continue

        if dup := validar_sin_duplicados(fila):
            errores.append(f"{prefijo}: {dup}")
        elif adm := validar_administracion(fila, personas, cfg):
            errores.append(f"{prefijo}: {adm}")
        elif zod := validar_zodiac_solo_socorrista(fila, personas):
            errores.append(f"{prefijo}: {zod}")
        elif no_llave := validar_no_abrir_puesto(fila, cfg):
            errores.append(f"{prefijo}: {no_llave}")
        elif no_pat := validar_no_patron(fila, cfg):
            errores.append(f"{prefijo}: {no_pat}")
        elif not congelada and (abren := validar_abren_zodiac(fila, cfg)):
            errores.append(f"{prefijo}: {abren}")
        elif not congelada:
            if cob := validar_cobertura_obligatoria(fila):
                errores.append(f"{prefijo}: {cob}")

            ausentes = nombres_completos_ausentes(
                fila.get("vacaciones", ""), personas, fila.get("horas_extras", "")
            ) | ausentes_por_disponibilidad(
                cfg, fecha_str, personas
            )
            n_soc = contar_socorristas_trabajando(
                personas, dia_idx, rotacion, ausentes, cfg, fecha_str
            )
            if ext := validar_cobertura_extendida(fila, n_soc):
                errores.append(f"{prefijo}: {ext}")

    errores.extend(errores_saldos_compensacion(cfg, filas))

    if errores:
        raise ErrorGeneracion(errores)

    columnas = columnas_csv_completas()
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)

    n_bloqueados = len(bloqueados_originales)
    return CSV_PATH, len(filas), n_congelados, limite, n_bloqueados


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Genera el cuadrante de turnos (CSV + HTML).")
    parser.add_argument(
        "--congelar-hasta",
        metavar="FECHA",
        help="Congela hasta esta fecha (YYYY-MM-DD) además del pasado automático",
    )
    args = parser.parse_args(argv)

    if not CONFIG_PATH.exists():
        print(f"No se encuentra {CONFIG_PATH}", file=sys.stderr)
        return 1

    congelar_hasta: date | None = None
    if args.congelar_hasta:
        try:
            congelar_hasta = parse_fecha(args.congelar_hasta)
        except ValueError:
            print(f"Fecha inválida: {args.congelar_hasta} (use YYYY-MM-DD)", file=sys.stderr)
            return 1

    try:
        cfg = cargar_config_validada()
        out, n, n_cong, limite, n_bloqueados = generar_csv(
            cfg,
            congelar_hasta=congelar_hasta,
        )
    except ErrorConfig as e:
        print(f"Configuración inválida:\n{e}", file=sys.stderr)
        return 1
    except ErrorGeneracion as e:
        print(f"\n⚠ Cuadrante inválido ({len(e.errores)} error(es)); CSV no actualizado:", file=sys.stderr)
        for err in e.errores[:15]:
            print(f"  - {err}", file=sys.stderr)
        if len(e.errores) > 15:
            print(f"  ... y {len(e.errores) - 15} más", file=sys.stderr)
        return 1

    print(f"CSV generado: {out} ({n} días)")
    if n_bloqueados:
        print(f"  {n_bloqueados} día(s) con bloqueado=1 (intactos)")
    if limite:
        print(f"  Congelado hasta {limite.isoformat()} ({n_cong} día(s) conservados del CSV)")
        if n_cong == 0 and not n_bloqueados:
            print("  ⚠ No se conservó ninguna fila: ¿existía CSV con esas fechas?", file=sys.stderr)
    elif not n_bloqueados:
        print("  Sin congelación: se recalculó todo el periodo")
    if n_cong and n_cong != n:
        print(f"  {n - n_cong} día(s) recalculados")

    try:
        from generar_vista import main as generar_vista_main

        generar_vista_main()
    except OSError as e:
        print(f"⚠ No se pudo generar HTML: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
