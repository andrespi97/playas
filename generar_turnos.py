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

import yaml

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
OUTPUT_PATH = ROOT / "turnos_jul_sep_2026.csv"


@dataclass(frozen=True)
class Persona:
    nombre: str
    grupo: int
    rol: str  # "socorrista" | "patron"


def cargar_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_fecha(s: str) -> date:
    y, m, d = map(int, s.split("-"))
    return date(y, m, d)


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
    bloque = 0
    for d in range(dia_idx + 1):
        if (d + desfase) % ciclo == 0:
            bloque += 1
    return bloque - 1


def bloque_calendario(dia_idx: int, rotacion: dict) -> int:
    """Bloques de 4 días naturales alineados al ciclo de trabajo."""
    return dia_idx // rotacion["dias_trabajo"]


def elegir_fijo_por_bloque(
    bloque: int,
    roster: list[str],
    candidatos: list[Persona],
    excluidos: set[str],
) -> Persona | None:
    """Elige del roster según bloque; solo entre candidatos del día."""
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


def dias_bloque_g2(bloque: int, rotacion: dict, total: int) -> tuple[list[int], list[int]]:
    """Días de trabajo y libres del grupo 2 en un bloque concreto."""
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


def precomputar_roles_bloque(
    personas: list[Persona],
    rotacion: dict,
    total: int,
    prefs: dict,
) -> tuple[dict[int, str], dict[int, str]]:
    """
    Zodiac y llave cesantes fijos por bloque G2 (4 días seguidos).
    En días libres de G2 se usa la reserva del mismo bloque.
    """
    pref_zodiac = sin_vacantes_roster(prefs.get("socorrista_zodiac", []))
    pref_zodiac_reserva = sin_vacantes_roster(prefs.get("socorrista_zodiac_reserva", []))
    pool_llave_g2 = sin_vacantes_roster(prefs.get("llave_cesantes_pool_g2", []))
    pool_llave_g3 = sin_vacantes_roster(prefs.get("llave_cesantes_pool_g3", []))
    roster_zodiac = pref_zodiac + pref_zodiac_reserva

    zodiac: dict[int, str] = {}
    llave: dict[int, str] = {}

    bloque = 0
    while True:
        trabajo, _ = dias_bloque_g2(bloque, rotacion, total)
        if not trabajo:
            break

        nombre_z = pref_zodiac[bloque % len(pref_zodiac)] if pref_zodiac else ""
        zodiac[bloque] = nombre_z

        if pref_zodiac_reserva:
            zodiac[(-bloque - 1)] = pref_zodiac_reserva[bloque % len(pref_zodiac_reserva)]

        # Llave cesantes: nunca los de zodiac fijo (Claudio/Alex)
        solo_zodiac = set(sin_vacantes_roster(prefs.get("solo_zodiac", pref_zodiac)))
        candidatos_g2 = [n for n in pool_llave_g2 if n not in solo_zodiac] or pool_llave_g2
        if candidatos_g2:
            llave[bloque] = candidatos_g2[(bloque + 1) % len(candidatos_g2)]

        candidatos_g3 = [n for n in pool_llave_g3 if n not in solo_zodiac] or pool_llave_g3
        if candidatos_g3:
            llave[(-bloque - 1)] = candidatos_g3[bloque % len(candidatos_g3)]

        bloque += 1

    return zodiac, llave


def bloque_g2_activo(dia_idx: int, rotacion: dict) -> int:
    return indice_bloque_grupo(dia_idx, 2, rotacion)


def g2_trabaja(dia_idx: int, rotacion: dict) -> bool:
    return trabaja_en_dia(dia_idx, 2, rotacion)


def es_vacante(p: Persona) -> bool:
    return p.nombre.startswith("Vacante")


def confirmados(candidatos: list[Persona]) -> list[Persona]:
    return [p for p in candidatos if not es_vacante(p)]


