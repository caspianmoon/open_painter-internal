"""Check installed macOS branding; remove only obsolete generated icon resources."""
from pathlib import Path
import plistlib,sys
bundle=Path(sys.argv[1]).resolve()
assert bundle.name=='OpenPainter.app',bundle
resources=bundle/'Contents/Resources'
legacy=['Assets.car','blender_icon_legacy.icns','Blender_Legacy_Document_Icon.icns']
if '--clean-legacy' in sys.argv:
    for name in legacy:(resources/name).unlink(missing_ok=True)
with (bundle/'Contents/Info.plist').open('rb') as file:info=plistlib.load(file)
assert info['CFBundleIdentifier']=='org.openpainter.application'
assert info['CFBundleName']=='Open Painter'
assert info['CFBundleIconFile']=='OpenPainter.icns'
assert 'CFBundleIconName' not in info
assert (resources/info['CFBundleIconFile']).is_file()
for document in info['CFBundleDocumentTypes']:
    assert document['CFBundleTypeIconFile']=='OpenPainterDocument.icns'
    assert (resources/document['CFBundleTypeIconFile']).is_file()
assert not any((resources/name).exists() for name in legacy)
print('OPEN_PAINTER_BRANDING_PASS')
