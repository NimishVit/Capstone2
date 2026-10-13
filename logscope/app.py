"""Flask API + same dashboard. Run locally or deploy the supplied Docker image."""
import io
import json
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from flask import Flask, jsonify, request, send_file, send_from_directory, g
from werkzeug.exceptions import HTTPException

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'dist'))
import pipeline

app=Flask(__name__,static_folder='dist',static_url_path='')
app.config['MAX_CONTENT_LENGTH']=20*1024*1024
DB=os.environ.get('LOGSCOPE_DB',str(ROOT/'runtime'/'runs.sqlite3'))
Path(DB).parent.mkdir(parents=True,exist_ok=True)
LOCK=threading.Lock()

@contextmanager
def connection():
    db=sqlite3.connect(DB, timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created TEXT, dataset TEXT, result TEXT, model BLOB)')
    db.execute('CREATE TABLE IF NOT EXISTS incidents (run_id TEXT, window_id INTEGER, status TEXT, PRIMARY KEY(run_id,window_id))')
    columns={row[1] for row in db.execute('PRAGMA table_info(incidents)')}
    for name,declaration in [('notes',"TEXT NOT NULL DEFAULT ''"),('owner',"TEXT NOT NULL DEFAULT ''"),('updated',"TEXT NOT NULL DEFAULT ''"),('version','INTEGER NOT NULL DEFAULT 0')]:
        if name not in columns:db.execute(f'ALTER TABLE incidents ADD COLUMN {name} {declaration}')
    db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, window_id INTEGER, created TEXT, request_id TEXT, before_json TEXT, after_json TEXT)')
    db.commit()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:db.close()

@contextmanager
def computation():
    if not LOCK.acquire(blocking=False):
        from werkzeug.exceptions import Conflict
        raise Conflict('Another analysis is running. Try again after it completes.')
    try:yield
    finally:LOCK.release()

def json_body():
    body=request.get_json()
    if not isinstance(body,dict):raise ValueError('Request body must be a JSON object.')
    return body

def analysis_body():
    body=json_body()
    text=body.get('text');labels=body.get('labels','');options=body.get('options',{})
    if not isinstance(text,str) or not text.strip():raise ValueError('Upload a nonempty log or CSV file.')
    if len(text.encode())>15*1024*1024:raise ValueError('Log file exceeds 15 MB.')
    if not isinstance(labels,str) or len(labels.encode())>3*1024*1024:raise ValueError('Labels must be CSV text up to 3 MB.')
    if not isinstance(options,dict):raise ValueError('Analysis options must be a JSON object.')
    if not set(options)<=set(pipeline.Config.__dataclass_fields__):raise ValueError('Unknown analysis option.')
    pipeline.Config(**options).validate()
    if not isinstance(body.get('input_name',''),str) or len(body.get('input_name',''))>255:raise ValueError('Input filename must be text up to 255 characters.')
    return body

with connection():pass

@app.before_request
def protect_api():
    g.request_id=str(uuid.uuid4())
    token=os.environ.get('LOGSCOPE_API_TOKEN')
    if token and request.path.startswith('/api/') and request.path!='/api/health':
        import hmac
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+token):
            return jsonify(error='A valid API bearer token is required.'),401

@app.after_request
def headers(response):
    response.headers['X-Request-ID']=getattr(g,'request_id','')
    response.headers['X-Frame-Options']='DENY'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Cache-Control']='no-store' if request.path.startswith('/api') else 'no-cache'
    return response

@app.errorhandler(Exception)
def error(exc):
    if isinstance(exc,HTTPException):return jsonify(error=exc.description,request_id=getattr(g,'request_id','')),exc.code
    if isinstance(exc,(ValueError,TypeError,KeyError)):return jsonify(error=str(exc),request_id=getattr(g,'request_id','')),400
    app.logger.exception('Request failed')
    return jsonify(error='Internal error; consult server logs.',request_id=getattr(g,'request_id','')),500

@app.get('/')
def index():return send_from_directory('dist','index.html')

@app.get('/api/health')
def health():return jsonify(status='ok',runtime='flask',versions=pipeline.versions(),release='1.1.0',auth_required=bool(os.environ.get('LOGSCOPE_API_TOKEN')))

@app.post('/api/analyze')
def analyze():
    body=analysis_body()
    with computation():
        result=pipeline.run_analysis(body['text'],body.get('options'),body.get('labels',''))
        model=pipeline.model_bytes()
    rid=str(uuid.uuid4());result['run_id']=rid
    result['created_at']=datetime.now(timezone.utc).isoformat()
    result['release']='1.1.0'
    result['client']={'input_name':body.get('input_name','Uploaded input'),'input_kind':'upload','created_at':result['created_at'],'release':'1.1.0'}
    with connection() as db:
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,datetime.now(timezone.utc).isoformat(),result['dataset'],json.dumps(result),model))
    return jsonify(result)

