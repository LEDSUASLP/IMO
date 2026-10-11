#!/usr/bin/env python3
"""Rescate reproducible de identificaciones de OBRAS en la biblioteca IMO.

Entradas: XLSM Revision_D_253 (lectura sola) o CSV con id_registro,
 titulo_original, autor_original. Salida principal: un libro .xlsx
 con hojas de revisión; CSV privados para checkpoints y reanudación. No modifica el original.

Una coincidencia automática es una HIPÓTESIS, nunca prueba de la lectura del libro.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from rapidfuzz import fuzz

ENDPOINTS = {
    'openlibrary': 'https://openlibrary.org/search.json',
    'internet_archive': 'https://archive.org/advancedsearch.php',
    'google_books': 'https://www.googleapis.com/books/v1/volumes',
    'gutenberg': 'https://gutendex.com/books',
}
AGENT = 'IMO-biblioteca-research/2.0 (bibliographic matching; respectful API use)'


def clean(v):
    if v is None: return ''
    s = str(v).strip()
    return '' if s.lower() in ('nan','none','null') else re.sub(r'\s+', ' ', s)


def norm(s):
    s = unicodedata.normalize('NFKD', clean(s).casefold())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s)).strip()


def author_key(s):
    v = norm(s)
    if ',' in clean(s):
        parts = clean(s).split(',',1)
        v = norm(parts[1]+' '+parts[0])
    return v


def year_from(s):
    match=re.search(r'\b(1[5-9]\d\d|20\d\d)\b',clean(s))
    return int(match.group()) if match else None


def pick_url(notes):
    m=re.search(r'https?://[^\s<>\]\)\";]+',clean(notes))
    return m.group().rstrip('.,') if m else ''


def csv_out(path, rows, fields):
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        w.writeheader();w.writerows(rows)


def read_catalog(path, sheet):
    if path.suffix.lower() in ('.xlsm','.xlsx'):
        import pandas as pd
        df=pd.read_excel(path,sheet_name=sheet,engine='openpyxl',dtype=object)
        rows=df.where(df.notnull(),None).to_dict('records')
    elif path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    else: raise ValueError('Entrada debe ser .csv, .xlsx o .xlsm')
    for r in rows:
        if 'id_registro' not in r or 'titulo_original' not in r:
            raise ValueError('Faltan columnas id_registro, titulo_original')
        r['id_registro']=clean(r.get('id_registro')).removesuffix('.0')
    ids=[r['id_registro'] for r in rows]
    if len(ids)!=len(set(ids)):raise ValueError('id_registro repetido en archivo de entrada')
    return rows


def build_queries(title,author):
    t=clean(title);a=clean(author)
    if not t:return []
    # Algunos títulos se documentan abreviados: consultas específicas y relajadas.
    words=t.split()
    short=' '.join(words[:9]); short2=' '.join(words[:5])
    variations=[(t+' '+a).strip(),short+' '+a if a else '', t,
                short, short2+' '+a if a else '', short2]
    seen=set();out=[]
    for q in variations:
        q=clean(q)
        if len(norm(q))>=4 and norm(q) not in seen:
            out.append(q);seen.add(norm(q))
    return out[:5]


class Client:
    def __init__(self, dbfile, delay=0.8, timeout=20, refresh=False):
        self.db=sqlite3.connect(dbfile)
        self.db.execute('CREATE TABLE IF NOT EXISTS cache (cache_key TEXT PRIMARY KEY, status INTEGER, body TEXT, fetched_utc TEXT)')
        self.db.commit()
        self.s=requests.Session()
        self.s.headers.update({'User-Agent':AGENT,'Accept':'application/json'})
        self.delay=delay;self.timeout=timeout;self.refresh=refresh
        self.last={}
        self.disabled_sources={}  # fuente -> motivo; no volver a consultar en esta ejecución
    def get(self,source,params,headers=None):
        if source in self.disabled_sources:
            return 0,{},'disabled:'+self.disabled_sources[source]
        url=ENDPOINTS[source]
        key=hashlib.sha256(json.dumps([url,params],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if not self.refresh:
            row=self.db.execute('SELECT status,body FROM cache WHERE cache_key=?',(key,)).fetchone()
            if row:
                try:return row[0],json.loads(row[1]),'cache'
                except ValueError:pass
        since=time.monotonic()-self.last.get(source,0)
        if since<self.delay:time.sleep(self.delay-since)
        for attempt in range(3):
            try:
                self.last[source]=time.monotonic()
                response=self.s.get(url,params=params,headers=headers,timeout=self.timeout)
                status=response.status_code
                if status == 429:
                    try:
                        error=response.json().get('error',{})
                        details=error.get('details',[])
                        zero=any(str(d.get('metadata',{}).get('quota_limit_value',''))=='0'
                                 for d in details if isinstance(d,dict))
                        msg=str(error.get('message',''))
                    except ValueError:
                        zero=False;msg=''
                    # Una cuota diaria agotada tampoco se resuelve reintentando inmediatamente.
                    reason='cuota_cero' if zero else 'http_429'
                    self.disabled_sources[source]=reason
                    print(f'AVISO: {source} desactivada ({reason}). {msg[:160]}',file=sys.stderr,flush=True)
                    return status,{},'disabled:'+reason
                if status in (500,502,503,504):
                    time.sleep(min(5,2**attempt));continue
                if status!=200:return status,{},'http_error'
                obj=response.json()
                self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)',
                    (key,status,json.dumps(obj,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
                self.db.commit()
                return status,obj,'live'
            except (requests.RequestException,ValueError):time.sleep(2**attempt)
        return 0,{},'network_error'



def build_web_queries(title,author):
    """Variantes tolerantes a títulos históricos mal transcritos.

    Las palabras originales siempre se conservan en los resultados para trazabilidad.
    """
    title=clean(title); author=clean(author)
    tokens=norm(title).split()
    if not tokens:return []
    # Las búsquedas entre comillas usan fragmentos distintivos, no todo el título OCR.
    fragments=[" ".join(tokens[:min(6,len(tokens))]),
               " ".join(tokens[:min(3,len(tokens))])]
    author_tokens=author_key(author).split()
    author_short=' '.join(author_tokens[-2:]) if author_tokens else ''
    guesses=[f'"{fragments[0]}" {author_short}',
             f'{fragments[0]} {author_short}',
             f'"{fragments[1]}" {author_short}',
             f'{fragments[1]} {author_short}',
             f'{fragments[0]} book',
             f'{fragments[0]} site:archive.org OR site:books.google.com OR site:gutenberg.org']
    out=[];seen=set()
    for q in guesses:
        q=clean(q)
        if q and q not in seen:out.append(q);seen.add(q)
    return out


class FreeWebSearch:
    """Búsqueda web de bajo volumen, con caché persistente y circuito de errores.

    ddgs no es una API oficial: resultados y límites pueden cambiar sin aviso.
    No se utiliza para decisiones automáticas de existencia de una obra.
    """
    def __init__(self, cache_path,delay=1.5):
        try:
            from ddgs import DDGS
        except ImportError as e:
            raise SystemExit('Falta ddgs. Instala: pip install ddgs') from e
        self.engine=DDGS(timeout=12)
        self.db=sqlite3.connect(cache_path)
        self.db.execute('CREATE TABLE IF NOT EXISTS web_cache (query TEXT PRIMARY KEY, body TEXT, fetched_utc TEXT)')
        self.db.commit()
        self.delay=delay
        self.last=0.0
        self.failures=0
        self.disabled=False

    def search(self,query):
        if self.disabled:return [],'disabled:errores_repetidos'
        prior=self.db.execute('SELECT body FROM web_cache WHERE query=?',(query,)).fetchone()
        if prior:
            try:return json.loads(prior[0]),'cache'
            except ValueError:pass
        since=time.monotonic()-self.last
        if since<self.delay:time.sleep(self.delay-since)
        self.last=time.monotonic()
        try:
            hits=list(self.engine.text(query,max_results=8) or [])
            filtered=[]
            for hit in hits:
                link=clean(hit.get('href') or hit.get('url'))
                if not link.startswith(('https://','http://')):continue
                filtered.append(dict(title=clean(hit.get('title')),
                                     href=link,body=clean(hit.get('body'))))
            self.db.execute('INSERT OR REPLACE INTO web_cache VALUES (?,?,?)',
                            (query,json.dumps(filtered,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            self.db.commit()
            self.failures=0
            return filtered,'live'
        except Exception as exc:
            # La biblioteca DDGS puede cambiar excepciones entre versiones.
            self.failures+=1
            reason=f'{type(exc).__name__}: {str(exc)[:120]}'
            print(f'AVISO: búsqueda web falló ({self.failures}/3): {reason}',file=sys.stderr,flush=True)
            if self.failures>=3:
                self.disabled=True
                print('AVISO: ddgs desactivado por errores consecutivos.',file=sys.stderr)
            return [],'disabled:errores_repetidos' if self.disabled else 'error:'+type(exc).__name__


def candidate(source,title,author,year,url,query,ident='',notes='',catalog_year=''):
    return dict(fuente=source,titulo_candidato=clean(title),autor_candidato=clean(author),
        anio_candidato=clean(year),url_fuente=clean(url),query_usada=query,
        id_fuente=clean(ident),detalles=clean(notes),anio_original=clean(catalog_year))


def search_source(client,source,query,google_key=''):
    if source=='openlibrary':
        status,data,where=client.get(source,{'q':query,'limit':10,'fields':'key,title,author_name,first_publish_year,edition_key'})
        items=data.get('docs',[])
        out=[candidate(source,d.get('title'),'; '.join(d.get('author_name',[]) or []),
                d.get('first_publish_year'), 'https://openlibrary.org'+d.get('key',''),query,d.get('key')) for d in items]
    elif source=='internet_archive':
        # NO incluir el autor dentro de un campo title forzado.
        # Solr permite consulta general y títulos parciales.
        # OR en palabras distintivas: errores OCR no deben anular todas las coincidencias.
        # La similitud se evalúa después contra el catálogo, no aquí.
        terms=[w for w in norm(query).split() if len(w)>=4][:5]
        q=' OR '.join('title:'+w+'*' for w in terms) if terms else 'title:'+norm(query).split()[0]
        status,data,where=client.get(source,{'q':'('+q+') AND mediatype:texts',
                'fl[]':['identifier','title','creator','year','date'],'rows':10,'output':'json'})
        items=data.get('response',{}).get('docs',[])
        out=[candidate(source,d.get('title'),'; '.join(d['creator']) if isinstance(d.get('creator'),list) else d.get('creator',''),
                 d.get('year',d.get('date','')), 'https://archive.org/details/'+d.get('identifier',''),query,d.get('identifier')) for d in items]
    elif source=='google_books':
        params={'q':query,'maxResults':10,'printType':'books'}
        if google_key:params['key']=google_key
        status,data,where=client.get(source,params)
        out=[]
        for d in data.get('items',[]):
            v=d.get('volumeInfo',{})
            out.append(candidate(source, (v.get('title','')+' '+v.get('subtitle','')).strip(),
                  '; '.join(v.get('authors',[]) or []),v.get('publishedDate'),
                  v.get('infoLink','https://books.google.com/books?id='+d.get('id','')),query,d.get('id')))
    elif source=='gutenberg':
        status,data,where=client.get(source,{'search':query})
        out=[]
        for d in data.get('results',[])[:20]:
            name='; '.join(x.get('name','') for x in d.get('authors',[]))
            out.append(candidate(source,d.get('title'),name,'',
                'https://www.gutenberg.org/ebooks/'+str(d['id']),query,d['id'],
                '; '.join(d.get('subjects',[])[:3])))
    else:raise ValueError(source)
    return out,status,where


def score(r,c):
    t=norm(r.get('titulo_original')); ct=norm(c['titulo_candidato'])
    a=author_key(r.get('autor_original')); ca=author_key(c['autor_candidato'])
    if not t or not ct:return (0,0,0,'sin_titulo')
    st=0.45*fuzz.token_sort_ratio(t,ct)+0.40*fuzz.token_set_ratio(t,ct)+0.15*fuzz.ratio(t,ct)
    # Partial matching útil para títulos largos, pero NO suficiente para confirmar un título genérico.
    if len(t.split())>=4:
        st=max(st,min(93,fuzz.partial_ratio(t,ct)*0.93))
    sa=fuzz.token_set_ratio(a,ca) if a and ca else None
    conflict=bool(a and ca and sa<42)
    # Año de edición deliberadamente EXCLUIDO de la clasificación de obra.
    if sa is None:total=st*0.84
    else:total=0.73*st+0.27*sa
    if conflict:total=min(total,52)
    return round(total,1),round(st,1),round(sa,1) if sa is not None else '',('autor_en_conflicto' if conflict else '')


def label(best, r, distinct_sources):
    if not best:return 'SIN_CANDIDATO'
    if best['conflicto'] or best['score_titulo']<65:return 'NO_CONCLUYENTE'
    generic=len(norm(r.get('titulo_original')).split())<=2
    if (best['score_total']>=91 and best['score_titulo']>=88 and best['score_autor']!=''
        and best['score_autor']>=84 and not generic):return 'PROBABLE_ALTA'
    if best['score_total']>=77 and best['score_titulo']>=76:return 'PROBABLE_MEDIA'
    return 'REVISAR'


# ==============================================================
# PIPELINE UNIFICADO: catálogo original -> cotejo A/B/C/D -> rescate D
# -> libro único de validación bibliográfica humana.
# ==============================================================
from collections import Counter
from urllib.parse import urlparse


def auto_grade(record, candidates):
    """Grado de recuperación de OBRA (nunca edición), basado en título y autor.

    No confunde hallazgo de URL web con identificación bibliográfica.
    """
    eligible=[c for c in candidates if c['fuente']!='ddgs_web' and c['fuente']!='evidencia_manual']
    eligible.sort(key=lambda c:c['score_total'],reverse=True)
    if not eligible:return ('D',None,'No se obtuvo candidato de catálogo bibliográfico')
    best=eligible[0]; st=best['score_titulo']; sa=best['score_autor']
    title=norm(record.get('titulo_original',''))
    generic=(len(title.split())<=1 or title in ('obras','works','opera','history','historia','poems','sermons','oeuvres'))
    if best['conflicto'] or st<62:return ('D',best,'Conflicto de autor o semejanza insuficiente')
    if (not generic and st>=91 and sa!='' and sa>=83 and best['score_total']>=88):
        return ('A',best,'Título y autor altamente concordantes: requiere verificación humana')
    if st>=78 and ((sa!='' and sa>=65) or (sa=='' and st>=91 and not generic)):
        return ('B',best,'Identificación probable: requiere verificación humana')
    if st>=65:return ('C',best,'Candidato ambiguo: requiere revisión humana')
    return ('D',best,'Candidato débil: se activa rescate')


def get_original(path, sheet_name):
    from artifact_tool import Blob,SpreadsheetFile
    wb=SpreadsheetFile.import_xlsx(Blob.load(str(path)))
    try:ws=wb.worksheets.get_item(sheet_name)
    except Exception:ws=wb.worksheets.get_item_at(0)
    matrix=ws.get_range('A1:D1500').values
    headers=[clean(x).upper() for x in matrix[0]]
    required=('AUTOR','TITULO','LUGAR DE PUBLICACION','AÑO')
    if tuple(headers)!=required:
        raise ValueError('Columnas esperadas A:D: '+', '.join(required)+'; recibidas: '+str(headers))
    records=[]
    for excel_row,values in enumerate(matrix[1:],2):
        if not any(clean(v) for v in values):continue
        r={'id_registro':len(records)+1,'fila_excel_original':excel_row,
           'autor_original':clean(values[0]),'titulo_original':clean(values[1]),
           'lugar_original':clean(values[2]),'anio_original':clean(values[3])}
        records.append(r)
    return records


def export_xlsx(path, records, all_candidates, logs, meta):
    from artifact_tool import Workbook,SpreadsheetFile
    wb=Workbook.create()
    fields=[
      'id_registro','fila_excel_original','titulo_original','autor_original',
      'lugar_original','anio_original','grado_inicial_auto','grado_post_rescate_auto',
      'rescate_aplicado','estado_fuentes','candidato_1_titulo','candidato_1_autor',
      'candidato_1_fuente','candidato_1_score','candidato_1_url',
      'candidato_2_titulo','candidato_2_autor','candidato_2_fuente',
      'candidato_2_score','candidato_2_url','candidato_3_titulo',
      'candidato_3_autor','candidato_3_fuente','candidato_3_score',
      'candidato_3_url',
      'candidato_elegido_humano','grado_humano','url_evidencia_humana',
      'justificacion_match_humana',
      'revisor','estado_revision'
    ]
    ws=wb.worksheets.add('Revision_biblioteca')
    matrix=[fields]
    for r in records:
        vals=[r.get(f,'') for f in fields]
        matrix.append(vals)
    ws.get_range_by_indexes(0,0,len(matrix),len(fields)).values=matrix
    ws.freeze_panes.freeze_rows(1)
    ws.get_range_by_indexes(0,0,1,len(fields)).format={
        'fill':'#183153','font':{'bold':True,'color':'#FFFFFF'},'row_height':32,'wrap_text':True}
    for index,f in enumerate(fields):
        width=43 if ('titulo' in f or 'justificacion' in f) else (35 if 'url' in f else 20)
        ws.get_range_by_indexes(0,index,len(matrix),1).format.column_width=width
    for f,values in [('grado_humano',['A','B','C','D']),
                     ('candidato_elegido_humano',['1','2','3','OTRO','NINGUNO']),
                     ('estado_revision',['PENDIENTE','VALIDADO','REVISAR'])]:
        col=fields.index(f)
        if len(matrix)>1:
            rng=ws.get_range_by_indexes(1,col,len(matrix)-1,1)
            rng.data_validation={'rule':{'type':'list','values':values}}
            rng.format.fill='#FFF2CC'
    # Enlaces clicables en Excel/Sheets mediante HYPERLINK (no simples cadenas).
    for f in ('candidato_1_url','candidato_2_url','candidato_3_url'):
        col=fields.index(f)
        for idx,r in enumerate(records,1):
            url=clean(r.get(f))
            if url.startswith(('https://','http://')):
                safe=url.replace('"','""')
                ws.get_cell(idx,col).formulas=[[f'=HYPERLINK("{safe}","Abrir enlace")']]
    cf=['id_registro','fase','rank','fuente','titulo_candidato','autor_candidato','anio_candidato',
        'score_total','score_titulo','score_autor','conflicto','url_fuente','query_usada','detalles']
    cs=wb.worksheets.add('Todos_candidatos')
    candrows=[cf]+[[c.get(k,'') for k in cf] for c in all_candidates]
    cs.get_range_by_indexes(0,0,len(candrows),len(cf)).values=candrows
    cs.freeze_panes.freeze_rows(1)
    cs.get_range_by_indexes(0,0,1,len(cf)).format={'fill':'#183153','font':{'bold':True,'color':'#FFFFFF'}}
    cs.get_range('B:B').format.column_width=18
    cs.get_range('E:F').format.column_width=48
    cs.get_range('L:L').format.column_width=42
    for i,c in enumerate(all_candidates,1):
        url=clean(c.get('url_fuente'))
        if url.startswith(('https://','http://')):
            cs.get_cell(i,cf.index('url_fuente')).formulas=[[f'=HYPERLINK("{url.replace(chr(34),chr(34)*2)}","Abrir enlace")']]
    qf=['id_registro','fase','fuente','query','status_http','estado','n_candidatos']
    qs=wb.worksheets.add('Consultas')
    qrows=[qf]+[[l.get(k,'') for k in qf] for l in logs]
    qs.get_range_by_indexes(0,0,len(qrows),len(qf)).values=qrows
    qs.freeze_panes.freeze_rows(1)
    qs.get_range('D:D').format.column_width=55
    qs.get_range('A1:G1').format={'fill':'#183153','font':{'bold':True,'color':'#FFFFFF'}}
    guide=wb.worksheets.add('Guia_y_metodo')
    docs=[
       ['Campo','Interpretación'],
       ['Objeto','Identificar obras registradas en el catálogo, no edición exacta ni prueba de lectura'],
       ['A/B/C/D automático','Propuesta basada en similitud de título y autor; jamás validación final'],
       ['Rescate','Solo para grado inicial D; fuentes estructuradas + búsqueda web gratuita opcional'],
       ['Web','Las páginas DDGS son pistas; no promueven automáticamente un caso a A o B'],
       ['Fuente original',str(meta.get('input'))],
       ['N registros',str(len(records))],
       ['Ejecución UTC',meta.get('utc','')],
       ['Grados propuestos',str(dict(Counter(r.get('grado_post_rescate_auto') for r in records)))],
       ['Revisión','Completar grado_humano, candidato elegido, evidencia y justificación'],
       ['Reanudación','No reemplazar con una nueva exportación una hoja ya revisada en Google Sheets'],
    ]
    guide.get_range_by_indexes(0,0,len(docs),2).values=docs
    guide.get_range('A:A').format.column_width=25
    guide.get_range('B:B').format.column_width=115
    guide.get_range('A1:B1').format={'fill':'#183153','font':{'bold':True,'color':'#FFFFFF'}}
    path.parent.mkdir(parents=True,exist_ok=True)
    SpreadsheetFile.export_xlsx(wb).save(str(path))


def main():
    ap=argparse.ArgumentParser(description='Pipeline bibliográfico IMO: A/B/C/D + rescate D + revisión humana')
    ap.add_argument('--input',type=Path,default=Path('MONTES DE OCA_library.xlsx'))
    ap.add_argument('--sheet',default='COLEC. MONTES DE OCA')
    ap.add_argument('--out',type=Path,default=Path('resultado_biblioteca'))
    ap.add_argument('--max-records',type=int,default=0)
    ap.add_argument('--no-web',action='store_true',help='Omitir DDGS, que es no oficial')
    ap.add_argument('--google',action='store_true',help='Google Books opcional; deshabilitado por defecto')
    ap.add_argument('--offline',action='store_true',help='Prueba de formato sin solicitudes de red')
    ap.add_argument('--restart',action='store_true',help='Reprocesar registros guardados (con caché de consultas)')
    ap.add_argument('--refresh',action='store_true',help='No usar caché de consultas HTTP')
    ap.add_argument('--delay',type=float,default=1.0)
    ap.add_argument('--max-queries',type=int,default=2)
    ap.add_argument('--web-max-queries',type=int,default=2)
    args=ap.parse_args()
    if not args.input.exists():ap.error('No existe --input')
    args.out.mkdir(parents=True,exist_ok=True)
    records=get_original(args.input,args.sheet)
    if args.max_records:records=records[:args.max_records]
    checkpoint=args.out/'procesados.jsonl'
    prior={}
    # Un checkpoint nunca se reutiliza con otro catálogo o parámetros de búsqueda.
    fingerprint=hashlib.sha256(args.input.read_bytes()).hexdigest()
    run_options={'sha256':fingerprint,'sheet':args.sheet,'google':args.google,
                 'web':not args.no_web and not args.offline,'offline':args.offline,
                 'max_queries':args.max_queries,'web_max_queries':args.web_max_queries}
    meta_path=args.out/'ejecucion.json'
    if args.restart:
        checkpoint.unlink(missing_ok=True)
    elif meta_path.exists():
        previous=json.loads(meta_path.read_text(encoding='utf-8'))
        if previous!=run_options:
            ap.error('Configuración o catálogo distinto en --out. Usa otra carpeta o --restart')
    elif checkpoint.exists():
        ap.error('Checkpoint sin metadatos; utiliza otra carpeta o --restart')
    meta_path.write_text(json.dumps(run_options,ensure_ascii=False,indent=2),encoding='utf-8')
    if checkpoint.exists() and not args.restart:
        for line in checkpoint.read_text(encoding='utf-8').splitlines():
            try:
                obj=json.loads(line);prior[int(obj['record']['id_registro'])]=obj
            except (ValueError,KeyError):pass
    client=Client(args.out/'cache.sqlite',delay=args.delay,timeout=12,refresh=args.refresh)
    web=None if (args.no_web or args.offline) else FreeWebSearch(args.out/'web_cache.sqlite',delay=max(2.5,args.delay))
    sources=['openlibrary','internet_archive']
    if args.google:sources.append('google_books')
    all_candidates=[];logs=[];processed=[]
    for i,r in enumerate(records,1):
        key=r['id_registro']
        if key in prior:
            obj=prior[key];processed.append(obj['record']);all_candidates.extend(obj['candidates']);logs.extend(obj['queries'])
            continue
        candidates=[];qlog=[]
        if not args.offline and r['titulo_original']:
            queries=build_queries(r['titulo_original'],r['autor_original'])[:max(1,args.max_queries)]
            for source in sources:
                for q in queries:
                    hits,status,where=search_source(client,source,q,os.getenv('GOOGLE_BOOKS_API_KEY',''))
                    qlog.append(dict(id_registro=key,fase='inicial',fuente=source,query=q,
                                 status_http=status,estado=where,n_candidatos=len(hits)))
                    for c in hits:c['fase']='inicial'
                    candidates.extend(hits)
        def unique_rank(items):
            seen=set();result=[]
            for c in items:
                u=(c.get('fuente'),c.get('url_fuente') or norm(c.get('titulo_candidato')))
                if u in seen:continue
                seen.add(u)
                sc,st,sa,conf=score(r,c)
                c.update(id_registro=key,score_total=sc,score_titulo=st,score_autor=sa,conflicto=conf)
                result.append(c)
            return sorted(result,key=lambda c:(c['score_total'],c['score_titulo']),reverse=True)
        initial=unique_rank(candidates)
        initial_grade,initial_best,reason=auto_grade(r,initial)
        rescued=False
        if initial_grade=='D' and not args.offline and r['titulo_original']:
            rescued=True
            # Gutenberg se consulta solo para D, con fragmentos en vez del título OCR completo.
            for q in build_queries(r['titulo_original'],r['autor_original'])[:max(1,args.max_queries)]:
                hits,status,where=search_source(client,'gutenberg',' '.join(q.split()[:7]))
                qlog.append(dict(id_registro=key,fase='rescate',fuente='gutenberg',query=q,
                                 status_http=status,estado=where,n_candidatos=len(hits)))
                for c in hits:c['fase']='rescate'
                candidates.extend(hits)
            # Solo si no hay evidencia sólida en catálogos: búsqueda web general.
            stage=unique_rank(candidates); g,b,_=auto_grade(r,stage)
            if g in ('C','D') and web:
                for q in build_web_queries(r['titulo_original'],r['autor_original'])[:max(0,args.web_max_queries)]:
                    hits,state=web.search(q)
                    qlog.append(dict(id_registro=key,fase='rescate_web',fuente='ddgs_web',query=q,
                                     status_http='',estado=state,n_candidatos=len(hits)))
                    for h in hits:
                        c=candidate('ddgs_web',h.get('title'),'', '',h.get('href'),q,notes=h.get('body'))
                        c['fase']='rescate_web';candidates.append(c)
                    if state.startswith('disabled'):break
        ranked=unique_rank(candidates)
        grade,best,reason=auto_grade(r,ranked)
        # Resultado web se muestra aunque su score sea mediocre; no es A/B automático.
        shown=sorted(ranked,key=lambda c:(c['fuente']!='ddgs_web',c['score_total']),reverse=True)
        output=dict(r,grado_inicial_auto=initial_grade,grado_post_rescate_auto=grade,
                    rescate_aplicado='SI' if rescued else 'NO',
                    estado_fuentes='SIN_BUSQUEDA' if args.offline else reason)
        for j,c in enumerate(shown[:3],1):
            for target,source_key in [('titulo','titulo_candidato'),('autor','autor_candidato'),
                       ('fuente','fuente'),('score','score_total'),('url','url_fuente')]:
                output[f'candidato_{j}_{target}']=c.get(source_key,'')
        for k in ['candidato_elegido_humano','grado_humano','url_evidencia_humana',
                  'justificacion_match_humana',
                  'revisor','estado_revision']:output[k]=''
        for rank,c in enumerate(ranked,1):c['rank']=rank
        obj={'record':output,'candidates':ranked,'queries':qlog}
        with checkpoint.open('a',encoding='utf-8') as f:
            f.write(json.dumps(obj,ensure_ascii=False,default=str)+'\n')
        processed.append(output);all_candidates.extend(ranked);logs.extend(qlog)
        print(f'[{i}/{len(records)}] inicial={initial_grade} final={grade} | {r["titulo_original"][:55]}',flush=True)
    result=args.out/'biblioteca_para_validacion.xlsx'
    export_xlsx(result,processed,all_candidates,logs,{'input':str(args.input),'utc':datetime.now(timezone.utc).isoformat()})
    print(f'\n{len(processed)} registros; {len(all_candidates)} candidatos; XLSX: {result}')
    print('ATENCIÓN: la salida contiene PROPUESTAS automáticas, no validaciones humanas.')

if __name__=='__main__':main()
