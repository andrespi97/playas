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
    ErrorGeneracion,
    cargar_config_validada,
    construir_personas,
    contar_socorristas_trabajando,
    generar_csv,
    max_racha_dias,
    validar_config,
    validar_cobertura_extendida,
    validar_cobertura_obligatoria,
    validar_rotacion_4_2,
    validar_sin_duplicados,
)
from turnos_common import (  # noqa: E402
    CSV_PATH,
    cargar_config,
    cargar_filas_csv,
    fecha_congelacion_limite,
    parse_fecha,
)


def filas_csv() -> list[dict[str, str]]:
    generar_csv(cargar_config_validada(), congelar=False)
    return cargar_filas_csv()


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
            "socorrista_chapela": "Robson",
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
        personas = construir_personas(cfg)
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = filas_csv()
        for dia_idx, fila in enumerate(filas):
            n = contar_socorristas_trabajando(personas, dia_idx, rot)
            err = validar_cobertura_extendida(fila, n)
            self.assertIsNone(err, f"{fila['fecha']} ({n} socorristas): {err}")

    def test_detecta_falta_torre_con_personal(self) -> None:
        self.assertEqual(
            validar_cobertura_extendida(
                {
                    "socorrista_chapela": "Robson",
                    "llave_cesantes": "Sergio",
                    "socorrista_zodiac": "Claudio",
                    "abrir_torre": "",
                },
                4,
            ),
            "Falta torre",
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
        )
        self.assertIsNone(err, err)

    def test_detecta_asignacion_en_dia_libre(self) -> None:
        cfg = cargar_config_validada()
        self.assertEqual(
            validar_rotacion_4_2(
                [
                    {
                        "fecha": "2026-07-03",
                        "socorrista_chapela": "Claudio",
                        "patron_chapela": "Adrián",
                        "llave_cesantes": "Robson",
                    }
                ],
                construir_personas(cfg),
                cfg["rotacion"],
                parse_fecha("2026-07-01"),
            ),
            "Robson asignado el 2026-07-03 en día libre (grupo 3)",
        )

    def test_detecta_racha_de_5_dias(self) -> None:
        self.assertEqual(max_racha_dias([0, 1, 2, 3, 4]), 5)


class TestPreferenciaZodiac(unittest.TestCase):
    def test_claudio_alex_prefieren_zodiac_si_hay_otro_para_abrir(self) -> None:
        """1/jul: Robson chapela, Sergio abre puesto, Claudio zodiac (no al revés)."""
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


class TestCongelado(unittest.TestCase):
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