def vacantes_de(candidatos: list[Persona]) -> list[Persona]:
    return [p for p in candidatos if es_vacante(p)]


def sin_vacantes_roster(roster: list[str]) -> list[str]:
    return [n for n in roster if not n.startswith("Vacante")]


def solo_nombre(nombre: str) -> str:
    """Solo nombre de pila en el CSV (Vacante 1, 2… se mantiene entero)."""
    if not nombre:
        return ""
    if nombre.startswith("Vacante"):
        return nombre
    return nombre.split()[0]


def columnas_puesto(fila: dict[str, str]) -> list[str]:
    """Columnas con puesto asignado (llave_chapela es metadato, no puesto extra)."""
    base = [
        "socorrista_chapela",
        "patron_chapela",
        "patron_cesantes",
        "llave_cesantes",
        "socorrista_zodiac",
        "abrir_torre",
    ]
    return base + sorted(k for k in fila if k.startswith("cesantes"))


def validar_sin_duplicados(fila: dict[str, str]) -> str | None:
    """Error si la misma persona aparece en más de un puesto el mismo día."""
    vistos: dict[str, str] = {}
    for col in columnas_puesto(fila):
        nombre = fila.get(col, "").strip()
        if not nombre:
            continue
        if nombre in vistos:
            return f"{nombre} repetido ({vistos[nombre]} y {col})"
        vistos[nombre] = col
    return None


CAMPOS_OBLIGATORIOS = (
    ("socorrista_chapela", "socorrista chapela"),
    ("patron_chapela", "patrón chapela"),
    ("llave_cesantes", "abrir puesto"),
)


def validar_cobertura_obligatoria(fila: dict[str, str]) -> str | None:
    """Error si falta Chapela o abrir puesto (llave_cesantes)."""
    for col, etiqueta in CAMPOS_OBLIGATORIOS:
        if not fila.get(col, "").strip():
            return f"Falta {etiqueta}"
    return None


def nombres_asignados_fila(fila: dict[str, str]) -> set[str]:
    """Nombres de pila con puesto asignado en un día."""
    return {fila.get(col, "").strip() for col in columnas_puesto(fila) if fila.get(col, "").strip()}


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
) -> str | None:
    """Error si alguien trabaja fuera de su grupo o supera el bloque de días de trabajo."""
    if not filas:
        return None

    max_trabajo = rotacion["dias_trabajo"]
    indice = indice_por_nombre_pila(personas)
    if inicio is None:
        inicio = parse_fecha(filas[0]["fecha"])

    for fila in filas:
        dia_idx = (parse_fecha(fila["fecha"]) - inicio).days
        for nombre in nombres_asignados_fila(fila):
            persona = indice.get(nombre)
            if not persona:
                continue
            if not trabaja_en_dia(dia_idx, persona.grupo, rotacion):
                return (
                    f"{nombre} asignado el {fila['fecha']} en día libre "
                    f"(grupo {persona.grupo})"
                )

    for nombre in indice:
        dias = [
            (parse_fecha(fila["fecha"]) - inicio).days
            for fila in filas
            if nombre in nombres_asignados_fila(fila)
        ]
        racha = max_racha_dias(dias)
        if racha > max_trabajo:
            return f"{nombre}: {racha} días seguidos asignados (máximo {max_trabajo})"

    return None


def personal_del_dia(
    personas: list[Persona],
    dia_idx: int,
    rotacion: dict,
) -> tuple[list[Persona], list[Persona]]:
    trabajando = [p for p in personas if trabaja_en_dia(dia_idx, p.grupo, rotacion)]
    patrones = [p for p in trabajando if p.rol == "patron"]
    socorristas = [p for p in trabajando if p.rol == "socorrista"]
    return patrones, socorristas


def buscar_por_nombre(lista: list[Persona], nombre: str) -> Persona | None:
    for p in lista:
        if p.nombre == nombre:
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


