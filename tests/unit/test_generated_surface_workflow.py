"""Evidence gates and numerical diagnostics without Blender or a provider."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.authoring import schema, surface_workflow as workflow


class WorkflowTests(unittest.TestCase):
    def request(self, count=1):
        return dict(mode='CREATE', context=dict(image_generation=dict(enabled=True)), preview_count=count,
                    candidate=dict(digest='a'*64, result=dict(remaining_corrections=3-count,
                                   images=[dict(id='candidate:close')])) )

    def critique(self, weaknesses=True):
        return dict(candidate_digest='a'*64, weaknesses=[dict(component='Grip',
                    observation='Grip is featureless in the closeup', correction='Add visible molded grain',
                    evidence_image_id='candidate:close')] if weaknesses else [],
                    generated_contribution='The bypass loses machining variation',
                    preserved_decisions='Preserve component regions and substrate values')

    def test_requires_evidence_then_corrected_material(self):
        captured = self.request()
        with self.assertRaises(schema.AuthoringError): workflow.require_review(captured)
        with self.assertRaises(schema.AuthoringError): workflow.critique(captured, self.critique(False))
        invalid = self.critique()
        invalid['weaknesses'][0]['evidence_image_id'] = 'not-returned'
        with self.assertRaises(schema.AuthoringError): workflow.critique(captured, invalid)
        workflow.critique(captured, self.critique())
        with self.assertRaises(schema.AuthoringError): workflow.require_review(captured)
        captured = self.request(2)
        workflow.critique(captured, self.critique(False))
        workflow.require_review(captured)

    def test_two_correction_limit_discloses_remaining_weaknesses(self):
        captured = self.request(3)
        result = workflow.critique(captured, self.critique())
        workflow.require_review(captured)
        self.assertIn('disclose', result['next_step'])
        self.assertEqual(len(captured['visual_critiques']), 1)

    def test_plan_checks_editable_materials_and_invalidates_prior_critique(self):
        captured = self.request()
        captured['target_digests'] = {'target': 'digest'}
        plan = dict(components=[dict(texture_set_id='foreign')])
        with self.assertRaises(schema.AuthoringError): workflow.store_plan(captured, plan)
        plan['components'][0]['texture_set_id'] = 'target'
        captured['candidate']['critique'] = self.critique()
        workflow.store_plan(captured, plan)
        self.assertNotIn('critique', captured['candidate'])


if __name__ == '__main__':
    unittest.main()
