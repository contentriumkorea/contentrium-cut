"""Retain dependency metadata and supplied license files from the build environment."""
import importlib.metadata,json,re,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'licenses';root.mkdir(exist_ok=True)
records=[]
for dist in importlib.metadata.distributions():
    name=dist.metadata.get('Name','unknown');key=re.sub('[^A-Za-z0-9._-]','_',name)
    copied=[]
    for file in dist.files or []:
        if not any(word in str(file).lower() for word in ['license','licence','copying','notice']):continue
        source=Path(dist.locate_file(file))
        if source.is_file() and source.stat().st_size<2*1024*1024 and source.suffix.lower() not in ['.py','.pyc','.dll','.pyd','.exe']:
            relative=Path(key)/Path(*[p for p in file.parts if p not in ['..','.']])
            target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);copied.append(relative.as_posix())
    records.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License'),'projectUrls':dist.metadata.get_all('Project-URL',[]),'files':copied})
(root/'THIRD-PARTY-NOTICES.json').write_text(json.dumps(sorted(records,key=lambda r:r['name'].lower()),ensure_ascii=False,indent=2),encoding='utf-8')
print('Dependency license records retained: '+str(len(records)))
