import importlib
import json
from pathlib import Path
import pytest

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('LOGSCOPE_DB',str(tmp_path/'test.sqlite3'))
    import app
    importlib.reload(app)
    return app.app.test_client()

def test_health_and_dashboard(client):
    assert client.get('/api/health').json['status']=='ok'
    assert client.get('/').status_code==200

def test_analysis_history_model_and_incident(client):
    text=(Path(__file__).parents[1]/'dist/data/HDFS_2k.log').read_text()
    response=client.post('/api/analyze',json={'text':text,'options':{'trees':20}})
    assert response.status_code==200,response.json
    r=response.json;rid=r['run_id']
    assert client.get('/api/runs').json[0]['id']==rid
    assert client.get('/api/runs/'+rid).json['summary']==r['summary']
    assert len(client.get('/api/runs/'+rid+'/model').data)>1000
    alerts=[w for w in r['windows'] if w['anomaly'] and w['split']=='test']
    assert alerts
    wid=alerts[0]['id']
    assert client.post(f'/api/runs/{rid}/incidents/{wid}',json={'status':'acknowledged'}).status_code==200
    assert client.get(f'/api/runs/{rid}/incidents').json[str(wid)]=='acknowledged'
    assert client.post(f'/api/runs/{rid}/incidents/99999',json={'status':'open'}).status_code==404

def test_invalid_input_and_optional_auth(client,monkeypatch):
    assert client.post('/api/analyze',json={'text':'bad'}).status_code==400
    monkeypatch.setenv('LOGSCOPE_API_TOKEN','test-only')
    assert client.get('/api/runs').status_code==401
    assert client.get('/api/runs',headers={'Authorization':'Bearer test-only'}).status_code==200

@pytest.mark.parametrize('body', [[], {'text':''}, {'text':42}, {'text':'x','options':[]}, {'text':'x','options':{'trees':True}}, {'text':'x','options':{'trees':20.5}}, {'text':'x','options':{'bogus':1}}, {'text':'x','labels':[]}, {'text':'x','input_name':4}])
def test_invalid_payload_contract(client,body):
    response=client.post('/api/analyze',json=body)
    assert response.status_code==400
    assert response.headers['X-Request-ID']
    assert response.json['request_id']

def test_busy_request_releases_no_owned_lock(client):
    import app
    app.LOCK.acquire()
    try:
        r=client.post('/api/analyze',json={'text':'test'})
        assert r.status_code==409
        assert app.LOCK.locked()
    finally:app.LOCK.release()

def test_triage_audit_conflict_and_metadata(client):
    text=(Path(__file__).parents[1]/'dist/data/HDFS_2k.log').read_text()
    r=client.post('/api/analyze',json={'text':text,'input_name':'client.log','options':{'trees':20}}).json
    rid=r['run_id'];wid=next(w['id'] for w in r['windows'] if w['anomaly'] and w['split']=='test')
    assert client.get('/api/runs/'+rid).json['client']['input_name']=='client.log'
    url=f'/api/runs/{rid}/incidents/{wid}'
    body={'status':'acknowledged','owner':'Analyst','notes':'Investigating <input>','expected_version':0}
    saved=client.post(url,json=body)
    assert saved.status_code==200
    assert saved.json['version']==1
    assert client.post(url,json=body).status_code==409
    assert client.get(f'/api/runs/{rid}/triage').json[str(wid)]['notes']==body['notes']
    changes=client.get(f'/api/runs/{rid}/audit').json
    assert len(changes)==1 and changes[0]['before']['status']=='open'
    assert changes[0]['request_id']==saved.headers['X-Request-ID']
    assert client.post(url,json={'status':'resolved','notes':'x'*2001}).status_code==400
    assert client.post(url,json={'status':'resolved','expected_version':1}).json['notes']==body['notes']

def test_existing_incident_schema_migrates(tmp_path,monkeypatch):
    import sqlite3,app
    dbpath=tmp_path/'old.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE incidents (run_id TEXT, window_id INTEGER, status TEXT, PRIMARY KEY(run_id,window_id))')
        db.execute("INSERT INTO incidents VALUES ('old',1,'acknowledged')")
    monkeypatch.setattr(app,'DB',str(dbpath))
    with app.connection() as db:
        row=db.execute('SELECT status,notes,owner,version FROM incidents').fetchone()
        assert row==('acknowledged','','',0)

def test_online_backup_includes_wal_and_refuses_overwrite(tmp_path):
    import sqlite3
    from scripts.backup_database import backup
    src=tmp_path/'live.db';dst=tmp_path/'backup.db'
    with sqlite3.connect(src) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE t(x)');db.execute('INSERT INTO t VALUES (42)');db.commit()
        backup(src,dst)
        with sqlite3.connect(dst) as snap:assert snap.execute('SELECT x FROM t').fetchone()==(42,)
        with pytest.raises(FileExistsError):backup(src,dst)
