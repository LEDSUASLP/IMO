#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Control de longitud y género para el corpus de Obras Pastorales y Oratorias.

Entradas
--------
1. documentos_revision.csv generado por reconstruir_imo_v3.py a partir de los
   Golden_per_Chapter canónicos. Debe contener documento_id, palabras_total y
   n_ocurrencias_total (o las cinco columnas n_*).
2. IMO_fechas_genero_final.csv con periodo y grupo_genero_final validados.

Modelo
------
Regresión binomial negativa NB2 del número total de ocurrencias seleccionadas
por documento, con log(palabras_total) como offset y periodo + grupo de género
como predictores. Se calculan pruebas de razón de verosimilitud (LR) para los
bloques de periodo, género y la interacción periodo×género.

La finalidad es un control de robustez sobre la TASA COMBINADA de las cinco
familias léxicas, no un modelo término por término ni una prueba semántica.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2
import statsmodels.formula.api as smf

PERIODS = ["1865-1879", "1880-1895", "1896-1905", "1906-1921"]
GENRES = [
    "Pastoral-administrativo",
    "Homilético-devocional",
    "Oratorio-ceremonial / intelectual",
]


def lr_test(full, reduced):
    stat = 2.0 * (full.llf - reduced.llf)
    df = int(full.df_model - reduced.df_model)
    return {
        "chi2": float(stat),
        "df": df,
        "p": float(chi2.sf(stat, df)),
    }


def read_inputs(documents_path: Path, metadata_path: Path) -> pd.DataFrame:
    docs = pd.read_csv(documents_path, dtype={"documento_id": str})
    meta = pd.read_csv(metadata_path, dtype={"documento_id": str})

    required_docs = {"documento_id", "palabras_total"}
    missing = required_docs - set(docs.columns)
    if missing:
        raise SystemExit(f"Faltan columnas en documentos: {sorted(missing)}")

    if "n_ocurrencias_total" not in docs.columns:
        count_cols = ["n_razon", "n_ciencia", "n_progreso", "n_moderno", "n_modernidad"]
        missing_counts = set(count_cols) - set(docs.columns)
        if missing_counts:
            raise SystemExit(
                "Falta n_ocurrencias_total y no se pueden reconstruir los conteos; "
                f"faltan: {sorted(missing_counts)}"
            )
        docs["n_ocurrencias_total"] = docs[count_cols].fillna(0).sum(axis=1)

    required_meta = {"documento_id", "periodo", "grupo_genero_final"}
    missing = required_meta - set(meta.columns)
    if missing:
        raise SystemExit(f"Faltan columnas en metadata: {sorted(missing)}")

    if docs["documento_id"].duplicated().any():
        raise SystemExit("Hay documento_id duplicados en el archivo de documentos.")
    if meta["documento_id"].duplicated().any():
        raise SystemExit("Hay documento_id duplicados en el archivo de metadata.")

    df = docs[["documento_id", "palabras_total", "n_ocurrencias_total"]].merge(
        meta[["documento_id", "periodo", "grupo_genero_final"]],
        on="documento_id",
        how="left",
        validate="one_to_one",
    )
    df = df.rename(
        columns={"n_ocurrencias_total": "n_total", "grupo_genero_final": "grupo_genero"}
    )

    if df[["periodo", "grupo_genero"]].isna().any().any():
        bad = df[df[["periodo", "grupo_genero"]].isna().any(axis=1)]["documento_id"].tolist()
        raise SystemExit(f"Falta periodo o género para: {bad[:20]}")

    main = df[df["periodo"].isin(PERIODS)].copy()
    if len(main) != 220:
        raise SystemExit(f"Se esperaban 220 documentos en los cuatro periodos; se obtuvieron {len(main)}")
    if (main["palabras_total"] <= 0).any():
        raise SystemExit("Hay documentos con palabras_total <= 0.")

    main["periodo"] = pd.Categorical(main["periodo"], categories=PERIODS, ordered=True)
    main["grupo_genero"] = pd.Categorical(main["grupo_genero"], categories=GENRES, ordered=True)
    if main["grupo_genero"].isna().any():
        bad = main[main["grupo_genero"].isna()]["documento_id"].tolist()
        raise SystemExit(f"Categoría de género no reconocida en: {bad[:20]}")
    return main