def asignar_puestos(
    patrones: list[Persona],
    socorristas: list[Persona],
    dia_idx: int,
    rotacion: dict,
    prefs: dict,
    roles_bloque: tuple[dict[int, str], dict[int, str]],
) -> dict[str, str]:
    fila: dict[str, str] = {}

    if len(patrones) < 1:
        fila["_error"] = f"Faltan patrones ({len(patrones)}/1 mínimo)"
        return fila
    if len(socorristas) < 2:
        fila["_error"] = f"Faltan socorristas ({len(socorristas)}/2 mínimo)"
        return fila

    pref_patron_chapela = prefs.get("patron_chapela", [])
    pareja_chapela: dict[str, str] = prefs.get("pareja_chapela", {})
    pref_soc_chapela = prefs.get("socorrista_chapela", [])
    pref_zodiac = sin_vacantes_roster(prefs.get("socorrista_zodiac", []))
    pref_zodiac_reserva = sin_vacantes_roster(prefs.get("socorrista_zodiac_reserva", []))
    solo_zodiac = set(sin_vacantes_roster(prefs.get("solo_zodiac", pref_zodiac)))
    llave_patrones = prefs.get("llave_chapela_patrones", [])
    pool_llave_g2 = sin_vacantes_roster(prefs.get("llave_cesantes_pool_g2", []))
    pool_llave_g3 = sin_vacantes_roster(prefs.get("llave_cesantes_pool_g3", []))
    pool_torre = sin_vacantes_roster(prefs.get("abrir_torre_pool", []))
    zodiac_por_bloque, llave_por_bloque = roles_bloque

    bloque = bloque_g2_activo(dia_idx, rotacion)
    bloque_cal = bloque_calendario(dia_idx, rotacion)
    soc_confirmados = confirmados(socorristas)

    # --- Chapela (patrón + socorrista) ---
    esther = buscar_por_nombre(patrones, pref_patron_chapela[0]) if pref_patron_chapela else None
    if esther:
        patron_chapela = esther
    else:
        otros = [p for p in patrones if p.nombre in llave_patrones] or patrones
        patron_chapela = elegir_fijo_por_bloque(bloque_cal, llave_patrones, otros, set()) or otros[0]

    otros_patrones = [p for p in patrones if p.nombre != patron_chapela.nombre]
    patron_cesantes = otros_patrones[0] if otros_patrones else None

    pareja = pareja_chapela.get(patron_chapela.nombre)
    if pareja and (soc := buscar_por_nombre(soc_confirmados, pareja)):
        socorrista_chapela = soc
    else:
        socorrista_chapela = elegir_preferido(soc_confirmados, pref_soc_chapela, set())

    if not socorrista_chapela:
        fila["_error"] = "Sin socorrista chapela"
        return fila

    excluidos: set[str] = {socorrista_chapela.nombre}

    # --- 1. Abrir puesto (llave cesantes) — nunca Claudio/Alex ---
    if g2_trabaja(dia_idx, rotacion):
        nombre_lc = llave_por_bloque.get(bloque, pool_llave_g2[0] if pool_llave_g2 else "")
    else:
        nombre_lc = llave_por_bloque.get(-bloque - 1, pool_llave_g3[0] if pool_llave_g3 else "")

    pool_llave = [n for n in pool_llave_g2 + pool_llave_g3 if n not in solo_zodiac]
    candidatos_llave = [s for s in soc_confirmados if s.nombre not in excluidos and s.nombre not in solo_zodiac]
    llave_cesantes = buscar_por_nombre(candidatos_llave, nombre_lc)
    if not llave_cesantes:
        llave_cesantes = elegir_fijo_por_bloque(bloque + 1, pool_llave, candidatos_llave, set())
    if not llave_cesantes and candidatos_llave:
        llave_cesantes = candidatos_llave[(bloque + 1) % len(candidatos_llave)]

    # Último recurso: solo entre quienes trabajan hoy (p. ej. G3 libra y no hay Sergio/Robson)
    if not llave_cesantes:
        emergencia = [
            s for s in soc_confirmados if s.nombre not in excluidos and s.nombre in solo_zodiac
        ]
        llave_cesantes = elegir_fijo_por_bloque(bloque + 1, list(solo_zodiac), emergencia, set())
        if not llave_cesantes and emergencia:
            llave_cesantes = emergencia[(bloque + 1) % len(emergencia)]

    if llave_cesantes:
        excluidos.add(llave_cesantes.nombre)

    # --- 2. Socorrista zodiac — Claudio/Alex (4 días); reserva si G2 libra ---
    if g2_trabaja(dia_idx, rotacion):
        nombre_z = zodiac_por_bloque.get(bloque, pref_zodiac[0] if pref_zodiac else "")
        roster_z = pref_zodiac
    else:
        nombre_z = zodiac_por_bloque.get(-bloque - 1, pref_zodiac_reserva[0] if pref_zodiac_reserva else "")
        roster_z = pref_zodiac_reserva

    candidatos_zodiac = [s for s in soc_confirmados if s.nombre not in excluidos]
    # Priorizar Claudio/Alex cuando trabajan
    if g2_trabaja(dia_idx, rotacion):
        candidatos_zodiac = [
            s for s in candidatos_zodiac if s.nombre in solo_zodiac
        ] or candidatos_zodiac

    socorrista_zodiac = buscar_por_nombre(candidatos_zodiac, nombre_z)
    if not socorrista_zodiac:
        socorrista_zodiac = elegir_fijo_por_bloque(bloque, roster_z, candidatos_zodiac, set())
    if not socorrista_zodiac and candidatos_zodiac:
        socorrista_zodiac = candidatos_zodiac[0]

    if socorrista_zodiac:
        excluidos.add(socorrista_zodiac.nombre)

    # --- 3. Abrir torre — otro socorrista; Claudio/Alex solo si no son zodiac hoy ---
    excl_torre = excluidos.copy()
    candidatos_torre = [s for s in soc_confirmados if s.nombre not in excl_torre]
    pool_torre_filtrado = list(pool_torre)
    abrir_torre = elegir_fijo_por_bloque(bloque_cal, pool_torre_filtrado, candidatos_torre, set())
    if not abrir_torre and candidatos_torre:
        abrir_torre = candidatos_torre[0]

    if abrir_torre:
        excluidos.add(abrir_torre.nombre)

    # Puestos críticos → CSV
    fila["socorrista_chapela"] = solo_nombre(socorrista_chapela.nombre)
    fila["patron_chapela"] = solo_nombre(patron_chapela.nombre)
    patrones_con_llave = [p for p in patrones if p.nombre in llave_patrones] or patrones
    titular = elegir_fijo_por_bloque(bloque_cal, llave_patrones, patrones_con_llave, set())
    fila["llave_chapela"] = solo_nombre(titular.nombre if titular else patron_chapela.nombre)
    fila["patron_cesantes"] = solo_nombre(patron_cesantes.nombre) if patron_cesantes else ""
    fila["llave_cesantes"] = solo_nombre(llave_cesantes.nombre) if llave_cesantes else ""
    fila["socorrista_zodiac"] = solo_nombre(socorrista_zodiac.nombre) if socorrista_zodiac else ""
    fila["abrir_torre"] = solo_nombre(abrir_torre.nombre) if abrir_torre else ""

    faltan = []
    libres = [s for s in soc_confirmados if s.nombre != socorrista_chapela.nombre]
    hay_para_tres = len(libres) >= 3

    if not socorrista_zodiac:
        faltan.append("zodiac")
    if not llave_cesantes:
        faltan.append("abrir puesto")
    if hay_para_tres and not abrir_torre:
        faltan.append("torre")
    if faltan:
        fila["_error"] = f"Sin cubrir: {', '.join(faltan)}"
        return fila

    # --- Cesantes2+ → socorristas sobrantes ---
    asignados = excluidos | {patron_chapela.nombre}
    if patron_cesantes:
        asignados.add(patron_cesantes.nombre)
    extras = [s for s in soc_confirmados if s.nombre not in asignados]
    for i, extra in enumerate(extras, start=2):
        fila[f"cesantes{i}"] = solo_nombre(extra.nombre)

    if dup := validar_sin_duplicados(fila):
        fila["_error"] = dup
    elif (cob := validar_cobertura_obligatoria(fila)):
        fila["_error"] = cob

    return fila


