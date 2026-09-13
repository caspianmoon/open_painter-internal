"""Explicit three-way update preview for declarative material metadata.

No live parent subscriptions. Local changes win conflicts and every conflict is
returned for review. Layer lists merge by stable IDs; assets remain opaque data.
"""
from copy import deepcopy


def preview(base,parent,local):
    conflicts=[]
    missing=object()
    def merge(old,new,own,path):
        if own==old: return deepcopy(new)
        if new==old or own==new: return deepcopy(own)
        if all(isinstance(value,dict) for value in (old,new,own)):
            result={}
            for key in old.keys()|new.keys()|own.keys():
                a,b,c=old.get(key,missing),new.get(key,missing),own.get(key,missing)
                if b is missing and c==a: continue
                if c is missing:
                    if b!=a: conflicts.append(path+'.'+key)
                    continue
                if b is missing:
                    conflicts.append(path+'.'+key); result[key]=deepcopy(c)
                else: result[key]=merge(a,b,c,path+'.'+key)
            return result
        if all(isinstance(value,list) and all(isinstance(item,dict) and 'id' in item for item in value) for value in (old,new,own)):
            mapped=merge(*({item['id']:item for item in value} for value in (old,new,own)),path)
            order=list(dict.fromkeys([item['id'] for item in new]+[item['id'] for item in own]))
            return [mapped[key] for key in order if key in mapped]
        conflicts.append(path)
        return deepcopy(own)
    return merge(base,parent,local,'material'),conflicts
