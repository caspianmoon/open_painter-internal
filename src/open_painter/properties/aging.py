"""Material aging settings of a texture set (0.10.0).

What the thing is (material and construction), how old it is, where it lived
(environment presets that expand to physical exposure, or a custom exposure,
optionally as a history of periods), how it was used and maintained, plus the
art-direction multipliers applied after the simulation. Every edit re-drives
the aging folder through ``aging.engine`` while ``live`` is on.
"""
import bpy

from ..aging import coatings, environments, materials


def _resolve(self, context):
    """Re-drive the aging folder after any settings edit, when it exists and follows live."""
    from ..aging import engine
    from ..core.layer_stack import replaying
    if replaying():
        return
    ts = engine.owning_set(self)
    if ts is not None and ts.aging.live and ts.aging.folder_id:
        engine.apply(ts)


_copying = 0


def _copy_preset(self, values, prefix=''):
    """Preset sliders are written in one batch: one re-drive, not one per slider."""
    global _copying
    from ..aging import engine
    _copying += 1
    try:
        with engine.batch():
            for field, value in values.items():
                if abs(getattr(self, prefix + field) - value) > 1e-6:
                    setattr(self, prefix + field, value)
    finally:
        _copying -= 1


def _environment_changed(self, context):
    """Choosing a preset copies its exposure into the custom sliders, which stay editable."""
    if self.environment != 'CUSTOM':
        self.environment_base = self.environment   # what a later Custom was modified from
        _copy_preset(self, environments.ENVIRONMENTS[self.environment].as_dict())
    _resolve(self, context)


def _usage_changed(self, context):
    if self.usage != 'CUSTOM':
        _copy_preset(self, environments.USAGE[self.usage].as_dict(), 'use_')
    _resolve(self, context)


def _maintenance_changed(self, context):
    if self.maintenance != 'CUSTOM':
        _copy_preset(self, environments.MAINTENANCE[self.maintenance].as_dict(), 'care_')
    _resolve(self, context)


_PRESETS = {'exposure': ('environment', environments.ENVIRONMENTS, ''), 'usage': ('usage', environments.USAGE, 'use_'),
            'maintenance': ('maintenance', environments.MAINTENANCE, 'care_')}


def _slider_changed(group):
    """A slider moved off its preset turns the preset selector to Custom, then re-drives."""
    selector, presets, prefix = _PRESETS[group]

    def update(self, context):
        choice = getattr(self, selector)
        if not _copying and choice != 'CUSTOM' and any(abs(getattr(self, prefix + field) - value) > 1e-6
                                      for field, value in presets[choice].as_dict().items()):
            from ..aging import engine
            with engine.batch():
                setattr(self, selector, 'CUSTOM')
        _resolve(self, context)
    return update


def _exposure_slider(name, description, default, group='exposure'):
    # Relative intensities, not physical units: the model scales its rates by them (0 none, 1 the
    # strongest the presets describe), so the tooltip says what the endpoints mean.
    return bpy.props.FloatProperty(name=name, description=description + ' — relative intensity, 0 none to 1 extreme',
                                   default=default, min=0, max=1, subtype='FACTOR', update=_slider_changed(group))


ENVIRONMENT_ITEMS = [(key, environments.ENVIRONMENT_LABELS[key], f"Exposure preset: {key.replace('_', ' ').lower()}")
                     for key in environments.ENVIRONMENTS] + [('CUSTOM', 'Custom', 'Exposure set by the sliders')]
USAGE_ITEMS = [(key, environments.USAGE_LABELS[key], '') for key in environments.USAGE] + [('CUSTOM', 'Custom', '')]
MAINTENANCE_ITEMS = [(key, environments.MAINTENANCE_LABELS[key], '') for key in environments.MAINTENANCE] + [('CUSTOM', 'Custom', '')]
EVENT_ITEMS = [(key, environments.EVENT_LABELS[key], '') for key in environments.EVENT_KINDS]
NONE_ITEM = ('NONE', 'None', '')


class OP_AgingPeriod(bpy.types.PropertyGroup):
    """One period of the asset's life: a duration in an environment, used and cared for somehow."""
    years: bpy.props.FloatProperty(name='Years', default=5, min=0, soft_max=50, update=_resolve)
    environment: bpy.props.EnumProperty(name='Environment', items=ENVIRONMENT_ITEMS[:-1], default='INDOOR', update=_resolve)
    usage: bpy.props.EnumProperty(name='Usage', items=USAGE_ITEMS[:-1], default='MODERATE', update=_resolve)
    maintenance: bpy.props.EnumProperty(name='Maintenance', items=MAINTENANCE_ITEMS[:-1], default='OCCASIONAL', update=_resolve)


class OP_AgingEvent(bpy.types.PropertyGroup):
    """A maintenance event at one moment of the history."""
    year: bpy.props.FloatProperty(name='Year', default=10, min=0, soft_max=50, update=_resolve,
                                  description='Year of the history the event happens: 0 is new, the age is now')
    kind: bpy.props.EnumProperty(name='Event', items=EVENT_ITEMS, default='REPAINT', update=_resolve)


