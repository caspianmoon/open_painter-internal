"""Aging recipe v2: shared coating failure and separate fine surface relief.

The original recipe remains available for saved folders. Explicit Rebuild opts
those folders into this version; live edits never silently replace artist work.
"""
from ..smart_materials.catalog import mask
from . import document_v1

VERSION = 2
DRIVEN = (*document_v1.DRIVEN, "mask.seed")


def build(state, construction, seed=0):
    doc = document_v1.build(state, construction, seed)
    o, material = state.outcomes(), construction.substrate
    roles = dict(zip(doc['roles'], doc['layers']))
    seed = int(seed) & 0x7fffffff
    coats = construction.coatings
    paint = next((c for c in coats if c.role == 'PAINT'), None)
    primer = construction.primer
    paint_loss = min(1., o['coating_loss'] * .7 + o['corrosion'] * .5 + o['coating_cracking'] * .3)
    primer_loss = min(paint_loss, o['primer_loss'] * .9 + o['corrosion'] * .4) if primer else paint_loss
    if primer:
        # Under-film corrosion undermines the primer within existing paint
        # failure, leaving a narrower primer transition around exposed metal.
        primer_loss = min(paint_loss, max(primer_loss, paint_loss * o['corrosion'] ** .35))
    # The lower coat can never fail outside the upper coat's damage footprint.
    paint_edge = min(1., o['coating_loss'])
    primer_edge = min(paint_edge, o['primer_loss']) if primer else paint_edge

    def growth(amount, invert=False, operation='MULTIPLY'):
        # Thresholds move, coordinates don't. Thin transition avoids translucent,
        # cloudy paint and keeps chip relief at the actual paint/metal boundary.
        black = 1.0 - .92 * amount
        return mask('NOISE', scale=6, procedural='WEATHERING_FRONT', seed=seed,
                    black=black, white=min(1., black + .025), invert=invert,
                    operation=operation, projection='TRIPLANAR', triplanar_hardness=8)

    def edge(amount, invert=False):
        return mask('CHIPS', amount * .8, 48, seed=seed, invert=invert,
                    noise_strength=.65, contrast=2.4, black=.12, white=.48)

    def remaining(spec, amount, edge_amount):
        spec['mask'] = edge(edge_amount, True)
        spec['mask_effects'] = [growth(amount, True)]

    for role, amount, edge_amount, height in (
        ('conversion', primer_loss * .8, primer_edge * .8, .502),
        ('primer', primer_loss, primer_edge, .506),
        ('paint', paint_loss, paint_edge, .514),
        ('topcoat', paint_loss, paint_edge, None),
    ):
        if role in roles:
            spec = roles[role]
            remaining(spec, amount, edge_amount)
            if height is not None:
                spec['height_value'] = height

    if paint:
        film = roles['paint']
        film.pop('height_value', None)
        film.update(procedural='ORANGE_PEEL', scale=45, detail=.08,
                    height_range=(.513, .515), projection='TRIPLANAR', triplanar_hardness=8)
        film['channel_procedurals'] = {
            'BASE_COLOR': {'id': 'WEATHERING_FRONT', 'scale': 2.5, 'detail': .08},
            'ROUGHNESS': {'id': 'GRUNGE_FINE', 'scale': 25, 'detail': .14},
        }

    if material.family == 'METAL':
        rusty = material.patina == 'RUST'
        oxide = roles['oxidation']
        # Each deposited region is opaque at modest ages; age primarily grows
        # its footprint, rather than turning every metal pixel orange at once.
        oxide['opacity'] = min(1., o['oxidation'] * 4)
        oxide['mask'] = growth(o['oxidation'], operation='NORMAL')
        oxide['mask_effects'] = [mask('CAVITY_DIRT', .18, 28, noise_strength=.6,
                                     operation='MAX', strength=o['oxidation'] * .65)]
        oxide.pop('generated_source', None)
        oxide.update(procedural='CORROSION_GRAIN', scale=22, detail=.48,
                     height_range=(.499, .513) if rusty else (.500, .504),
                     projection='TRIPLANAR', triplanar_hardness=8)
        oxide['roughness'] = .83 if rusty else .68
        corrosion = roles['corrosion']
        corrosion.pop('generated_source', None)
        corrosion.update(procedural='CORROSION_GRAIN', scale=28, detail=.65,
                         height_range=(.492, .522) if rusty else (.499, .508),
                         projection='TRIPLANAR', triplanar_hardness=8,
                         roughness=.88 if rusty else .74,
                         opacity=min(1., o['corrosion'] * 4))
        for spec in (oxide, corrosion):
            if rusty:
                spec['color'] = tuple(c * .38 for c in material.patina_color)
            spec['channel_procedurals'] = {
                'BASE_COLOR': {'id': 'WEATHERING_FRONT', 'scale': 19, 'detail': 1.5},
            }
        if coats:
            # MAX is deliberately conservative: corrosion is confined to exposed
            # substrate even where the two damage masks have soft edge texels.
            deepest = .8 if 'conversion' in roles else 1.
            corrosion['mask'] = edge(primer_edge * deepest)
            corrosion['mask_effects'] = [growth(primer_loss * deepest, operation='MAX')]
        else:
            corrosion['mask'] = growth(o['corrosion'], operation='NORMAL')
            corrosion['mask_effects'] = [mask('CAVITY_DIRT', .22, 31, noise_strength=.65,
                                             operation='MAX', strength=o['corrosion'] * .65)]
        if 'patina' in roles:
            spec = roles['patina']
            spec.update(opacity=min(1., o['patina'] * 3), procedural='CORROSION_GRAIN',
                        scale=60, detail=.45, height_range=(.500, .507), projection='TRIPLANAR')
            spec['mask'] = growth(o['patina'], operation='NORMAL')
            spec['mask_effects'] = []
        if coats and 'handling_polish' in roles:
            # Rubbing enamel changes its sheen; it cannot paint silver metal
            # through an otherwise intact primer/paint stack.
            roles['handling_polish']['channels'] = ('ROUGHNESS',)

    if 'cracking' in roles:
        spec = roles['cracking']
        amount = o['coating_cracking']
        spec['mask'] = mask('NOISE', scale=18, procedural='FINE_FISSURES', seed=seed + 53,
                            black=.015, white=.25, projection='TRIPLANAR', triplanar_hardness=8)
        spec['mask_effects'] = [growth(amount)]
        if coats:
            spec['mask_effects'].insert(0, edge(paint_edge, True))
            spec['mask_effects'].append(growth(paint_loss, True))
        spec['height_value'] = .502 if paint else .490
    if 'wood_checking' in roles:
        spec = roles['wood_checking']
        spec['mask'] = mask('NOISE', scale=10, procedural='FINE_FISSURES', seed=seed + 71,
                            uv_scale=(2.5, .35), black=.015, white=.3, projection='TRIPLANAR')
        spec['height_value'] = .488
    if 'micro_scratching' in roles:
        spec = roles['micro_scratching']
        spec['mask'] = mask('NOISE', scale=29, procedural='TAPERED_SCRATCHES', seed=seed + 101,
                            black=.01, white=.3, projection='TRIPLANAR', triplanar_hardness=8)
        spec['mask_effects'] = [growth(min(1., o['micro_scratching'] * 2))]
        if material.family not in {'TEXTILE', 'LEATHER'}:
            spec['height_value'] = .508 if paint else .496
        if paint:
            spec['mask_effects'].extend([edge(paint_edge, True), growth(paint_loss, True)])
    if 'impact' in roles:
        roles['impact']['height_value'] = .488

    # Keep the legacy optional geometry masks and their fallbacks. The procedural
    # base has finite tails instead of full-height noise bands or periodic drips.
    streaks = roles['water_staining']
    streaks['mask'] = mask('NOISE', scale=9, procedural='RUNOFF_TRAILS', seed=seed + 211,
                           black=.015, white=.65, projection='TRIPLANAR', triplanar_hardness=8)
    streaks['mask_effects'] = [mask('RUNOFF', .35 + o['water_staining'] * .5, 9,
                                  seed=seed + 211, contrast=1.8, optional=True, operation='NORMAL')]
    for role in ('salt', 'biological', 'soil', 'dust'):
        spec = roles[role]
        spec['mask'] = growth(o[role], operation='NORMAL')
        spec['mask']['seed'] = (seed + {'salt': 307, 'biological': 401, 'soil': 503, 'dust': 601}[role]) & 0x7fffffff
        # Deposits can accumulate in cavities, but do not blanket every cavity
        # equally. Existing optional normal/position restrictions are retained.
        for effect in spec.get('mask_effects', ()):
            if effect['kind'] == 'CAVITY_DIRT':
                effect['operation'] = 'MAX'
                effect['strength'] = o[role] * .55
    # Crystalline residue is sparse and granular, not a white camouflage coat.
    salt = roles['salt']
    salt['mask'] = growth(o['salt'] * .60, operation='NORMAL')
    salt['mask']['seed'] = (seed + 307) & 0x7fffffff
    salt['mask_effects'] = [mask('NOISE', scale=85, procedural='CORROSION_GRAIN',
                                  seed=seed + 311, black=.46, white=.56,
                                  projection='TRIPLANAR', triplanar_hardness=8)]
    salt.update(color=(.65, .63, .57), opacity=o['salt'] * .45, height_value=.516)
    doc['layers'] = [roles[role] for role in doc['roles']]
    doc['version'] = VERSION
    return doc
