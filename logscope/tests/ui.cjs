const fs=require('fs'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');const {indexedDB}=require('fake-indexeddb');
const root=require('path').resolve(__dirname,'../dist')+'/';
const dom=new JSDOM(fs.readFileSync(root+'index.html','utf8'),{url:'https://logscope.test/',runScripts:'outside-only'}),w=dom.window;
w.indexedDB=indexedDB;w.structuredClone=structuredClone;w.Blob=Blob;w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};w.HTMLAnchorElement.prototype.click=function(){};
let blobs=[];w.URL.createObjectURL=b=>{blobs.push(b);return 'blob:test'};w.URL.revokeObjectURL=()=>{};
w.fetch=async url=>{if(url==='/api/health')return new Response('',{status:404});const path=root+String(url).replace(/^\.\//,'').split('?')[0];return new Response(fs.readFileSync(path),{headers:{'content-type':path.endsWith('.json')?'application/json':'text/plain'}})};
w.eval(fs.readFileSync(root+'app.js','utf8')+'\nwindow.testEval=code=>eval(code);');
const e=code=>w.testEval(code),el=id=>w.document.getElementById(id);
(async()=>{
for(let n=0;n<100&&!e('result');n++)await new Promise(r=>setTimeout(r,10));
assert.equal(e('result.dataset'),'HDFS');assert.equal(e('result.metrics.tp'),2);
for(const page of ['overview','windows','templates','evaluation','incidents']){e(`page='${page}';render()`);assert.ok(el('content').textContent.length>100)}
const wid=e("testWindows().find(w=>w.anomaly).id");await e(`showWindow(${wid})`);
el('incidentOwner').value='Client analyst';el('incidentNotes').value='<img src=x onerror=alert(1)> =SUM(1)';el('incidentStatus').value='resolved';await e(`saveIncident(${wid},0)`);
assert.match(el('triageFeedback').textContent,/Saved/);await e(`showWindow(${wid})`);assert.equal(el('incidentOwner').value,'Client analyst');assert.equal(el('incidentStatus').value,'resolved');
e("page='incidents';incidentFilter='resolved';render()");assert.equal(w.document.querySelectorAll('#content tbody tr').length,1);
const report=e('clientReport()');assert.ok(report.includes('&lt;img'));assert.ok(!report.includes('<img'));assert.ok(!report.includes('undefined'));assert.ok(report.includes('0.1481'));
e('exportIncidents()');assert.match(await blobs.at(-1).text(),/Client analyst/);assert.ok(e("csvCell('=1+1')").includes("'=1+1"));
e('setBusy(true)');assert.ok(el('dataset').disabled);assert.ok(el('trees').disabled);e('setBusy(false)');assert.ok(!el('dataset').disabled);
assert.equal(el('file'),null);el('dataset').value='OpenStack';
e("inputKind='sample'");await e('loadSample()');assert.equal(e('result.dataset'),'OpenStack');assert.ok(e('result.metrics.f1')>0);assert.ok(!e('clientReport()').includes('undefined'));
el('sampleProfile').value='original';await e('loadSample()');assert.equal(e('result.metrics.recall'),null);assert.match(e('clientReport()'),/Unavailable/);
assert.equal(await e("saveHistory(result,'test-history')"),true);await e("restore('test-history')");assert.equal(e('source'),'history');assert.equal(e('inputText'),'');
el('dataset').value='HDFS';await e('loadSample()');
w.Worker=class {postMessage({id}){setTimeout(()=>this.onmessage({data:{id,result:JSON.parse(fs.readFileSync(root+'data/HDFS_result.json','utf8'))}}),20)}terminate(){}};
const running=e("execute('analyze')");assert.ok(el('dataset').disabled);await running;assert.equal(e('source'),'fresh');assert.ok(!e('busy'));assert.match(el('status').textContent,/Analysis complete/);
const old=e('result');w.Worker=class{postMessage({id}){setTimeout(()=>this.onmessage({data:{id,type:'error',message:'Deliberate test failure'}}),5)}terminate(){}};e('worker=null');await e("execute('analyze')");assert.equal(e('result'),old);assert.match(el('status').textContent,/Deliberate test failure/);assert.ok(!e('busy'));

console.log('PASS: five pages, HDFS metrics, triage save/reopen/filter, report escaping and values, CSV safety, busy controls, sample selection, OpenStack failure report');
w.close();
})().catch(err=>{console.error(err);process.exitCode=1;w.close()});
