"""Build a per-material review that reports the recorded render conditions."""

import argparse
import base64
import html
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--before', type=Path, default=ROOT / '.blender-test/material-corrections/before')
parser.add_argument('--after', type=Path, default=ROOT / '.blender-test/material-corrections/after')
parser.add_argument('--before-context', type=Path, help='Explicit context JSON; images resolve under --before')
parser.add_argument('--after-context', type=Path, help='Explicit context JSON; images resolve under --after')
parser.add_argument('--before-label', help='Override the recorded revision label')
parser.add_argument('--after-label', help='Override the recorded revision label')
parser.add_argument('--allow-partial', action='store_true', help='Review a matching subset while renders finish')
parser.add_argument('--linked-images', action='store_true', help='Use relative image links instead of embedding PNGs')
parser.add_argument('--output', type=Path, default=ROOT / 'docs/material-surface-review.html')
args = parser.parse_args()
before = json.loads((args.before_context or args.before / 'render-context.json').read_text(encoding="utf-8"))
after = json.loads((args.after_context or args.after / 'render-context.json').read_text(encoding="utf-8"))


def index(context):
    records = {record['material']: record for record in context['records']}
    if len(records) != len(context['records']):
        raise ValueError('A render context contains duplicate material names')
    return records


old_records, new_records = index(before), index(after)
if args.allow_partial:
    names = [name for name in new_records if name in old_records]
    if not names:
        parser.error('The contexts have no materials in common')
else:
    if old_records.keys() != new_records.keys() or len(new_records) != 50:
        parser.error('A complete review needs the same 50 material identities; use --allow-partial for a subset')
    names = list(new_records)

notes = {}
for filename in ('material-review-metals.md', 'material-review-natural.md'):
    file = ROOT / 'docs' / filename
    if not file.is_file():
        continue
    for line in file.read_text(encoding="utf-8").splitlines():
        # Ignore later three-column notes tables: only numbered defect/correction
        # rows with all four columns supply this optional historical text.
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) == 4 and cells[0].isdigit() and cells[1] in new_records:
            notes[cells[1]] = cells[2:]


def esc(value):
    return html.escape(str(value), quote=True)


def uri(path):
    if args.linked_images:
        return esc(Path(os.path.relpath(path.resolve(), args.output.parent.resolve())).as_posix())
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()


def figure(path, caption):
    if not path.is_file():
        raise FileNotFoundError('Recorded review image is missing: ' + str(path))
    return (f'<figure><figcaption>{esc(caption)}</figcaption>'
            f'<img loading="lazy" src="{uri(path)}" alt="{esc(caption)}"></figure>')


def value_text(value):
    return 'Not recorded' if value is None else json.dumps(value, ensure_ascii=False)


condition_keys = (
    'blender', 'shape', 'closeup', 'texture_resolution', 'render_resolution',
    'samples', 'engine', 'device', 'denoising', 'seed', 'camera', 'lighting',
    'world_color', 'color_management', 'geometry_sha256', 'uv_sha256', 'baked_maps_sha256',
)
conditions = []
different, unrecorded = [], []
for key in condition_keys:
    old, new = before.get(key), after.get(key)
    if old is None or new is None:
        status = 'Not fully recorded'
        unrecorded.append(key)
    elif old != new:
        status = 'Different'
        different.append(key)
    else:
        status = 'Same'
    conditions.append(f'<tr class="{status.lower().split()[0]}"><th>{esc(key)}</th>'
                      f'<td>{esc(value_text(old))}</td><td>{esc(value_text(new))}</td><td>{status}</td></tr>')

