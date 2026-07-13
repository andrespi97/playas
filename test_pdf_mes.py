#!/usr/bin/env python3
"""Prueba E2E: descarga PDF del mes y verifica que contiene datos visibles."""

from __future__ import annotations

import http.server
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

import fitz

from turnos_common import cargar_filas_csv, parse_fecha

ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "turnos.html"


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _servir_directorio(directorio: Path, puerto: int) -> http.server.ThreadingHTTPServer:
    handler = type(
        "Handler",
        (http.server.SimpleHTTPRequestHandler,),
        {"directory": str(directorio)},
    )
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), handler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor


def _regenerar_html() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "generar_vista.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _nombres_julio_csv() -> set[str]:
    nombres: set[str] = set()
    for fila in cargar_filas_csv():
        if parse_fecha(fila["fecha"]).month != 7:
            continue
        for clave, valor in fila.items():
            if clave == "fecha" or not valor.strip():
                continue
            if clave in ("cesantes", "vacaciones"):
                for parte in valor.replace(",", ";").split(";"):
                    n = parte.strip()
                    if n and not n.startswith("Vacante"):
                        nombres.add(n.split()[0])
            elif not valor.startswith("Vacante"):
                nombres.add(valor.strip().split()[0])
    return nombres


def _pixeles_no_blancos(pix: fitz.Pixmap, umbral: int = 245) -> int:
    muestras = pix.samples
    n = 0
    for i in range(0, len(muestras), 3):
        if muestras[i] < umbral or muestras[i + 1] < umbral or muestras[i + 2] < umbral:
            n += 1
    return n


def _pagina_tiene_contenido_calendario(pix: fitz.Pixmap) -> bool:
    """Detecta texto oscuro o bloques de color del calendario (no solo fondo blanco)."""
    muestras = pix.samples
    for i in range(0, len(muestras) - 2, 3):
        r, g, b = muestras[i], muestras[i + 1], muestras[i + 2]
        if r < 120 and g < 120 and b < 120:
            return True
        if g > 170 and b > 170 and r < 240:
            return True
        if g > 200 and r > 200 and b < 180:
            return True
    return False


def verificar_pdf_mes(
    pdf_bytes: bytes,
    *,
    mes_etiqueta: str = "Julio",
    nombres_csv: set[str] | None = None,
    min_paginas: int = 2,
    min_pixeles_por_pagina: int = 500,
) -> None:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.page_count < min_paginas:
        raise AssertionError(f"PDF con {doc.page_count} páginas; esperaba al menos {min_paginas}")

    if len(pdf_bytes) < 30_000:
        raise AssertionError(f"PDF demasiado pequeño ({len(pdf_bytes)} bytes)")

    texto_total = ""
    paginas_con_contenido = 0
    for i, pagina in enumerate(doc):
        imagenes = pagina.get_images(full=True)
        if not imagenes:
            raise AssertionError(f"Página {i + 1} sin imágenes embebidas")
        for info in imagenes:
            datos = doc.extract_image(info[0])
            if len(datos["image"]) < 5_000:
                raise AssertionError(f"Imagen vacía en página {i + 1}")

        pix = pagina.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        oscuros = _pixeles_no_blancos(pix)
        if oscuros < min_pixeles_por_pagina:
            raise AssertionError(
                f"Página {i + 1} casi en blanco ({oscuros} píxeles con contenido)"
            )
        if _pagina_tiene_contenido_calendario(pix):
            paginas_con_contenido += 1
        texto_total += pagina.get_text()

    if paginas_con_contenido < max(1, min_paginas - 1):
        raise AssertionError("El PDF no muestra contenido de calendario en las páginas")

    if texto_total.strip():
        if mes_etiqueta not in texto_total:
            raise AssertionError(f"No aparece el mes «{mes_etiqueta}» en el texto del PDF")
        if nombres_csv:
            presentes = [n for n in nombres_csv if n in texto_total]
            if len(presentes) < 3:
                raise AssertionError(
                    f"Pocos nombres del CSV en el PDF: {presentes}"
                )
        return

    if nombres_csv and len(nombres_csv) < 3:
        raise AssertionError("CSV de julio sin nombres esperados para contrastar")


def descargar_pdf_julio(playwright, url: str, destino: Path) -> None:
    browser = playwright.chromium.launch()
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    errores: list[str] = []
    page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))
    page.on("dialog", lambda d: d.accept())

    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_function(
        "() => typeof html2canvas === 'function' && window.jspdf?.jsPDF",
        timeout=30_000,
    )

    with page.expect_download(timeout=120_000) as dl_info:
        page.click("#btn-pdf-mes")
    descarga = dl_info.value
    if errores:
        raise RuntimeError("Errores en la página: " + "; ".join(errores))
    descarga.save_as(destino)
    browser.close()


class TestPdfMes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not HTML_PATH.exists():
            _regenerar_html()
        from playwright.sync_api import sync_playwright

        cls._playwright = sync_playwright().start()
        cls._puerto = _puerto_libre()
        cls._servidor = _servir_directorio(ROOT, cls._puerto)
        cls._url = f"http://127.0.0.1:{cls._puerto}/turnos.html"
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._servidor.shutdown()
        cls._playwright.stop()

    def test_pdf_julio_contiene_datos(self) -> None:
        destino = ROOT / ".test_artifacts" / "turnos-2026-07.pdf"
        destino.parent.mkdir(exist_ok=True)
        descargar_pdf_julio(self._playwright, self._url, destino)
        datos = destino.read_bytes()
        nombres = _nombres_julio_csv()
        self.assertIn("Esther", nombres)
        self.assertIn("Adrián", nombres)
        verificar_pdf_mes(datos, nombres_csv=nombres)


if __name__ == "__main__":
    unittest.main()
