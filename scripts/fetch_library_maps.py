"""Fetch pinned, checksummed CC0 maps at build time. No runtime network dependency.
Catalog/file metadata: Poly Haven API (https://polyhaven.com/our-api).
Powered by Poly Haven. License: https://polyhaven.com/license
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.request,json,hashlib
root=Path(__file__).resolve().parents[1]/'src/open_painter/assets/surfaces'
root.mkdir(parents=True,exist_ok=True)
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'OpenPainterDevelopment/0.1.9 (Powered by Poly Haven)'}),timeout=60).read()
def fetch(item):
    name,identifier=item
    metadata=json.loads(get('https://api.polyhaven.com/files/'+identifier))
    folder=root/identifier;folder.mkdir(exist_ok=True)
    record={'name':name,'id':identifier,'source':'https://polyhaven.com/a/'+identifier,'license':'CC0-1.0','maps':{}}
    for source,channel in [('Diffuse','BASE_COLOR'),('Rough','ROUGHNESS'),('nor_gl','NORMAL'),('Metal','METALLIC')]:
        if source not in metadata:continue
        variants=metadata[source]['1k'];format='jpg' if channel=='BASE_COLOR' else 'png'
        data=variants.get(format) or next(iter(variants.values()))
        content=get(data['url']);assert hashlib.md5(content).hexdigest()==data['md5']
        target=folder/Path(data['url']).name;target.write_bytes(content)
        record['maps'][channel]={'file':str(target.relative_to(root)),'url':data['url'],'sha256':hashlib.sha256(content).hexdigest()}
    print(name, list(record['maps']),flush=True)
    return record
with ThreadPoolExecutor(max_workers=3) as pool:
    records=list(pool.map(fetch,[('Rough Wood','rough_wood'),('Fine Wood','fine_grained_wood'),('Weathered Paint','rusty_painted_metal')]))
(root/'catalog.json').write_text(json.dumps(records,indent=2)+'\n', encoding="utf-8")
