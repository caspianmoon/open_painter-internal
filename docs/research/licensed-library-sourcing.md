# Asset sourcing for a sellable Open Painter library

Reviewed **2026-09-14**, against Open Painter commit
`25368daac46f6df9885e74deca692b10f6686390`, on
`task/licensed-library-research`. Research, file inspection and a licensing
shortlist are complete. This document is not a claim that the assets have been
converted into native smart materials or installed in Blender.

**Recommendation:** curate CC0 PBR maps from Poly Haven, ambientCG and cgbookcase;
combine them with selected CC0 imperfection images and individually licensed
Material Maker generators. Build Open Painter's own editable mask/layer recipes
around those inputs. The published grants below permit commercial redistribution
of the selected upstream files, including distribution in a paid asset library.
They do not make those upstream files exclusive to Open Painter.

## Redistribution decisions

| Source | Permission for our paid library | Scope and evidence |
| --- | --- | --- |
| Poly Haven | Yes, CC0 asset files | Expressly permits inclusion in products sold. Website example renders, gallery images, branding and copy are outside the asset grant. Generate our own previews. [License](https://polyhaven.com/license) |
| ambientCG | Yes, CC0 asset files and material previews | Commercial distribution and inclusion of raw files are expressly covered. [License](https://docs.ambientcg.com/license/) |
| cgbookcase | Yes, CC0 texture files | Catalog dedicates textures under CC0 1.0. Do not infer rights over all website graphics. [Catalog/license](https://www.cgbookcase.com/textures) |
| Blender texture archive | Yes, CC0 texture files | Archive states all its textures are CC0 for any purpose. The two selected downloaded files are real grayscale PNGs. [Archive/license](https://download.blender.org/archive/textures/) |
| Material Maker community | Yes for the two selected CC0 uploads | The community library has mixed licenses. Each selected page explicitly identifies CC0; dependency and adaptation review remains necessary. [Nodes](https://www.materialmaker.org/material?id=1245), [brush patterns](https://www.materialmaker.org/material?id=1256) |
| 3DTextures | Yes for the CC0 texture images | Free 1K images are covered. Higher-resolution images and project-source access are advertised through Patreon; do not infer that paid SBS/SBSAR project files share the texture license without checking their supplied terms. [FAQ/license](https://3dtextures.me/about/) |

CC0 permits commercial copying, modification and distribution without an
attribution requirement. Preserve author/source credits anyway. It does not
clear third-party trademarks, privacy rights or ownership disputes, and supplies
no warranty. Avoid branded scans and keep the acquisition evidence. These are
copyright-permission findings from the publishers, not blanket legal clearance.
[CC0 terms and scope](https://creativecommons.org/publicdomain/zero/1.0/)

Open Painter's code remains under its GPL license. Blender explicitly permits
selling add-ons under GPL-compliant terms; customers retain those license rights.
Upstream CC0 maps also retain their public-domain freedoms after sale. Our value
is the working integration, authored recipes, useful controls and support.
[Blender licensing](https://www.blender.org/about/license/)

Poly Haven's API terms changed on **18 July 2026**: commercial use is now allowed
without an API key or revenue share, with visible source credit and an identifying
User-Agent for a live integration. This is separate from its asset license.
The research requests identify `OpenPainterAssetResearch/0.2.4 (Powered by Poly Haven)`.
No runtime network dependency is proposed; acquire and pin files at build time.
[Current API policy](https://polyhaven.com/our-api)

## Concrete candidates

These are specific asset candidates, not an instruction to ingest whole websites.
Resolutions below are publisher download options unless marked downloaded; no
existing authored texture was reduced. The machine-readable
[18-entry catalog](licensed-library-candidates.json) separates surface textures,
mask components and procedural source graphs.

| Asset | Source / available resolution or format | Role in Open Painter | Priority |
| --- | --- | --- | --- |
| [Rusty Metal 02](https://polyhaven.com/a/rusty_metal_02) | Poly Haven; 1K–8K | Weathered painted-metal surface; not an edge-adaptive mask | primary |
| [Wood Table 001](https://polyhaven.com/a/wood_table_001) | Poly Haven; 1K–16K | Varnished furniture wood grain and fine roughness | primary |
| [Concrete Floor Worn 001](https://polyhaven.com/a/concrete_floor_worn_001) | Poly Haven; 1K–16K | Concrete base with multiscale pores and abrasion | primary |
| [Metal 032](https://ambientcg.com/view?id=Metal032) | ambientCG; 1K–8K | Clean metallic base for editable wear overlays | primary |
| [Wood 051](https://ambientcg.com/view?id=Wood051) | ambientCG; 1K–8K | Dark hardwood, published physical scale 80 cm | reserve |
| [Leather 030](https://ambientcg.com/view?id=Leather030) | ambientCG; 1K–8K | Leather grain, published physical scale 45 cm | primary |
| [Fabric 067](https://ambientcg.com/view?id=Fabric067) | ambientCG; 1K–8K | Photogrammetric fabric surface | primary |
| [Black Leather 01](https://www.cgbookcase.com/textures/black-leather-01) | cgbookcase; 4K option verified | Alternative leather grain; DirectX normal requires conversion | reserve |
| [Brushed Iron 02](https://www.cgbookcase.com/textures/brushed-iron-02) | cgbookcase; 4K option verified | Directional metal brushing; DirectX normal requires conversion | primary |
| [Surface Imperfections 001](https://ambientcg.com/view?id=SurfaceImperfections001) | ambientCG; 1K–4K | Smears and water-stain breakup | primary |
| [Surface Imperfections 007](https://ambientcg.com/view?id=SurfaceImperfections007) | ambientCG; 1K–4K | Dried liquid/coffee spots; use supplied opacity as scalar mask | primary |
| [Surface Imperfections 016](https://ambientcg.com/view?id=SurfaceImperfections016) | ambientCG; 1K–8K | Dust distribution detail; combine with orientation and AO | primary |
| [Grunge Sharp Scratches](https://download.blender.org/archive/textures/grunge/grunge_sharp_scratches/) | Blender texture archive; 2K downloaded | Dense, aggressive scratch breakup; reserve for heavy damage, not polished micro-scratches | reserve |
| [Grunge Plaster Peeling](https://download.blender.org/archive/textures/grunge/grunge_plaster_peeling/) | Blender texture archive; 2K downloaded | Peeling islands with fine cracks; combine with edge/paint coverage | primary |
| [Free Custom Nodes (V2)](https://www.materialmaker.org/material?id=1245) | Material Maker community; Material Maker 1.4 graph | Editable chipping, scratches, rust leaks and warp source; port or export, not native import | adaptation |
| [Paint Brush Patterns #1](https://www.materialmaker.org/material?id=1256) | Material Maker community; Material Maker 1.4 graph | Dirt, dust, peeling and leak patterns for mask/brush preparation | adaptation |
| [Surface Imperfections Grunge 001](https://3dtextures.me/2021/10/19/surface-imperfections-grunge-001/) | 3DTextures; 1K free; 4K advertised paid | Additional grime breakup; free resolution limits fine detail | reserve |
| [Surface Imperfections Scratches 001](https://3dtextures.me/2020/01/06/surface-imperfections-scratches-001/) | 3DTextures; 1K free; 4K advertised paid | Additional scratch pattern; paid project-source rights need separate evidence | reserve |

Start with Brushed Iron 02, Leather 030, Fabric 067, Wood Table 001 and Concrete
Floor Worn 001 as distinct bases. Add the dust, liquid spots and peeling inputs
as separate overlays. Keep Rusty Metal 02 as a weathered surface option: its
paint and oxidation are already present in the photograph, so it cannot provide
independently adjustable bare metal, paint loss and edge wear on its own.

## Downloaded sample findings

The [verification record](licensed-library-sample-verification.json) contains
seven downloaded file hashes, source/download URLs, dimensions, bit depth and
which images were visually inspected. Files remain in
`.blender-test/asset-research/`; they were not added to the runtime library.

- Blender's two archive masks are **2048 x 2048, 8-bit grayscale** PNGs. The sharp
  scratch image is dense and aggressive: suitable for severe damage breakup,
  unsuitable as the default on clean chrome or brushed metal. The peeling map
  has larger islands and fine branching cracks useful for chipped coatings.
- ambientCG's **SurfaceImperfections007 2K PNG ZIP** is 29,444,169 bytes and passes
  ZIP CRC validation. It contains grayscale Color and Opacity, **16-bit RGB**
  DirectX/OpenGL normal maps, and a 512-pixel preview. It has **no Roughness map**.
  Its opacity image supplies dried-liquid spots; feed it into a mask controlling
  chosen roughness/color values instead of assuming every PBR archive has the
  same channel set. The preview must never be imported as the full-size mask.
- Four **2K PNG** maps from Poly Haven Rusty Metal 02—diffuse, roughness, OpenGL
  normal and displacement—match the publisher's MD5 values; SHA-256 values are
  also recorded. The available set has no Metallic map. Do not blindly set the
  entire rust/paint photograph to Metallic=1 or substitute Specular as Metallic.
- The cgbookcase pages explicitly offer DirectX normals. Their download pages
  were checked, but their ZIP contents were not downloaded in this review.
- Material Maker graph availability and individual CC0 labels were verified;
  graphs have not been rendered or ported. They are adaptation candidates, not
  directly compatible `.openpaintmaterial` files.

The images were inspected at their supplied resolution, without conversion or
resampling. These observations establish useful source detail and data layout;
they do not establish the final material's lighting, scale, seams or performance.
No Blender scene, screenshot comparison or runtime benchmark was run for this
research-only change. Existing 0.2.4 test results remain historical here.

## Why this needs native recipes

The repository already bundles three Poly Haven surfaces in
`src/open_painter/assets/surfaces/catalog.json`. They are 1K Base Color/Roughness/
OpenGL Normal inputs with source URLs and SHA-256 hashes. Simply adding another
pre-weathered image does not solve the earlier edge-wear and whole-surface-noise
complaints.

The existing `core/surface_presets.py:scanned` loads the actual channel images,
sets Base Color to sRGB and data maps to Non-Color, and packs the images.
`ui/native.py:OP_OT_import_texture` imports individual image files;
`ui/assets_panel.py:OP_OT_import_material` expects `.openpaintmaterial`.
`smart_masks/library.py` currently composes parameterized mesh-map generators.
The `.spsm`, `.sbsar` and Material Maker `.ptex` formats are not native import
formats. Licensing permission does not create format compatibility.

The smallest useful integration builds on those existing paths:

1. Package correctly mapped PBR images, preserving source precision. Prefer
   supplied OpenGL normals; convert a DirectX normal's green channel exactly
   once when required. Keep scalar masks and data maps in Non-Color. Retain
   physical scale metadata and treat displacement amplitude explicitly.
   [cgbookcase normal-map guidance](https://www.cgbookcase.com/learn/how-to-use-pbr-textures-in-blender)
2. Compose clean substrate, coating, wear, corrosion and deposits as separate
   native layers. Edge masks must be gated by the object's baked convexity;
   grunge changes breakup inside that coverage. Use cavity/AO and orientation
   inputs for dirt or dust. A static scratch map contains no knowledge of the
   object's geometry.
3. Expose useful controls—amount, physical scale, direction, breakup, seed and
   layer/channel strength—through existing Blender controls. Export native
   `.openpaintmaterial` v2 recipes while preserving v1 readers and old presets.
4. Keep asset images under **Textures**, grayscale components under the relevant
   texture/alpha categories, and only composed mesh-aware presets under **Smart
   Masks**. Material Maker graphs require porting selected CC0 operations or
   exporting licensed images; never execute downloaded code during import.
5. Generate our own previews using the exact shipped recipe. Before shipping,
   compare a beveled cube, smooth sphere and production UV mesh: check clean
   flat faces, localized edges, controllable roughness and repeat scale. Then
   verify native save/reload/export and cold/warm evaluation at the authored
   resolution. Do not call a source-image inspection that acceptance test.

## Sources excluded from automatic bundling

| Source / license | Decision for this reusable library |
| --- | --- |
| Adobe Substance 3D Assets | The official FAQ disallows standalone sharing/sale. Commercial rendered output does not establish permission to redistribute an asset library. [FAQ](https://www.adobe.com/products/substance3d/assets.html) |
| Adobe Community Assets License 1.1 | Disallows unmodified standalone copies while granting rights for specified modified/larger works. Do not classify it as unrestricted source-library permission. [License, sections 2–3](https://www.adobe.com/legal/terms/community-assets-license.html) |
| Historic Substance Share downloads | Some earlier downloads retain CC BY or other previous licenses. Review the exact asset/acquisition license; do not assume all historic Share assets share today's restriction. [Adobe terms, section 4.2](https://wwwimages2.adobe.com/content/dam/cc/en/legal/servicetou/Adobe-Substance-3D-Community-Assets-Product-Specific-Terms-en_US-20240618.pdf) |
| Fab Standard / Megascans on Fab | Standalone redistribution/resale prohibited; Personal/Professional pricing does not create source-library redistribution rights. Separately listed CC BY assets are a different license. [Fab license](https://www.fab.com/eula) |
| ArtStation Standard / Extended | Do not infer source resale rights from Extended commercial usage. Require a creator's explicit suitable redistribution agreement or a separately applicable open license. [Marketplace agreement](https://www.artstation.com/marketplace-product-eula) |
| Textures.com | Explicitly excludes redistribution as competing texture/material packs, including modified images and materials made with them. [Official FAQ](https://www.textures.com/faq/) |

No account was created, purchase made, subscription started, author contacted or
restricted library imported. For future paid acquisitions, require explicit
permission covering source redistribution, derivatives and downstream artist
use; buying an ordinary commercial-use tier is insufficient.

## Records to retain when packaging

Keep publisher and author, exact asset URL, asset-specific license evidence,
acquisition date, original download URL and cryptographic hash, source resolution/
precision, channel and normal conventions, any conversion performed, and the
native recipe that consumes each file. A changed source file requires a new
hash and review; an unchanged CC0 asset does not lose its dedication because a
website later changes its general policy.

Current changes are limited to this report, the candidate and sample-verification
JSON files, and `docs/implementation-status.md`. No source code, UI, material
preset, installer or Blender profile changed in this task. The manifest and
sample checks validate this research artifact, not a new addon release.


Artifact verification run: **PASS** for 18 unique license-linked entries,
seven file-size/SHA-256 checks, PNG header dimensions/precision, ZIP CRC and local
Markdown links. `git diff --check` also passed. A minimal repeat of the core
provenance checks, from the repository root:

```sh
python3 - <<'PYVERIFY'
import hashlib, json, zipfile
from pathlib import Path
root = Path('docs/research')
catalog = json.loads((root/'licensed-library-candidates.json').read_text())
report = json.loads((root/'licensed-library-sample-verification.json').read_text())
assert len(catalog['assets']) == len({a['id'] for a in catalog['assets']}) == 18
for asset in catalog['assets']:
    assert asset['license'] == 'CC0-1.0' and asset['license_evidence_url']
for item in report['downloaded_assets']:
    path = Path(report['local_directory']) / item['file']
    data = path.read_bytes()
    assert len(data) == item['bytes']
    assert hashlib.sha256(data).hexdigest() == item['sha256']
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
print('PASS: catalog and locally downloaded samples')
PYVERIFY
git diff --check
```

The sample check requires the local downloaded files. The JSON catalog alone
can be validated without downloading assets. Publisher URLs are recorded for
reacquisition; images are not silently embedded in this documentation commit.