class OP_Aging(bpy.types.PropertyGroup):
    live: bpy.props.BoolProperty(name='Live', default=True, description='Re-simulate and re-drive the aging folder after every edit')
    folder_id: bpy.props.StringProperty()
    state_data: bpy.props.StringProperty()   # last resolved values and artist overrides, JSON
    # What it is ------------------------------------------------------------------
    material: bpy.props.EnumProperty(name='Material', items=materials.items(), default='CARBON_STEEL', update=_resolve)
    primer: bpy.props.EnumProperty(name='Primer', items=[NONE_ITEM] + coatings.items('PRIMER'), default='EPOXY_PRIMER', update=_resolve)
    conversion: bpy.props.EnumProperty(name='Conversion Coating', items=[NONE_ITEM] + coatings.items('CONVERSION'), default='NONE', update=_resolve)
    paint: bpy.props.EnumProperty(name='Paint', items=[NONE_ITEM] + coatings.items('PAINT'), default='POLYURETHANE_PAINT', update=_resolve)
    topcoat: bpy.props.EnumProperty(name='Top Coat', items=[NONE_ITEM] + coatings.items('TOPCOAT'), default='NONE', update=_resolve)
    paint_color: bpy.props.FloatVectorProperty(name='Paint Colour', subtype='COLOR', size=3, min=0, max=1, default=(0.18, 0.32, 0.58), update=_resolve)
    coating_condition: bpy.props.FloatProperty(name='Coating Condition', description='Condition of the coating when the history starts',
                                               default=1, min=0, max=1, subtype='FACTOR', update=_resolve)
    # How old, where, how used --------------------------------------------------------
    age_years: bpy.props.FloatProperty(name='Age', description='Simulated exposure time', default=10, min=0, soft_max=50, max=200,
                                       unit='NONE', precision=1, update=_resolve)
    use_history: bpy.props.BoolProperty(name='Environment History', default=False, update=_resolve,
                                        description='Age through a sequence of periods instead of one environment')
    periods: bpy.props.CollectionProperty(type=OP_AgingPeriod)
    active_period: bpy.props.IntProperty()
    events: bpy.props.CollectionProperty(type=OP_AgingEvent)
    active_event: bpy.props.IntProperty()
    environment: bpy.props.EnumProperty(name='Environment', items=ENVIRONMENT_ITEMS, default='OUTDOOR_URBAN', update=_environment_changed)
    environment_base: bpy.props.StringProperty(default='OUTDOOR_URBAN', options={'HIDDEN'},
                                               description='The preset the custom exposure was modified from')
    uv: _exposure_slider('UV Exposure', 'Sunlight: fading, chalking, embrittlement', .6)
    humidity: _exposure_slider('Humidity', 'Moisture in the air: corrosion, swelling, growth', .55)
    rain: _exposure_slider('Rain / Wetness', 'Direct wetting: washing, streaks, corrosion', .5)
    temperature: _exposure_slider('Temperature', 'Heat accelerates light and chemical attack (0 cold, 1 hot; not degrees)', .5)
    temperature_cycles: _exposure_slider('Temperature Cycles', 'Swings crack brittle films and check wood', .55)
    salt: _exposure_slider('Salt Exposure', 'Chlorides: the strongest corrosion accelerator', .1)
    pollution: _exposure_slider('Pollution', 'Acid deposition and soot', .6)
    soil: _exposure_slider('Soil / Dirt', 'Particulates that settle and stain', .5)
    wind: _exposure_slider('Wind', 'Carries dust and erodes soft surfaces', .4)
    usage: bpy.props.EnumProperty(name='Usage', items=USAGE_ITEMS, default='MODERATE', update=_usage_changed)
    use_handling: _exposure_slider('Handling', 'Touch: polish on edges, oils, fine scratches', .5, 'usage')
    use_abrasion: _exposure_slider('Abrasion', 'Rubbing and scuffing wear', .4, 'usage')
    use_impact: _exposure_slider('Impact Damage', 'Knocks and drops: chips and dents', .15, 'usage')
    use_cleaning: _exposure_slider('Cleaning', 'Wiping down as part of use', .25, 'usage')
    maintenance: bpy.props.EnumProperty(name='Maintenance', items=MAINTENANCE_ITEMS, default='OCCASIONAL', update=_maintenance_changed)
    care_cleaning: _exposure_slider('Cleaning Frequency', 'Regular removal of dust and soil', .25, 'maintenance')
    care_protective_treatment: _exposure_slider('Protective Treatment', 'Wax, oil or sealant slowing light and moisture', .1, 'maintenance')
    care_repainting: _exposure_slider('Repainting', 'Inclination to repaint (continuous form; add events for dated repaints)', 0, 'maintenance')
    care_polishing: _exposure_slider('Polishing', 'Restores gloss and removes light oxide', .05, 'maintenance')
    care_rust_removal: _exposure_slider('Rust Removal', 'Regular treatment of corrosion', 0, 'maintenance')
    care_wood_treatment: _exposure_slider('Wood Treatment', 'Oiling, staining, sealing wood', 0, 'maintenance')
    seed: bpy.props.IntProperty(name='Seed', default=4182, min=0, update=_resolve, description='Which particular history of this kind happened')
    # Art direction, after the physics ---------------------------------------------------
    direct_corrosion: bpy.props.FloatProperty(name='Corrosion', default=1, min=0, soft_max=3, update=_resolve)
    direct_fading: bpy.props.FloatProperty(name='Fading', default=1, min=0, soft_max=3, update=_resolve)
    direct_damage: bpy.props.FloatProperty(name='Damage', default=1, min=0, soft_max=3, update=_resolve)
    direct_dirt: bpy.props.FloatProperty(name='Dirt', default=1, min=0, soft_max=3, update=_resolve)


CLASSES = (OP_AgingPeriod, OP_AgingEvent, OP_Aging)
