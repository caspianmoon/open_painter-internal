"""Coordinated semantic responses with persistent local override protection."""
import json
from .environment_profiles import responses,VERSION
from .variation_rules import factor
from .layer_stack import invalidate

STATES={'CLEAN':(0.,.02,.9),'USED':(.5,.55,.4),'DAMAGED':(.95,.9,.05)}


def apply(ts,state=None,*,age=.5,usage=.5,maintenance=.5,profile='NEUTRAL',seed=1):
    if state is not None:
        age,usage,maintenance=STATES[state]
    weights=responses(age,usage,maintenance,profile)
    stored=json.loads(ts.material_state_data or '{}')
    baseline=stored.get('baseline',{})
    previous=stored.get('resolved',{})
    overrides=set(stored.get('overrides',[]))
    resolved={}
    for layer in ts.layers:
        if layer.type!='FILL' or layer.effect_semantic=='NONE':
            continue
        identifier=layer.id
        if identifier not in baseline:
            baseline[identifier]=layer.opacity
        if identifier in previous and abs(layer.opacity-previous[identifier])>1e-6:
            overrides.add(identifier)
        if identifier in overrides:
            continue
        value=min(1.,baseline[identifier]*weights[layer.effect_semantic]*factor(seed,layer.effect_semantic,layer.name))
        layer.opacity=value
        resolved[identifier]=value
    ts.material_state_data=json.dumps(dict(version=VERSION,baseline=baseline,resolved=resolved,
        overrides=sorted(overrides),controls=dict(state=state,age=age,usage=usage,maintenance=maintenance,profile=profile,seed=seed)),sort_keys=True)
    invalidate(ts)
    return resolved


def clear_overrides(ts):
    stored=json.loads(ts.material_state_data or '{}')
    stored['overrides']=[]
    stored['resolved']={layer.id:layer.opacity for layer in ts.layers}
    ts.material_state_data=json.dumps(stored)
