# HNDM — extracción y normalización reproducible

Este paquete trabaja con **snapshots HTML guardados manualmente desde HNDM**. No hace scraping directo del sitio.

## 1. Estructura

```text
hndm_revision_tools/
├── scripts/
│   ├── 01_parse_hndm.py
│   └── 02_normalize_hndm.py
├── data/
│   └── raw/
├── output/
├── requirements.txt
└── README.md
```

## 2. Qué HTML colocar en `data/raw/`

Para la normalización deben estar solamente snapshots compatibles entre sí:

- `HNDM-1907.html`: consulta base, **sin palabra**, con filtros **México + Español + año 1907**. Este archivo ya sirve como denominador porque la faceta Fecha contiene los conteos de los otros años y la precisión aplicada contiene 1907.
- una consulta por cada término, con **México + Español**, **sin filtro de fecha**, por ejemplo:
  - `HNDM-moderno.html`
  - `HNDM-modernidad.html`
  - `HNDM-ciencia.html`
  - `HNDM-razon.html`
  - `HNDM-progreso.html`

**Importante:** no uses para la normalización el `HNDM-modernidad.html` antiguo que se guardó sin seleccionar México y Español. El segundo programa lo rechazará deliberadamente porque los filtros del numerador y el denominador no coinciden.

La forma más limpia de guardar cada término es usar Búsqueda avanzada, poner el término, seleccionar **País = México** e **Idioma = Español**, dejar la fecha sin restringir y guardar la página como “Página web, solo HTML”.

## 3. Preparar Python

Desde la carpeta `hndm_revision_tools`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Parsear snapshots

```bash
python scripts/01_parse_hndm.py \
    data/raw \
    output \
    --start-year 1722 \
    --end-year 1939 \
    --retrieved-at 2026-09-13
```

Genera:

- `queries.csv`: consulta, término, filtros activos, total de resultados, URL guardada, SHA-256.
- `facets.csv`: todas las facetas que HNDM dejó en cada snapshot.
- `annual_counts.csv`: conteos por año, con ceros explícitos en el rango 1722–1939.
- `titles.csv`: faceta Título.
- `periodicity.csv`: faceta Periodicidad.
- `languages.csv`: faceta Idioma.
- `countries.csv`: faceta País.
- `manifest.jsonl`: manifiesto reproducible de cada snapshot.

## 5. Normalizar

```bash
python scripts/02_normalize_hndm.py \
    output \
    --base-query HNDM-1907
```

El programa compara primero los filtros. Si una consulta por término no usa exactamente los mismos filtros no temporales que el corpus base, se detiene con un error.

Genera:

- `normalized_by_year.csv`
- `normalized_by_decade.csv`

La tasa anual se calcula como:

```text
rate_per_10000_pages = 10000 * page_hits / total_pages
```

La tasa decenal se calcula como razón de sumas:

```text
10000 * sum(page_hits) / sum(total_pages)
```

No se usa el promedio simple de las tasas anuales.

## 6. Qué conservar

No edites los HTML originales ni los CSV generados. Los HTML son snapshots del estado de HNDM en la fecha de consulta y cada uno queda identificado mediante SHA-256.

La faceta `Título` siempre corresponde al conjunto definido por los filtros activos en ese HTML. Por ejemplo, un snapshot sin rango temporal no permite interpretar `titles.csv` como “títulos de 1722–1939” sin un filtrado adicional específico.