def fit_models(df: pd.DataFrame):
    period_term = "C(periodo, Treatment(reference='1865-1879'))"
    genre_term = "C(grupo_genero, Treatment(reference='Pastoral-administrativo'))"
    offset = np.log(df["palabras_total"].astype(float))

    full = smf.negativebinomial(
        f"n_total ~ {period_term} + {genre_term}", data=df, offset=offset
    ).fit(disp=0, maxiter=500)
    no_period = smf.negativebinomial(
        f"n_total ~ {genre_term}", data=df, offset=offset
    ).fit(disp=0, maxiter=500)
    no_genre = smf.negativebinomial(
        f"n_total ~ {period_term}", data=df, offset=offset
    ).fit(disp=0, maxiter=500)
    interaction = smf.negativebinomial(
        f"n_total ~ {period_term} * {genre_term}", data=df, offset=offset
    ).fit(disp=0, maxiter=1000)

    for name, model in {
        "full": full,
        "no_period": no_period,
        "no_genre": no_genre,
        "interaction": interaction,
    }.items():
        if not bool(model.mle_retvals.get("converged", False)):
            raise RuntimeError(f"El modelo {name} no convergió.")
    return full, no_period, no_genre, interaction


def effects_table(model, prefix: str) -> pd.DataFrame:
    ci = model.conf_int()
    rows = []
    for term, coef in model.params.items():
        if term in ("Intercept", "alpha") or not term.startswith(prefix):
            continue
        rows.append(
            {
                "term": term,
                "IRR": float(np.exp(coef)),
                "CI95_low": float(np.exp(ci.loc[term, 0])),
                "CI95_high": float(np.exp(ci.loc[term, 1])),
                "p": float(model.pvalues[term]),
            }
        )
    return pd.DataFrame(rows)


def composition_table(df: pd.DataFrame) -> pd.DataFrame:
    g = (
        df.groupby(["periodo", "grupo_genero"], observed=False)
        .agg(documentos=("documento_id", "count"), palabras=("palabras_total", "sum"), ocurrencias=("n_total", "sum"))
        .reset_index()
    )
    totals = df.groupby("periodo", observed=False).agg(
        docs_periodo=("documento_id", "count"), palabras_periodo=("palabras_total", "sum")
    )
    g = g.merge(totals, on="periodo", how="left")
    g["ocurrencias_por_10000"] = 10000.0 * g["ocurrencias"] / g["palabras"]
    g["pct_docs_periodo"] = g["documentos"] / g["docs_periodo"]
    g["pct_palabras_periodo"] = g["palabras"] / g["palabras_periodo"]
    return g.drop(columns=["docs_periodo", "palabras_periodo"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--documents", required=True, type=Path, help="documentos_revision.csv regenerado desde los Golden canónicos")
    ap.add_argument("--metadata", required=True, type=Path, help="CSV con fechas/periodos y género final")
    ap.add_argument("--out", type=Path, default=Path("control_genero_out"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    df = read_inputs(args.documents, args.metadata)
    full, no_period, no_genre, interaction = fit_models(df)

    tests = {
        "period": lr_test(full, no_period),
        "genre": lr_test(full, no_genre),
        "period_x_genre": lr_test(interaction, full),
    }

    result = {
        "documents": int(len(df)),
        "words": int(df["palabras_total"].sum()),
        "selected_occurrences": int(df["n_total"].sum()),
        "negative_binomial_alpha": float(full.params["alpha"]),
        "period_global_LR": tests["period"],
        "genre_global_LR": tests["genre"],
        "interaction_LR": tests["period_x_genre"],
        "reference_period": PERIODS[0],
        "reference_genre": GENRES[0],
        "model": "NB2 count regression; offset = log(words); outcome = combined count of five lexical families",
    }

    (args.out / "model_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    effects_table(full, "C(periodo").to_csv(args.out / "effects_period.csv", index=False)
    effects_table(full, "C(grupo_genero").to_csv(args.out / "effects_genre.csv", index=False)
    composition_table(df).to_csv(args.out / "period_genre_composition.csv", index=False)
    df.to_csv(args.out / "analysis_dataset.csv", index=False)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
