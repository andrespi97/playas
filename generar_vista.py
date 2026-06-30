#!/usr/bin/env python3
"""Genera turnos.html a partir del CSV de turnos."""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict

from turnos_common import (
    CAMPOS_OCULTOS_HTML,
    CONFIG_PATH,
    CSV_PATH,
    ETIQUETAS_VISTA,
    HTML_PATH,
    PUESTOS_ASIGNACION,
    cargar_config,
    cargar_filas_csv,
    etiqueta_periodo,
    parse_fecha,
)

MESES = (
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
DIAS_SEM = ("L", "M", "X", "J", "V", "S", "D")

ICONO_LLAVE = '<span class="icono-llave" title="Lleva la llave">🔑</span>'


def etiqueta_campo(campo: str) -> str:
    if campo.startswith("cesantes"):
        return campo.replace("cesantes", "Cesantes ")
    return ETIQUETAS_VISTA.get(campo, campo.replace("_", " ").title())


def filas_por_mes(filas: list[dict[str, str]]) -> dict[tuple[int, int], list[dict]]:
    por_mes: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for fila in filas:
        d = parse_fecha(fila["fecha"])
        por_mes[(d.year, d.month)].append(fila)
    for clave in por_mes:
        por_mes[clave].sort(key=lambda r: r["fecha"])
    return por_mes


def nombres_unicos(filas: list[dict[str, str]]) -> list[str]:
    nombres: set[str] = set()
    for fila in filas:
        for k, v in fila.items():
            if k != "fecha" and v.strip():
                nombres.add(v.strip())
    return sorted(nombres, key=str.casefold)


def puestos_dia(fila: dict[str, str]) -> list[dict[str, str | bool]]:
    llave_chapela = fila.get("llave_chapela", "").strip()
    puestos: list[dict[str, str | bool]] = []

    def anadir(campo: str, valor: str) -> None:
        if not valor:
            return
        tiene_llave = campo == "llave_cesantes" or (
            campo in ("socorrista_chapela", "patron_chapela") and valor == llave_chapela
        )
        puestos.append(
            {
                "campo": campo,
                "rol": etiqueta_campo(campo),
                "persona": valor,
                "tiene_llave": tiene_llave,
            }
        )

    for campo in PUESTOS_ASIGNACION:
        if campo not in CAMPOS_OCULTOS_HTML:
            anadir(campo, fila.get(campo, "").strip())

    for campo in sorted(fila.keys()):
        if campo.startswith("cesantes"):
            anadir(campo, fila.get(campo, "").strip())

    return puestos


def render_puesto(p: dict[str, str | bool]) -> str:
    llave = ICONO_LLAVE if p.get("tiene_llave") else ""
    return (
        f'<div class="puesto" data-campo="{html.escape(str(p["campo"]))}" '
        f'data-persona="{html.escape(str(p["persona"]))}">'
        f'<span class="rol">{html.escape(str(p["rol"]))}</span>'
        f'<span class="nombre">{html.escape(str(p["persona"]))}{llave}</span></div>'
    )


def generar_html(filas: list[dict[str, str]], titulo: str, subtitulo: str) -> str:
    por_mes = filas_por_mes(filas)
    trabajadores = nombres_unicos(filas)
    puestos_por_fecha = {fila["fecha"]: puestos_dia(fila) for fila in filas}
    datos = {
        fecha: [
            {
                "campo": p["campo"],
                "rol": p["rol"] + (" 🔑" if p.get("tiene_llave") else ""),
                "persona": p["persona"],
            }
            for p in puestos
        ]
        for fecha, puestos in puestos_por_fecha.items()
    }
    meses_nav = [
        {"y": y, "m": m, "label": f"{MESES[m]} {y}"}
        for (y, m) in sorted(por_mes.keys())
    ]

    bloques_mes: list[str] = []
    for y, m in sorted(por_mes.keys()):
        filas_mes = por_mes[(y, m)]
        celdas: list[str] = []
        if filas_mes:
            primer = parse_fecha(filas_mes[0]["fecha"])
            for _ in range(primer.weekday()):
                celdas.append('<div class="dia vacio"></div>')

        for fila in filas_mes:
            d = parse_fecha(fila["fecha"])
            puestos = puestos_por_fecha[fila["fecha"]]
            personas = sorted({p["persona"] for p in puestos})
            data_personas = html.escape(json.dumps(personas, ensure_ascii=False))
            lineas = "".join(render_puesto(p) for p in puestos)
            celdas.append(
                f'<article class="dia" data-fecha="{fila["fecha"]}" '
                f'data-personas=\'{data_personas}\'>'
                f'<header class="dia-cab"><span class="num">{d.day}</span>'
                f'<span class="sem">{DIAS_SEM[d.weekday()]}</span></header>'
                f'<div class="puestos">{lineas}</div></article>'
            )

        grid = "\n".join(celdas)
        bloques_mes.append(
            f'<section class="mes" id="mes-{y}-{m:02d}" data-y="{y}" data-m="{m}">'
            f'<h2>{MESES[m]} {y}</h2>'
            f'<div class="cab-sem">'
            + "".join(f"<span>{d}</span>" for d in ("Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do"))
            + f'</div><div class="rejilla">{grid}</div></section>'
        )

    opts = "".join(
        f'<option value="{html.escape(n)}">{html.escape(n)}</option>' for n in trabajadores
    )
    mes_btns = "".join(
        f'<button type="button" class="tab-mes" data-target="mes-{m["y"]}-{m["m"]:02d}">'
        f'{html.escape(m["label"])}</button>'
        for m in meses_nav
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(titulo)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
  <style>
    :root {{
      --arena: #f4efe6;
      --mar: #0c4a6e;
      --mar-claro: #0369a1;
      --espuma: #e0f2fe;
      --sol: #f59e0b;
      --texto: #1e293b;
      --muted: #64748b;
      --borde: #cbd5e1;
      --tarjeta: #ffffff;
      --resalt: #fef3c7;
      --resalt-borde: #f59e0b;
      --sombra: 0 4px 24px rgba(12, 74, 110, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: "DM Sans", system-ui, sans-serif;
      background: var(--arena);
      color: var(--texto);
      min-height: 100vh;
      background-image:
        radial-gradient(ellipse at 0% 0%, rgba(3, 105, 161, 0.07) 0%, transparent 50%),
        radial-gradient(ellipse at 100% 100%, rgba(245, 158, 11, 0.06) 0%, transparent 45%);
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }}
    header.page {{
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 2px solid var(--mar);
    }}
    header.page h1 {{
      font-family: "Instrument Serif", Georgia, serif;
      font-size: clamp(1.75rem, 5vw, 2.5rem);
      font-weight: 400;
      color: var(--mar);
      line-height: 1.1;
    }}
    header.page p {{ color: var(--muted); margin-top: 0.35rem; font-size: 0.95rem; }}
    .controles {{
      display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center;
      margin-bottom: 1.25rem;
    }}
    .controles label {{ font-size: 0.85rem; font-weight: 600; color: var(--mar); }}
    .controles select {{
      flex: 1; min-width: 160px; max-width: 240px;
      padding: 0.55rem 0.75rem; border: 1px solid var(--borde);
      border-radius: 8px; background: var(--tarjeta); font: inherit;
    }}
    .tabs-mes {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem; }}
    .tab-mes {{
      padding: 0.5rem 1rem; border: 1px solid var(--borde);
      border-radius: 999px; background: var(--tarjeta);
      font: inherit; font-weight: 600; cursor: pointer; color: var(--mar);
      transition: background 0.15s, color 0.15s;
    }}
    .tab-mes:hover {{ background: var(--espuma); }}
    .tab-mes.activo {{ background: var(--mar); color: #fff; border-color: var(--mar); }}
    .mes {{ display: none; animation: fade 0.25s ease; }}
    .mes.visible {{ display: block; }}
    @keyframes fade {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: none; }} }}
    .mes h2 {{
      font-family: "Instrument Serif", Georgia, serif;
      font-size: 1.5rem; font-weight: 400; color: var(--mar);
      margin-bottom: 0.75rem;
    }}
    .cab-sem {{
      display: grid; grid-template-columns: repeat(7, 1fr);
      gap: 4px; margin-bottom: 4px;
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
      color: var(--muted); text-align: center;
    }}
    .rejilla {{
      display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;
    }}
    .dia {{
      background: var(--tarjeta); border: 1px solid var(--borde);
      border-radius: 10px; min-height: 110px; padding: 0.4rem;
      box-shadow: var(--sombra); display: flex; flex-direction: column;
    }}
    .dia.vacio {{ background: transparent; border: none; box-shadow: none; min-height: 0; }}
    .dia-cab {{
      display: flex; justify-content: space-between; align-items: baseline;
      margin-bottom: 0.35rem; padding-bottom: 0.25rem;
      border-bottom: 1px solid var(--espuma);
    }}
    .dia-cab .num {{ font-weight: 700; font-size: 1rem; color: var(--mar); }}
    .dia-cab .sem {{ font-size: 0.65rem; color: var(--muted); font-weight: 600; }}
    .puestos {{ flex: 1; display: flex; flex-direction: column; gap: 2px; overflow: hidden; }}
    .puesto {{
      font-size: 0.62rem; line-height: 1.25; padding: 2px 4px;
      border-radius: 4px; background: var(--espuma);
      display: flex; flex-direction: column;
    }}
    .puesto .rol {{ color: var(--mar-claro); font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; }}
    .puesto .nombre {{ color: var(--texto); font-weight: 600; display: flex; align-items: center; gap: 3px; flex-wrap: wrap; }}
    .icono-llave {{ font-size: 0.75em; line-height: 1; opacity: 0.9; }}
    .puesto[data-campo="llave_cesantes"] {{ background: #fef9c3; border: 1px solid #fde047; }}
    .puesto[data-campo="llave_cesantes"] .rol {{ color: #a16207; }}
    .puesto[data-campo="socorrista_zodiac"] {{ background: #dbeafe; border: 1px solid #93c5fd; }}
    .puesto[data-campo="socorrista_zodiac"] .rol {{ color: #1d4ed8; }}
    .puesto[data-campo^="cesantes"] {{ background: #f1f5f9; }}
    .puesto[data-campo^="cesantes"] .rol {{ color: var(--muted); }}
    .dia.resaltado {{ border-color: var(--resalt-borde); background: var(--resalt); }}
    .dia.atenuado {{ opacity: 0.35; }}
    .puesto.resaltado {{ outline: 2px solid var(--sol); background: #fffbeb; }}
    .leyenda {{
      display: flex; flex-wrap: wrap; gap: 0.5rem 1rem;
      margin-top: 1.5rem; padding: 1rem; background: var(--tarjeta);
      border-radius: 12px; border: 1px solid var(--borde); font-size: 0.8rem;
    }}
    .leyenda span {{ color: var(--muted); }}
    .leyenda strong {{ color: var(--mar); }}
    .mi-resumen {{
      display: none; margin-top: 1rem; padding: 1rem;
      background: var(--tarjeta); border-radius: 12px; border-left: 4px solid var(--sol);
      box-shadow: var(--sombra);
    }}
    .mi-resumen.visible {{ display: block; }}
    .mi-resumen h3 {{ font-size: 1rem; color: var(--mar); margin-bottom: 0.5rem; }}
    .mi-resumen ul {{ list-style: none; font-size: 0.85rem; }}
    .mi-resumen li {{ padding: 0.25rem 0; border-bottom: 1px solid var(--espuma); }}
    @media (max-width: 768px) {{
      .rejilla {{ grid-template-columns: repeat(2, 1fr); }}
      .cab-sem {{ display: none; }}
      .dia {{ min-height: 100px; }}
      .dia.vacio {{ display: none; }}
    }}
    @media print {{
      body {{ background: #fff; }}
      .controles, .tabs-mes, .leyenda {{ display: none; }}
      .mes {{ display: block !important; page-break-after: always; }}
      .dia {{ break-inside: avoid; box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="page">
      <h1>{html.escape(titulo)}</h1>
      <p>{html.escape(subtitulo)} · 4 días trabajo / 2 libres</p>
    </header>
    <div class="controles">
      <label for="filtro">Ver turnos de:</label>
      <select id="filtro">
        <option value="">— Todos —</option>
        {opts}
      </select>
    </div>
    <nav class="tabs-mes">{mes_btns}</nav>
    <div id="mi-resumen" class="mi-resumen"></div>
    {"".join(bloques_mes)}
    <div class="leyenda">
      <span><strong>Chapela</strong> · playa Chapela · 🔑 lleva llave</span>
      <span><strong>Abrir puesto</strong> · cesantes · 🔑</span>
      <span><strong>Zodiac</strong> · apertura puerto</span>
      <span><strong>Torre</strong></span>
      <span><strong>Cesantes 2+</strong> · refuerzo</span>
    </div>
  </div>
  <script>
    const DATOS = {json.dumps(datos, ensure_ascii=False)};

    const tabs = document.querySelectorAll(".tab-mes");
    const meses = document.querySelectorAll(".mes");
    const filtro = document.getElementById("filtro");
    const resumen = document.getElementById("mi-resumen");

    function activarMes(id) {{
      meses.forEach(m => m.classList.toggle("visible", m.id === id));
      tabs.forEach(t => t.classList.toggle("activo", t.dataset.target === id));
    }}

    tabs.forEach(t => t.addEventListener("click", () => activarMes(t.dataset.target)));
    if (tabs.length) activarMes(tabs[0].dataset.target);

    function aplicarFiltro(nombre) {{
      document.querySelectorAll(".dia:not(.vacio)").forEach(dia => {{
        const personas = JSON.parse(dia.dataset.personas || "[]");
        const match = !nombre || personas.includes(nombre);
        dia.classList.toggle("resaltado", !!nombre && match);
        dia.classList.toggle("atenuado", !!nombre && !match);
        dia.querySelectorAll(".puesto").forEach(p => {{
          p.classList.toggle("resaltado", !!nombre && p.dataset.persona === nombre);
        }});
      }});

      if (!nombre) {{
        resumen.classList.remove("visible");
        resumen.innerHTML = "";
        return;
      }}

      const lineas = [];
      for (const [fecha, puestos] of Object.entries(DATOS).sort()) {{
        const mios = puestos.filter(p => p.persona === nombre);
        if (mios.length) {{
          const f = new Date(fecha + "T12:00:00");
          const txt = f.toLocaleDateString("es-ES", {{ weekday: "short", day: "numeric", month: "short" }});
          const roles = mios.map(p => p.rol).join(", ");
          lineas.push(`<li><strong>${{txt}}</strong> — ${{roles}}</li>`);
        }}
      }}
      resumen.innerHTML = `<h3>${{nombre}} — ${{lineas.length}} días</h3><ul>${{lineas.join("")}}</ul>`;
      resumen.classList.add("visible");
    }}

    filtro.addEventListener("change", () => aplicarFiltro(filtro.value));
  </script>
</body>
</html>"""


def main() -> int:
    if not CSV_PATH.exists():
        print(f"No se encuentra {CSV_PATH}. Ejecuta primero generar_turnos.py", file=sys.stderr)
        return 1

    filas = cargar_filas_csv()
    cfg = cargar_config() if CONFIG_PATH.exists() else {}
    anio = cfg.get("periodo", {}).get("inicio", "")[:4] or "2026"
    titulo = f"Turnos playas {anio}"
    subtitulo = etiqueta_periodo(cfg) if cfg else ""

    HTML_PATH.write_text(generar_html(filas, titulo, subtitulo), encoding="utf-8")
    print(f"HTML generado: {HTML_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
