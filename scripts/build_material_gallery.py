"""Build a standalone review gallery from actual Blender renders, without editing pixels."""
import base64
import json
from pathlib import Path
import runpy

root = Path(__file__).resolve().parents[1]
catalog = runpy.run_path(str(root / 'src/open_painter/smart_materials/catalog.py'))['CATALOG']


def data_image(path):
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()


cards = []
for name, recipe in catalog.items():
    filename = name.replace(' ', '_') + '.png'
    preview = root / 'src/open_painter/assets/previews' / filename
    before = root / '.blender-test/generated-quality-before' / filename
    after = root / '.blender-test/generated-quality-after' / filename
    cards.append(dict(name=name, category=recipe['category'], layers=len(recipe['layers']),
                      image=data_image(preview),
                      before=data_image(before) if before.exists() and after.exists() else None,
                      after=data_image(after) if before.exists() and after.exists() else None))

page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open Painter · Generated material review</title>
<style>
:root{color-scheme:dark;font:15px system-ui;background:#282828;color:#ddd}body{margin:0 auto;padding:28px;max-width:1480px}
h1{font-size:25px;font-weight:600;margin:0 0 10px}p{line-height:1.5;color:#aaa;max-width:1000px}
nav{display:flex;gap:12px;flex-wrap:wrap;padding:18px 0;position:sticky;top:0;background:#282828;z-index:1}
input,select,button{font:inherit;color:inherit;background:#383838;border:1px solid #555;border-radius:5px;padding:8px 12px}
input{min-width:240px}label{display:flex;gap:8px;align-items:center}label input{min-width:0}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:16px}
article{border:1px solid #494949;border-radius:6px;background:#323232;overflow:hidden}article img{width:100%;display:block}
article h2{font-size:15px;margin:12px 14px 5px}article p{font-size:12px;margin:0 14px 14px}.pair{display:grid;grid-template-columns:1fr 1fr}
figure{margin:0}figcaption{text-align:center;font-size:12px;padding:8px;color:#bbb}.compare{grid-column:span 2}@media(max-width:580px){.compare{grid-column:auto}}
footer{margin-top:25px;color:#aaa;font-size:13px}
</style>
<h1>Open Painter · All 50 smart materials</h1>
<p>Actual Blender renders of the editable layer stacks. Fifteen AI-generated scalar texture inputs add surface detail; baked mesh maps still place wear and grime. These images are rendered materials, not AI-generated preview objects.</p>
<nav><input id="search" placeholder="Find a material" aria-label="Find a material"><select id="category" aria-label="Material category"><option>All categories</option></select><label><input id="compare" type="checkbox">Before / after comparisons</label><span id="count"></span></nav>
<main></main><footer>Blender 5.2.1 LTS · Cycles CPU · same UV-mapped recessed preview object and lights. Library thumbnails: 256px / 16 samples. Nine comparisons: 512px / 32 samples. Both use 1024px mesh maps and output textures; source images retain their actual 1254px size. Comparison renders isolate generated detail from the preserved procedural recipe. UV density and mapping remain project-dependent.</footer>
<script>const cards=DATA;
const main=document.querySelector('main'),search=document.querySelector('#search'),category=document.querySelector('#category'),compare=document.querySelector('#compare');
for(const name of [...new Set(cards.map(c=>c.category))]){const option=document.createElement('option');option.textContent=name;category.append(option)}
function draw(){main.replaceChildren();const shown=cards.filter(c=>(category.value==='All categories'||c.category===category.value)&&c.name.toLowerCase().includes(search.value.toLowerCase())&&(!compare.checked||c.before));
for(const c of shown){const card=document.createElement('article');if(compare.checked){card.className='compare';const pair=document.createElement('div');pair.className='pair';for(const [label,src]of[['Before',c.before],['After',c.after]]){const f=document.createElement('figure'),im=new Image(),cap=document.createElement('figcaption');im.src=src;im.alt=c.name+' '+label;cap.textContent=label;f.append(im,cap);pair.append(f)}card.append(pair)}else{const im=new Image();im.src=c.image;im.alt=c.name;im.loading='lazy';card.append(im)}const h=document.createElement('h2'),p=document.createElement('p');h.textContent=c.name;p.textContent=c.category+' · '+c.layers+' editable layers';card.append(h,p);main.append(card)}document.querySelector('#count').textContent=shown.length+' materials'}
search.oninput=category.onchange=compare.onchange=draw;draw();</script></html>'''
path = root / 'docs/generated-material-gallery.html'
path.write_text(page.replace('DATA', json.dumps(cards)), encoding="utf-8")
print(path)
