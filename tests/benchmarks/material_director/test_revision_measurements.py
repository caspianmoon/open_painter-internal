"""Surface-weighted numeric controls; synthetic arrays are not artistic scores."""
import unittest
import numpy as np
from revision_measurements import coverage, compare, effect_control, measurement_policy

class RevisionMeasurements(unittest.TestCase):
    def samples(self, weights=(9.,1.,1.,1.), valid=None):
        return dict(uv=np.array([[.25,.25],[.75,.25],[.25,.75],[.75,.75]]),
                    weight=np.array(weights),valid=np.array(valid if valid is not None else [True]*4))
    def test_area_weighting_is_not_uv_pixel_fraction(self):
        result=coverage(np.array([[1.,0.],[0.,0.]]),self.samples())
        self.assertAlmostEqual(result['occupied_fraction'],.75)
        self.assertNotEqual(result['occupied_fraction'],.25)
    def test_invalid_surface_stays_in_denominator_and_invalidates_claim(self):
        result=coverage(np.ones((2,2)),self.samples(valid=[False,True,True,True]))
        self.assertAlmostEqual(result['occupied_fraction'],.25)
        self.assertAlmostEqual(result['invalid_area_fraction'],.75)
        self.assertEqual(result['status'],'NOT_ASSESSABLE')
    def test_near_zero_source_coverage_has_no_unstable_ratio(self):
        result=compare(np.zeros((2,2)),np.ones((2,2)),self.samples())
        self.assertIsNone(result['occupied_area_ratio'])
        self.assertEqual(result['occupied_area_ratio_status'],'NOT_ASSESSABLE')
        self.assertEqual(result['mask_equality_status'],'MEASURED')
    def test_exact_mask_equality_and_new_support_are_separate(self):
        before=np.array([[1.,0.],[0.,0.]])
        same=compare(before,before.copy(),self.samples())
        self.assertTrue(same['mask_equal']);self.assertEqual(same['occupied_area_ratio'],1.)
        changed=compare(before,np.array([[0.,1.],[0.,0.]]),self.samples())
        self.assertFalse(changed['mask_equal']);self.assertAlmostEqual(changed['new_support_fraction'],1/12)
    def test_nonfinite_valid_samples_rejected(self):
        with self.assertRaises(ValueError):coverage(np.full((2,2),np.nan),self.samples())
    def test_native_inverse_coating_retains_source_and_halves_declared_opacity(self):
        native=dict(id='coating',surface_role='COATING',surface_family='PAINTED_METAL',opacity=1,
            masks=[dict(id='paint-loss',parent_id='',surface_role='PAINT_LOSS',source='GENERATOR',invert=True,strength=1.,seed=37)])
        source=np.array([[.9,.1],[0.,.3]],dtype=np.float32)
        first,a=effect_control(native,1-source)
        native['masks'][0]['strength']=.5
        last,b=effect_control(native,1-.5*source)
        np.testing.assert_allclose(a,b,atol=1e-6)
        self.assertEqual(first['source_document'],last['source_document'])
        self.assertEqual(last['opacity']/first['opacity'],.5)
        self.assertEqual(last['opacity_control_path'],['layers','coating','masks','paint-loss','strength'])
        measured=compare(a,b,self.samples())
        self.assertTrue(measured['mask_equal']);self.assertEqual(measured['occupied_area_ratio'],1)
        self.assertNotEqual(coverage(1-.5*source,self.samples())['occupied_fraction'],coverage(b,self.samples())['occupied_fraction'])
    def test_zero_strength_and_composite_coating_are_not_claimed_as_raw_loss(self):
        native=dict(id='c',surface_role='COATING',surface_family='PAINTED_METAL',masks=[dict(
            id='p',surface_role='PAINT_LOSS',source='GROUP',invert=True,strength=0)])
        record,values=effect_control(native,np.ones((2,2)))
        self.assertEqual(record['status'],'NOT_MEASURED');self.assertIsNone(values)
        native['masks'][0]['strength']=1
        native['masks'].append(dict(id='unrelated',source='NOISE'))
        self.assertEqual(effect_control(native,np.ones((2,2)))[0]['status'],'NOT_MEASURED')
    def test_baseline_layer_evidence_does_not_infer_semantics_from_names(self):
        native=dict(id='legacy',name='Paint Loss Moss Mud',opacity=.25,masks=[dict(id='m',source='NOISE')])
        record,values=effect_control(native,np.ones((2,2)))
        self.assertEqual(record['semantic'],'UNASSIGNED_LAYER_MASK')
        self.assertEqual(record['opacity'],.25)
        self.assertEqual(record['semantic_assessment'],'NOT_MEASURED_WITHOUT_EXPLICIT_COMPONENT_MAPPING')
    def test_frozen_threshold_invalid_area_and_small_support_are_independent(self):
        policy=measurement_policy();policy.update(mask_threshold=.8,invalid_uv_area_max_fraction=.3,mask_absolute_tolerance=.02)
        self.assertEqual(measurement_policy({'measurement_policy':policy}),policy)
        before=np.array([[.75,.9],[0.,0.]])
        samples=self.samples(weights=(1.,1.,1000.,1.))
        result=compare(before,before+.01,samples,threshold=policy['mask_threshold'],tolerance=policy['mask_absolute_tolerance'])
        self.assertEqual(result['before']['threshold'],.8);self.assertTrue(result['mask_equal'])
        self.assertEqual(result['occupied_area_ratio_status'],'MEASURED')
        moss=compare(before,before,samples,threshold=.8,minimum_initial_fraction=.01)
        self.assertEqual(moss['occupied_area_ratio_status'],'NOT_ASSESSABLE')
        self.assertEqual(moss['mask_equality_status'],'MEASURED')
        invalid=self.samples(weights=(1.,1.,1.,1.),valid=[False,True,True,True])
        self.assertEqual(compare(before,before,invalid,invalid_area_tolerance=.3)['support_status'],'MEASURED')
        self.assertEqual(compare(before,before,invalid,invalid_area_tolerance=.001)['support_status'],'NOT_ASSESSABLE')
if __name__=='__main__':unittest.main()
