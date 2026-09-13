"""Bounded surface prototypes; no volumetric cross-section claims."""


def mesh_data(kind):
    if kind=='SNOW':
        return [(-.5,0,.2),(.5,0,.2),(0,-.5,.2),(0,.5,.2),(0,0,.6),(0,0,0)], [(0,2,4),(2,1,4),(1,3,4),(3,0,4),(2,0,5),(1,2,5),(3,1,5),(0,3,5)]
    if kind=='SPLINTERS':
        return [(-.08,-1,0),(.08,-1,0),(.12,.6,0),(0,1,.15),(-.12,.6,0),(0,-.6,.15)], [(0,4,3,2,1),(0,1,5),(1,2,3,5),(3,4,0,5)]
    if kind=='PEEL':
        return [(-.5,-.5,0),(.5,-.5,0),(-.5,0,.05),(.5,0,.05),(-.5,.5,.4),(.5,.5,.4)], [(0,1,3,2),(2,3,5,4)]
    raise ValueError('Unknown advanced geometry prototype')
