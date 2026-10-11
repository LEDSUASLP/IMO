#!/usr/bin/env python3
"""Normaliza consultas por término usando un snapshot base sin palabra.

Requisitos:
- output/queries.csv y output/annual_counts.csv generados por 01_parse_hndm.py
- exactamente un snapshot base, o especificar --base-query
- los filtros no temporales del snapshot base y de cada término deben coincidir

Produce:
- normalized_by_year.csv
- normalized_by_decade.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_filters(value: str) -> list[str]:
    if not value or pd.isna(value):
        return []
    return sorted(json.loads(value))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output_dir", type=Path, help="Carpeta producida por 01_parse_hndm.py")
    ap.add_argument(
        "--base-query",
        default=None,
        help="query_id del snapshot base si hay más de uno",
    )
    ap.add_argument("--rate-scale", type=float, default=10000.0)
    args = ap.parse_args()

    qpath = args.output_dir / "queries.csv"
    apath = args.output_dir / "annual_counts.csv"
    if not qpath.exists() or not apath.exists():
        raise SystemExit("Faltan queries.csv o annual_counts.csv. Ejecuta primero 01_parse_hndm.py")

    queries = pd.read_csv(qpath, dtype=str).fillna("")
    annual = pd.read_csv(apath)

    bases = queries[queries["query_type"] == "base"].copy()
    if args.base_query:
        bases = bases[bases["query_id"] == args.base_query]
    if len(bases) != 1:
        ids = ", ".join(bases["query_id"].tolist()) or "ninguno"
        raise SystemExit(
            "Necesito exactamente un snapshot base. "
            f"Candidatos: {ids}. Usa --base-query si corresponde."
        )

    base = bases.iloc[0]
    base_id = base["query_id"]
    base_filters = parse_filters(base["non_date_filters_json"])

    terms = queries[queries["query_type"] == "term"].copy()
    if terms.empty:
        raise SystemExit("No encontré snapshots de consultas por término.")

    problems = []
    for _, row in terms.iterrows():
        term_filters = parse_filters(row["non_date_filters_json"])
        if term_filters != base_filters:
            problems.append(
                {
                    "query_id": row["query_id"],
                    "term": row["term"],
                    "term_filters": term_filters,
                }
            )

    if problems:
        print("ERROR: los filtros del numerador y del denominador no coinciden.\n")
        print("Filtros del corpus base:")
        print("  ", base_filters)
        print("\nConsultas incompatibles:")
        for p in problems:
            print(f"  - {p['query_id']} ({p['term']}): {p['term_filters']}")
        print(
            "\nVuelve a guardar esas consultas aplicando exactamente los mismos "
            "filtros no temporales que el corpus base (por ejemplo México + Español)."
        )
        raise SystemExit(2)

    base_year = annual[annual["query_id"] == base_id][["year", "page_count"]].copy()
    base_year = base_year.rename(columns={"page_count": "total_pages"})

    frames = []
    for _, q in terms.iterrows():
        term_id = q["query_id"]
        term = q["term"]
        df = annual[annual["query_id"] == term_id][["year", "page_count"]].copy()
        df = df.rename(columns={"page_count": "page_hits"})
        df = df.merge(base_year, on="year", how="left")
        df.insert(0, "term", term)
        df.insert(0, "query_id", term_id)

        impossible = df[(df["page_hits"] > 0) & (df["total_pages"] <= 0)]
        if not impossible.empty:
            years = impossible["year"].tolist()
            raise SystemExit(
                f"Inconsistencia para {term}: hay hits con denominador 0 en {years}"
            )

        df["proportion"] = df.apply(
            lambda r: (r.page_hits / r.total_pages) if r.total_pages > 0 else float("nan"),
            axis=1,
        )
        df["rate_per_10000_pages"] = df["proportion"] * args.rate_scale
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["term", "year"])
    out.to_csv(args.output_dir / "normalized_by_year.csv", index=False)

    # Décadas: razón de sumas, NO promedio simple de tasas anuales.
    d = out.copy()
    d["decade"] = (d["year"] // 10) * 10
    decade = (
        d.groupby(["term", "decade"], as_index=False)
        .agg(page_hits=("page_hits", "sum"), total_pages=("total_pages", "sum"))
    )
    decade["proportion"] = decade.apply(
        lambda r: (r.page_hits / r.total_pages) if r.total_pages > 0 else float("nan"),
        axis=1,
    )
    decade["rate_per_10000_pages"] = decade["proportion"] * args.rate_scale
    decade.to_csv(args.output_dir / "normalized_by_decade.csv", index=False)

    print("Corpus base:", base_id)
    print("Filtros:", base_filters)
    print("Términos:", ", ".join(sorted(terms["term"].tolist())))
    print("\nArchivos creados:")
    print(" -", args.output_dir / "normalized_by_year.csv")
    print(" -", args.output_dir / "normalized_by_decade.csv")


if __name__ == "__main__":
    main()
