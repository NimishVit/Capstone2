/* Python stays in a dedicated worker, leaving the dashboard responsive. */
let ready;
async function boot(){
  if(!ready) ready=(async()=>{
    postMessage({type:'status',message:'Loading Python runtime (first run may take a minute)…'});
    importScripts('https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js');
    const py=await loadPyodide({indexURL:'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/'});
    postMessage({type:'status',message:'Loading NumPy, SciPy and scikit-learn…'});
    await py.loadPackage(['scikit-learn','joblib']);
    const response=await fetch('./pipeline.py',{cache:'no-store'});if(!response.ok)throw Error('Cannot load analysis source');
    py.FS.writeFile('/home/pyodide/pipeline.py',await response.text());
    const mr=await fetch('./data/label_manifest.json',{cache:'no-store'});if(!mr.ok)throw Error('Cannot load annotation manifest');
    const manifest=await mr.json();py.FS.mkdirTree('/home/pyodide/data');
    py.FS.writeFile('/home/pyodide/data/label_manifest.json',JSON.stringify(manifest));
    await Promise.all(manifest.profiles.map(async p=>{const r=await fetch('./data/'+p.labels_path,{cache:'no-store'});if(!r.ok)throw Error('Cannot load annotation labels');py.FS.writeFile('/home/pyodide/data/'+p.labels_path,await r.text())}));
    py.runPython('import pipeline');
    return py;
  })();
  try{return await ready}catch(e){ready=null;throw e}
}
onmessage=async({data})=>{
  try{
    const py=await boot();
    postMessage({type:'status',message:'Running real Isolation Forest analysis…'});
    py.globals.set('payload',JSON.stringify(data.payload||{}));
    let result;
    if(data.action==='model'){
      result=py.runPython('import base64; base64.b64encode(pipeline.model_bytes()).decode()');
    }else if(data.action==='parse'){
      result=py.runPython('import json; p=json.loads(payload); pipeline.structured_csv(p["text"],p["options"]["dataset"],p.get("labels",""))');
    }else{
      result=JSON.parse(await py.runPythonAsync('pipeline.json_request(payload)'));
    }
    postMessage({type:'result',id:data.id,result});
  }catch(e){postMessage({type:'error',id:data.id,message:e.message||String(e)})}
};
