"""Declarative authoring entry point; imported material data is never executable."""
import json


def apply_recipe(texture_set,document):
    from .smart_materials.composer import apply_document
    if isinstance(document,str): document=json.loads(document)
    if not isinstance(document,dict): raise ValueError('Recipe must be a JSON object')
    encoded=json.dumps(document,allow_nan=False)
    if len(encoded)>4*1024*1024 or set(document)-{'name','layers','surface'}:
        raise ValueError('Only bounded declarative recipe fields are supported')
    return apply_document(texture_set,json.loads(encoded),document.get('name','Declarative Material'))
