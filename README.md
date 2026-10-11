# IMO — Reproducibility Materials for the Montes de Oca Study

This repository contains the code, derived datasets, validation files, and methodological documentation associated with the article:

**“Two Scales of the Modern: Distant and Close Reading of Modernity in Mexico through the Case of Ignacio Montes de Oca y Obregón”**

The study combines three distinct empirical components:

1. lexical analysis of the **Hemeroteca Nacional Digital de México (HNDM)**;
2. computational reconstruction and thematic classification of **Ignacio Montes de Oca's personal library**;
3. document-level lexical analysis of the eight-volume **Obras Pastorales y Oratorias**.

The repository is organized accordingly.

## Repository structure

### `hndm/`

Materials used for the analysis of lexical prevalence in the Hemeroteca Nacional Digital de México.

This directory includes:

- preserved query metadata and manifests;
- annual page-level counts;
- normalized annual and decadal series;
- scripts used to parse preserved HNDM search-result snapshots;
- scripts used to normalize lexical prevalence against the annual number of searchable pages.

The principal measure reported in the article is the number of matching pages per 10,000 searchable pages under the same country and language filters.

The analysis uses the lexical families associated with:

- `moderno`
- `ciencia`
- `razon`
- `progreso`
- `modernidad`

together with supplementary morphological variants used as robustness checks.

---

### `library/`

Materials used to reconstruct the surviving catalog of Ignacio Montes de Oca's personal library.

The original inventory contains **824 catalog records** with heterogeneous and sometimes incomplete bibliographic metadata.

The workflow combines:

- multisource bibliographic searches;
- computational candidate ranking;
- a second-stage rescue procedure for unresolved records;
- human validation of bibliographic matches;
- LLM-assisted Brunet classification;
- human validation of the resulting thematic classifications.

The final bibliographic confidence categories are:

| Grade | Interpretation |
|---|---|
| A | Robust identification |
| B | Highly probable match |
| C | Tentative identification |
| D | Unverifiable record |

After human validation, 788 of the 824 records were bibliographically identified with some degree of confidence.

The complete inventory was subsequently classified according to the five major branches of the Brunet bibliographic system:

- Belles-lettres
- History, Geography and Biography
- Sciences and Arts
- Theology
- Jurisprudence

LLM outputs are treated only as provisional annotations. The classifications reported in the article correspond to human-validated assignments.

---

### `obras_pastorales/`

Materials used for the analysis of the eight volumes of Ignacio Montes de Oca's *Obras Pastorales y Oratorias*.

The corpus is processed at the level of individual documents.

The scripts:

- define the analytic corpus;
- normalize line-end hyphenation and paragraph structure;
- count alphabetic words;
- identify occurrences of five predefined lexical families;
- construct document-level and paragraph-level output tables;
- assign documents to the historical periods used in the article;
- calculate normalized lexical prevalence;
- perform the genre-control analysis reported in Appendix C.

The lexical families are:

- reason: `razón`, `razon`, `razones`
- science: `ciencia`, `ciencias`
- progress: `progreso`, `progresos`
- modern: `moderno`, `moderna`, `modernos`, `modernas`
- modernity: `modernidad`, `modernidades`

The canonical corpus contains **227 documents**. The principal diachronic analysis uses **220 dated documents** assigned to four periods:

- 1865–1879
- 1880–1895
- 1896–1905
- 1906–1921

Document dates and broad genre categories were manually reviewed.

---

## Reproducibility

The repository is intended to make the quantitative and computational components of the article inspectable and reproducible.

It provides the scripts and derived research files necessary to reproduce the principal:

- figures;
- normalized frequency tables;
- bibliographic matching summaries;
- Brunet distributions;
- document-level lexical counts;
- robustness checks;
- genre-control models.

Some analyses combine automated procedures with explicit human validation. Where this occurs, the corresponding validation files are included so that the distinction between machine-generated proposals and final research decisions remains visible.

## Primary sources and redistribution

Not all primary source materials can necessarily be redistributed.

Where source images, copyrighted texts, or other restricted materials are not included, the repository documents:

- their provenance;
- the procedures used to obtain or process them;
- the derived data used in the published analysis.

Derived datasets should therefore not be interpreted as substitutes for the original historical sources.

## Methodological principle

Computational outputs in this project are treated as tools for organizing, measuring, and inspecting historical evidence rather than as autonomous historical interpretations.

Bibliographic matches, thematic classifications, document dates, and genre assignments were subjected to human validation where appropriate.

The repository should therefore be read together with the methodological appendices of the article.

## Citation

If you use these materials, please cite the associated article.

**Article citation:**  
`[Full citation to be inserted after publication]`

**Repository:**  
`https://github.com/LEDSUASLP/IMO.git`

Laboratorio de Estructuras y Dinámicas Sociales (LEDS)  
Universidad Autónoma de San Luis Potosí