def construir_personas(cfg: dict) -> list[Persona]:
    personas: list[Persona] = []
    for s in cfg["socorristas"]:
        personas.append(Persona(s["nombre"], s["grupo"], "socorrista"))
    for p in cfg["patrones"]:
        personas.append(Persona(p["nombre"], p["grupo"], "patron"))
    return personas


def generar_csv(cfg: dict) -> tuple[Path, int, list[str]]:
    inicio = parse_fecha(cfg["periodo"]["inicio"])
    fin = parse_fecha(cfg["periodo"]["fin"])
    fechas = rango_fechas(inicio, fin)
    personas = construir_personas(cfg)
    rotacion = cfg["rotacion"]
    prefs = cfg.get("preferencias", {})
    total = len(rango_fechas(parse_fecha(cfg["periodo"]["inicio"]), parse_fecha(cfg["periodo"]["fin"])))
    roles_bloque = precomputar_roles_bloque(personas, rotacion, total, prefs)

    filas: list[dict[str, str]] = []
    max_cesantes = 0
    errores: list[str] = []

    for dia_idx, fecha in enumerate(fechas):
        patrones, socorristas = personal_del_dia(personas, dia_idx, rotacion)
        asignacion = asignar_puestos(patrones, socorristas, dia_idx, rotacion, prefs, roles_bloque)

        if "_error" in asignacion:
            errores.append(f"{fecha.isoformat()}: {asignacion['_error']}")
            asignacion = {k: v for k, v in asignacion.items() if k != "_error"}
        elif (dup := validar_sin_duplicados(asignacion)):
            errores.append(f"{fecha.isoformat()}: {dup}")
        elif (cob := validar_cobertura_obligatoria(asignacion)):
            errores.append(f"{fecha.isoformat()}: {cob}")

        cesantes_cols = [k for k in asignacion if k.startswith("cesantes")]
        max_cesantes = max(max_cesantes, len(cesantes_cols))

        fila = {"fecha": fecha.isoformat(), **asignacion}
        filas.append(fila)

    if rot_err := validar_rotacion_4_2(filas, personas, rotacion, inicio):
        errores.append(rot_err)

    columnas_base = [
        "fecha",
        "socorrista_chapela",
        "patron_chapela",
        "llave_chapela",
        "patron_cesantes",
        "socorrista_zodiac",
        "llave_cesantes",
        "abrir_torre",
    ]
    columnas_cesantes = [f"cesantes{i}" for i in range(2, 2 + max_cesantes)]
    columnas = columnas_base + columnas_cesantes

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)

    return OUTPUT_PATH, len(filas), errores


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"No se encuentra {CONFIG_PATH}", file=sys.stderr)
        return 1

    cfg = cargar_config()
    out, n, errores = generar_csv(cfg)

    print(f"CSV generado: {out} ({n} días)")

    try:
        from generar_vista import main as generar_vista_main

        generar_vista_main()
    except Exception as e:
        print(f"⚠ No se pudo generar HTML: {e}", file=sys.stderr)

    if errores:
        print(f"\n⚠ {len(errores)} días con cobertura insuficiente:", file=sys.stderr)
        for e in errores[:10]:
            print(f"  - {e}", file=sys.stderr)
        if len(errores) > 10:
            print(f"  ... y {len(errores) - 10} más", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
