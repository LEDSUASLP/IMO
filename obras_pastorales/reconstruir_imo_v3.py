#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reconstruye de forma reproducible una base tipo IMO-AS a partir de los capítulos
completos (golden truth) de las Obras de Ignacio Montes de Oca.

Uso básico:
    python reconstruir_imo.py /ruta/a/los/txt --salida salida_imo

Entrada esperada (puede estar en subdirectorios):
    IMO_I_cap_2.txt
    IMO_II_cap_17.txt
    ...

Salidas:
    documentos_revision.csv   Una fila por capítulo: extensión, fecha inferida y campos de validación.
    ocurrencias.csv            Una fila por ocurrencia léxica (token) de los cinco términos.
    contextos.csv              Una fila por término y párrafo (más parecido a IMO-AS).
    resumen_inferido.csv       Conteos y tasas preliminares por periodo usando fechas inferidas.
    metadata_metodo.json       Reglas de búsqueda, tokenización y versión del programa.

Flujo de validación de fechas:
1) Ejecutar una primera vez y revisar documentos_revision.csv (idealmente en Google Sheets).
2) Para cada fila llenar:
       validacion_fecha = ACEPTAR | CORREGIR | SIN_FECHA
       fecha_validada   = YYYY-MM-DD o YYYY (solo si CORREGIR)
   Se puede editar también tipo_documento_validado si se desea.
3) Guardar el archivo como CSV y ejecutar:
       python reconstruir_imo.py /ruta/txt --salida salida_final \
           --fechas-validadas /ruta/documentos_revision_validado.csv
   El resumen final usará primero la fecha validada y, si no existe, la inferida.

Notas:
- Las búsquedas son insensibles a mayúsculas/minúsculas pero conservan la forma encontrada.
- Se buscan familias morfológicas explícitas; cada match conserva `forma_encontrada`.
- El denominador es número de palabras alfabéticas del capítulo tras recomponer palabras
  partidas por guion de fin de línea.
- Una fila de `ocurrencias.csv` es una ocurrencia léxica. `contextos.csv` colapsa múltiples
  ocurrencias del mismo término dentro del mismo párrafo.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

VERSION = "3.0.0"

# Corpus analítico validado contra los índices de los ocho tomos.
# Los límites son inclusivos y reflejan la numeración ACTUAL del golden truth.
# En el tomo I, los antiguos cap_11/cap_12 fueron fusionados y los posteriores renumerados.
CORPUS_RANGES = {
    1: (2, 21),
    2: (2, 23),
    3: (2, 37),
    4: (1, 49),
    5: (1, 25),
    6: (1, 26),
    7: (2, 32),
    8: (3, 20),
}

def corpus_status(tomo: int, cap: int) -> tuple[bool, str]:
    if tomo not in CORPUS_RANGES:
        return False, "tomo fuera del corpus esperado"
    a, b = CORPUS_RANGES[tomo]
    if a <= cap <= b:
        return True, "cuerpo principal validado contra índice"
    if cap < a:
        return False, "prefacio/prólogo/dedicatoria/advertencia u otro material preliminar"
    return False, "notas/documentos/apéndice/epílogo u otro material posterior"

# Periodización utilizada en el manuscrito. Fácil de cambiar aquí si fuera necesario.
PERIODOS = [
    (1865, 1879, "1865-1879"),
    (1880, 1895, "1880-1895"),
    (1896, 1905, "1896-1905"),
    (1906, 1921, "1906-1921"),
]

# Familias explícitas. No se usa stemming ni lematización opaca.
# Esto permite saber exactamente qué formas fueron aceptadas como equivalentes.
TERM_PATTERNS = {
    "razón": re.compile(r"\b(?:raz[oó]n|razones)\b", re.IGNORECASE),
    "ciencia": re.compile(r"\bciencias?\b", re.IGNORECASE),
    "progreso": re.compile(r"\bprogresos?\b", re.IGNORECASE),
    "moderno": re.compile(r"\bmodern(?:o|a|os|as)\b", re.IGNORECASE),
    "modernidad": re.compile(r"\bmodernidad(?:es)?\b", re.IGNORECASE),
}

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MONTH_ALT = "|".join(MONTHS.keys())

