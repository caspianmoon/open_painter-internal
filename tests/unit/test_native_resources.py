"""Shared resource admission and host metadata allocation rollback."""
from collections import OrderedDict
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.native_bridge import load_library
from open_painter.native_bridge.resources import NativePool
from open_painter.evaluation.engine.program_cache import ProgramCache
from unittest.mock import patch


class FailInsert(dict):
    def __setitem__(self, key, value): raise MemoryError('registry allocation')


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class ResourceTests(unittest.TestCase):
    def pool(self):
        lib,_=load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        dev=SimpleNamespace(pool=OrderedDict(),_leases={},_pooled_ids=set(),_pooled_bytes=0)
        return NativePool(lib,dev),dev

    def test_metadata_allocation_failure_rolls_back_native_lease(self):
        for field in ('objects','tokens','_leases'):
            pool,dev=self.pool()
            owner=dev if field=='_leases' else pool
            original=getattr(owner,field)
            setattr(owner,field,FailInsert())
            try:
                with self.assertRaisesRegex(MemoryError,'registry allocation'):
                    pool.register(SimpleNamespace(width=8,height=8,format='RGBA32F'),8,8,'RGBA32F')
                self.assertEqual(pool.memory(),dict(leased_bytes=0,pooled_bytes=0))
                self.assertFalse(pool.objects or pool.tokens or dev._leases)
                setattr(owner,field,original)
                pool.register(SimpleNamespace(width=8,height=8,format='RGBA32F'),8,8,'RGBA32F')
                self.assertEqual(pool.memory()['leased_bytes'],1024)
            finally: pool.close()

    def test_global_admission_retires_only_free_storage(self):
        pool,dev=self.pool()
        try:
            pool.configure(4096,3,2048)
            a=SimpleNamespace(width=8,height=8,format='RGBA32F')
            b=SimpleNamespace(width=8,height=8,format='RGBA32F')
            pool.register(a,8,8,'RGBA32F');pool.register(b,8,8,'RGBA32F')
            self.assertFalse(pool.admit(8,8,'RGBA32F'))
            pool.release(b,8,'RGBA32F')
            self.assertTrue(pool.admit(8,8,'RGBA32F'))
            self.assertIn(id(a),dev._leases)
            self.assertNotIn(id(b),pool.tokens)
            self.assertEqual(pool.memory(),dict(leased_bytes=1024,pooled_bytes=0))
        finally: pool.close()

    def test_bounded_shader_policy_keeps_live_borrowers(self):
        lib,_=load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])
        for selected in (lib,None):
            with patch('open_painter.native_bridge.lifecycle.active_library',return_value=selected):
                cache=ProgramCache(budget=6,minimum=2)
                a,b,c,d=(object() for _ in range(4))
                cache.put('a',a,1);cache.put('b',b,1);cache.put('c',c,1)
                self.assertIs(cache.get('a'),a)
                self.assertEqual(cache.put('d',d,1),[b])
                self.assertEqual(len(cache),3)
                self.assertEqual(cache.bytes,6)
                self.assertIs(cache.get('a'),a)
                self.assertIsNotNone(b)  # the caller's active shader is still owned
                retired=cache.put('large',object(),12)
                self.assertEqual(len(retired),4)
                self.assertEqual(len(cache),0)
                self.assertEqual(cache.bytes,0)
                cache.close()
