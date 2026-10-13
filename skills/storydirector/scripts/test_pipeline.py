import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pipeline import Pipeline, digest, save, NeedsAgent, read, HOME


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        save(self.root / 'project_state.json', {'topic': 'test', 'stages': {}, 'requests': {}})
        self.p = Pipeline(self.root, read(HOME / 'references/local-profile.json'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_uncertain_paid_request_never_repeats(self):
        endpoint, payload = '/test', {'text': 'test'}
        self.p.state['requests']['tts'] = {'hash': digest({'endpoint': endpoint, 'payload': payload, 'multipart': None}), 'status': 'uncertain'}
        with patch('pipeline.subprocess.run') as request:
            with self.assertRaisesRegex(RuntimeError, 'Uncertain'):
                self.p.api('tts', endpoint, payload)
            request.assert_not_called()

    def test_missing_script_goes_to_agent_not_user(self):
        with self.assertRaises(NeedsAgent):
            self.p.script()
        self.assertFalse(read(self.root / 'agent_tasks.json')['ask_user'])

    def test_over_quota_jokes_are_rejected(self):
        save(self.root / 'story.json', {'chunks': [{'id': 'C1', 'text': 'text'}], 'shots': [{'id': 'K1', 'chunk_id': 'C1', 'humorous_object_text': 'joke'}]})
        with self.assertRaisesRegex(ValueError, 'quota'):
            self.p.script()

    def test_paid_call_without_quota_does_not_send(self):
        with patch('pipeline.subprocess.run') as request:
            with self.assertRaisesRegex(RuntimeError, 'quota not verified'):
                self.p.api('tts', '/v1/text-to-speech/test', {'text': 'test'})
            request.assert_not_called()

    def test_half_humorous_shots_accepts_gags_and_deduplicates(self):
        shots = [{'id': 'K'+str(i), 'chunk_id': 'C1'} for i in range(5)]
        shots[0].update(visual_gag='funny prop', humorous_object_text='joke')
        shots[1]['visual_gag'] = 'another gag'
        save(self.root / 'story.json', {'chunks': [{'id': 'C1', 'text': 'text'}], 'shots': shots})
        self.p.script()
        shots[1].pop('visual_gag')
        save(self.root / 'story.json', {'chunks': [{'id': 'C1', 'text': 'text'}], 'shots': shots})
        with self.assertRaisesRegex(ValueError, 'quota'):
            self.p.script()



if __name__ == '__main__':
    unittest.main()
