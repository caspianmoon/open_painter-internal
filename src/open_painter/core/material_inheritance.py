"""Explicit three-way update preview for declarative material metadata.

No live parent subscriptions. Local changes win conflicts and every conflict is
returned for review. Layer lists merge by stable IDs (positionally when items
have no ids); assets remain opaque data. A key that only the local side adds is
a silent local addition, not a conflict.
"""
from copy import deepcopy


def preview(base,parent,local):
    conflicts=[]
    missing=object()

    def keyed(value):
        return isinstance(value,list) and value and all(isinstance(item,dict) and item.get('id') for item in value)

    def merge(old,new,own,path):
        if own==old: return deepcopy(new)
        if new==old or own==new: return deepcopy(own)
        if all(isinstance(value,dict) for value in (old,new,own)):
            result={}
            for key in old.keys()|new.keys()|own.keys():
                a,b,c=old.get(key,missing),new.get(key,missing),own.get(key,missing)
                if b is missing and c==a: continue
                if c is missing:
                    if a is missing: result[key]=deepcopy(b)          # parent added it
                    elif b!=a: conflicts.append(path+'.'+key)          # local removed what parent changed
                    continue
                if b is missing:
                    if a is not missing: conflicts.append(path+'.'+key)
                    result[key]=deepcopy(c)
                else: result[key]=merge(a,b,c,path+'.'+key)
            return result
        if all(keyed(value) or value==[] for value in (old,new,own)):
            mapped=merge(*({item['id']:item for item in value} for value in (old,new,own)),path)
            order=list(dict.fromkeys([item['id'] for item in new]+[item['id'] for item in own]))
            return [mapped[key] for key in order if key in mapped]
        if all(isinstance(value,list) for value in (old,new,own)) and len(old)==len(new)==len(own):
            return [merge(a,b,c,f'{path}[{index}]') for index,(a,b,c) in enumerate(zip(old,new,own))]
        conflicts.append(path)
        return deepcopy(own)
    return merge(base,parent,local,'material'),conflicts
