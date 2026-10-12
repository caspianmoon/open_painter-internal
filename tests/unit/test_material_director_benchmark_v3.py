"""V3 control/evidence regressions. Synthetic files never represent live AI quality."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import threading
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/benchmarks'))
from material_director import manifest, outcomes, runner, scoring
from material_director.png_validation import decode_png
from material_director.view_inventory import filenames
spec = importlib.util.spec_from_file_location('director_gallery', ROOT/'scripts/benchmark_material_director_gallery.py')
gallery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gallery)


def png(size=1024):
    def chunk(kind, payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    pixels=(b'\x00'+b'\x80\x90\xa0'*size)*size
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def synthetic_no_edit(config, root):
    """Synthetic gate boundary, never an actual no-edit/evaluator result."""
    proof=dict(status='PASS',kind='SYNTHETIC_UNIT_TEST_NO_EVALUATOR',product=config['builds']['improved'],
        frozen_tolerances=dict(mask_absolute_tolerance=1e-6,channel_absolute_tolerance=1e-6),
        cases={f'T{i:02d}':dict(status='PASS',actual_recaptures=2,source_unchanged=True,
            maximum_channel_delta=0.,maximum_mask_delta=0.) for i in range(1,6)},
        implementation={name:manifest.file_sha256(ROOT/'tests/benchmarks/material_director'/name)
            for name in ('capture_evidence.py','revision_measurements.py')})
    path=root/'synthetic-repeatability.json';write(path,proof)
    config['evidence']['no_edit_calibration']=dict(status='PASS',evidence=str(path),sha256=manifest.file_sha256(path),
        report=proof,report_sha256=manifest.digest(proof))


def example(root, execution='APPLIED'):
    cases=manifest.load_cases()['cases']
    entry={'run_id':'unit-run','kind':'core','case_id':'T03','mode':'H','repeat':1,'relative_directory':'T03/H/1'}
    write(root/'manifest.json',dict(schema_version=3,protocol=manifest.PROTOCOL,
        configuration=dict(comparison_id='synthetic-gallery',build={'commit':'unit-test'},provider={'model_id':'synthetic-no-model'}),
        specification=dict(cases=cases),schedule=[entry],fixtures={}))
    directory=root/entry['relative_directory'];directory.mkdir(parents=True)
    stage={'name':'create','status':execution,'applied':execution=='APPLIED','receipt_count':1 if execution=='APPLIED' else 0,
           'response':'Synthetic response','submitted':True,'visible_complete':execution=='APPLIED'}
    if execution=='PROVIDER_FAILED':stage.update(provider_status='failed',status='RESPONSE_ONLY')
    result=dict(record_version=3,status='FAILED',stages=[stage],run_id='unit-run',generated_inputs={'assigned':0})
    write(directory/'result.json',result)
    (directory/'provider_and_tool_trace.jsonl').write_text('{}\n', encoding="utf-8")
    for name in ('00_before.png','01_create_front.png','02_create_back.png','03_create_close.png','v3_create_underside.png'):
        (directory/name).write_bytes(png())
    for label,names in [('before',['00_before.png']),('after_create',['01_create_front.png','02_create_back.png','03_create_close.png','v3_create_underside.png'])]:
        write(directory/(label+'_state.json'),{'views':{'status':'CAPTURED','views':[
            {'path':name,'pose':pose,'sha256':gallery.digest(directory/name)} for pose,name in zip(('front','back','close','underside'),names)]}})
    return entry,directory


class V3ProtocolTests(unittest.TestCase):
    def test_exact_v3_prompts_and_balanced_sixty_sessions(self):
        source=ROOT/'Open_Painter_Material_Director_Capability_Remedy.md'
        if not source.is_file():
            self.skipTest('the benchmark brief is not part of this checkout')
        spec=manifest.load_cases(); doc=source.read_text(encoding="utf-8")
        for case in spec['cases']:
            for prompt in [case['create'],*case['refinements']]:self.assertIn(prompt,doc)
        schedule=manifest.build_schedule(); self.assertEqual(len(schedule),60)
        self.assertEqual(len({r['run_id'] for r in schedule}),60)
        self.assertEqual(len({r['review_id'] for r in schedule}),60)
        for i in range(0,60,2):
            pair=schedule[i:i+2]
            self.assertEqual({r['build_id'] for r in pair},{'baseline','improved'})
            self.assertEqual(len({(r['case_id'],r['mode'],r['repeat']) for r in pair}),1)
        for case in spec['cases']:
            self.assertEqual(sum(r['case_id']==case['id'] for r in schedule),12)
        self.assertEqual(schedule,manifest.build_schedule())
        self.assertEqual(len(manifest.build_schedule(correction_ablation=True)),90)

    def test_v3_view_inventory_preserves_originals_and_adds_required_angles(self):
        self.assertEqual(len(filenames({'case_id':'T03','protocol_version':2})),10)
        self.assertEqual(len(filenames({'case_id':'T03','protocol_version':3})),13)
        self.assertEqual(len(filenames({'case_id':'T04','protocol_version':3})),16)
        self.assertEqual(len(filenames({'case_id':'T05','protocol_version':3})),13)
        self.assertEqual(len(filenames({'case_id':'T01','protocol_version':3})),10)

    def test_v2_adapter_is_read_only_and_provider_failure_is_not_region_guess(self):
        original={'stages':[{'name':'create','status':'RESPONSE_ONLY','provider_status':'failed','receipt_count':0,'response':''}]}
        saved=deepcopy(original); result=outcomes.adapt_result(original)
        self.assertEqual(original,saved)
        self.assertEqual(result['stages'][0]['execution'],{'state':'PROVIDER_FAILED','cause_code':'PROVIDER_TERMINAL_FAILED'})
        self.assertTrue(all(s['execution']['state']=='SKIPPED_DEPENDENCY' for s in result['stages'][1:]))
        self.assertTrue(all(s['execution']['dependency_stage']=='create' for s in result['stages'][1:]))

    def test_question_requires_structured_outcome_never_question_mark_guess(self):
        self.assertEqual(outcomes.terminal_execution({'response':'Why?','submitted':True})['state'],'RESPONSE_ONLY')
        self.assertEqual(outcomes.terminal_execution({}, {'execution':'NEEDS_INPUT','cause_code':'REGION_UNRESOLVED'})['state'],'NEEDS_INPUT')
        self.assertEqual(outcomes.terminal_execution({'validation_errors':['bad input']})['state'],'VALIDATION_FAILED')

    def test_quality_policy_is_frozen_and_failure_capture_cannot_pass(self):
        result={'status':'FAILED','stages':[{'name':'create','applied':False,'visible_complete':True,
                'evidence':{'state':'CAPTURED_VERIFIED','role':'UNCHANGED_AFTER_ATTEMPT'}}]}
        scored=scoring.score_session(result,{'requirements':[]})
        self.assertEqual(scored['verdict'],'FAIL');self.assertIsNone(scored['final_score'])
        self.assertEqual(scored['quality_status'],'NOT_ASSESSABLE')
        with self.assertRaises(ValueError):scoring.score_session(result,{'requirements':[]},policy=dict(scoring.QUALITY_POLICY,final_overall_minimum=0))

    def test_no_op_review_cannot_reclassify_failed_execution_or_failure_capture(self):
        from test_director_quality_gate_regressions import records
        for execution,role in [('PROVIDER_FAILED',None),('NEEDS_INPUT',None),('VALIDATION_FAILED',None),
                               ('CANCELLED',None),('RESPONSE_ONLY','UNCHANGED_AFTER_ATTEMPT'),
                               ('RESPONSE_ONLY','RECOVERED_EVIDENCE')]:
            with self.subTest(execution=execution,role=role):
                result,case,review=records();stage=result['stages'][-1]
                stage.update(applied=False,execution={'state':execution},evidence={'role':role})
                review['stages']['refine_2']['no_op_acceptance']={'passed':True,'evidence':['synthetic-unit-only']}
                value=scoring.score_session(result,case,review)
                self.assertEqual(value['verdict'],'FAIL')
                self.assertFalse(value['stages']['refine_2']['delivery'])
                self.assertEqual(value['quality_status'],'NOT_ASSESSABLE')
        result,case,review=records();result['stages'][-1].update(applied=False,execution={'state':'RESPONSE_ONLY'})
        review['stages']['refine_2']['no_op_acceptance']={'passed':True,'evidence':['synthetic-unit-only']}
        self.assertEqual(scoring.score_session(result,case,review)['verdict'],'PASS')

    def test_v3_configuration_requires_both_real_build_pins(self):
        with self.assertRaisesRegex(ValueError,'baseline'):
            manifest.validate_configuration(manifest.configuration_template())
        self.assertEqual(manifest.configuration_template()['calibration']['reviewer'],'PENDING')

    def test_no_edit_calibration_and_live_smoke_cannot_be_setup_only(self):
        config=manifest.configuration_template()
        with self.assertRaisesRegex(ValueError,'no-edit'):
            manifest.validate_no_edit_calibration(config)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);synthetic_no_edit(config,root)
            manifest.validate_no_edit_calibration(config)
            control=config['evidence']['no_edit_calibration']
            control['report']['cases']['T01']['maximum_mask_delta']=.1
            control['report_sha256']=manifest.digest(control['report'])
            with self.assertRaisesRegex(ValueError,'tolerance'):
                manifest.validate_no_edit_calibration(config)
            cases=[]
            for i in range(1,6):
                directory=root/f'T{i:02d}';result=directory/'result.json'
                write(result,dict(pilot=True,quick=False,status='UNAVAILABLE',attempted=False,stages=[]))
                write(directory/'job.json',dict(protocol_version=3,limits=config['limits']))
                cases.append(dict(case_id=f'T{i:02d}',result_path=str(result),inspection_notes='Synthetic setup failure'))
            index=root/'smoke_index.json';write(index,dict(status='INSPECTED',cases=cases))
            config['pilot']['evidence']=str(index)
            with self.assertRaisesRegex(ValueError,'submitted provider'):
                runner._verify_pilot(config)

    def test_live_smoke_pins_exact_product_provider_and_environment(self):
        """Synthetic transport records prove only the freeze boundary, not AI quality."""
        config=manifest.configuration_template()
        config['builds']['improved']={'commit':'a'*40,'source_sha256':'b'*64}
        config['provider'].update(runtime_version='synthetic-runtime',returned_model_id='synthetic-model')
        config['environment'].update(blender_version='synthetic-blender',render_backend='BLENDER_EEVEE',
            evaluator_backend='synthetic-evaluator',hardware={'system':'synthetic'},material_library_sha256='c'*64)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cases=[]
            for i in range(1,6):
                directory=root/f'T{i:02d}';result=directory/'result.json'
                write(result,dict(pilot=True,quick=False,status='FAILED',attempted=True,
                    stages=[dict(name='create',submitted=True)],provider=config['provider'],
                    environment=dict(build=config['builds']['improved'],blender_version='synthetic-blender',
                        render_engine='BLENDER_EEVEE',evaluation='synthetic-evaluator',hardware={'system':'synthetic'},
                        material_library_sha256='c'*64,png_decoder=config['environment']['png_decoder'])))
                write(directory/'job.json',dict(protocol_version=3,limits=config['limits'],build=config['builds']['improved']))
                cases.append(dict(case_id=f'T{i:02d}',result_path=str(result),inspection_notes='Synthetic boundary inspected'))
            index=root/'smoke_index.json';write(index,dict(status='INSPECTED',cases=cases));config['pilot']['evidence']=str(index)
            runner._verify_pilot(config)
            for keys in [('provider','runtime_version'),('provider','returned_model_id'),
                         ('environment','blender_version'),('environment','render_backend'),
                         ('environment','evaluator_backend'),('environment','hardware'),
                         ('environment','material_library_sha256'),('environment','png_decoder'),('builds','improved')]:
                changed=deepcopy(config);changed[keys[0]][keys[1]]='different-pin'
                with self.subTest(pin=keys),self.assertRaisesRegex(ValueError,'Smoke .* differs'):
                    runner._verify_pilot(changed)


class PairedExecutionTests(unittest.TestCase):
    def test_c_owned_blender_signal_handlers_are_never_overwritten(self):
        import signal
        with patch.object(signal,'getsignal',return_value=None),patch.object(signal,'signal') as install:
            self.assertEqual(runner._install_cancel_handlers(threading.Event()),{})
            install.assert_not_called()
        with patch.object(signal,'getsignal',side_effect=[None,signal.SIG_DFL]),patch.object(signal,'signal') as install:
            self.assertEqual(runner._install_cancel_handlers(threading.Event()),{signal.SIGTERM:signal.SIG_DFL})
            self.assertEqual(install.call_count,1)

    def test_two_jobs_are_pinned_and_wave_results_remain_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); fixtures=root/'fixtures';fixtures.mkdir()
            config=manifest.configuration_template();config['comparison_id']='synthetic-concurrency-control'
            config['build'].update(commit='a'*40,source_sha256='b'*64)
            config['builds']['baseline']['source_sha256']='c'*64
            config['builds']['improved']={'commit':'d'*40,'source_sha256':'e'*64}
            config['provider'].update(runtime_version='synthetic',model_id='synthetic',returned_model_id='synthetic',image_generation_available=True)
            config['environment'].update(hardware='synthetic',blender_version='synthetic',render_backend='BLENDER_EEVEE',evaluator_backend='synthetic',material_library_sha256='f'*64)
            config['pilot']={'status':'PASS','evidence':'synthetic-only'}
            synthetic_no_edit(config,root)
            proof={'status':'PASS','processes':2,'isolated_capture':True,'kind':'SYNTHETIC_UNIT_TEST'}
            proof_path=root/'concurrency.json';write(proof_path,proof)
            config['execution'].update(jobs=2,policy='PAIRED_WAVES',concurrency_evidence=str(proof_path),
                concurrency_evidence_sha256=manifest.file_sha256(proof_path),concurrency_proof=proof,concurrency_proof_sha256=manifest.digest(proof))
            pins={}
            for case in manifest.load_cases()['cases']:
                path=fixtures/case['fixture'];path.write_bytes(b'synthetic-fixture-boundary')
                pins[path.name]={'sha256':manifest.file_sha256(path)}
            comparison=root/'comparison';frozen=manifest.freeze_manifest(comparison,config,pins)
            barrier=threading.Barrier(2); calls=[]
            def execute(job,**kwargs):
                calls.append(job)
                barrier.wait(timeout=3)
                entry=next(e for e in frozen['schedule'] if e['run_id']==job['run_id'])
                result=manifest.result_template(entry);result.update(status='UNAVAILABLE',reason='Synthetic no-provider concurrency boundary')
                write(Path(job['output'])/'result.json',result)
                return result
            baseline=root/'baseline';improved=root/'improved'
            def fingerprint(path):return config['builds']['baseline' if Path(path)==baseline else 'improved']
            with patch.object(runner,'preflight_login',return_value={'status':'READY'}) as preflight,patch.object(runner,'_verify_run_pins',return_value='synthetic-codex'),patch.object(runner,'product_fingerprint',side_effect=fingerprint),patch.object(runner,'execute',side_effect=execute),redirect_stdout(io.StringIO()):
                runner.main(['run','--comparison',str(comparison),'--fixtures',str(fixtures),'--baseline-checkout',str(baseline),
                    '--improved-checkout',str(improved),'--jobs','2','--max-sessions','2','--use-codex-login'])
                self.assertEqual(preflight.call_count,1)
            self.assertEqual(len(calls),2)
            self.assertEqual({j['build_id'] for j in calls},{'baseline','improved'})
            self.assertEqual(len({j['output'] for j in calls}),2)
            self.assertEqual({j['wave_id'] for j in calls},{1})
            self.assertEqual({j['concurrent_jobs'] for j in calls},{2})
            self.assertEqual(len(json.loads((comparison/'report.json').read_text(encoding="utf-8"))['core_results']),60)
            with self.assertRaises(SystemExit),redirect_stdout(io.StringIO()):
                runner.main(['run','--comparison',str(comparison),'--fixtures',str(fixtures),'--jobs','1','--use-codex-login'])

    def test_ablation_does_not_split_baseline_improved_pairs(self):
        schedule=manifest.build_schedule(correction_ablation=True)
        self.assertEqual(len(schedule),90)
        for i in range(0,60,2):
            pair=schedule[i:i+2]
            self.assertEqual({r['build_id'] for r in pair},{'baseline','improved'})
        self.assertTrue(all(r.get('ablation')=='correction' for r in schedule[60:]))
        frozen=dict(schema_version=3,protocol=manifest.PROTOCOL,configuration=manifest.configuration_template(),
            specification=manifest.load_cases(),schedule=schedule)
        report=scoring.summarize(frozen,{})
        self.assertEqual(report['denominators']['planned'],90)
        self.assertEqual(report['primary_denominators']['planned'],60)
        self.assertEqual(report['ablation_denominators']['planned'],30)
        self.assertEqual([report['modes'][m]['scheduled'] for m in ('P','H')],[30,30])
        self.assertEqual(len(report['ablation_results']),30)

    def test_stage_quality_uses_final_and_intermediate_policy_gates(self):
        from test_director_quality_gate_regressions import records
        result,case,review=records()
        review['acceptance_checks']['AC-01']['passed']=False
        self.assertEqual(scoring.score_session(result,case,review)['quality_status'],'FAIL')
        review['acceptance_checks']['AC-01']['passed']=True
        for name,appearance in [('create',2),('refine_2',3)]:
            with self.subTest(stage=name):
                edited=deepcopy(review);edited['stages'][name]['ratings'][0]['dimensions']['material_appearance']=appearance
                score=scoring.score_session(result,case,edited)
                self.assertEqual(score['stages'][name]['quality_status'],'FAIL')
                with tempfile.TemporaryDirectory() as folder:
                    root=Path(folder);example(root)
                    score['run_id']='unit-run';write(root/'report.json',dict(core_results=[score]))
                    data=gallery.collect(root,root/'gallery.html')
                    self.assertEqual(next(r for c in data['cards'] for r in c['runs'])['stages'][name]['quality']['state'],'FAIL')


class GalleryTests(unittest.TestCase):
    def test_generation_permission_requests_outputs_and_assignments_are_distinct(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);entry,directory=example(root)
            collected=gallery.collect_run(root,entry,root/'gallery.html')
            self.assertTrue(collected['generation_allowed'])
            self.assertFalse(collected['requested_generation'])
            self.assertEqual(collected['generation_requests'],0)
            rendered=gallery.render(gallery.collect(root,root/'gallery.html'))
            self.assertIn('Generation allowed True',rendered)
            self.assertIn('Observed generation requests 0',rendered)
            self.assertNotIn('Requested generation True',rendered)
            # An observed failed call is a request even without a generated image.
            result=json.loads((directory/'result.json').read_text(encoding="utf-8"))
            result['stages'][0]['generation_call_count']=1
            write(directory/'result.json',result)
            collected=gallery.collect_run(root,entry,root/'gallery.html')
            self.assertTrue(collected['requested_generation'])
            self.assertEqual(collected['generation_requests'],1)
            self.assertEqual(collected['actual_generated_images'],0)
            # Generated but unused sources and reuse retain their own evidence.
            result['actual_generated_inputs']=[{'image_id':'synthetic-unused'}]
            result['generated_inputs'].update(assigned=0,reused_source_ids=['existing-source'])
            write(directory/'result.json',result)
            collected=gallery.collect_run(root,entry,root/'gallery.html')
            self.assertEqual(collected['actual_generated_images'],1)
            self.assertEqual(collected['generated_inputs']['assigned'],0)
            self.assertEqual(collected['generated_inputs']['reused_source_ids'],['existing-source'])

    def test_portable_completion_requires_evidence_even_when_html_is_self_contained(self):
        for defect in ('missing','changed_hash'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as path:
                root=Path(path);_,directory=example(root)
                if defect=='missing':
                    (directory/'01_create_front.png').unlink()
                else:
                    state=json.loads((directory/'after_create_state.json').read_text(encoding="utf-8"))
                    state['views']['views'][0]['sha256']='0'*64
                    write(directory/'after_create_state.json',state)
                checked=gallery.export_html(root,root/'portable.html',portable=True)
                self.assertEqual(checked['html_status'],'COMPLETE')
                self.assertEqual(checked['offline_empty_directory']['status'],'COMPLETE')
                self.assertEqual(checked['evidence_status'],'INCOMPLETE')
                self.assertEqual(checked['status'],'INCOMPLETE')
                cases=gallery.export_portable_cases(root,root/'cases')
                self.assertEqual(cases['status'],'INCOMPLETE')
                self.assertEqual(cases['index_validation']['status'],'COMPLETE')
                self.assertTrue(next(c for c in cases['cases'] if c['case_id']=='T03')['validation']['evidence_errors'])

    def test_response_only_create_and_dependency_skips_are_distinct(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);entry,directory=example(root,'RESPONSE_ONLY')
            collected=gallery.collect_run(root,entry,root/'gallery.html')
            self.assertEqual(collected['stages']['create']['execution']['state'],'RESPONSE_ONLY')
            self.assertEqual(collected['stages']['refine_1']['execution']['state'],'SKIPPED_DEPENDENCY')
            self.assertEqual(collected['frames']['refine_1']['front']['status'],'NOT_APPLICABLE')
            self.assertEqual(collected['stages']['create']['quality']['state'],'NOT_ASSESSABLE')

    def test_render_exception_moved_artifact_hash_mismatch_and_corrupt_png(self):
        for defect,expected in [('exception','CAPTURE_FAILED'),('moved','ARTIFACT_MISSING'),('hash','HASH_MISMATCH'),('corrupt','CAPTURE_FAILED')]:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as path:
                root=Path(path);entry,directory=example(root);target=directory/'01_create_front.png'
                if defect=='exception':
                    target.unlink();record=json.loads((directory/'after_create_state.json').read_text(encoding="utf-8"));record['views'].update(status='FAILED',errors=['synthetic render exception']);write(directory/'after_create_state.json',record)
                elif defect=='moved':target.rename(directory/'moved.png')
                elif defect=='hash':
                    record=json.loads((directory/'after_create_state.json').read_text(encoding="utf-8"));record['views']['views'][0]['sha256']='0'*64;write(directory/'after_create_state.json',record)
                else:target.write_bytes(b'\x89PNG\r\n\x1a\ninvalid compressed pixels')
                collected=gallery.collect_run(root,entry,root/'gallery.html')
                self.assertEqual(collected['frames']['create']['front']['status'],expected)

    def test_portable_html_loads_from_empty_offline_directory(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);example(root)
            output=root/'portable.html';checked=gallery.export_html(root,output,portable=True)
            self.assertEqual(checked['status'],'COMPLETE')
            self.assertEqual(checked['offline_empty_directory']['status'],'COMPLETE')
            self.assertGreater(checked['decoded_images'],0)
            self.assertIn('data:image/png;base64,',output.read_text(encoding="utf-8"))
            self.assertIn('<h4>Create</h4>',output.read_text(encoding="utf-8"))
            self.assertIn('<h4>Final revision</h4>',output.read_text(encoding="utf-8"))
            self.assertNotIn('src="http',output.read_text(encoding="utf-8"))
            metadata=output.read_text(encoding="utf-8").split('<script id="gallery-data" type="application/json">',1)[1].split('</script>',1)[0]
            self.assertNotIn('data:image/png;base64,',metadata)
            cases=gallery.export_portable_cases(root,root/'cases')
            self.assertEqual(cases['status'],'COMPLETE')
            self.assertEqual(cases['index_validation']['status'],'COMPLETE')
            self.assertEqual(len(cases['cases']),5)
            selected=gallery.collect(root,root/'selected.html',case_ids={'T03'})
            self.assertEqual([card['id'] for card in selected['cards']],['T03'])
            with self.assertRaisesRegex(ValueError,'Unknown case'):
                gallery.collect(root,root/'invalid.html',case_ids={'T99'})

    def test_archive_inventory_rejects_missing_file_and_incomplete_capture(self):
        for separate_blends in (False, True):
            with self.subTest(separate_blends=separate_blends), tempfile.TemporaryDirectory() as path:
                base=Path(path);root=base/'source';root.mkdir();_,directory=example(root)
                blend=directory/'nested'/'scene.blend';blend.parent.mkdir()
                blend.write_bytes(b'synthetic archive inventory fixture, not a Blender scene')
                checked=gallery.export_archive(root,base/'evidence.zip',separate_blends=separate_blends)
                self.assertEqual(checked['status'],'COMPLETE')
                with zipfile.ZipFile(base/'evidence.zip') as archive,zipfile.ZipFile(base/'truncated.zip','w') as changed:
                    inventory=json.loads(archive.read('evidence_inventory.json'))
                    self.assertTrue(all('\\' not in name for name in inventory['files']))
                    self.assertLessEqual(set(inventory['files']),set(archive.namelist()))
                    for item in archive.infolist():
                        if not item.filename.endswith('01_create_front.png'):changed.writestr(item,archive.read(item.filename))
                self.assertEqual(gallery.validate_archive(base/'truncated.zip')['status'],'INCOMPLETE')
                if separate_blends:
                    name='T03/H/1/nested/scene.blend'
                    self.assertEqual(set(inventory['companion_blends']),{name})
                    companion=base/inventory['companion_archive']
                    with zipfile.ZipFile(companion) as archive:
                        self.assertEqual(archive.namelist(),[name])
                        self.assertEqual(archive.read(name),blend.read_bytes())
                    companion.unlink()
                    self.assertEqual(gallery.validate_archive(base/'evidence.zip')['status'],'INCOMPLETE')
                (directory/'01_create_front.png').unlink()
                with self.assertRaisesRegex(ValueError,'Incomplete'):gallery.export_archive(root,base/'bad.zip')

    def test_png_requires_actual_decoding_and_expected_dimensions(self):
        valid=png(4);self.assertEqual(decode_png(valid,(4,4))['status'],'DECODED')
        with self.assertRaisesRegex(ValueError,'dimensions'):decode_png(valid,(1024,1024))
        with self.assertRaises(ValueError):decode_png(valid[:-4])
        data=bytearray(valid);data[-20]^=255
        with self.assertRaises(ValueError):decode_png(bytes(data))


if __name__=='__main__':unittest.main()
