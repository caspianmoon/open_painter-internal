"""Float32 CPU reference for the experimental fill/mask/filter image chain."""
import numpy as np
from .base import validate
from ...texture_engine.filters import apply


def execute(source,mask,operations):
    validate(operations)
    result=np.asarray(source,np.float32).copy()
    for op in operations:
        kind=op['kind']
        if kind=='FILL': result[:]=op['color']
        elif kind=='OVER':
            alpha=np.clip(source[...,3:4]*mask[...,0:1]*op.get('opacity',1),0,1)
            result[...,:3]=source[...,:3]*alpha+result[...,:3]*(1-alpha)
            result[...,3:4]=alpha+result[...,3:4]*(1-alpha)
        else:
            result[...,:3]=apply(result[...,:3],kind,**{k:v for k,v in op.items() if k!='kind'})
    return result