before_label = args.before_label or before.get('label') or args.before.name
after_label = args.after_label or after.get('label') or args.after.name
cards = []
for i, name in enumerate(names, 1):
    old, new = old_records[name], new_records[name]
    default_name = name.replace(' ', '_') + '.png'
    pair = figure(args.before / old.get('render', default_name), before_label)
    pair += figure(args.after / new.get('render', default_name), after_label)
    recipe_changed = old.get('recipe') != new.get('recipe')
    source_status = 'Not recorded in both contexts'
    if 'source_sha256' in old and 'source_sha256' in new:
        source_status = 'Changed' if old['source_sha256'] != new['source_sha256'] else 'Unchanged'
    maps = []
    for label, directory, record in ((before_label, args.before, old), (after_label, args.after, new)):
        row = []
        for key, title in (('height', 'Height · 16-bit'),
                           ('normal_combined', 'Normal · includes surface relief')):
            filename = record.get('maps', {}).get(key)
            if filename:
                row.append(figure(directory / filename, label + ' · ' + title))
        if row:
            maps.append('<details><summary>' + esc(label)
                        + ' · inspect height and normal maps</summary><div class="maps">'
                        + ''.join(row) + '</div></details>')
    statistics = []
    for key in ('minimum', 'maximum', 'range', 'standard_deviation', 'gradient_rms_per_texel',
                'height_distance', 'world_height_span', 'combined_normal_xy_rms'):
        values = [record.get('height_stats', {}).get(key) for record in (old, new)]
        if any(value is not None for value in values):
            formatted = ['Not recorded' if value is None else f'{value:.7g}' for value in values]
            statistics.append(f'<tr><th>{esc(key)}</th><td>{formatted[0]}</td><td>{formatted[1]}</td></tr>')
    stats = ('<details><summary>Measured height and normal values</summary><table>'
             f'<tr><th>Measurement</th><th>{esc(before_label)}</th><th>{esc(after_label)}</th></tr>'
             + ''.join(statistics) + '</table></details>') if statistics else ''
    historical_notes = ''
    if name in notes:
        defect, correction = notes[name]
        historical_notes = (f'<details><summary>Earlier authored review notes</summary>'
                            f'<p><b>Observed then:</b> {esc(defect)}</p>'
                            f'<p><b>Earlier correction:</b> {esc(correction)}</p></details>')
    cards.append(f'<article data-name="{esc(name.lower())}"><h2>{i}. {esc(name)}</h2>'
                 f'<div class="pair">{pair}</div><p>Recipe: {"changed" if recipe_changed else "unchanged"}. '
                 f'Source file hashes: {esc(source_status)}.</p>'
                 + ''.join(maps) + stats + historical_notes + '</article>')

summary = []
if different:
    summary.append('Different recorded conditions: ' + ', '.join(different) + '.')
if unrecorded:
    summary.append('Older or incomplete context does not establish equality for: ' + ', '.join(unrecorded) + '.')
if not different and not unrecorded:
    summary.append('All listed render conditions match.')
if args.allow_partial:
    summary.append(f'Partial review: {len(names)} shared materials; before has {len(old_records)}, after has {len(new_records)}.')
summary.append('Expand a material to inspect its 16-bit height and normal maps. Normals include the rendered surface relief. Map values are shown without contrast enhancement. Click an image to inspect its full resolution.')

document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Open Painter · material surface review</title><style>
*{box-sizing:border-box}body{margin:0;background:#202020;color:#ddd;font:16px/1.5 system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{border-bottom:1px solid #555}
h1{font-size:24px;margin:0}h2{font-size:20px}.pair,.maps{display:grid;grid-template-columns:1fr 1fr;gap:12px}.maps{margin-top:12px}
figure{margin:0}img{display:block;width:100%;height:auto;cursor:zoom-in}figcaption{padding:8px;background:#333}p{margin:8px 0}
article{margin-bottom:36px;padding-bottom:20px;border-bottom:1px solid #555}input{font:inherit;color:inherit;background:#333;border:1px solid #666;padding:8px;width:100%;margin-top:12px}
details{margin-top:12px}summary{cursor:pointer}table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}th,td{text-align:left;vertical-align:top;border:1px solid #555;padding:6px;overflow-wrap:anywhere}.different{background:#543c20}
@media(max-width:640px){header,main{padding:12px}.pair,.maps{gap:4px}h1{font-size:20px}figcaption{font-size:12px;padding:4px}}
</style>'''
document += (f'<header><h1>Open Painter · {len(names)} material surface reviews</h1><p>'
             + esc(' '.join(summary)) + '</p><details><summary>Exact recorded render conditions</summary>'
             f'<table><tr><th>Setting</th><th>{esc(before_label)}</th><th>{esc(after_label)}</th><th>Comparison</th></tr>'
             + ''.join(conditions) + '</table></details>'
             '<input type="search" id="search" aria-label="Find a material" placeholder="Find a material…"></header><main>')
document += ''.join(cards) + '''</main><script>
document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('article').forEach(a=>a.hidden=!a.dataset.name.includes(q));});
document.querySelectorAll('figure img').forEach(img=>img.addEventListener('click',()=>{const w=window.open();if(w){const full=w.document.createElement('img');full.src=img.src;full.alt=img.alt;w.document.body.append(full);}}));
</script></html>'''
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(document, encoding="utf-8")
print(args.output, args.output.stat().st_size, f'bytes; {len(names)} paired reviews')
