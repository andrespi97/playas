#!/usr/bin/env python3
"""Tests del cuadrante de turnos."""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from generar_turnos import (  # noqa: E402
    cargar_config,
    construir_personas,
    generar_csv,
    max_racha_dias,
    parse_fecha,
    validar_cobertura_obligatoria,
    validar_rotacion_4_2,
    validar_sin_duplicados,
)

CSV_PATH = ROOT / "turnos_jul_sep_2026.csv"


def filas_csv() -> list[dict[str, str]]:
    if not CSV_PATH.exists():
        generar_csv(cargar_config())
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
            "patron_cesantes": "Pablo",
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
        self.assertGreater(len(filas), 0, "CSV vacío")
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

    def test_detecta_falta_chapela_artificial(self) -> None:
        fila = {
            "fecha": "2026-07-03",
            "socorrista_chapela": "",
            "patron_chapela": "Esther",
            "llave_cesantes": "Sergio",
        }
        self.assertEqual(validar_cobertura_obligatoria(fila), "Falta socorrista chapela")


class TestRotacion4x2(unittest.TestCase):
    def test_csv_respeta_4_dias_trabajo_2_descanso(self) -> None:
        generar_csv(cargar_config())
        filas = filas_csv()
        cfg = cargar_config()
        personas = construir_personas(cfg)
        err = validar_rotacion_4_2(
            filas,
            personas,
            cfg["rotacion"],
            parse_fecha(cfg["periodo"]["inicio"]),
        )
        self.assertIsNone(err, err)

    def test_detecta_asignacion_en_dia_libre(self) -> None:
        cfg = cargar_config()
        personas = construir_personas(cfg)
        filas = [
            {
                "fecha": "2026-07-03",
                "socorrista_chapela": "Claudio",
                "patron_chapela": "Adrián",
                "llave_cesantes": "Robson",
            }
        ]
        # Robson es G3; el 3/jul (índice 2) G3 libra
        self.assertEqual(
            validar_rotacion_4_2(
                filas,
                personas,
                cfg["rotacion"],
                parse_fecha("2026-07-01"),
            ),
            "Robson asignado el 2026-07-03 en día libre (grupo 3)",
        )

    def test_detecta_racha_de_5_dias(self) -> None:
        self.assertEqual(max_racha_dias([0, 1, 2, 3, 4]), 5)
        self.assertEqual(max_racha_dias([0, 1, 3, 4]), 2)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
