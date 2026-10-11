#!/usr/bin/env python3
"""Parsea snapshots HTML guardados desde HNDM.

No consulta la web. Lee HTML preservados localmente y exporta:
- queries.csv: metadatos y filtros de cada consulta
- facets.csv: todas las facetas (fecha, titulo, frecuencia, idioma, etc.)
- annual_counts.csv: conteos por año dentro del rango analitico
- titles.csv: faceta de titulo
- periodicity.csv: faceta de periodicidad
- manifest.jsonl: registro reproducible con hash SHA-256
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup

PARSER_VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def text_until_br(input_tag) -> str:
    parts: list[str] = []
    for sibling in input_tag.next_siblings:
        if getattr(sibling, "name", None) == "br":
            break
        if hasattr(sibling, "get_text"):
            txt = sibling.get_text(" ", strip=True)
        else:
            txt = str(sibling).strip()
        if txt:
            parts.append(txt)
    return normalize_ws(" ".join(parts))


def source_url_from_saved_html(raw_html: str) -> str | None:
    m = re.search(
        r"saved from url=\(\d+\)(https?://[^\s]+)",
        raw_html[:12000],
        flags=re.I,
    )
    if not m:
        return None
    return m.group(1).replace("-->", "").strip()


def get_term(form) -> str:
    # Búsqueda básica por palabra: name=palabras
    # Búsqueda avanzada: name=palabrasTexto
    for name in ("palabras", "palabrasTexto"):
        inp = form.find("input", attrs={"name": name})
        if inp is not None:
            value = normalize_ws(inp.get("value") or "")
            if value:
                return value
    return ""


def get_total_results(soup) -> int | None:
    txt = soup.get_text(" ", strip=True)
    m = re.search(
        r"Mostrando resultados:\s*\d+\s+a\s+\d+\s+de\s+([\d,]+)",
        txt,
        flags=re.I,
    )
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def parse_facet_rows(form) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str, int, bool]] = set()

    for inp in form.find_all("input", attrs={"name": "filtros"}):
        if (inp.get("type") or "").lower() == "hidden":
            continue

        filter_value = inp.get("value") or ""
        if ":" not in filter_value:
            continue
        facet = filter_value.split(":", 1)[0].strip()

        label_count = text_until_br(inp)
        # Tomar el último paréntesis numérico para soportar títulos con paréntesis.
        m = re.match(r"^(.*)\(([\d,]+)\)\s*$", label_count, flags=re.S)
        if not m:
            continue

        value = normalize_ws(m.group(1))
        count = int(m.group(2).replace(",", ""))
        selected = inp.has_attr("checked")

        key = (facet, value, count, selected)
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "facet": facet,
                "value": value,
                "count": count,
                "selected": selected,
                "filter_value": filter_value,
            }
        )

    return rows


def parse_snapshot(path: Path, retrieved_at: str) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    form = soup.find("form", id="res-filtro")
    if form is None:
        raise ValueError(
            f"{path.name}: no se encontró form#res-filtro; "
            "el archivo no parece ser una página de resultados HNDM."
        )

    term = get_term(form)
    query_type = "term" if term else "base"
    facets = parse_facet_rows(form)

    selected_filters = sorted(
        row["filter_value"] for row in facets if row["selected"]
    )

    hidden_params = []
    for inp in form.find_all("input"):
        if (inp.get("type") or "").lower() == "hidden":
            hidden_params.append(
                {"name": inp.get("name"), "value": inp.get("value")}
            )

    return {
        "query_id": path.stem,
        "query_type": query_type,
        "term": term,
        "source_file": path.name,
        "source_url": source_url_from_saved_html(raw),
        "form_action": form.get("action"),
        "retrieved_at": retrieved_at,
        "sha256": sha256_file(path),
        "total_results": get_total_results(soup),
        "selected_filters": selected_filters,
        "hidden_params": hidden_params,
        "facets": facets,
    }


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path, help="Carpeta con snapshots .html")
    ap.add_argument("output_dir", type=Path, help="Carpeta de salida")
    ap.add_argument("--start-year", type=int, default=1722)
    ap.add_argument("--end-year", type=int, default=1939)
    ap.add_argument(
        "--retrieved-at",
        default=date.today().isoformat(),
        help="Fecha de consulta/snapshot, YYYY-MM-DD",
    )
    args = ap.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("--start-year no puede ser mayor que --end-year")

    html_files = sorted(args.input_dir.glob("*.html"))
    if not html_files:
        raise SystemExit(f"No encontré .html en {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    snapshots = []
    facet_rows = []

    for path in html_files:
        print(f"[parse] {path.name}")
        snap = parse_snapshot(path, args.retrieved_at)
        snapshots.append(snap)
        for row in snap["facets"]:
            facet_rows.append(
                {
                    "query_id": snap["query_id"],
                    "query_type": snap["query_type"],
                    "term": snap["term"],
                    "facet": row["facet"],
                    "value": row["value"],
                    "count": row["count"],
                    "selected": int(row["selected"]),
                    "filter_value": row["filter_value"],
                    "source_file": snap["source_file"],
                    "retrieved_at": snap["retrieved_at"],
                    "sha256": snap["sha256"],
                }
            )

    # Metadatos por consulta.
    query_rows = []
    for snap in snapshots:
        non_date_filters = sorted(
            f for f in snap["selected_filters"] if not f.startswith("fecha:")
        )
        query_rows.append(
            {
                "query_id": snap["query_id"],
                "query_type": snap["query_type"],
                "term": snap["term"],
                "source_file": snap["source_file"],
                "source_url": snap["source_url"] or "",
                "form_action": snap["form_action"] or "",
                "retrieved_at": snap["retrieved_at"],
                "total_results": "" if snap["total_results"] is None else snap["total_results"],
                "selected_filters_json": json.dumps(snap["selected_filters"], ensure_ascii=False),
                "non_date_filters_json": json.dumps(non_date_filters, ensure_ascii=False),
                "sha256": snap["sha256"],
                "parser_version": PARSER_VERSION,
            }
        )

    write_csv(
        args.output_dir / "queries.csv",
        query_rows,
        [
            "query_id", "query_type", "term", "source_file", "source_url",
            "form_action", "retrieved_at", "total_results",
            "selected_filters_json", "non_date_filters_json", "sha256",
            "parser_version",
        ],
    )

    write_csv(
        args.output_dir / "facets.csv",
        facet_rows,
        [
            "query_id", "query_type", "term", "facet", "value", "count",
            "selected", "filter_value", "source_file", "retrieved_at", "sha256",
        ],
    )

    # Conteos anuales por consulta. Años ausentes se convierten en cero dentro del rango.
    annual_rows = []
    for snap in snapshots:
        year_counts: dict[int, int] = {}
        for row in snap["facets"]:
            if row["facet"] == "fecha" and row["value"].isdigit():
                year_counts[int(row["value"])] = row["count"]

        for year in range(args.start_year, args.end_year + 1):
            annual_rows.append(
                {
                    "query_id": snap["query_id"],
                    "query_type": snap["query_type"],
                    "term": snap["term"],
                    "year": year,
                    "page_count": year_counts.get(year, 0),
                }
            )

    write_csv(
        args.output_dir / "annual_counts.csv",
        annual_rows,
        ["query_id", "query_type", "term", "year", "page_count"],
    )

    for facet_name, filename in (
        ("titulo", "titles.csv"),
        ("frecuencia", "periodicity.csv"),
        ("idioma", "languages.csv"),
        ("pais", "countries.csv"),
    ):
        rows = [r for r in facet_rows if r["facet"] == facet_name]
        write_csv(
            args.output_dir / filename,
            rows,
            [
                "query_id", "query_type", "term", "facet", "value", "count",
                "selected", "filter_value", "source_file", "retrieved_at", "sha256",
            ],
        )

    with (args.output_dir / "manifest.jsonl").open("w", encoding="utf-8") as f:
        for snap in snapshots:
            manifest = {k: v for k, v in snap.items() if k != "facets"}
            manifest["parser_version"] = PARSER_VERSION
            f.write(json.dumps(manifest, ensure_ascii=False) + "\n")

    print(f"\nProcesados: {len(snapshots)} snapshots")
    print(f"Facetas extraídas: {len(facet_rows)} filas")
    print(f"Salida: {args.output_dir.resolve()}")
    print("\nSiguiente paso: ejecutar 02_normalize_hndm.py sobre esta carpeta.")


if __name__ == "__main__":
    main()