@app.post('/api/compare')
def compare():
    body=analysis_body()
    with computation():return jsonify(pipeline.compare_modes(body['text'],body.get('options'),body.get('labels','')))

@app.post('/api/parse')
def parse():
    body=analysis_body()
    if body.get('dataset') not in ('HDFS','BGL','OpenStack'):raise ValueError('Select a supported dataset.')
    return send_file(io.BytesIO(pipeline.structured_csv(body['text'],body['dataset'],body.get('labels','')).encode()),mimetype='text/csv',as_attachment=True,download_name='parsed_logs.csv')

@app.get('/api/runs')
def runs():
    with connection() as db:items=db.execute('SELECT id,created,dataset FROM runs ORDER BY created DESC LIMIT 50').fetchall()
    return jsonify([dict(zip(['id','created','dataset'],x)) for x in items])

@app.get('/api/runs/<rid>')
def run(rid):
    with connection() as db:row=db.execute('SELECT result FROM runs WHERE id=?',(rid,)).fetchone()
    return (jsonify(json.loads(row[0])) if row else (jsonify(error='Run not found'),404))

@app.get('/api/runs/<rid>/model')
def model(rid):
    with connection() as db:row=db.execute('SELECT model FROM runs WHERE id=?',(rid,)).fetchone()
    if not row:return jsonify(error='Run not found'),404
    return send_file(io.BytesIO(row[0]),mimetype='application/octet-stream',as_attachment=True,download_name='logscope_model.joblib')

@app.post('/api/runs/<rid>/incidents/<int:wid>')
def incident(rid,wid):
    body=json_body();status=body.get('status');notes=body.get('notes','');owner=body.get('owner','')
    if status not in ('open','acknowledged','resolved'):raise ValueError('Invalid incident status')
    if not isinstance(notes,str) or len(notes)>2000:raise ValueError('Notes must be text up to 2,000 characters.')
    if not isinstance(owner,str) or len(owner)>100:raise ValueError('Owner must be text up to 100 characters.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT result FROM runs WHERE id=?',(rid,)).fetchone()
        if not row or not any(w['id']==wid and w['anomaly'] and w['split']=='test' for w in json.loads(row[0])['windows']):
            return jsonify(error='Incident not found'),404
        old=db.execute('SELECT status,notes,owner,updated,version FROM incidents WHERE run_id=? AND window_id=?',(rid,wid)).fetchone()
        before=dict(zip(['status','notes','owner','updated','version'],old)) if old else dict(status='open',notes='',owner='',updated='',version=0)
        if 'expected_version' in body and body['expected_version']!=before['version']:
            return jsonify(error='This incident changed. Reopen it before saving.'),409
        after=dict(status=status,notes=notes if 'notes' in body else before['notes'],owner=owner if 'owner' in body else before['owner'],updated=datetime.now(timezone.utc).isoformat(),version=before['version']+1)
        db.execute('INSERT OR REPLACE INTO incidents (run_id,window_id,status,notes,owner,updated,version) VALUES(?,?,?,?,?,?,?)',(rid,wid,after['status'],after['notes'],after['owner'],after['updated'],after['version']))
        db.execute('INSERT INTO audit(run_id,window_id,created,request_id,before_json,after_json) VALUES(?,?,?,?,?,?)',(rid,wid,after['updated'],g.request_id,json.dumps(before),json.dumps(after)))
    return jsonify(after)

@app.get('/api/runs/<rid>/triage')
def triage(rid):
    with connection() as db:
        if not db.execute('SELECT 1 FROM runs WHERE id=?',(rid,)).fetchone():return jsonify(error='Run not found'),404
        rows=db.execute('SELECT window_id,status,notes,owner,updated,version FROM incidents WHERE run_id=?',(rid,)).fetchall()
    return jsonify({str(r[0]):dict(zip(['status','notes','owner','updated','version'],r[1:])) for r in rows})

@app.get('/api/runs/<rid>/audit')
def audit(rid):
    with connection() as db:
        if not db.execute('SELECT 1 FROM runs WHERE id=?',(rid,)).fetchone():return jsonify(error='Run not found'),404
        rows=db.execute('SELECT window_id,created,request_id,before_json,after_json FROM audit WHERE run_id=? ORDER BY id DESC LIMIT 100',(rid,)).fetchall()
    return jsonify([dict(window_id=r[0],created=r[1],request_id=r[2],before=json.loads(r[3]),after=json.loads(r[4])) for r in rows])

@app.get('/api/runs/<rid>/incidents')
def incidents(rid):
    with connection() as db:rows=db.execute('SELECT window_id,status FROM incidents WHERE run_id=?',(rid,)).fetchall()
    return jsonify({str(k):v for k,v in rows})

if __name__=='__main__':app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5000')),debug=False)
