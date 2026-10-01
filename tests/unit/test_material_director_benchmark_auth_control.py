"""Synthetic scheduler boundaries; never reads a real account or invokes Blender."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_material_director_benchmark import configuration, manifest, runner


class AuthSchedulingTests(unittest.TestCase):
    def prepared(self, root):
        fixtures=root/'fixtures';fixtures.mkdir()
        pins={}
        for case in manifest.load_cases()['cases']:
            path=fixtures/case['fixture'];path.write_bytes(b'synthetic no-provider fixture')
            pins[path.name]={'sha256':manifest.file_sha256(path)}
        comparison=root/'comparison'
        frozen=manifest.freeze_manifest(comparison,configuration(),pins)
        return fixtures,comparison,frozen

    def test_each_wave_preflights_and_expiry_keeps_future_sessions_resumable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixtures,comparison,frozen=self.prepared(Path(temporary));calls=[]
            def execute(job,**kwargs):
                calls.append(job['run_id'])
                result=manifest.result_template(next(e for e in frozen['schedule'] if e['run_id']==job['run_id']))
                result.update(status='FAILED',attempted=True,reason='Synthetic attempted boundary; not a live model result')
                runner.write_json(Path(job['output'])/'attempt.json',{'synthetic':True})
                runner.write_json(Path(job['output'])/'result.json',result)
                return result
            args=['run','--comparison',str(comparison),'--fixtures',str(fixtures),'--use-codex-login']
            with patch.object(runner,'_verify_run_pins',return_value='synthetic'),patch.object(runner,'execute',side_effect=execute),redirect_stdout(io.StringIO()):
                with patch.object(runner,'preflight_login',side_effect=[{'status':'READY'},runner.AuthRefreshRequired('Synthetic expired login')]) as preflight:
                    self.assertEqual(runner.main(args),3)
                    self.assertEqual(preflight.call_count,2)
                self.assertEqual(calls,[frozen['schedule'][0]['run_id']])
                first=comparison/frozen['schedule'][0]['relative_directory']/'result.json';first_bytes=first.read_bytes()
                self.assertEqual(json.loads((comparison/'execution_control.json').read_text(encoding="utf-8"))['status'],'AUTH_REFRESH_REQUIRED')
                for entry in frozen['schedule'][1:]:
                    directory=comparison/entry['relative_directory']
                    self.assertEqual(json.loads((directory/'result.json').read_text(encoding="utf-8"))['status'],'NOT_RUN')
                    self.assertFalse((directory/'attempt.json').exists())
                with patch.object(runner,'preflight_login',return_value={'status':'READY'}):
                    self.assertEqual(runner.main(args+['--max-sessions','1']),0)
                self.assertEqual(calls,[entry['run_id'] for entry in frozen['schedule'][:2]])
                self.assertEqual(first.read_bytes(),first_bytes)

    def test_auth_race_after_preflight_restores_unattempted_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixtures,comparison,frozen=self.prepared(Path(temporary))
            with patch.object(runner,'_verify_run_pins',return_value='synthetic'),patch.object(runner,'preflight_login',return_value={'status':'READY'}),patch.object(runner,'execute',side_effect=runner.AuthRefreshRequired('Synthetic auth changed after preflight')),redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['run','--comparison',str(comparison),'--fixtures',str(fixtures),'--use-codex-login']),3)
            for entry in frozen['schedule']:
                directory=comparison/entry['relative_directory']
                self.assertEqual(json.loads((directory/'result.json').read_text(encoding="utf-8"))['status'],'NOT_RUN')
                self.assertFalse((directory/'attempt.json').exists())
            report=json.loads((comparison/'report.json').read_text(encoding="utf-8"))
            self.assertEqual(report['failures'],[])

    def test_smoke_stops_once_before_snapshot_or_any_attempt_on_expired_login(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'smoke'
            args=['smoke','--fixtures',str(Path(temporary)/'unused-fixtures'),'--output',str(output),
                '--model','synthetic','--use-codex-login']
            with patch.object(runner,'preflight_login',side_effect=runner.AuthRefreshRequired('Synthetic expired login')) as preflight,patch.object(runner,'execute') as execute,patch.object(runner,'snapshot_runtime') as snapshot,redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(args),3)
                self.assertEqual(preflight.call_count,1)
                execute.assert_not_called();snapshot.assert_not_called()
                self.assertEqual([p.name for p in output.iterdir()],['execution_control.json'])
                self.assertEqual(runner.main(args),3)
                self.assertEqual(preflight.call_count,2)
                self.assertFalse(list(output.rglob('attempt.json')))

    def test_partial_smoke_resumes_only_unattempted_cases_with_same_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);fixtures=root/'fixtures';fixtures.mkdir();output=root/'smoke';calls=[]
            for case in runner.load_cases()['cases']:
                (fixtures/case['fixture']).write_bytes(b'synthetic fixture')
            pin={'source_sha256':'b'*64,'commit':'a'*40,'fingerprint_scheme':'synthetic-test'}
            def snapshot(path):
                path.mkdir();return pin
            def execute(job,**kwargs):
                calls.append(job['run_id']);destination=Path(job['output'])
                result={'status':'FAILED','run_id':job['run_id'],'synthetic_boundary':True}
                runner.write_json(destination/'attempt.json',{'synthetic':True})
                runner.write_json(destination/'result.json',result)
                return result
            args=['smoke','--fixtures',str(fixtures),'--output',str(output),'--model','synthetic','--use-codex-login']
            with patch.object(runner,'source_fingerprint',return_value=pin),patch.object(runner,'snapshot_runtime',side_effect=snapshot) as copy,patch.object(runner,'execute',side_effect=execute),redirect_stdout(io.StringIO()):
                with patch.object(runner,'preflight_login',side_effect=[{'status':'READY'},{'status':'READY'},runner.AuthRefreshRequired('Synthetic expired between waves')]):
                    self.assertEqual(runner.main(args),3)
                first=output/'T01/H/1/result.json';original=first.read_bytes();frozen=(output/'manifest.json').read_bytes()
                self.assertEqual(len(calls),1)
                with patch.object(runner,'preflight_login',return_value={'status':'READY'}):
                    self.assertEqual(runner.main(args+['--resume']),0)
                self.assertEqual(copy.call_count,1)
                self.assertEqual(len(calls),5);self.assertEqual(len(set(calls)),5)
                self.assertEqual(first.read_bytes(),original)
                self.assertEqual((output/'manifest.json').read_bytes(),frozen)
                self.assertEqual(len(json.loads((output/'smoke_index.json').read_text(encoding="utf-8"))['cases']),5)


if __name__=='__main__':
    unittest.main()
