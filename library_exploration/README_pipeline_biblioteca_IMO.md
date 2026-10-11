# Pipeline bibliográfico unificado — biblioteca de Montes de Oca

## Alcance

Lee **MONTES DE OCA_library.xlsx**, hoja `COLEC. MONTES DE OCA`, con las columnas `AUTOR`, `TITULO`, `LUGAR DE PUBLICACION` y `AÑO`.

1. Conserva las cuatro columnas originales y asigna `id_registro` y fila de Excel.
2. Busca candidatos en Open Library e Internet Archive (y opcionalmente Google Books, que por defecto está desactivado por su cuota).
3. Produce una **clasificación automática A/B/C/D, provisional**, basada en similitud de título y autor **a nivel de obra**, no de edición.
4. Solo para los **D iniciales**, realiza búsquedas de rescate en Project Gutenberg (Gutendex) y, si sigue sin evidencia suficiente, en web general mediante `ddgs` (sin tarjeta ni API key). Los enlaces web no confirman una obra automáticamente.
5. Exporta `biblioteca_para_validacion.xlsx` con las hojas `Revision_biblioteca`, `Todos_candidatos`, `Consultas` y `Guia_y_metodo`. La primera deja vacías las decisiones humanas de identificación, ofrece tres alternativas y enlaces activos, y listas desplegables.

## Instalación (Ubuntu)

```bash
python -m venv .venv
source .venv/bin/activate
pip install requests rapidfuzz ddgs artifact-tool
```

## Prueba corta sin usar Internet

```bash
python pipeline_biblioteca_IMO.py --input 'MONTES DE OCA_library.xlsx' \
  --out prueba_imo --offline --max-records 10
```

## Ejecución completa

```bash
python pipeline_biblioteca_IMO.py --input 'MONTES DE OCA_library.xlsx' --out resultado_imo
```

Por defecto consulta Open Library e Internet Archive en todos los registros, Gutenberg en los D, y la búsqueda web gratuita solo para los D que continúan sin evidencia bibliográfica suficiente.

Para desactivar el buscador web no oficial: `--no-web`.

Para activar Google Books **solo si dispones de cuota válida**: `--google` y, opcionalmente, `export GOOGLE_BOOKS_API_KEY='...'`.

Para un ensayo conectado breve: `--max-records 10 --out prueba_con_red`.

Para cambiar los límites de consulta: `--max-queries 2 --web-max-queries 2 --delay 1.0`.

## Reanudación, evidencias y revisión

- Las respuestas HTTP se cachean en SQLite y los registros completos se guardan por línea en `procesados.jsonl`. Ejecutar de nuevo con los mismos parámetros y carpeta reutiliza el avance.
- **No usar `--restart` con el mismo XLSX ya revisado**: generaría otra salida con las columnas humanas vacías. La validación colaborativa en Google Sheets debe conservarse separadamente de las salidas automáticas.
- El `--out` queda asociado al archivo original y parámetros; al cambiar cualquiera de ellos usa otra carpeta o `--restart`.
- Los enlaces son *candidatos de evidencia*. Abrirlos y revisar título, autor y contenido antes de asignar el grado humano.
- `grado_humano`, `url_evidencia_humana`, `justificacion_match_humana`, `revisor` y `estado_revision` quedan disponibles para la revisión humana.

La clasificación Brunet se realiza por un procedimiento independiente y **no forma parte de este pipeline ni de su Excel de salida**.

## Limitaciones importantes

- No demuestra que Montes de Oca leyera las obras; solo ayuda a cotejar el inventario con testimonios bibliográficos independientes.
- No calibra estadísticamente las probabilidades A/B/C/D; se deben auditar los falsos positivos, falsos negativos y errores por variantes de nombres.
- Open Library e Internet Archive pueden no cubrir impresos de los siglos XVI–XIX; el resultado `D` **no significa inexistente**.
- `ddgs` es un intermediario no oficial y puede ser bloqueado o cambiar sin aviso.
