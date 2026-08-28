#!/usr/bin/env python3
"""Tests del cuadrante de turnos."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from datetime import date

from generar_turnos import (  # noqa: E402
    EQUILIBRIO_ZODIAC_DESDE,
    ErrorGeneracion,
    ausentes_por_disponibilidad,
    ausentes_por_hasta,
    cargar_config_validada,
    construir_personas,
    contar_socorristas_trabajando,
    generar_csv,
    hay_patron_cesantes,
    max_racha_dias,
    reubicar_zodiac_sin_patron,
    nombres_asignados_fila,
    nombres_completos_ausentes,
    nombres_refuerzo_disponibilidad,
    trabaja_en_dia,
    validar_administracion,
    validar_config,
    validar_cobertura_extendida,
    validar_cobertura_obligatoria,
    validar_rotacion_4_2,
    validar_sin_duplicados,
    validar_abren_zodiac,
    validar_no_patron,
    validar_zodiac_solo_socorrista,
)
from turnos_common import (  # noqa: E402
    CSV_PATH,
    cargar_config,
    cargar_filas_csv,
    celda_bloqueada,
    fechas_bloqueadas_csv,
    contar_horas_fila,
    contar_horas_por_mes,
    contar_socorristas_cesantes,
    cubridores_vacantes_fila,
    fecha_congelacion_limite,
    nombres_asignados_dia,
    nombres_en_cesantes,
    parse_fecha,
    parse_horas_extras,
    parse_horas_compensadas,
    parse_anotaciones_horas,
    parse_lista_nombres,
    parse_compensacion,
    saldos_compensacion,
    errores_saldos_compensacion,
    solo_nombre,
    sustitutos_presentes_fila,
)


def filas_csv() -> list[dict[str, str]]:
    return cargar_filas_csv()


class CsvBackupMixin:
    """Evita que tests que editan el CSV dejen datos basura (p. ej. vacaciones=Esther)."""

    _csv_backup: bytes

    def setUp(self) -> None:
        self._csv_backup = CSV_PATH.read_bytes()

    def tearDown(self) -> None:
        CSV_PATH.write_bytes(self._csv_backup)


class TestSinDuplicados(unittest.TestCase):
    def test_csv_sin_personas_repetidas_por_dia(self) -> None:
        filas = filas_csv()
        self.assertGreater(len(filas), 0, "CSV vacío")
        for fila in filas:
            err = validar_sin_duplicados(fila)
            self.assertIsNone(err, f"{fila['fecha']}: {err}")

    def test_detecta_duplicado_artificial(self) -> None:
        fila = {
            "fecha": "2026-07-01",
            "socorrista_chapela": "Robinson",
            "patron_chapela": "Adrián",
            "llave_chapela": "Adrián",
            "patron_cesantes": "Vacante 3",
            "llave_cesantes": "Sergio",
            "socorrista_zodiac": "Claudio",
            "abrir_torre": "Claudio",
        }
        self.assertEqual(
            validar_sin_duplicados(fila),
            "Claudio repetido (socorrista_zodiac y abrir_torre)",
        )


class TestCoberturaObligatoria(unittest.TestCase):
    def test_csv_chapela_y_abrir_puesto_en_cada_dia(self) -> None:
        filas = filas_csv()
        for fila in filas:
            err = validar_cobertura_obligatoria(fila)
            self.assertIsNone(err, f"{fila['fecha']}: {err}")

    def test_detecta_falta_abrir_puesto_artificial(self) -> None:
        fila = {
            "fecha": "2026-07-03",
            "socorrista_chapela": "Fernando",
            "patron_chapela": "Esther",
            "llave_cesantes": "",
        }
        self.assertEqual(validar_cobertura_obligatoria(fila), "Falta abrir puesto")


class TestCoberturaExtendida(unittest.TestCase):
    def test_csv_zodiac_y_torre_cuando_hay_personal(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        personas = construir_personas(cfg)
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = filas_csv()
        for dia_idx, fila in enumerate(filas):
            if celda_bloqueada(fila.get("bloqueado", "")):
                continue
            fecha_str = fila["fecha"]
            ausentes = nombres_completos_ausentes(
                fila.get("vacaciones", ""), personas, fila.get("horas_extras", "")
            ) | ausentes_por_disponibilidad(
                cfg, fecha_str, personas
            ) | ausentes_por_hasta(cfg, fecha_str)
            n = contar_socorristas_trabajando(personas, dia_idx, rot, ausentes, cfg, fecha_str)
            err = validar_cobertura_extendida(fila, n)
            if err == "Zodiac sin patrón Cesantes" and parse_fecha(fecha_str) < EQUILIBRIO_ZODIAC_DESDE:
                continue
            self.assertIsNone(err, f"{fecha_str} ({n} socorristas): {err}")

    def test_detecta_falta_torre_con_personal(self) -> None:
        self.assertEqual(
            validar_cobertura_extendida(
                {
                    "socorrista_chapela": "Robinson",
                    "patron_cesantes": "Adrián",
                    "llave_cesantes": "Sergio",
                    "socorrista_zodiac": "Claudio",
                    "abrir_torre": "",
                },
                4,
            ),
            "Falta torre",
        )

    def test_detecta_zodiac_sin_patron_cesantes(self) -> None:
        self.assertEqual(
            validar_cobertura_extendida(
                {
                    "socorrista_chapela": "Robinson",
                    "patron_cesantes": "Vacante 3",
                    "llave_cesantes": "Sergio",
                    "socorrista_zodiac": "Claudio",
                    "abrir_torre": "Alejandro",
                },
                4,
            ),
            "Zodiac sin patrón Cesantes",
        )
        self.assertIsNone(
            validar_cobertura_extendida(
                {
                    "socorrista_chapela": "Robinson",
                    "patron_cesantes": "Vacante 3",
                    "llave_cesantes": "Sergio",
                    "socorrista_zodiac": "",
                    "abrir_torre": "Alejandro",
                },
                4,
            )
        )


class TestRotacion4x2(unittest.TestCase):
    def test_csv_respeta_4_dias_trabajo_2_descanso(self) -> None:
        cfg = cargar_config_validada()
        filas = filas_csv()
        err = validar_rotacion_4_2(
            filas,
            construir_personas(cfg),
            cfg["rotacion"],
            parse_fecha(cfg["periodo"]["inicio"]),
            fechas_congeladas=fechas_bloqueadas_csv(),
            refuerzos_disponibilidad=nombres_refuerzo_disponibilidad(cfg),
        )
        self.assertIsNone(err, err)

    def test_vacantes_socorrista_en_cesantes_si_trabajan(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        # 3/jul: G1 y G2 trabajan; Vacante 1 y Vacante 2 deben aparecer en cesantes
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-03")
        cesantes = parse_lista_nombres(fila["cesantes"])
        self.assertIn("Vacante 1", cesantes)
        self.assertIn("Vacante 2", cesantes)

    def test_detecta_asignacion_en_dia_libre(self) -> None:
        cfg = cargar_config_validada()
        self.assertEqual(
            validar_rotacion_4_2(
                [
                    {
                        "fecha": "2026-07-03",
                        "socorrista_chapela": "Claudio",
                        "patron_chapela": "Adrián",
                        "llave_cesantes": "Robinson",
                    }
                ],
                construir_personas(cfg),
                cfg["rotacion"],
                parse_fecha("2026-07-01"),
            ),
            "Robinson asignado el 2026-07-03 en día libre (grupo 3); añádelo a horas_extras si es extra",
        )

    def test_detecta_patron_en_dia_libre(self) -> None:
        cfg = cargar_config_validada()
        self.assertEqual(
            validar_rotacion_4_2(
                [
                    {
                        "fecha": "2026-07-01",
                        "socorrista_chapela": "Robinson",
                        "patron_cesantes": "Esther",
                        "llave_cesantes": "Sergio",
                    }
                ],
                construir_personas(cfg),
                cfg["rotacion"],
                parse_fecha("2026-07-01"),
            ),
            "Esther asignado el 2026-07-01 en día libre (grupo 1); añádelo a horas_extras si es extra",
        )

    def test_patrones_no_asignados_en_dia_libre(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = filas_csv()
        err = validar_rotacion_4_2(
            filas,
            personas,
            rot,
            inicio,
            fechas_congeladas=fechas_bloqueadas_csv(),
            refuerzos_disponibilidad=nombres_refuerzo_disponibilidad(cfg),
        )
        self.assertIsNone(err, err)
        for fila in filas:
            if celda_bloqueada(fila.get("bloqueado", "")):
                continue
            dia_idx = (parse_fecha(fila["fecha"]) - inicio).days
            for nombre in ("Esther", "Fernando", "Adrián"):
                if nombre not in nombres_asignados_fila(fila):
                    continue
                p = next(x for x in personas if x.nombre.split()[0] == nombre)
                self.assertTrue(
                    trabaja_en_dia(dia_idx, p.grupo, rot)
                    or nombre in parse_horas_extras(fila.get("horas_extras", "")),
                    f"{nombre} asignado el {fila['fecha']} en día libre sin horas_extras",
                )

    def test_fernando_libra_13_sep_a_cambio_del_30_ago(self) -> None:
        """Fernando libra el 13 sep (trabajo G1); el 30 ago (libranza G1) cuenta como horas normales."""
        cfg = cargar_config_validada()
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = {f["fecha"]: f for f in filas_csv()}
        self.assertTrue(trabaja_en_dia((parse_fecha("2026-09-13") - inicio).days, 1, rot))
        self.assertFalse(trabaja_en_dia((parse_fecha("2026-08-30") - inicio).days, 1, rot))
        self.assertNotIn("Fernando", nombres_asignados_dia(filas["2026-09-13"]))
        self.assertIn("Fernando", nombres_asignados_dia(filas["2026-08-30"]))
        self.assertNotIn("Fernando", parse_horas_extras(filas["2026-08-30"].get("horas_extras", "")))
        self.assertIn("Esther", parse_horas_extras(filas["2026-08-30"].get("horas_extras", "")))
        self.assertEqual(filas["2026-09-13"]["socorrista_chapela"], "Claudio")
        self.assertEqual(filas["2026-09-13"]["patron_chapela"], "Esther")
        self.assertFalse(filas["2026-09-13"]["socorrista_zodiac"].strip())
        self.assertEqual(filas["2026-09-13"]["abrir_torre"], "Anxo")

    def test_rodrigo_cambia_miercoles_19_por_26(self) -> None:
        """Trueque de miércoles: libra el 19 (trabajo G3) y trabaja el 26 (libranza G3)."""
        cfg = cargar_config_validada()
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = {f["fecha"]: f for f in filas_csv()}
        self.assertTrue(trabaja_en_dia((parse_fecha("2026-08-19") - inicio).days, 3, rot))
        self.assertFalse(trabaja_en_dia((parse_fecha("2026-08-26") - inicio).days, 3, rot))
        self.assertNotIn("Rodrigo", nombres_asignados_dia(filas["2026-08-19"]))
        self.assertIn("Rodrigo", nombres_asignados_dia(filas["2026-08-26"]))
        self.assertNotIn("Rodrigo", parse_horas_extras(filas["2026-08-26"].get("horas_extras", "")))

    def test_rodrigo_viernes_21_ago(self) -> None:
        """Viernes 21 (libranza G3) a cambio del viernes 28."""
        filas = {f["fecha"]: f for f in filas_csv()}
        self.assertIn("Rodrigo", nombres_asignados_dia(filas["2026-08-21"]))
        self.assertNotIn("Rodrigo", parse_horas_extras(filas["2026-08-21"].get("horas_extras", "")))

    def test_rodrigo_no_fin_de_agosto(self) -> None:
        """Rodrigo no trabaja el 28–30 ago (vie–dom)."""
        filas = {f["fecha"]: f for f in filas_csv()}
        for fecha in ("2026-08-28", "2026-08-29", "2026-08-30"):
            self.assertNotIn("Rodrigo", nombres_asignados_dia(filas[fecha]), fecha)

    def test_rodrigo_ultimo_dia_3_sep(self) -> None:
        """Jueves 3 sep es el último día de Rodrigo."""
        filas = {f["fecha"]: f for f in filas_csv()}
        self.assertIn("Rodrigo", nombres_asignados_dia(filas["2026-09-03"]))
        for fila in filas_csv():
            if fila["fecha"] <= "2026-09-03":
                continue
            self.assertNotIn("Rodrigo", nombres_asignados_dia(fila), fila["fecha"])

    def test_detecta_racha_de_5_dias(self) -> None:
        self.assertEqual(max_racha_dias([0, 1, 2, 3, 4]), 5)


    def test_horas_extras_invalidas_en_rotacion(self) -> None:
        cfg = cargar_config_validada()
        err = validar_rotacion_4_2(
            [{"fecha": "2026-07-01", "horas_extras": "Esther:mal", "llave_cesantes": "Sergio"}],
            construir_personas(cfg),
            cfg["rotacion"],
            parse_fecha("2026-07-01"),
        )
        self.assertIsNotNone(err)
        self.assertIn("mal", err)


class TestPreferenciaZodiac(unittest.TestCase):
    def test_claudio_alex_prefieren_zodiac_si_hay_otro_para_abrir(self) -> None:
        """1/jul: Robinson chapela, Sergio abre puesto, Claudio zodiac (no al revés)."""
        filas = {f["fecha"]: f for f in filas_csv()}
        fila = filas["2026-07-01"]
        self.assertEqual(fila["llave_cesantes"], "Sergio")
        self.assertIn(fila["socorrista_zodiac"], ("Claudio", "Alejandro"))
        self.assertNotIn(fila["llave_cesantes"], ("Claudio", "Alejandro"))

    def test_pueden_abrir_puesto_si_no_hay_otro(self) -> None:
        """3/jul (G3 libra): solo Fernando + Claudio/Alex → uno abre puesto."""
        filas = {f["fecha"]: f for f in filas_csv()}
        fila = filas["2026-07-03"]
        self.assertIn(fila["llave_cesantes"], ("Claudio", "Alejandro"))
        self.assertTrue(fila["llave_cesantes"] != fila.get("socorrista_zodiac", ""))


    def test_cesantes_varios_en_una_columna(self) -> None:
        self.assertEqual(
            parse_lista_nombres("Vacante 2; Vacante 4"),
            ["Vacante 2", "Vacante 4"],
        )
        fila = next(f for f in filas_csv() if f["fecha"] == "2026-07-01")
        self.assertNotIn("cesantes2", fila)
        self.assertIn("cesantes", fila)


class TestSustitutos(unittest.TestCase):
    def test_cuenta_sustitutos_presentes(self) -> None:
        fila = {
            "fecha": "2026-07-10",
            "socorrista_chapela": "Fernando",
            "abrir_torre": "Arturo",
            "cesantes": "Vacante 1; Vacante 2",
            "vacaciones": "",
        }
        self.assertEqual(sustitutos_presentes_fila(fila, ["Arturo", "Anxo"]), ["Arturo"])

    def test_dos_sustitutos(self) -> None:
        fila = {
            "fecha": "2026-07-10",
            "socorrista_chapela": "Fernando",
            "abrir_torre": "Arturo",
            "cesantes": "Anxo; Vacante 1",
            "vacaciones": "",
        }
        self.assertEqual(sustitutos_presentes_fila(fila, ["Arturo", "Anxo"]), ["Arturo", "Anxo"])

    def test_marca_vacantes_en_vista(self) -> None:
        from generar_vista import puestos_dia

        fila = next(f for f in filas_csv() if f["fecha"] == "2026-07-10")
        puestos = puestos_dia(fila, ["Arturo", "Anxo"])
        cubiertas = [p for p in puestos if p.get("vacante_cubierta")]
        self.assertEqual(len(cubiertas), 1)
        self.assertEqual(cubiertas[0]["sustituto"], "Arturo")
        self.assertTrue(str(cubiertas[0]["persona"]).startswith("Vacante"))

    def test_extra_normal_cubre_vacante(self) -> None:
        from generar_vista import puestos_dia

        fila = {
            "fecha": "2026-07-10",
            "socorrista_chapela": "Fernando",
            "cesantes": "Vacante 1; Vacante 2",
            "vacaciones": "",
            "horas_extras": "Esther:8",
        }
        puestos = puestos_dia(fila, ["Arturo", "Anxo"])
        cubiertas = [p for p in puestos if p.get("vacante_cubierta")]
        self.assertEqual(len(cubiertas), 1)
        self.assertEqual(cubiertas[0]["sustituto"], "Esther")
        self.assertEqual(cubridores_vacantes_fila(fila, ["Arturo", "Anxo"]), ["Esther"])

    def test_sustituto_y_extra_cubren_dos_vacantes(self) -> None:
        from generar_vista import puestos_dia

        fila = {
            "fecha": "2026-07-10",
            "socorrista_chapela": "Fernando",
            "abrir_torre": "Arturo",
            "cesantes": "Vacante 1; Vacante 2",
            "vacaciones": "",
            "horas_extras": "Esther:8",
        }
        puestos = puestos_dia(fila, ["Arturo", "Anxo"])
        cubiertas = [p for p in puestos if p.get("vacante_cubierta")]
        self.assertEqual(len(cubiertas), 2)
        self.assertEqual([p["sustituto"] for p in cubiertas], ["Arturo", "Esther"])

    def test_arturo_en_dias_disponibilidad_julio(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fechas = (
            "2026-07-15",
            "2026-07-16",
            "2026-07-21",
            "2026-07-22",
            "2026-07-27",
            "2026-07-28",
        )
        sustitutos = cfg.get("sustitutos", [])
        for fecha in fechas:
            fila = next(f for f in cargar_filas_csv() if f["fecha"] == fecha)
            presentes = sustitutos_presentes_fila(fila, sustitutos)
            self.assertIn("Arturo", presentes, f"Arturo ausente el {fecha}")
            self.assertIn("Arturo", nombres_asignados_dia(fila), f"Arturo no asignado el {fecha}")

    def test_arturo_o_rober_abren_zodiac_si_presentes(self) -> None:
        """Días no bloqueados: si Arturo o Rober trabajan, abren Zodiac."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        abren = {"Arturo", "Rober"}
        for fila in cargar_filas_csv():
            if celda_bloqueada(fila.get("bloqueado", "")):
                continue
            presentes = set(nombres_asignados_dia(fila))
            if presentes & abren:
                if hay_patron_cesantes(fila):
                    self.assertIn(
                        fila.get("socorrista_zodiac"),
                        abren,
                        f"{fila['fecha']}: Zodiac debe ser Arturo o Rober",
                    )
                else:
                    self.assertNotIn(
                        fila.get("socorrista_zodiac"),
                        abren,
                        f"{fila['fecha']}: sin patrón Cesantes no se abre Zodiac",
                    )
            self.assertIsNone(validar_abren_zodiac(fila, cfg), fila["fecha"])

    def test_robinson_no_abre_zodiac_si_esta_en_chapela(self) -> None:
        """Robinson en Chapela no puede abrir Zodiac el mismo día."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        for fila in cargar_filas_csv():
            if fila.get("socorrista_chapela") == "Robinson":
                self.assertNotEqual(
                    fila.get("socorrista_zodiac"),
                    "Robinson",
                    f"{fila['fecha']}: Robinson en Chapela no abre Zodiac",
                )

    def test_reserva_zodiac_reparte_pool(self) -> None:
        """Sin Arturo/Rober, Zodiac lo cubren Robinson, Alejandro, Sergio, Rodrigo o Claudio."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        pool = {"Robinson", "Alejandro", "Sergio", "Rodrigo", "Claudio"}
        usados: set[str] = set()
        n = 0
        for fila in cargar_filas_csv():
            if parse_fecha(fila["fecha"]) < date(2026, 8, 18):
                continue
            z = fila.get("socorrista_zodiac", "").strip()
            presentes = set(nombres_asignados_dia(fila))
            if presentes & {"Arturo", "Rober"}:
                continue
            if not z:
                continue
            self.assertIn(z, pool, f"{fila['fecha']}: Zodiac de reserva debe ser {pool}")
            usados.add(z)
            n += 1
        self.assertGreaterEqual(n, 4)
        self.assertGreaterEqual(
            len(usados),
            3,
            "Zodiac de reserva no debe recaer siempre en las mismas personas",
        )

    def test_validar_abren_zodiac(self) -> None:
        cfg = cargar_config_validada()
        self.assertIsNone(
            validar_abren_zodiac(
                {"socorrista_zodiac": "Rober", "patron_cesantes": "Adrián", "cesantes": "Aaron"},
                cfg,
            )
        )
        self.assertIsNone(
            validar_abren_zodiac(
                {"socorrista_zodiac": "Claudio", "patron_cesantes": "Adrián", "cesantes": "Aaron"},
                cfg,
            )
        )
        err = validar_abren_zodiac(
            {"socorrista_zodiac": "Claudio", "patron_cesantes": "Adrián", "cesantes": "Arturo"},
            cfg,
        )
        self.assertIsNotNone(err)
        self.assertIn("Arturo", err or "")
        self.assertIsNone(
            validar_abren_zodiac(
                {
                    "socorrista_chapela": "Robinson",
                    "patron_cesantes": "Adrián",
                    "socorrista_zodiac": "Claudio",
                    "cesantes": "Aaron",
                },
                cfg,
            )
        )
        self.assertIsNone(
            validar_abren_zodiac(
                {"socorrista_zodiac": "Claudio", "patron_cesantes": "Adrián", "cesantes": "Robinson"},
                cfg,
            )
        )
        self.assertIsNone(
            validar_abren_zodiac(
                {
                    "socorrista_zodiac": "Claudio",
                    "patron_cesantes": "Vacante 3",
                    "cesantes": "Arturo",
                },
                cfg,
            )
        )

    def test_anxo_no_el_fin_de_semana_22_23_agosto(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = {f["fecha"]: f for f in cargar_filas_csv()}
        self.assertNotIn("Anxo", nombres_asignados_dia(filas["2026-08-22"]), "Anxo no debe trabajar el 22 ago")
        self.assertNotIn("Anxo", nombres_asignados_dia(filas["2026-08-23"]), "Anxo no debe trabajar el 23 ago")
        self.assertIn("Anxo", nombres_asignados_dia(filas["2026-08-16"]), "Anxo sigue los demás fines de semana")

    def test_rober_en_dias_disponibilidad_agosto(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fechas = (
            "2026-08-16",
            "2026-08-17",
            "2026-08-22",
            "2026-08-28",
            "2026-08-29",
        )
        sustitutos = cfg.get("sustitutos", [])
        for fecha in fechas:
            fila = next(f for f in cargar_filas_csv() if f["fecha"] == fecha)
            self.assertIn("Rober", nombres_asignados_dia(fila), f"Rober no asignado el {fecha}")
            self.assertIn("Rober", sustitutos_presentes_fila(fila, sustitutos), f"Rober ausente el {fecha}")
            if celda_bloqueada(fila.get("bloqueado", "")):
                continue
            if hay_patron_cesantes(fila):
                self.assertEqual(fila.get("socorrista_zodiac"), "Rober", fecha)
            else:
                self.assertNotEqual(fila.get("socorrista_zodiac"), "Rober", fecha)

    def test_sin_patron_cesantes_no_abre_zodiac_desde_equilibrio(self) -> None:
        """Sin patrón Cesantes (vacante o vacío) no hay Zodiac a partir del 18 ago."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        for fila in cargar_filas_csv():
            if parse_fecha(fila["fecha"]) < date(2026, 8, 18):
                continue
            if hay_patron_cesantes(fila):
                continue
            self.assertFalse(
                (fila.get("socorrista_zodiac") or "").strip(),
                f"{fila['fecha']}: Zodiac sin patrón Cesantes «{fila.get('patron_cesantes')}»",
            )

    def test_reubicar_zodiac_sin_patron(self) -> None:
        fila = {
            "socorrista_zodiac": "Claudio",
            "abrir_torre": "",
            "cesantes": "Vacante 2",
        }
        reubicar_zodiac_sin_patron(fila)
        self.assertEqual(fila["socorrista_zodiac"], "")
        self.assertEqual(fila["abrir_torre"], "Claudio")

        fila = {
            "socorrista_zodiac": "Rober",
            "abrir_torre": "Sergio",
            "cesantes": "Vacante 2; Anxo",
        }
        reubicar_zodiac_sin_patron(fila)
        self.assertEqual(fila["socorrista_zodiac"], "")
        self.assertEqual(fila["abrir_torre"], "Sergio")
        self.assertEqual(fila["cesantes"], "Anxo; Rober")

    def test_refuerzos_nunca_patron(self) -> None:
        from generar_vista import puestos_dia

        cfg = cargar_config_validada()
        sustitutos = cfg.get("sustitutos", [])
        prohibidos = {
            solo_nombre(n)
            for n in (cfg.get("preferencias") or {}).get("no_patron", [])
        }
        self.assertTrue({"Rober", "Aaron"} <= prohibidos)
        for fila in cargar_filas_csv():
            for col in ("patron_chapela", "patron_cesantes"):
                nombre = solo_nombre(fila.get(col, ""))
                self.assertNotIn(
                    nombre,
                    prohibidos,
                    f"{nombre} como {col} el {fila['fecha']}",
                )
            self.assertIsNone(validar_no_patron(fila, cfg), fila["fecha"])
            for puesto in puestos_dia(fila, sustitutos):
                if puesto.get("campo") in ("patron_chapela", "patron_cesantes"):
                    self.assertNotIn(
                        puesto.get("sustituto"),
                        prohibidos,
                        f"{puesto.get('sustituto')} cubre vacante de patrón el {fila['fecha']}",
                    )

    def test_aaron_en_dias_disponibilidad_agosto(self) -> None:
        cfg = cargar_config_validada()
        fechas = (
            "2026-08-14",
            "2026-08-17",
            "2026-08-20",
            "2026-08-21",
            "2026-08-24",
            "2026-08-27",
            "2026-08-28",
            "2026-08-31",
        )
        sustitutos = cfg.get("sustitutos", [])
        for fecha in fechas:
            fila = next(f for f in cargar_filas_csv() if f["fecha"] == fecha)
            self.assertIn("Aaron", nombres_asignados_dia(fila), f"Aaron no asignado el {fecha}")
            self.assertIn("Aaron", sustitutos_presentes_fila(fila, sustitutos), f"Aaron ausente el {fecha}")
            # Preferentemente Cesantes; el 14 va de zodiac
            if fecha == "2026-08-14":
                self.assertEqual(fila.get("socorrista_zodiac"), "Aaron")
            else:
                self.assertIn(
                    "Aaron",
                    parse_lista_nombres(fila.get("cesantes", "")),
                    f"Aaron no en Cesantes el {fecha}",
                )

    def test_asignado_no_aparece_como_libre(self) -> None:
        from generar_turnos import libran_por_fecha

        cfg = cargar_config_validada()
        fila = next(f for f in filas_csv() if f["fecha"] == "2026-07-11")
        from generar_vista import puestos_dia

        puestos = puestos_dia(fila, cfg.get("sustitutos", []))
        asignados = {p["persona"] for p in puestos}
        libres = libran_por_fecha(cfg, ["2026-07-11"])["2026-07-11"]
        self.assertIn("Anxo", asignados)
        libres_visibles = [n for n in libres if n not in asignados]
        self.assertNotIn("Anxo", libres_visibles)
        self.assertEqual(libres.count("Anxo"), 1)


@unittest.skip("Raúl fuera de momento")
class TestPatronSustituto(unittest.TestCase):
    def test_raul_cubre_esther_laborable(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-13")
        self.assertEqual(fila["patron_chapela"], "Adrián")
        self.assertEqual(fila["patron_cesantes"], "Raúl")
        self.assertNotEqual(fila.get("socorrista_zodiac"), "Adrián")

    def test_raul_cubre_adrian_laborable(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-17")
        self.assertEqual(fila["patron_cesantes"], "Raúl")

    def test_raul_no_trabaja_fin_de_semana(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-11")
        self.assertNotIn("Raúl", "".join(fila.values()))

    def test_raul_no_trabaja_si_esther_y_adrian(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-15")
        self.assertNotIn("Raúl", "".join(fila.values()))
        self.assertNotEqual(fila["socorrista_zodiac"], "Adrián")
        self.assertEqual(fila["patron_cesantes"], "Adrián")

    def test_adrian_es_patron_cesantes_cuando_esther_trabaja(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-15")
        self.assertNotEqual(fila["socorrista_zodiac"], "Adrián")
        self.assertEqual(fila["patron_cesantes"], "Adrián")


class TestAdministracion(CsvBackupMixin, unittest.TestCase):
    def test_parse_bloqueado(self) -> None:
        self.assertTrue(celda_bloqueada("1"))
        self.assertTrue(celda_bloqueada("x"))
        self.assertTrue(celda_bloqueada("Sí"))
        self.assertFalse(celda_bloqueada(""))
        self.assertFalse(celda_bloqueada("0"))

    def test_zodiac_rechaza_patron_puro(self) -> None:
        personas = construir_personas(cargar_config_validada())
        self.assertIsNotNone(
            validar_zodiac_solo_socorrista({"socorrista_zodiac": "Adrián"}, personas)
        )
        self.assertIsNone(
            validar_zodiac_solo_socorrista({"socorrista_zodiac": "Alejandro"}, personas)
        )
        # Anxo figura como socorrista y patrón: puede ir a Zodiac como socorrista
        self.assertIsNone(
            validar_zodiac_solo_socorrista({"socorrista_zodiac": "Anxo"}, personas)
        )

    def test_csv_sin_patrones_en_zodiac(self) -> None:
        cfg = cargar_config_validada()
        patrones = {
            n.split()[0]
            for n in (p["nombre"] for p in cfg["patrones"])
            if not str(n).startswith("Vacante")
        }
        # Anxo también es socorrista: no cuenta como patrón puro
        patrones.discard("Anxo")
        for f in cargar_filas_csv():
            z = f.get("socorrista_zodiac", "").strip()
            self.assertNotIn(
                z,
                patrones,
                f"{f['fecha']}: patrón «{z}» no puede estar en Zodiac",
            )

    def test_bloqueado_preserva_fila_fuera_de_hasta(self) -> None:
        cfg = cargar_config_validada()
        cfg["congelado"] = {"pasado_automatico": False, "hasta": "2026-07-01"}
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for f in filas:
            if f["fecha"] == "2026-07-15":
                f["bloqueado"] = "1"
                f["patron_chapela"] = "FIJO-15JUL"
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        generar_csv(cfg, congelar=True)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-15")
        self.assertEqual(fila["patron_chapela"], "FIJO-15JUL")
        self.assertEqual(fila["bloqueado"], "1")

    def test_bloqueado_preserva_congelar_false(self) -> None:
        """bloqueado=1 se respeta aunque congelar=False (p. ej. tests o regenerar futuro)."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-07":
                fila["socorrista_chapela"] = "Fernando"
                fila["patron_chapela"] = "Esther"
                fila["patron_cesantes"] = "Adrián"
                fila["cesantes"] = "Robinson; Vacante 2; Vacante 4"
                fila["horas_extras"] = "Fernando:8;Esther:8"
                fila["bloqueado"] = "1"
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-07")
        self.assertEqual(fila["socorrista_chapela"], "Fernando")
        self.assertEqual(fila["patron_chapela"], "Esther")
        self.assertEqual(fila["cesantes"], "Robinson; Vacante 2; Vacante 4")
        self.assertEqual(fila["horas_extras"], "Fernando:8;Esther:8")

    def test_bloqueado_inalterable_siempre(self) -> None:
        """Ninguna regeneración toca filas con bloqueado=1."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-08":
                fila["socorrista_chapela"] = "Fernando"
                fila["patron_chapela"] = "Esther"
                fila["llave_cesantes"] = "Robinson"
                fila["cesantes"] = "Adrián; Vacante 2; Vacante 4"
                fila["bloqueado"] = "1"
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        esperada = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-08")
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-08")
        self.assertEqual(fila, esperada)

    def test_bloqueado_no_valida_ni_impide_escritura(self) -> None:
        """Una fila bloqueada con datos manuales no bloquea el resto del cuadrante."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-11":
                fila["cesantes"] = "Anxo; Claudio; Vacante 1; Vacante 4"
                fila["bloqueado"] = "1"
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        esperada = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-11")
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-11")
        self.assertEqual(fila, esperada)

    def test_jul_11_sin_vacaciones_esther(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-11")
        self.assertNotIn("Esther", parse_lista_nombres(fila.get("vacaciones", "")))
        self.assertIn("Esther", nombres_asignados_fila(fila))

    def test_parse_vacaciones_y_extras(self) -> None:
        self.assertEqual(parse_lista_nombres("Esther; Fernando"), ["Esther", "Fernando"])
        self.assertEqual(parse_horas_extras("Esther:4; Adrián:6.5"), {"Esther": 4.0, "Adrián": 6.5})

    def test_parse_horas_compensadas(self) -> None:
        extras, compensado = parse_anotaciones_horas(
            "Alejandro:8; Claudio:8:compensado; Fernando:8"
        )
        self.assertEqual(extras, {"Alejandro": 8.0, "Fernando": 8.0})
        self.assertEqual(compensado, {"Claudio": 8.0})
        self.assertEqual(parse_horas_extras("Claudio:8:compensado"), {})
        self.assertEqual(parse_horas_compensadas("Claudio:8:compensado"), {"Claudio": 8.0})
        self.assertEqual(parse_horas_compensadas("Claudio:8:Compensado"), {"Claudio": 8.0})
        with self.assertRaises(ValueError):
            parse_anotaciones_horas("Claudio:8:otro")

    def test_saldos_compensacion_credito_1_5(self) -> None:
        cfg = cargar_config()
        factor, personas = parse_compensacion(cfg)
        self.assertEqual(factor, 1.5)
        self.assertEqual(personas, frozenset({"Alejandro", "Claudio"}))
        saldos = {s["nombre"]: s for s in saldos_compensacion(cfg, cargar_filas_csv())}
        self.assertEqual(saldos["Alejandro"]["pendientes"], 8.0)
        self.assertEqual(saldos["Alejandro"]["credito"], 12.0)
        self.assertEqual(saldos["Alejandro"]["gastado"], 0.0)
        self.assertEqual(saldos["Alejandro"]["restante"], 12.0)
        self.assertEqual(saldos["Claudio"]["credito"], 12.0)
        self.assertNotIn("Fernando", saldos)

    def test_saldos_compensacion_descuenta_csv(self) -> None:
        cfg = cargar_config()
        filas = [
            {"horas_extras": "Alejandro:8:compensado"},
            {"horas_extras": "Claudio:4:compensado; Fernando:8"},
        ]
        saldos = {s["nombre"]: s for s in saldos_compensacion(cfg, filas)}
        self.assertEqual(saldos["Alejandro"]["gastado"], 8.0)
        self.assertEqual(saldos["Alejandro"]["restante"], 4.0)
        self.assertEqual(saldos["Claudio"]["gastado"], 4.0)
        self.assertEqual(saldos["Claudio"]["restante"], 8.0)
        self.assertEqual(errores_saldos_compensacion(cfg, filas), [])
        filas_deuda = [{"horas_extras": "Alejandro:16:compensado"}]
        errores = errores_saldos_compensacion(cfg, filas_deuda)
        self.assertEqual(len(errores), 1)
        self.assertIn("Alejandro", errores[0])

    def test_contar_horas_no_suma_compensado_como_extra(self) -> None:
        fila = {
            "socorrista_chapela": "Fernando",
            "horas_extras": "Alejandro:8:compensado; Claudio:8",
        }
        horas = contar_horas_fila(fila)
        self.assertNotIn("Alejandro", horas)
        self.assertEqual(horas["Claudio"]["horas_extras"], 8.0)
        self.assertEqual(horas["Fernando"]["horas_turno"], 8.0)

    def test_contar_horas_fila_turno_y_extras(self) -> None:
        # Día ordinario: asignado sin extras → 8 h turno
        ordinario = contar_horas_fila(
            {
                "socorrista_chapela": "Fernando",
                "patron_chapela": "Esther",
                "llave_cesantes": "Sergio",
                "cesantes": "Vacante 1",
                "horas_extras": "",
            }
        )
        self.assertEqual(ordinario["Fernando"]["horas_turno"], 8.0)
        self.assertEqual(ordinario["Fernando"]["horas_extras"], 0.0)
        self.assertNotIn("Vacante 1", ordinario)

        # Extra puro (sin puesto): solo extras
        solo_extra = contar_horas_fila(
            {
                "socorrista_chapela": "Fernando",
                "horas_extras": "Adrián:8",
            }
        )
        self.assertEqual(solo_extra["Adrián"]["horas_extras"], 8.0)
        self.assertEqual(solo_extra["Adrián"]["horas_turno"], 0.0)
        self.assertEqual(solo_extra["Fernando"]["horas_turno"], 8.0)

        # Asignado + extras el mismo día: no duplica jornada
        ambos = contar_horas_fila(
            {
                "socorrista_chapela": "Fernando",
                "patron_chapela": "Esther",
                "horas_extras": "Fernando:8;Esther:8",
            }
        )
        self.assertEqual(ambos["Fernando"]["horas_turno"], 0.0)
        self.assertEqual(ambos["Fernando"]["horas_extras"], 8.0)
        self.assertEqual(ambos["Esther"]["horas_extras"], 8.0)

    def test_contar_horas_anxo_siempre_extras(self) -> None:
        # Asignado sin horas_extras → 8 h extra, no turno
        asignado = contar_horas_fila(
            {
                "abrir_torre": "Anxo",
                "cesantes": "Vacante 1",
                "horas_extras": "",
            }
        )
        self.assertEqual(asignado["Anxo"]["horas_turno"], 0.0)
        self.assertEqual(asignado["Anxo"]["horas_extras"], 8.0)
        self.assertEqual(asignado["Anxo"]["dias_extra"], 1)

        # Horas explícitas en CSV (p. ej. 4 h en vez de 8)
        parcial = contar_horas_fila(
            {
                "socorrista_chapela": "Fernando",
                "horas_extras": "Anxo:4",
            }
        )
        self.assertEqual(parcial["Anxo"]["horas_extras"], 4.0)
        self.assertEqual(parcial["Anxo"]["horas_turno"], 0.0)

    def test_contar_horas_arturo_siempre_extras(self) -> None:
        asignado = contar_horas_fila(
            {
                "abrir_torre": "Arturo",
                "cesantes": "Vacante 2",
                "horas_extras": "",
            }
        )
        self.assertEqual(asignado["Arturo"]["horas_turno"], 0.0)
        self.assertEqual(asignado["Arturo"]["horas_extras"], 8.0)

        en_cesantes = contar_horas_fila(
            {
                "socorrista_chapela": "Fernando",
                "cesantes": "Arturo; Vacante 2",
                "horas_extras": "",
            }
        )
        self.assertEqual(en_cesantes["Arturo"]["horas_turno"], 0.0)
        self.assertEqual(en_cesantes["Arturo"]["horas_extras"], 8.0)

    def test_contar_horas_por_mes_agrega(self) -> None:
        filas = [
            {
                "fecha": "2026-07-01",
                "socorrista_chapela": "Fernando",
                "horas_extras": "",
            },
            {
                "fecha": "2026-07-02",
                "socorrista_chapela": "Fernando",
                "horas_extras": "Fernando:4",
            },
            {
                "fecha": "2026-08-01",
                "socorrista_chapela": "Fernando",
                "horas_extras": "",
            },
        ]
        por_mes = contar_horas_por_mes(filas, plantilla=["Fernando", "Esther"])
        jul = por_mes[(2026, 7)]["Fernando"]
        self.assertEqual(jul["dias_turno"], 1)
        self.assertEqual(jul["horas_turno"], 8.0)
        self.assertEqual(jul["dias_extra"], 1)
        self.assertEqual(jul["horas_extras"], 4.0)
        self.assertEqual(jul["total"], 12.0)
        self.assertEqual(por_mes[(2026, 7)]["Esther"]["total"], 0.0)
        self.assertEqual(por_mes[(2026, 8)]["Fernando"]["horas_turno"], 8.0)

    def test_contar_socorristas_cesantes(self) -> None:
        fila = {
            "patron_cesantes": "Adrián",
            "llave_cesantes": "Claudio",
            "cesantes": "Vacante 1; Vacante 2",
            "horas_extras": "",
        }
        self.assertEqual(nombres_en_cesantes(fila), ["Adrián", "Claudio", "Vacante 1", "Vacante 2"])
        # Patrón no cuenta; vacantes no cuentan → solo Claudio
        self.assertEqual(contar_socorristas_cesantes(fila), 1)
        # Torre y Zodiac suman; patrón vacante no cuenta
        ago4 = {
            "patron_cesantes": "Vacante 3",
            "llave_cesantes": "Robinson",
            "cesantes": "Vacante 1",
            "socorrista_zodiac": "Sergio",
            "abrir_torre": "Rodrigo",
        }
        self.assertEqual(contar_socorristas_cesantes(ago4), 3)
        # Adrián patrón no cuenta; Claudio + Rodrigo + Alejandro + Anxo = 4
        ago8 = {
            "patron_cesantes": "Adrián",
            "llave_cesantes": "Claudio",
            "cesantes": "Rodrigo; Vacante 2",
            "socorrista_zodiac": "Alejandro",
            "abrir_torre": "Anxo",
            "horas_extras": "",
        }
        self.assertEqual(
            contar_socorristas_cesantes(ago8, sustitutos=["Arturo", "Anxo"]),
            4,
        )
        # 11 ago: Sergio + Rodrigo + Robinson (zodiac); vacantes no cuentan
        ago11 = {
            "patron_cesantes": "Vacante 3",
            "llave_cesantes": "Sergio",
            "cesantes": "Vacante 1",
            "socorrista_zodiac": "Robinson",
            "abrir_torre": "Rodrigo",
        }
        self.assertEqual(contar_socorristas_cesantes(ago11), 3)
        # 6 ago: patrón no cuenta → Sergio + Claudio + Rodrigo + Alejandro + Robinson = 5
        ago6 = {
            "patron_cesantes": "Adrián",
            "llave_cesantes": "Sergio",
            "cesantes": "Claudio; Rodrigo; Vacante 2",
            "horas_extras": "Esther:8; Fernando:8",
            "socorrista_chapela": "Fernando",
            "patron_chapela": "Esther",
            "socorrista_zodiac": "Robinson",
            "abrir_torre": "Alejandro",
        }
        self.assertEqual(
            contar_socorristas_cesantes(ago6, sustitutos=["Arturo", "Anxo"]),
            5,
        )
        # Cubridor de la vacante del patrón no cuenta
        ago5 = {
            "patron_cesantes": "Vacante 3",
            "llave_cesantes": "Robinson",
            "cesantes": "Vacante 1",
            "socorrista_zodiac": "Sergio",
            "abrir_torre": "Rodrigo",
            "horas_extras": "Anxo:4",
        }
        self.assertEqual(contar_socorristas_cesantes(ago5), 3)
        # 11 jul: Robinson + Anxo + Claudio + Sergio (zodiac); vacantes no
        jul11 = {
            "patron_cesantes": "Vacante 3",
            "llave_cesantes": "Robinson",
            "cesantes": "Anxo; Claudio; Vacante 1; Vacante 4",
            "socorrista_zodiac": "Sergio",
            "horas_extras": "Anxo:8",
        }
        self.assertEqual(contar_socorristas_cesantes(jul11), 4)

    def test_contar_socorristas_cesantes_csv_agosto(self) -> None:
        """Tabla de fechas reales del CSV; ampliar con más (fecha, esperado)."""
        cfg = cargar_config()
        sustitutos = [
            n for n in cfg.get("sustitutos", []) if not str(n).startswith("Vacante")
        ]
        por_fecha = {f["fecha"]: f for f in cargar_filas_csv()}
        casos = (
            ("2026-08-04", 3),  # Robinson + Sergio (zodiac) + Rodrigo (torre)
            ("2026-08-05", 3),  # igual que el 4; Anxo cubre patrón → no cuenta
            ("2026-08-06", 5),  # Sergio + Robinson + Claudio + Alejandro + Rodrigo
            ("2026-08-08", 4),  # Anxo + Alejandro + Claudio + Rodrigo
            ("2026-08-11", 3),  # Sergio + Robinson (zodiac) + Rodrigo (torre); Rober/Aaron desde el 13
            ("2026-07-11", 4),  # Robinson + Anxo + Claudio + Sergio (zodiac)
        )
        for fecha, esperado in casos:
            with self.subTest(fecha=fecha):
                self.assertIn(fecha, por_fecha)
                self.assertEqual(
                    contar_socorristas_cesantes(
                        por_fecha[fecha],
                        sustitutos=sustitutos,
                    ),
                    esperado,
                )

    def test_parse_horas_pendientes(self) -> None:
        from turnos_common import parse_horas_pendientes

        cfg = cargar_config()
        pendientes = parse_horas_pendientes(cfg)
        self.assertEqual(pendientes.get("Alejandro"), 8.0)
        self.assertEqual(pendientes.get("Claudio"), 8.0)
        self.assertEqual(pendientes.get("Fernando"), 16.0)
        self.assertEqual(sum(pendientes.values()), 32.0)

    def test_html_cuadrante_recuento_extras_y_cesantes(self) -> None:
        from generar_vista import generar_html

        cfg = cargar_config()
        html = generar_html(
            cargar_filas_csv(),
            "Turnos playas 2026",
            "Jul–Sep 2026",
            cfg,
            pages=False,
            ocultar_extras=False,
        )
        self.assertIn('class="recuento-extras"', html)
        self.assertIn("<details", html)
        self.assertIn("<summary>", html)
        self.assertIn("Horas extras · Julio 2026", html)
        self.assertIn('class="tabla-extras"', html)
        self.assertIn('id="pendientes-pagar"', html)
        self.assertIn("Pendientes de pagar", html)
        self.assertIn("Fernando", html)
        self.assertIn("16 h", html)
        self.assertIn('id="compensacion-dias"', html)
        self.assertIn("Compensación", html)
        self.assertIn("24 h restantes", html)
        self.assertIn("Nombre:8:compensado", html)
        self.assertIn("Pendientes de pagar no se descuenta", html)
        self.assertIn("div.extras", html)
        self.assertNotIn("\n    .extras {", html)
        self.assertIn('class="ces-n"', html)
        # 11 ago: Sergio + Robinson (zodiac) + Rodrigo (torre); Rober/Aaron desde el 13
        self.assertRegex(html, r'data-fecha="2026-08-11"[^>]*data-cesantes="3"')
        # 8 ago: Anxo + Alejandro + Claudio + Rodrigo (patrón no cuenta)
        self.assertRegex(html, r'data-fecha="2026-08-08"[^>]*data-cesantes="4"')
        # 4 ago: Robinson + Sergio (zodiac) + Rodrigo (torre)
        self.assertRegex(html, r'data-fecha="2026-08-04"[^>]*data-cesantes="3"')
        # 6 ago: 5 socos Cesantes (patrón no cuenta)
        self.assertRegex(html, r'data-fecha="2026-08-06"[^>]*data-cesantes="5"')
        # 11 jul: Robinson + Anxo + Claudio + Sergio (zodiac)
        self.assertRegex(html, r'data-fecha="2026-07-11"[^>]*data-cesantes="4"')
        # Ningún patrón en Zodiac
        self.assertNotRegex(
            html,
            r'data-campo="socorrista_zodiac" data-persona="Adrián"',
        )

    def test_html_agosto_publico_sin_extras(self) -> None:
        from generar_vista import generar_html

        cfg = cargar_config()
        html = generar_html(
            cargar_filas_csv(),
            "Turnos playas 2026",
            "Agosto 2026 · cuadrante",
            cfg,
            pages=True,
            solo_mes=(2026, 8),
            ocultar_extras=True,
        )
        self.assertIn("mes-2026-08", html)
        self.assertIn("Agosto 2026", html)
        self.assertNotIn("mes-2026-07", html)
        self.assertNotIn("mes-2026-09", html)
        self.assertNotIn('id="mostrar-extras"', html)
        self.assertNotIn("Mostrar horas extra", html)
        self.assertNotIn("horas.html", html)
        self.assertNotIn('class="recuento-extras"', html)
        self.assertNotIn("tabla-extras", html)
        self.assertNotIn("recuento-extras", html)
        self.assertNotIn("pendientes-pagar", html)
        self.assertNotIn("Pendientes de pagar", html)
        self.assertNotIn("compensacion-dias", html)
        self.assertNotIn("24 h restantes", html)
        self.assertIn("const EXTRAS = {}", html)
        # Sin horas de extras en el DOM (bloques vacíos / JSON vacío)
        self.assertNotIn('class="extra"', html)
        self.assertNotIn("data-horas=", html)
        # Contador Cesantes sí (no es info de extras)
        self.assertIn('class="ces-n"', html)
        # El cuadrante de puestos sí está
        self.assertIn("Soc. Chapela", html)
        self.assertIn("Robinson", html)

    def test_html_badge_compensado(self) -> None:
        from generar_vista import render_compensado

        html = render_compensado("Alejandro", 8.0)
        self.assertIn('class="compensado"', html)
        self.assertIn("Compensado", html)
        self.assertIn("Alejandro", html)
        self.assertIn("8 h", html)
        self.assertNotIn("data-horas=", html)

    def test_vacaciones_solo_manuales(self) -> None:
        cfg = cargar_config_validada()
        filas = cargar_filas_csv()
        vacaciones_previas = {f["fecha"]: f.get("vacaciones", "") for f in filas}
        generar_csv(cfg, congelar=False)
        for f in cargar_filas_csv():
            self.assertEqual(
                f.get("vacaciones", ""),
                vacaciones_previas[f["fecha"]],
                f"vacaciones en {f['fecha']} no debe cambiar al regenerar",
            )

        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-09":
                fila["vacaciones"] = "Esther"
                fila["bloqueado"] = ""
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        self.assertEqual(
            next(f for f in filas if f["fecha"] == "2026-07-09")["vacaciones"],
            "Esther",
        )
        self.assertEqual(
            sum(1 for f in filas if f.get("vacaciones")),
            1,
            "solo debe haber vacaciones donde se pusieron a mano",
        )

    def test_vacaciones_excluye_de_generacion(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-09":
                fila["vacaciones"] = "Esther"
                fila["bloqueado"] = ""
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-09")
        asignados = {
            fila.get(c, "")
            for c in fila
            if c not in ("fecha", "vacaciones", "horas_extras", "llave_chapela") and fila.get(c)
        }
        self.assertNotIn("Esther", asignados)

    def test_detecta_vacaciones_y_asignacion(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        fila = {
            "fecha": "2026-07-01",
            "socorrista_chapela": "Esther",
            "patron_chapela": "Adrián",
            "llave_cesantes": "Sergio",
            "vacaciones": "Esther",
        }
        err = validar_administracion(fila, personas)
        self.assertIn("vacaciones", err or "")

    def test_compensado_cuenta_como_ausente(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        ausentes = nombres_completos_ausentes("", personas, "Alejandro:8:compensado")
        self.assertIn("Alejandro Panadeiros Covelo", ausentes)

    def test_detecta_compensado_y_asignacion(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        fila = {
            "fecha": "2026-07-09",
            "socorrista_chapela": "Alejandro",
            "patron_chapela": "Adrián",
            "llave_cesantes": "Sergio",
            "horas_extras": "Alejandro:8:compensado",
        }
        err = validar_administracion(fila, personas, cfg)
        self.assertIn("compensación", err or "")

    def test_compensado_solo_alex_claudio(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        fila = {
            "fecha": "2026-07-09",
            "socorrista_chapela": "Robinson",
            "patron_chapela": "Adrián",
            "llave_cesantes": "Sergio",
            "horas_extras": "Fernando:8:compensado",
        }
        err = validar_administracion(fila, personas, cfg)
        self.assertIn("no puede marcar compensado", err or "")

    def test_compensado_excluye_de_generacion(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-07":
                extras = fila.get("horas_extras", "").strip()
                marca = "Alejandro:8:compensado"
                fila["horas_extras"] = f"{extras}; {marca}" if extras else marca
                fila["bloqueado"] = ""
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-07")
        asignados = {
            fila.get(c, "")
            for c in fila
            if c not in ("fecha", "vacaciones", "horas_extras", "llave_chapela") and fila.get(c)
        }
        self.assertNotIn("Alejandro", asignados)
        self.assertIn("Alejandro:8:compensado", fila.get("horas_extras", ""))

    def test_horas_extras_permite_socorrista_en_dia_libre(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        filas = [
            {
                "fecha": "2026-07-03",
                "socorrista_chapela": "Robinson",
                "patron_chapela": "Adrián",
                "llave_cesantes": "Claudio",
                "horas_extras": "Robinson:8",
            }
        ]
        err = validar_rotacion_4_2(
            filas,
            personas,
            cfg["rotacion"],
            parse_fecha("2026-07-01"),
        )
        self.assertIsNone(err)


class TestCongelado(CsvBackupMixin, unittest.TestCase):
    def test_fecha_limite_pasado_automatico(self) -> None:
        cfg = {"congelado": {"pasado_automatico": True}}
        self.assertEqual(
            fecha_congelacion_limite(cfg, hoy=date(2026, 7, 15)),
            date(2026, 7, 15),
        )

    def test_hasta_manual_extiende_congelado(self) -> None:
        cfg = {"congelado": {"pasado_automatico": True, "hasta": "2026-08-01"}}
        self.assertEqual(
            fecha_congelacion_limite(cfg, hoy=date(2026, 7, 15)),
            date(2026, 8, 1),
        )

    def test_congelado_hasta_config_preserva_2_jul(self) -> None:
        """congelado.hasta en config.yaml debe fijar el 2/jul aunque hoy sea 1/jul."""
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-02":
                fila["socorrista_chapela"] = "FIJO-2JUL"
                fila["bloqueado"] = ""
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        generar_csv(cfg, congelar=True, hoy=date(2026, 7, 1))
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-02")
        self.assertEqual(fila["socorrista_chapela"], "FIJO-2JUL")
        generar_csv(cfg, congelar=False)

    def test_sin_hasta_solo_congela_hasta_hoy(self) -> None:
        """Sin congelado.hasta, días posteriores a hoy se recalculan."""
        cfg = cargar_config_validada()
        cfg["congelado"] = {"pasado_automatico": True}
        generar_csv(cfg, congelar=False)
        filas = cargar_filas_csv()
        for fila in filas:
            if fila["fecha"] == "2026-07-02":
                fila["socorrista_chapela"] = "FIJO-2JUL"
                fila["bloqueado"] = ""
                break
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=filas[0].keys())
            writer.writeheader()
            writer.writerows(filas)

        generar_csv(cfg, congelar=True, hoy=date(2026, 7, 1))
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-02")
        self.assertNotEqual(fila["socorrista_chapela"], "FIJO-2JUL")
        generar_csv(cfg, congelar=False)

    def test_regenerar_conserva_filas_congeladas(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        original = cargar_filas_csv()[0].copy()
        original["socorrista_chapela"] = "EDITADO"

        filas = cargar_filas_csv()
        filas[0] = original
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=original.keys())
            writer.writeheader()
            writer.writerows(filas)

        generar_csv(cfg, congelar=True, hoy=date(2026, 7, 10), congelar_hasta=date(2026, 7, 1))
        conservada = cargar_filas_csv()[0]
        self.assertEqual(conservada["socorrista_chapela"], "EDITADO")
        generar_csv(cfg, congelar=False)


class TestConfig(unittest.TestCase):
    def test_plantilla_con_refuerzos(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        soc = [p for p in personas if p.rol == "socorrista"]
        pat = [p for p in personas if p.rol == "patron"]
        self.assertEqual(len(soc), 12)
        self.assertEqual(len(pat), 4)
        self.assertEqual(len(personas), 16)
        nombres = {p.nombre for p in soc}
        self.assertTrue({"Rober", "Aaron"} <= nombres)
        vacantes = [p.nombre for p in personas if p.nombre.startswith("Vacante")]
        self.assertEqual(sorted(vacantes), ["Vacante 1", "Vacante 2", "Vacante 3"])

    def test_config_actual_es_valida(self) -> None:
        self.assertEqual(validar_config(cargar_config_validada()), [])

    def test_detecta_nombre_desconocido(self) -> None:
        cfg = cargar_config()
        cfg["preferencias"]["patron_chapela"] = ["No Existe"]
        self.assertTrue(any("desconocido" in e for e in validar_config(cfg)))

    def test_no_escribe_csv_si_generacion_invalida(self) -> None:
        cfg = cargar_config_validada()
        mtime = CSV_PATH.stat().st_mtime
        with patch("generar_turnos.validar_rotacion_4_2", return_value="error simulado"):
            with self.assertRaises(ErrorGeneracion):
                generar_csv(cfg)
        self.assertEqual(CSV_PATH.stat().st_mtime, mtime)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
