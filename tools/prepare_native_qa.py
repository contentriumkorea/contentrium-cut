"""Prepare an isolated, unshipped UXP harness using current production host code."""
import json,os,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/qa/private/native-qa-panel'
OUT.mkdir(parents=True,exist_ok=True)
for name in ['host.js','guard.js','sync.js','mutation.js']:
    shutil.copy2(ROOT/'plugin'/name,OUT/name)
shutil.copy2(ROOT/'tools/native_qa_main.js',OUT/'main.js')
shutil.copy2(ROOT/'tools/native_qa_guard.js',OUT/'qa-guard.js')
private=ROOT/'docs/qa/private'
qa_config={'paths':[str(private/'fixture'/name) for name in ['QA60-A.mp4','QA60-B.mp4','QA60-C.mp4','QA60.wav']],'projectPath':str(private/'Contentrium CUT 60min QA.prproj'),'exportPath':str(private)}
(OUT/'qa-config.js').write_text('module.exports='+json.dumps(qa_config)+';\n',encoding='utf-8')
(OUT/'vendor').mkdir(exist_ok=True);shutil.copy2(ROOT/'plugin/vendor/sha256.js',OUT/'vendor/sha256.js')
(OUT/'manifest.json').write_text(json.dumps({'manifestVersion':5,'id':'com.contentrium.cut.qa','name':'Contentrium CUT QA','version':'0.1.2','main':'index.html','host':{'app':'premierepro','minVersion':'26.5.0'},'requiredPermissions':{'network':{'domains':['http://localhost:41738']}},'entrypoints':[{'type':'panel','id':'cut-qa','label':{'default':'Contentrium CUT QA'},'minimumSize':{'width':560,'height':650},'maximumSize':{'width':2000,'height':2400},'preferredFloatingSize':{'width':600,'height':720}}]},indent=2),encoding='utf-8')
(OUT/'index.html').write_text('<html style="height:100%;overflow:auto"><body style="background:#171717;color:#eee;padding:18px;overflow:auto;height:100%"><h1>Owned native QA</h1><button id="read">Read native sources</button><button id="fixture">60 min fixture</button><button id="apply">Apply 60 min / FX / overlay</button><button id="export">Export frame/audio proof</button><pre id="status" style="white-space:pre-wrap"></pre><script src="vendor/sha256.js"></script><script src="host.js"></script><script src="sync.js"></script><script src="main.js"></script></body></html>',encoding='utf-8')
print(OUT/'manifest.json')