# Fecha del tipo 20 de Junio de 1871; tolera puntuación/ordinales leves.
FULL_DATE_RE = re.compile(
    rf"(?<!\d)([0-3]?\d)(?:\.?[º°o])?\s+de\s+({MONTH_ALT})\s+(?:de\s+)?((?:18|19)\d{{2}})(?!\d)",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"(?<!\d)((?:18|19)\d{2})(?!\d)")

# Nombre de archivo: acepta IMO_I_cap_2.txt, IMO_VIII_cap_123.txt, mayúsculas/minúsculas.
FILE_RE = re.compile(r"IMO[_\-\s]*([IVXLCDM]+|\d+)[_\-\s]*cap[_\-\s]*(\d+)\.txt$", re.IGNORECASE)

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

def roman_to_int(s: str) -> int:
    s = s.upper()
    if s.isdigit():
        return int(s)
    total = 0
    prev = 0
    for ch in reversed(s):
        val = ROMAN_MAP.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_text(raw: str) -> str:
    """Normaliza solo lo necesario para análisis, sin reescribir el golden truth."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Une palabras partidas tipográficamente por guion al final de línea: consi-\nderar -> considerar.
    raw = re.sub(r"(?<=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])-\s*\n\s*(?=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", "", raw)
    # Conserva párrafos (líneas en blanco), pero recompone líneas visuales dentro de un mismo párrafo.
    paras = re.split(r"\n\s*\n+", raw)
    paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in paras]
    return "\n\n".join(p for p in paras if p)


WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:[’'][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)?", re.UNICODE)

def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def split_sentences(paragraph: str) -> list[str]:
    # Segmentador deliberadamente simple y auditable; el párrafo completo siempre queda disponible.
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÜÑ¿¡«“\"])", paragraph)
    return [p.strip() for p in pieces if p.strip()]


def sentence_for_offset(paragraph: str, offset: int) -> tuple[str, str, str]:
    sents = split_sentences(paragraph)
    if not sents:
        return "", paragraph, ""
    cursor = 0
    for i, s in enumerate(sents):
        start = paragraph.find(s, cursor)
        end = start + len(s)
        if start <= offset <= end:
            prev_s = sents[i - 1] if i > 0 else ""
            next_s = sents[i + 1] if i + 1 < len(sents) else ""
            return prev_s, s, next_s
        cursor = max(end, cursor)
    return "", paragraph, ""


@dataclass
class DateCandidate:
    iso: str
    year: int
    score: int
    position: float
    evidence: str
    reason: str
    role: str = "unknown"  # document | historical | weak


def _context(text: str, start: int, end: int, radius: int = 160) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[a:b]).strip()


# Señales documentales: intentamos identificar la fecha DEL TEXTO, no cualquier fecha mencionada.
DOC_HEADER_CUE = re.compile(
    r"\b(?:homil[ií]a|serm[oó]n|paneg[ií]rico|discurso|oraci[oó]n(?:\s+f[uú]nebre)?|"
    r"carta(?:\s+pastoral)?|alocuci[oó]n|pastoral)\b.{0,180}?"
    r"\b(?:predicad[ao]|pronunciad[ao]|le[ií]d[ao]|dirigid[ao]|dich[ao]|celebrad[ao]|el\s+d[ií]a|la\s+noche|en\s+la\s+fiesta)\b",
    re.IGNORECASE | re.DOTALL,
)
DOC_CLOSE_CUE = re.compile(
    r"\b(?:dada|dado|fechad[ao]|firmad[ao]|expedid[ao])\b",
    re.IGNORECASE,
)
AUTHOR_SIGNATURE_CUE = re.compile(
    r"\b(?:\+\s*)?ignacio\b.{0,120}?\b(?:obispo|arzobispo|episcop)\w*\b",
    re.IGNORECASE | re.DOTALL,
)
NARRATIVE_CUE = re.compile(
    r"\b(?:naci[oó]|naci[oó]\s+en|falleci[oó]|muri[oó]|era\s+el|era\s+el\s+a[nñ]o|"
    r"por\s+los\s+a[nñ]os|desde|hace\s+\w+\s+a[nñ]os|en\s+su\s+bula|bula|"
    r"acontecimiento|guerra|batalla|revoluci[oó]n|ca[ií]da|aniversario|recordad|"
    r"impres[oa]|publicad[oa]|lleg[oó]|se\s+abri[oó]|se\s+fund[oó]|desastres?)\b",
    re.IGNORECASE,
)
OTHER_AUTHOR_CUE = re.compile(
    r"\b(?:p[ií]o\s+(?:ix|x)|le[oó]n\s+xiii|pont[ií]fice|cardenal|bula|enc[ií]clica)\b",
    re.IGNORECASE,
)

# Captura años en fórmulas de cierre aun cuando el día esté escrito con palabras:
# "a los diez días del mes de Junio del año del Señor de 1880".
YEAR_IN_FORMULA_RE = re.compile(
    r"(?:dada|dado|fechad[ao]|firmad[ao]|expedid[ao])[^\n]{0,260}?"
    r"(?<!\d)((?:18|19)\d{2})(?!\d)",
    re.IGNORECASE,
)
YEAR_OF_LORD_RE = re.compile(
    r"(?:a[nñ]o\s+del\s+se[nñ]or\s+de|del\s+a[nñ]o\s+del\s+se[nñ]or\s+de)\s*"
    r"(?<!\d)((?:18|19)\d{2})(?!\d)",
    re.IGNORECASE,
)


def _classify_candidate(text: str, start: int, end: int, iso: str, year: int, base_score: int, base_reason: str) -> DateCandidate:
    n = max(len(text), 1)
    pos = start / n
    ev = _context(text, start, end, 220)
    ev_norm = strip_accents(ev.lower())
    score = base_score
    reasons = [base_reason]
    role = "weak"

    # Fechas anteriores al comienzo del corpus autoral o posteriores a su muerte
    # se conservan como evidencia histórica, pero nunca se infieren como fecha del documento.
    if year < 1865 or year > 1921:
        score -= 50
        reasons.append("fuera del rango autoral 1865-1921")
        role = "historical"

    # Ventanas más amplias para detectar encabezados/cierres documentales.
    h0 = max(0, start - 500)
    h1 = min(len(text), end + 500)
    window = text[h0:h1]
    near_end = start >= max(0, len(text) - 2200)
    near_start = start <= min(2200, int(0.18 * len(text)))

    close_cue = bool(DOC_CLOSE_CUE.search(window))
    header_cue = bool(DOC_HEADER_CUE.search(window))
    signature_near = bool(AUTHOR_SIGNATURE_CUE.search(text[max(0, start-350):min(len(text), end+700)]))
    narrative = bool(NARRATIVE_CUE.search(window))

    if near_end and close_cue:
        score += 12
        reasons.append("fórmula explícita de cierre")
        role = "document"
        if signature_near:
            score += 6
            reasons.append("firma de Ignacio cercana")

    if near_start and header_cue:
        score += 12
        reasons.append("fecha anclada al encabezado/tipo documental")
        role = "document"

    # Una fecha completa muy al inicio, asociada a un título de discurso/carta, suele ser documental.
    if near_start and re.search(r"\b(?:homil[ií]a|serm[oó]n|paneg[ií]rico|discurso|carta|pastoral|oraci[oó]n|alocuci[oó]n)\b", window, re.IGNORECASE):
        score += 4
        reasons.append("género documental cerca del encabezado")

    # Cierre con "año del Señor" + firma del autor, aunque no se haya podido parsear el día.
    if near_end and re.search(r"a[nñ]o\s+del\s+se[nñ]or", window, re.IGNORECASE) and signature_near:
        score += 8
        reasons.append("fórmula 'año del Señor' + firma")
        role = "document"

    # Penalizaciones de contexto narrativo. No anulamos una fecha documental explícita,
    # pero sí evitamos convertir recuerdos, bulas citadas, nacimientos, etc. en fecha del capítulo.
    if narrative and role != "document":
        score -= 10
        reasons.append("contexto narrativo/histórico")
        role = "historical"
    if OTHER_AUTHOR_CUE.search(window) and role != "document":
        score -= 4
        reasons.append("posible documento/acto de tercero citado")

    return DateCandidate(iso, year, score, pos, ev, "; ".join(reasons), role)


def infer_dates(text: str) -> list[DateCandidate]:
    cands: list[DateCandidate] = []

    # 1) Fechas completas numéricas en español.
    for m in FULL_DATE_RE.finditer(text):
        day = int(m.group(1))
        month_name = strip_accents(m.group(2).lower())
        year = int(m.group(3))
        month = MONTHS.get(month_name)
        if month is None:
            continue
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        cands.append(_classify_candidate(text, m.start(), m.end(), d.isoformat(), year, 3, "fecha completa"))

    # 2) Años dentro de fórmulas explícitas de cierre. Sirve cuando el día aparece en letras.
    for rx, reason, base in [
        (YEAR_IN_FORMULA_RE, "año en fórmula de datación/cierre", 8),
        (YEAR_OF_LORD_RE, "año en fórmula 'año del Señor'", 6),
    ]:
        for m in rx.finditer(text):
            year = int(m.group(1))
            cands.append(_classify_candidate(text, m.start(1), m.end(1), str(year), year, base, reason))

    # 3) Años aislados se conservan SOLO como pistas para revisión manual. No reciben
    #    suficiente puntaje para ser inferidos automáticamente salvo que queden anclados
    #    por las reglas documentales anteriores.
    for m in YEAR_RE.finditer(text):
        year = int(m.group(1))
        if not (1800 <= year <= 1925):
            continue
        cands.append(_classify_candidate(text, m.start(), m.end(), str(year), year, -2, "año aislado (pista)"))

    # Deduplicación por iso + fragmento; prioriza candidatos documentales y mayor score.
    seen = set()
    out = []
    for c in sorted(cands, key=lambda x: (x.role != "document", -x.score, -x.position, x.iso)):
        key = (c.iso, c.evidence)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def choose_date(cands: list[DateCandidate]) -> tuple[str, str, str, str, str, str]:
    """Devuelve fecha inferida, año, confianza, evidencia, sugerencia débil y evidencia débil.

    Solo se infiere automáticamente si existe una señal documental clara. Las fechas narrativas
    quedan como candidatos para inspección, pero no contaminan el denominador temporal.
    """
    if not cands:
        return "", "", "Sin candidato", "", "", ""

    doc_cands = [c for c in cands if c.role == "document" and c.score >= 8 and 1865 <= c.year <= 1921]
    if doc_cands:
        ranked = sorted(doc_cands, key=lambda x: (-x.score, -x.position))
        top = ranked[0]
        # Si una fecha completa (YYYY-MM-DD) del mismo año está casi empatada con un
        # candidato que sólo aporta el año, conservar la información más específica.
        same_year_full = [c for c in doc_cands if c.year == top.year and len(c.iso) == 10 and c.score >= top.score - 5]
        if same_year_full:
            top = sorted(same_year_full, key=lambda x: (-x.score, -x.position))[0]
        second_score = max([c.score for c in doc_cands if c is not top], default=-999)
        gap = top.score - second_score
        if top.score >= 18 and gap >= 2:
            conf = "Alta"
        elif top.score >= 12:
            conf = "Media"
        else:
            conf = "Baja"
        return top.iso, str(top.year), conf, top.evidence, "", ""

    # Sin fecha documental segura: mostramos el mejor candidato plausible como SUGERENCIA,
    # pero fecha_inferida queda vacía para obligar a revisión humana.
    plausible = [c for c in cands if 1865 <= c.year <= 1921]
    if plausible:
        top = sorted(plausible, key=lambda x: (-x.score, x.role == "historical", -x.position))[0]
        return "", "", "Sin evidencia documental", "", top.iso, top.evidence

    top = cands[0]
    return "", "", "Sin evidencia documental", "", top.iso, top.evidence

def infer_doc_type(text: str) -> tuple[str, str]:
    """Clasificación auxiliar muy conservadora para futura validación de género."""
    sample = text[:2500]
    rules = [
        (r"\bcarta\s+pastoral\b", "Carta pastoral"),
        (r"\bhomil[ií]a\b", "Homilía"),
        (r"\bpaneg[ií]rico\b", "Panegírico"),
        (r"\bserm[oó]n\b", "Sermón"),
        (r"\boraci[oó]n\s+f[uú]nebre\b", "Oración fúnebre"),
        (r"\bdiscurso\b", "Discurso"),
        (r"\balocuci[oó]n\b", "Alocución"),
        (r"\bcarta\b", "Carta"),
    ]
    for pat, lab in rules:
        m = re.search(pat, sample, re.IGNORECASE)
        if m:
            return lab, _context(sample, m.start(), m.end(), 90)
    return "Sin inferir", ""


def period_for_year(year: Optional[int]) -> str:
    if not year:
        return "Sin fecha"
    for a, b, label in PERIODOS:
        if a <= year <= b:
            return label
    return "Fuera de periodización"


def load_validated_dates(path: Optional[Path]) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = r.get("documento_id", "").strip()
            if key:
                out[key] = r
    return out


def final_date_for(doc: dict, validation: Optional[dict]) -> tuple[str, Optional[int], str]:
    if validation:
        decision = strip_accents(validation.get("validacion_fecha", "").strip().upper())
        if decision == "ACEPTAR":
            val = doc.get("fecha_inferida", "")
            yr = int(doc["anio_inferido"]) if doc.get("anio_inferido") else None
            return val, yr, "validada_aceptada"
        if decision == "CORREGIR":
            val = validation.get("fecha_validada", "").strip()
            m = re.search(r"(18|19)\d{2}", val)
            yr = int(m.group(0)) if m else None
            return val, yr, "validada_corregida"
        if decision == "SIN_FECHA":
            return "", None, "validada_sin_fecha"
    val = doc.get("fecha_inferida", "")
    yr = int(doc["anio_inferido"]) if doc.get("anio_inferido") else None
    return val, yr, "inferida_no_validada"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconstruye el corpus analítico de IMO desde capítulos completos, aplicando la delimitación validada por índices y las búsquedas léxicas de v2.")
    ap.add_argument("entrada", type=Path, help="Carpeta raíz que contiene los .txt (se recorre recursivamente).")
    ap.add_argument("--salida", type=Path, default=Path("salida_imo"), help="Directorio de salida.")
    ap.add_argument("--fechas-validadas", type=Path, default=None, help="documentos_revision.csv editado por una persona.")
    args = ap.parse_args()

    root = args.entrada.expanduser().resolve()
    outdir = args.salida.expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    validations = load_validated_dates(args.fechas_validadas)

    all_files = []
    for p in root.rglob("*.txt"):
        m = FILE_RE.search(p.name)
        if m:
            all_files.append((roman_to_int(m.group(1)), int(m.group(2)), p))
    all_files.sort(key=lambda x: (x[0], x[1], str(x[2])))

    if not all_files:
        print("No se encontraron archivos con patrón IMO_<tomo>_cap_<n>.txt", file=sys.stderr)
        return 2

    # Auditoría estructural: no asumimos que los rangos esperados existan sin comprobarlos.
    found_ids = {(t, c) for t, c, _ in all_files}
    expected_ids = {(t, c) for t, (a, b) in CORPUS_RANGES.items() for c in range(a, b + 1)}
    missing = sorted(expected_ids - found_ids)
    duplicate_keys = []
    seen = set()
    for t, c, p in all_files:
        k = (t, c)
        if k in seen:
            duplicate_keys.append((t, c, str(p.relative_to(root))))
        seen.add(k)

    audit_rows = []
    for t, c, p in all_files:
        inc, reason = corpus_status(t, c)
        audit_rows.append({
            "documento_id": f"T{t}.C{c}",
            "tomo": t, "capitulo": c,
            "archivo": str(p.relative_to(root)),
            "incluir_analisis": "SI" if inc else "NO",
            "motivo": reason,
            "estado_archivo": "encontrado",
        })
    for t, c in missing:
        audit_rows.append({
            "documento_id": f"T{t}.C{c}",
            "tomo": t, "capitulo": c, "archivo": "",
            "incluir_analisis": "SI",
            "motivo": "esperado por delimitación del corpus",
            "estado_archivo": "FALTA",
        })
    audit_rows.sort(key=lambda r: (int(r["tomo"]), int(r["capitulo"]), r["estado_archivo"]))
    write_csv(outdir / "auditoria_corpus.csv", audit_rows,
              ["documento_id", "tomo", "capitulo", "archivo", "incluir_analisis", "motivo", "estado_archivo"])

    if missing:
        print(f"ADVERTENCIA: faltan {len(missing)} archivos esperados del corpus. Ver auditoria_corpus.csv", file=sys.stderr)
    if duplicate_keys:
        print(f"ADVERTENCIA: hay {len(duplicate_keys)} identificadores tomo/capítulo duplicados.", file=sys.stderr)

    files = [(t, c, p) for t, c, p in all_files if corpus_status(t, c)[0]]

    docs = []
    occurrences = []
    contexts_map = {}

    for tomo, cap, path in files:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        text = normalize_text(raw)
        paragraphs = split_paragraphs(text)
        cands = infer_dates(text)
        fecha_inf, anio_inf, conf, evidence, fecha_sug, evidencia_sug = choose_date(cands)
        tipo_inf, tipo_ev = infer_doc_type(text)
        documento_id = f"T{tomo}.C{cap}"
        term_counts = {lemma: len(pattern.findall(text)) for lemma, pattern in TERM_PATTERNS.items()}
        doc = {
            "documento_id": documento_id,
            "tomo": tomo,
            "capitulo": cap,
            "archivo": str(path.relative_to(root)),
            "incluir_analisis": "SI",
            "motivo_inclusion": "cuerpo principal validado contra índice",
            "palabras_total": count_words(text),
            "caracteres_total": len(text),
            "parrafos_total": len(paragraphs),
            "n_razon": term_counts["razón"],
            "n_ciencia": term_counts["ciencia"],
            "n_progreso": term_counts["progreso"],
            "n_moderno": term_counts["moderno"],
            "n_modernidad": term_counts["modernidad"],
            "n_ocurrencias_total": sum(term_counts.values()),
            "fecha_inferida": fecha_inf,
            "anio_inferido": anio_inf,
            "confianza_fecha": conf,
            "evidencia_fecha": evidence,
            "fecha_sugerida": fecha_sug,
            "evidencia_sugerida": evidencia_sug,
            "encabezado_revision": re.sub(r"\s+", " ", text[:900]).strip(),
            "cierre_revision": re.sub(r"\s+", " ", text[-900:]).strip(),
            "candidatos_fecha": " || ".join(
                f"{c.iso} [role={c.role}; score={c.score}; pos={c.position:.3f}; {c.reason}] :: {c.evidence}" for c in cands[:15]
            ),
            "validacion_fecha": "",
            "fecha_validada": "",
            "nota_fecha": "",
            "tipo_documento_inferido": tipo_inf,
            "evidencia_tipo": tipo_ev,
            "tipo_documento_validado": "",
            "nota_tipo": "",
        }
        docs.append(doc)

        validation = validations.get(documento_id)
        fecha_final, anio_final, estado_fecha = final_date_for(doc, validation)

        for p_idx, paragraph in enumerate(paragraphs, start=1):
            for lemma, pattern in TERM_PATTERNS.items():
                matches = list(pattern.finditer(paragraph))
                if not matches:
                    continue

                # Contexto agregado: una fila por término y párrafo.
                key = (documento_id, p_idx, lemma)
                contexts_map[key] = {
                    "Chapter": documento_id,
                    "Year": anio_final if anio_final is not None else anio_inf,
                    "Fecha": fecha_final or fecha_inf,
                    "Concept": lemma,
                    "formas_encontradas": " | ".join(m.group(0) for m in matches),
                    "n_ocurrencias_en_parrafo": len(matches),
                    "Lectura Fluida": paragraph,
                    "palabras_capitulo": doc["palabras_total"],
                    "estado_fecha": estado_fecha,
                }

                # Base analítica: una fila por token encontrado.
                for j, m in enumerate(matches, start=1):
                    prev_s, sent, next_s = sentence_for_offset(paragraph, m.start())
                    occurrences.append({
                        "occurrence_id": f"{documento_id}.P{p_idx}.{lemma}.{j}",
                        "documento_id": documento_id,
                        "tomo": tomo,
                        "capitulo": cap,
                        "parrafo": p_idx,
                        "concepto": lemma,
                        "forma_encontrada": m.group(0),
                        "fecha": fecha_final or fecha_inf,
                        "anio": anio_final if anio_final is not None else anio_inf,
                        "periodo": period_for_year(anio_final if anio_final is not None else (int(anio_inf) if anio_inf else None)),
                        "estado_fecha": estado_fecha,
                        "confianza_fecha_inferida": conf,
                        "oracion_anterior": prev_s,
                        "oracion_objetivo": sent,
                        "oracion_siguiente": next_s,
                        "parrafo_completo": paragraph,
                        "palabras_capitulo": doc["palabras_total"],
                        "archivo": str(path.relative_to(root)),
                    })

    # Si hay validación, reflejarla también en documentos_revision.
    for doc in docs:
        vr = validations.get(doc["documento_id"])
        if vr:
            for k in ("validacion_fecha", "fecha_validada", "nota_fecha", "tipo_documento_validado", "nota_tipo"):
                if k in vr:
                    doc[k] = vr.get(k, "")

    doc_fields = [
        "documento_id", "tomo", "capitulo", "archivo", "incluir_analisis", "motivo_inclusion",
        "palabras_total", "caracteres_total", "parrafos_total",
        "n_razon", "n_ciencia", "n_progreso", "n_moderno", "n_modernidad", "n_ocurrencias_total",
        "fecha_inferida", "anio_inferido", "confianza_fecha", "evidencia_fecha", "fecha_sugerida", "evidencia_sugerida", "encabezado_revision", "cierre_revision", "candidatos_fecha",
        "validacion_fecha", "fecha_validada", "nota_fecha",
        "tipo_documento_inferido", "evidencia_tipo", "tipo_documento_validado", "nota_tipo",
    ]
    occ_fields = [
        "occurrence_id", "documento_id", "tomo", "capitulo", "parrafo", "concepto", "forma_encontrada",
        "fecha", "anio", "periodo", "estado_fecha", "confianza_fecha_inferida",
        "oracion_anterior", "oracion_objetivo", "oracion_siguiente", "parrafo_completo", "palabras_capitulo", "archivo",
    ]
    ctx_fields = ["Chapter", "Year", "Fecha", "Concept", "formas_encontradas", "n_ocurrencias_en_parrafo", "Lectura Fluida", "palabras_capitulo", "estado_fecha"]

    write_csv(outdir / "documentos_revision.csv", docs, doc_fields)
    write_csv(outdir / "ocurrencias.csv", occurrences, occ_fields)
    contexts = list(contexts_map.values())
    contexts.sort(key=lambda r: (int(r["Chapter"].split(".")[0][1:]), int(r["Chapter"].split("C")[1]), r["Concept"]))
    write_csv(outdir / "contextos.csv", contexts, ctx_fields)

    # Resumen por periodo y término. Denominador: suma de palabras de TODOS los capítulos del periodo,
    # no solo de los capítulos con hits.
    doc_word_by_period = defaultdict(int)
    doc_count_by_period = defaultdict(int)
    for doc in docs:
        vr = validations.get(doc["documento_id"])
        _, yr, _ = final_date_for(doc, vr)
        per = period_for_year(yr)
        doc_word_by_period[per] += int(doc["palabras_total"])
        doc_count_by_period[per] += 1

    occ_count = Counter((r["periodo"], r["concepto"]) for r in occurrences)
    summary = []
    ordered_periods = [p[2] for p in PERIODOS] + ["Sin fecha", "Fuera de periodización"]
    for per in ordered_periods:
        total_words = doc_word_by_period.get(per, 0)
        total_docs = doc_count_by_period.get(per, 0)
        for term in TERM_PATTERNS:
            nocc = occ_count.get((per, term), 0)
            rate = (10000.0 * nocc / total_words) if total_words else ""
            summary.append({
                "periodo": per,
                "concepto": term,
                "ocurrencias": nocc,
                "palabras_total_periodo": total_words,
                "documentos_periodo": total_docs,
                "ocurrencias_por_10000_palabras": rate,
            })
    write_csv(outdir / "resumen_inferido.csv", summary,
              ["periodo", "concepto", "ocurrencias", "palabras_total_periodo", "documentos_periodo", "ocurrencias_por_10000_palabras"])

    metadata = {
        "programa": "reconstruir_imo_v3.py",
        "version": VERSION,
        "unidad_ocurrencias": "token léxico: una fila por match regex",
        "unidad_contextos": "una fila por término y párrafo",
        "denominador": "palabras alfabéticas de todos los capítulos asignados al periodo",
        "periodos": [{"inicio": a, "fin": b, "label": lab} for a, b, lab in PERIODOS],
        "familias_lexicas": {k: v.pattern for k, v in TERM_PATTERNS.items()},
        "normalizacion": "recomposición de palabras partidas por guion de fin de línea; unión de saltos simples dentro de párrafos",
        "fecha": "heredada de v2: solo infiere automáticamente fechas ancladas a encabezado/cierre documental; menciones históricas quedan como sugerencias para validación humana",
        "corpus_rangos_inclusivos": {str(t): [a, b] for t, (a, b) in CORPUS_RANGES.items()},
        "corpus_regla": "solo cuerpo principal; prefacios/prologos/dedicatorias/advertencias, notas y documentos, apendices y epilogos quedan fuera segun auditoria manual de indices",
        "n_documentos": len(docs),
        "n_ocurrencias": len(occurrences),
        "n_contextos": len(contexts),
    }
    (outdir / "metadata_metodo.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: {len(docs)} capítulos incluidos, {len(occurrences)} ocurrencias, {len(contexts)} contextos.")
    print(f"Archivos detectados en total: {len(all_files)}")
    print(f"Archivos esperados por los rangos del corpus: {len(expected_ids)}")
    print(f"Faltantes esperados: {len(missing)}")
    print(f"Salida: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
