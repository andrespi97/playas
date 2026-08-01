# AGENTS.md

## Cursor Cloud specific instructions

Generador de cuadrantes de turnos de socorristas (Python, sin framework web). Scripts CLI que leen `config.yaml`, escriben un CSV y publican vistas HTML estáticas.

### Servicios / componentes
- `generar_turnos.py`: valida `config.yaml`, calcula la rotación y escribe `turnos_jul_sep_2026.csv`; al terminar llama a `generar_vista.py`.
- `generar_vista.py`: genera `turnos.html`, `docs/index.html` y `docs/agosto.html` (GitHub Pages, agosto sin extras) a partir del CSV.
- `turnos.html` / `docs/index.html`: SPA estática (calendario + checkbox de extras + PDF). Usa `vendor/jspdf.umd.min.js` y `vendor/html2canvas.min.js`.
- `docs/agosto.html`: cuadrante público solo de agosto, sin horas extras.

### Entorno
- Las dependencias se instalan en un virtualenv en `.venv/` (ver update script). Usa `.venv/bin/python` para todo.
- `requirements.txt` solo declara `PyYAML`. Los tests necesitan además `PyMuPDF` (`import fitz`) y `playwright` con Chromium; el update script los instala.

### Ejecutar / build (dev)
- Regenerar cuadrante: `.venv/bin/python generar_turnos.py` (acepta `--congelar-hasta YYYY-MM-DD`).
- Solo regenerar HTML desde el CSV: `.venv/bin/python generar_vista.py`.
- Gotcha: `generar_turnos.py` recalcula los días futuros según la fecha de HOY y reescribe archivos versionados (`turnos_jul_sep_2026.csv`, `turnos.html`, `docs/index.html`). Si solo estás probando, restaura con `git checkout -- <archivos>` para no ensuciar el diff.
- Ver la app: servir el directorio y abrir la página, p.ej. `python3 -m http.server 8099` y visitar `http://127.0.0.1:8099/turnos.html`.

### Tests
- Framework: `unittest` (NO hay `pytest`).
- Unit: `.venv/bin/python -m unittest test_turnos` (algunos tests de "Raúl" salen como skipped a propósito).
- E2E PDF: `.venv/bin/python -m unittest test_pdf_mes` — lanza Chromium con Playwright, sirve `turnos.html` y verifica el PDF descargado; escribe `.test_artifacts/turnos-2026-07.pdf` (versionado, restáuralo si no quieres el cambio).

### Lint
- No hay linter configurado en el repo (sin `pyproject.toml`/`.flake8`/`ruff`). Los `# noqa` en el código son solo anotaciones.
