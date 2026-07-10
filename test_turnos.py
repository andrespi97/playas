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
    ausentes_por_disponibilidad,
    cargar_config_validada,
    construir_personas,
    contar_socorristas_trabajando,
    generar_csv,
    max_racha_dias,
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
)
from turnos_common import (  # noqa: E402
    CSV_PATH,
    cargar_config,
    cargar_filas_csv,
    celda_bloqueada,
    fecha_congelacion_limite,
    parse_fecha,
    parse_horas_extras,
    parse_lista_nombres,
    sustitutos_presentes_fila,
)


def filas_csv() -> list[dict[str, str]]:
    generar_csv(cargar_config_validada(), congelar=False)
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
        personas = construir_personas(cfg)
        rot = cfg["rotacion"]
        inicio = parse_fecha(cfg["periodo"]["inicio"])
        filas = filas_csv()
        for dia_idx, fila in enumerate(filas):
            if celda_bloqueada(fila.get("bloqueado", "")):
                continue
            fecha_str = fila["fecha"]
            ausentes = nombres_completos_ausentes(fila.get("vacaciones", ""), personas) | ausentes_por_disponibilidad(
                cfg, fecha_str, personas
            )
            n = contar_socorristas_trabajando(personas, dia_idx, rot, ausentes, cfg, fecha_str)
            err = validar_cobertura_extendida(fila, n)
            self.assertIsNone(err, f"{fecha_str} ({n} socorristas): {err}")

    def test_detecta_falta_torre_con_personal(self) -> None:
        self.assertEqual(
            validar_cobertura_extendida(
                {
                    "socorrista_chapela": "Robinson",
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
            refuerzos_disponibilidad=nombres_refuerzo_disponibilidad(cfg),
        )
        self.assertIsNone(err, err)
        for fila in filas:
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
        self.assertEqual(fila["socorrista_zodiac"], "Adrián")
        self.assertNotIn("Adrián", (fila.get("patron_cesantes", ""), fila.get("patron_chapela", "")))

    def test_adrian_solo_zodiac_cuando_esther_trabaja(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-15")
        self.assertEqual(fila["socorrista_zodiac"], "Adrián")
        self.assertNotIn("Adrián", (fila.get("patron_cesantes", ""), fila.get("patron_chapela", "")))


class TestAdministracion(CsvBackupMixin, unittest.TestCase):
    def test_parse_bloqueado(self) -> None:
        self.assertTrue(celda_bloqueada("1"))
        self.assertTrue(celda_bloqueada("x"))
        self.assertTrue(celda_bloqueada("Sí"))
        self.assertFalse(celda_bloqueada(""))
        self.assertFalse(celda_bloqueada("0"))

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

    def test_bloqueado_preserva_regenerar_todo(self) -> None:
        """--regenerar-todo no toca filas con bloqueado=1."""
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

        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-08")
        self.assertEqual(fila["socorrista_chapela"], "Fernando")
        self.assertEqual(fila["patron_chapela"], "Esther")
        self.assertEqual(fila["llave_cesantes"], "Robinson")
        self.assertEqual(fila["cesantes"], "Adrián; Vacante 2; Vacante 4")

    def test_jul_11_sin_vacaciones_esther(self) -> None:
        cfg = cargar_config_validada()
        generar_csv(cfg, congelar=False)
        fila = next(f for f in cargar_filas_csv() if f["fecha"] == "2026-07-11")
        self.assertNotIn("Esther", parse_lista_nombres(fila.get("vacaciones", "")))
        self.assertIn("Esther", nombres_asignados_fila(fila))

    def test_parse_vacaciones_y_extras(self) -> None:
        self.assertEqual(parse_lista_nombres("Esther; Fernando"), ["Esther", "Fernando"])
        self.assertEqual(parse_horas_extras("Esther:4; Adrián:6.5"), {"Esther": 4.0, "Adrián": 6.5})

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
    def test_plantilla_once_personas(self) -> None:
        cfg = cargar_config_validada()
        personas = construir_personas(cfg)
        soc = [p for p in personas if p.rol == "socorrista"]
        pat = [p for p in personas if p.rol == "patron"]
        self.assertEqual(len(soc), 10)
        self.assertEqual(len(pat), 5)
        self.assertEqual(len(personas), 15)
        vacantes = [p.nombre for p in personas if p.nombre.startswith("Vacante")]
        self.assertEqual(sorted(vacantes), ["Vacante 1", "Vacante 2", "Vacante 3", "Vacante 4"])

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
