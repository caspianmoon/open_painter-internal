"""GPU root-folder checkpoint parity, invalidation and bounded ownership.

Run Blender with --factory-startup --python and a real GPU window. Readbacks
below are validation only; the production checkpoint path performs no copies,
uploads, image writes or readbacks when it reuses a completed group.
"""
from collections import Counter
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
import gpu
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from open_painter.evaluation.live_gpu import Device, MODES
from open_painter.evaluation.metrics import Metrics

REPORT=Path(os.environ.get('OP_CHECKPOINT_REPORT','/private/tmp/op-live-checkpoints.json'))


def run():
    assert not bpy.app.background,'A real Blender GPU window is required'
    n=32
    size=n*n*16
    device=Device(checkpoint_bytes=size*2)
    reference=Device(checkpoint_bytes=0)
    target=gpu.types.GPUTexture((n,n),format='RGBA32F')
    expected=gpu.types.GPUTexture((n,n),format='RGBA32F')
    checks=[]
    rng=np.random.default_rng(51073)
    max_error=0.
    def leaf(mode='NORMAL',opacity=.71):
        texture=device.upload(rng.random((n,n,4),dtype=np.float32))
        return dict(mode=mode,opacity=opacity,source=dict(texture=texture))
    def root(owner='owner-a',signature=1,channel='BASE_COLOR',**changes):
        result=dict(children=[leaf(),leaf('MULTIPLY'),dict(children=[leaf('SCREEN'),leaf('OVERLAY')],opacity=.67),leaf()],
                    passthrough=True,opacity=.63)
        result.update(changes)
        result.setdefault('checkpoint_key',(owner,n,channel,'root-folder',(result['passthrough'],signature)))
        return result
    def compare(name,layers,channel='BASE_COLOR'):
        nonlocal max_error
        reference.compose(layers,channel,n,expected)
        with Metrics() as report:
            device.compose(layers,channel,n,target)
        actual=np.array(target.read(),np.float32)
        wanted=np.array(expected.read(),np.float32)
        error=float(np.max(np.abs(actual-wanted)))
        max_error=max(max_error,error)
        np.testing.assert_array_equal(actual,wanted,err_msg=name)
        assert not device._leased
        assert not ({id(texture) for texture in device._pool}&
                    {id(texture) for texture,_ in device._checkpoints.values()})
        assert device._checkpoint_bytes<=device._checkpoint_limit
        checks.append(name)
        return report
    try:
        folder=root()
        first=compare('first-fractional-edit-miss',[folder])
        assert first.counts['live_checkpoint_misses']==1
        assert first.counts['live_checkpoint_adoptions']==1
        folder['opacity']=.27
        warm=compare('folder-opacity-edit-reuses-children',[folder])
        assert warm.counts['live_checkpoint_hits']==1
        assert warm.counts['live_gpu_passes']==3
        assert warm.counts['live_gpu_allocations']==0
        assert warm.counts['live_gpu_uploads']==0
        assert first.counts['live_gpu_passes']>warm.counts['live_gpu_passes']
        folder['mask']=device.upload(rng.random((n,n),dtype=np.float32))
        masked=compare('folder-mask-edit-reuses-children',[folder])
        assert masked.counts['live_checkpoint_hits']==1
        folder['mask']=device.upload(rng.random((n,n),dtype=np.float32))
        compare('second-folder-mask-edit',[folder])
        folder['opacity']=1.
        compare('masked-unit-folder-still-caches',[folder])
        del folder['mask']
        unit=compare('unit-unmasked-folder-elides-checkpoint',[folder])
        assert unit.counts['live_checkpoint_hits']==0
        assert unit.counts['live_checkpoint_adoptions']==0
        assert unit.counts['live_gpu_passthrough_elisions']>=1
        folder['opacity']=.48
        compare('fractional-after-unit-reuses-valid-checkpoint',[folder])
        for mode in MODES:
            folder=root(signature=('blend',mode),children=[leaf(mode),leaf('MULTIPLY')])
            compare(mode+':cold',[folder])
            folder['opacity']=.23
            hit=compare(mode+':warm',[folder])
            assert hit.counts['live_checkpoint_hits']==1
        normal=root(signature='normal',channel='NORMAL',children=[leaf('RNM'),leaf('NORMAL_COMBINE')])
        compare('normal-final-normalization-cold',[normal],'NORMAL')
        normal['opacity']=.19
        compare('normal-final-normalization-warm',[normal],'NORMAL')

        # Worn Painted Metal uses an isolated root. Its checkpoint must retain
        # premultiplied RGB AND partial coverage, before root mask/opacity/blend.
        device.discard_checkpoints()
        a=rng.random((n,n,4),dtype=np.float32)
        b=rng.random((n,n,4),dtype=np.float32)
        a[:4,:,3]=b[:4,:,3]=0
        a[4:8,:,3]=b[4:8,:,3]=1
        a[8:,:,3],b[8:,:,3]=.25,.65
        isolated=root(signature='isolated-coverage',passthrough=False,children=[
            dict(mode='MULTIPLY',source=dict(texture=device.upload(a))),
            dict(mode='SCREEN',source=dict(texture=device.upload(b)))])
        isolated_first=compare('isolated-root-cold-retains-coverage',[isolated])
        assert isolated_first.counts['live_checkpoint_adoptions']==1
        checkpoint=device._checkpoints[isolated['checkpoint_key']][0]
        coverage=np.array(checkpoint.read(),np.float32).reshape(n,n,4)[...,3]
        np.testing.assert_array_equal(coverage[:4],0)
        np.testing.assert_array_equal(coverage[4:8],1)
        assert np.all((coverage[8:]>0)&(coverage[8:]<1))
        isolated['opacity']=.29
        isolated_warm=compare('isolated-opacity-reuses-complete-coverage',[isolated])
        assert isolated_warm.counts['live_checkpoint_hits']==1
        assert isolated_warm.counts['live_gpu_passes']==3
        assert isolated_warm.counts['live_gpu_allocations']==0
        assert isolated_warm.counts['live_gpu_uploads']==0
        for index in range(2):
            isolated['mask']=device.upload(rng.random((n,n),dtype=np.float32))
            hit=compare('isolated-mask-edit-'+str(index),[isolated])
            assert hit.counts['live_checkpoint_hits']==1
        isolated['mode']='MULTIPLY'
        hit=compare('isolated-root-blend-reuses-children',[isolated])
        assert hit.counts['live_checkpoint_hits']==1
        # Same children evaluated against an opaque backdrop are a different
        # intermediate. Group mode belongs to the final key component.
        for passthrough in (True,False):
            isolated['passthrough']=passthrough
            isolated['checkpoint_key']=('owner-a',n,'BASE_COLOR','root-folder',
                                        (passthrough,'isolated-coverage'))
            changed=compare('group-mode-change-'+str(passthrough),[isolated])
            assert changed.counts['live_checkpoint_misses']==1
            assert changed.counts['live_checkpoint_invalidations']==1
        isolated['children'][0]=leaf('DIFFERENCE')
        isolated['checkpoint_key']=('owner-a',n,'BASE_COLOR','root-folder',(False,'edited-child'))
        changed=compare('isolated-child-edit-invalidates-checkpoint',[isolated])
        assert changed.counts['live_checkpoint_misses']==1
        assert changed.counts['live_checkpoint_invalidations']==1

        device.discard_checkpoints()
        old=root(signature='before')
        compare('children-before-edit',[old])
        old['children'][0]=leaf('DIFFERENCE')
        old['checkpoint_key']=('owner-a',n,'BASE_COLOR','root-folder',(True,'after'))
        edited=compare('children-edit-misses-and-prunes-history',[old])
        assert edited.counts['live_checkpoint_misses']==1
        assert edited.counts['live_checkpoint_invalidations']==1
        assert len(device._checkpoints)==1
        old['checkpoint_key']=('owner-a',n,'BASE_COLOR','replacement-folder',(True,'after'))
        compare('replacement-root-prunes-history',[old])
        assert len(device._checkpoints)==1

        device.discard_checkpoints()
        a,b,c=root('a'),root('b'),root('c')
        compare('LRU-a',[a]);compare('LRU-b',[b]);compare('LRU-touch-a',[a])
        third=compare('LRU-c-evicts-b',[c])
        assert third.counts['live_checkpoint_evictions']==1
        assert {key[0] for key in device._checkpoints}=={'a','c'}
        assert device._checkpoint_bytes==size*2
        device.discard_checkpoints('a')
        assert {key[0] for key in device._checkpoints}=={'c'}
        device.prune_checkpoints({'a','b'})
        assert not device._checkpoints and device._checkpoint_bytes==0

        # One root touching more channels than fit is composed as a cycle on
        # every edit. Resident sibling channels must keep hitting instead of
        # thrashing, and no scratch texture may be allocated per edit.
        cycle=[root('cycle',channel=channel) for channel in ('BASE_COLOR','ROUGHNESS','METALLIC')]
        def edit(opacity):
            totals=Counter()
            for folder in cycle:
                folder['opacity']=opacity
                channel=folder['checkpoint_key'][2]
                totals.update(compare('cycle-'+channel+'-'+str(opacity),[folder],channel).counts)
            return totals
        cold=edit(.63)
        assert cold['live_checkpoint_adoptions']==2
        assert cold['live_checkpoint_capacity_skips']==1
        assert cold['live_checkpoint_evictions']==0
        for opacity in (.41,.22):
            warm_cycle=edit(opacity)
            assert warm_cycle['live_checkpoint_hits']==2 and warm_cycle['live_checkpoint_misses']==1
            assert warm_cycle['live_checkpoint_capacity_skips']==1
            assert warm_cycle['live_checkpoint_evictions']==warm_cycle['live_checkpoint_adoptions']==0
            assert warm_cycle['live_gpu_allocations']==0
        pool=len(device._pool)
        # Another owner's edit still ages the cycle out; the evicted texture
        # returns to the scratch pool rather than being freed and reallocated.
        other=root('other',channel='NORMAL')
        evicting=compare('other-owner-evicts-resident-cycle',[other],'NORMAL')
        assert evicting.counts['live_checkpoint_evictions']==1
        assert evicting.counts['live_checkpoint_adoptions']==1
        assert evicting.counts['live_gpu_allocations']==0
        assert len(device._pool)==pool
        assert {key[:3] for key in device._checkpoints}=={('cycle',n,'ROUGHNESS'),('other',n,'NORMAL')}
        cycle[1]['children'][0]=leaf('DIFFERENCE')
        cycle[1]['checkpoint_key']=('cycle',n,'ROUGHNESS','root-folder',(True,'edited'))
        replaced=compare('child-edit-replaces-checkpoint-without-allocation',[cycle[1]],'ROUGHNESS')
        assert replaced.counts['live_checkpoint_invalidations']==1
        assert replaced.counts['live_checkpoint_adoptions']==1
        assert replaced.counts['live_gpu_allocations']==0
        assert len(device._pool)==pool
        device.discard_checkpoints()

        # An untrusted annotation on another backdrop must never get cached.
        annotation=root(signature='wrong-position')
        later=compare('annotation-on-second-root-ignored',[leaf(),annotation])
        assert later.counts['live_checkpoint_hits']==later.counts['live_checkpoint_adoptions']==0
        nested=compare('annotation-on-nested-folder-ignored',[
            dict(children=[annotation],passthrough=True,opacity=.4)])
        assert nested.counts['live_checkpoint_adoptions']==0
        stale_mode=root(passthrough=False,checkpoint_key=('owner-a',n,'BASE_COLOR','root-folder',(True,1)))
        ignored=compare('annotation-with-wrong-group-mode-ignored',[stale_mode])
        assert ignored.counts['live_checkpoint_adoptions']==0
        wrong=root(checkpoint_key=('owner-a',64,'BASE_COLOR','root-folder',(True,1)))
        compare('wrong-resolution-key-ignored',[wrong])
        assert not device._checkpoints

        folder=root(signature='good-before-failure')
        compare('successful-output-before-failed-job',[folder])
        before=np.array(target.read(),np.float32)
        failed=root(signature='broken',children=[leaf(),leaf('INVALID')])
        try:
            device.compose([failed],'BASE_COLOR',n,target)
        except ValueError:
            pass
        else:
            raise AssertionError('Failed child job was accepted')
        np.testing.assert_array_equal(np.array(target.read()),before)
        assert not device._checkpoints and not device._leased
        checks.append('failed-child-never-published-or-cached')
        compare('recovery-after-failed-child',[folder])
        before=np.array(target.read(),np.float32)
        try:
            device.compose([folder,leaf('INVALID')],'BASE_COLOR',n,target)
        except ValueError:
            pass
        else:
            raise AssertionError('Failed later layer was accepted')
        np.testing.assert_array_equal(np.array(target.read()),before)
        assert len(device._checkpoints)==1 and not device._leased
        compare('complete-checkpoint-survives-unrelated-failure',[folder])

        # Exercise eviction while work is queued, without per-frame CPU fences.
        for index in range(12):
            queued=root(owner='queued-'+str(index),children=[leaf(),leaf('OVERLAY')])
            device.compose([queued],'BASE_COLOR',n,target)
        compare('queued-eviction-resource-lifetime',[queued])
        other=gpu.types.GPUTexture((16,16),format='RGBA32F')
        device.compose([],'BASE_COLOR',16,other)
        assert not device._checkpoints and device._checkpoint_bytes==0
        checks.append('resolution-change-clears-checkpoints')

        small=Device(checkpoint_bytes=1)
        try:
            with Metrics() as oversized:
                small.compose([root()],'BASE_COLOR',n,target)
            assert oversized.counts['live_checkpoint_oversize_skips']==1
            assert not small._checkpoints
        finally:
            small.close()
        checks.append('oversize-result-remains-uncached')
        record=dict(passed=True,blender=bpy.app.version_string,backend=gpu.platform.backend_type_get(),
                    checks=checks,max_error=max_error,cold_counters=dict(first.counts),warm_counters=dict(warm.counts),
                    isolated_cold_counters=dict(isolated_first.counts),isolated_warm_counters=dict(isolated_warm.counts))
        REPORT.write_text(json.dumps(record,indent=2))
        print('LIVE_CHECKPOINT_PASS',json.dumps(record),flush=True)
    finally:
        device.close();reference.close()
        assert not device._checkpoints and device._checkpoint_bytes==0


def main():
    try:
        run()
    except Exception:
        detail=traceback.format_exc()
        REPORT.write_text(json.dumps(dict(passed=False,traceback=detail),indent=2))
        print(detail,flush=True)
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(main,first_interval=1.)
