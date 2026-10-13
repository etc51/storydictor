import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from editorial_guard import validate, sha256
from pipeline import Pipeline, NeedsAgent, save, digest, read, HOME


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'narration.txt').write_text('Ты открыл дверь.\nОн уже ждал тебя.', encoding='utf-8')
        self.hash = sha256(self.root / 'narration.txt')
        self.story = {'chunks': [{'id': 'C1', 'text': '[serious] Ты открыл дверь.'},
                                 {'id': 'C2', 'text': 'Он уже ждал тебя.'}]}
        self.qa = {'schema_version': 1, 'status': 'passed', 'writer_id': '/writer',
                   'narration_file': 'narration.txt', 'input_sha256': self.hash,
                   'reports': {}, 'reports_sha256': {}, 'findings_resolution': []}
        for role in ('critic', 'reader'):
            report = {'schema_version': 1, 'role': role, 'reviewer_id': '/' + role,
                      'independent': True, 'input_sha256': self.hash,
                      'scope': 'full_script', 'summary': 'Полный текст прочитан.',
                      'findings': [], 'persona': 'Зритель приключений'}
            save(self.root / (role + '.json'), report)
            self.qa['reports'][role] = role + '.json'
            self.qa['reports_sha256'][role] = sha256(self.root / (role + '.json'))
        save(self.root / 'editorial_qa.json', self.qa)

    def tearDown(self):
        self.tmp.cleanup()

    def edit_report(self, role, **changes):
        path = self.root / (role + '.json')
        report = read(path)
        report.update(changes)
        save(path, report)
        self.qa['reports_sha256'][role] = sha256(path)
        save(self.root / 'editorial_qa.json', self.qa)

    def finding(self, severity='blocking'):
        return {'id': 'F1', 'severity': severity,
                'location': {'line_start': 1, 'line_end': 1, 'quote': 'Ты открыл дверь.'},
                'issue': 'Причина поступка неясна.', 'reason': 'Герой ожидал опасности.'}

    def test_valid_evidence_and_chunk_direction_tags(self):
        self.assertEqual(validate(self.root, self.story)['status'], 'passed')

    def test_changed_words_or_punctuation_block_current_story(self):
        self.story['chunks'][0]['text'] = 'Ты закрыл дверь.'
        self.assertTrue(validate(self.root, self.story)['errors'])

    def snapshot_with_headers(self, text, headers):
        (self.root / 'narration.txt').write_text(text, encoding='utf-8')
        self.hash = sha256(self.root / 'narration.txt')
        self.qa.update(input_sha256=self.hash, nonspoken_headers=headers)
        for role in ('critic', 'reader'):
            self.edit_report(role, input_sha256=self.hash)

    def test_explicit_chunk_headers_pass_without_changing_snapshot(self):
        self.story['chunks'][0]['id'] = 'C001'
        self.story['chunks'][1]['id'] = 'C002'
        self.snapshot_with_headers('## C001\nТы открыл дверь.\n\n## C002\nОн уже ждал тебя.',
                                   [{'line': 1, 'text': '## C001'}, {'line': 4, 'text': '## C002'}])
        original = (self.root / 'narration.txt').read_bytes()
        self.assertEqual(validate(self.root, self.story)['status'], 'passed')
        self.assertEqual((self.root / 'narration.txt').read_bytes(), original)

    def test_explicit_ladder_headers_pass_and_title_binding(self):
        self.story['chunks'][0]['title'] = 'Носильщик'
        self.story['chunks'][1]['title'] = 'Караванный охранник'
        self.snapshot_with_headers('@1 Носильщик\nТы открыл дверь.\n\n@2 Караванный охранник\nОн уже ждал тебя.',
                                   [{'line': 1, 'text': '@1 Носильщик'}, {'line': 4, 'text': '@2 Караванный охранник'}])
        self.assertEqual(validate(self.root, self.story)['status'], 'passed')
        self.story['chunks'][0]['title'] = 'Другое название'
        self.assertTrue(validate(self.root, self.story)['errors'])

    def test_arbitrary_spoken_line_cannot_be_excluded(self):
        self.snapshot_with_headers('Ты открыл дверь.\nТы бросил друга.\nОн уже ждал тебя.',
                                   [{'line': 2, 'text': 'Ты бросил друга.'}])
        errors = validate(self.root, self.story)['errors']
        self.assertIn('not a supported structural nonspoken header: 2', errors)
        self.assertIn('reviewed narration differs from current story chunks', errors)

    def test_false_header_line_or_text_blocks(self):
        for header in ({'line': 2, 'text': '## C001'}, {'line': 1, 'text': '## C002'},
                       {'line': True, 'text': '## C001'}, {'line': 900, 'text': '## C001'}):
            with self.subTest(header=header):
                self.snapshot_with_headers('## C001\nТы открыл дверь.\nОн уже ждал тебя.', [header])
                self.assertEqual(validate(self.root, self.story)['status'], 'needs_agent')

    def test_unlisted_structural_header_still_mismatches(self):
        self.snapshot_with_headers('@1 Носильщик\nТы открыл дверь.\nОн уже ждал тебя.', [])
        self.assertIn('reviewed narration differs from current story chunks', validate(self.root, self.story)['errors'])

    def test_unknown_chunk_duplicate_header_and_bad_field_block(self):
        self.story['chunks'][0]['id'] = 'C001'
        self.snapshot_with_headers('## C999\nТы открыл дверь.\nОн уже ждал тебя.',
                                   [{'line': 1, 'text': '## C999'}])
        self.assertIn('nonspoken header refers to unknown chunk: C999', validate(self.root, self.story)['errors'])
        self.snapshot_with_headers('@1 Носильщик\nТы открыл дверь.\nОн уже ждал тебя.',
                                   [{'line': 1, 'text': '@1 Носильщик'}] * 2)
        self.assertIn('duplicate nonspoken header line: 1', validate(self.root, self.story)['errors'])
        for invalid in (None, 'invalid', [None]):
            self.qa['nonspoken_headers'] = invalid
            save(self.root / 'editorial_qa.json', self.qa)
            self.assertEqual(validate(self.root, self.story)['status'], 'needs_agent')

    def test_stale_snapshot_hash_blocks(self):
        (self.root / 'narration.txt').write_text('Другая история.', encoding='utf-8')
        self.assertIn('editorial decision has stale narration hash', validate(self.root)['errors'])

    def test_missing_report_blocks(self):
        (self.root / 'reader.json').unlink()
        self.assertTrue(validate(self.root)['errors'])

    def test_replaced_report_requires_new_evidence_hash(self):
        report = read(self.root / 'critic.json')
        report['summary'] = 'Подменённый отчёт'
        save(self.root / 'critic.json', report)
        self.assertIn('critic report hash missing or stale', validate(self.root)['errors'])

    def test_stale_report_input_blocks_even_with_current_report_hash(self):
        self.edit_report('critic', input_sha256='0' * 64)
        self.assertIn('critic report has stale narration hash', validate(self.root)['errors'])

    def test_writer_cannot_mark_blocking_finding_resolved(self):
        self.qa['findings_resolution'] = [{'report_role': 'critic', 'finding_id': 'F1',
                                          'status': 'resolved', 'rationale': 'Я считаю исправленным.'}]
        self.edit_report('critic', findings=[self.finding()])
        self.assertIn('critic open blocking finding: F1', validate(self.root)['errors'])

    def test_self_review_not_independent(self):
        self.edit_report('critic', reviewer_id='/writer')
        self.assertTrue(validate(self.root)['errors'])

    def test_same_critic_and_reader_not_independent(self):
        self.edit_report('reader', reviewer_id='/critic')
        self.assertIn('critic and reader must be different agents', validate(self.root)['errors'])

    def test_sample_review_or_limited_review_cannot_pass(self):
        self.edit_report('critic', scope='sample')
        self.assertTrue(validate(self.root)['errors'])
        self.qa['status'] = 'limited_review'
        save(self.root / 'editorial_qa.json', self.qa)
        self.assertTrue(validate(self.root)['errors'])

    def test_unaddressed_major_and_false_quote_block(self):
        finding = self.finding('major')
        finding['location']['quote'] = 'Вымышленная цитата'
        self.edit_report('critic', findings=[finding])
        self.assertIn('critic unaddressed major finding: F1', validate(self.root)['errors'])
        self.assertIn('critic invalid quotation/location: F1', validate(self.root)['errors'])

    def test_reasoned_major_acceptance(self):
        self.qa['findings_resolution'] = [{'report_role': 'critic', 'finding_id': 'F1',
                                          'status': 'accepted', 'rationale': 'Намеренная тайна; ответ дан во второй сцене.'}]
        self.edit_report('critic', findings=[self.finding('major')])
        self.assertEqual(validate(self.root)['status'], 'passed')

    def test_non_object_decision_fails_closed(self):
        for invalid in (None, [], 'invalid'):
            with self.subTest(invalid=invalid):
                save(self.root / 'editorial_qa.json', invalid)
                self.assertEqual(validate(self.root)['status'], 'needs_agent')

    def test_malformed_findings_or_report_fail_closed(self):
        for invalid in (None, 'invalid', [], 3):
            with self.subTest(invalid=invalid):
                self.edit_report('critic', findings=[invalid])
                self.assertEqual(validate(self.root)['status'], 'needs_agent')
        save(self.root / 'critic.json', [])
        self.assertEqual(validate(self.root)['status'], 'needs_agent')

    def test_malformed_resolution_fails_closed(self):
        for invalid in (None, 'invalid', []):
            with self.subTest(invalid=invalid):
                self.qa['findings_resolution'] = [invalid]
                save(self.root / 'editorial_qa.json', self.qa)
                self.assertEqual(validate(self.root)['status'], 'needs_agent')

    def pipeline(self):
        save(self.root / 'project_state.json', {'topic': 'test', 'stages': {}, 'requests': {}})
        save(self.root / 'story.json', self.story)
        return Pipeline(self.root, read(HOME / 'references/local-profile.json'))

    def test_new_paid_request_stops_without_network(self):
        p = self.pipeline()
        p.state['quota_verified'] = {'remaining_credits': 1000, 'checked_at': 0}
        (self.root / 'editorial_qa.json').unlink()
        with patch('pipeline.subprocess.run') as network, patch.object(p, 'account_check') as account:
            with self.assertRaises(NeedsAgent):
                p.api('tts', '/v1/text-to-speech/test', {'text': 'test'})
            account.assert_not_called()
            network.assert_not_called()
        task = read(self.root / 'agent_tasks.json')
        self.assertEqual(task['stage'], 'story_editorial')
        self.assertFalse(task['ask_user'])

    def test_cached_paid_response_still_recovers_without_editorial(self):
        p = self.pipeline()
        (self.root / 'editorial_qa.json').unlink()
        payload = {'text': 'test'}
        request_hash = digest({'endpoint': '/test', 'payload': payload, 'multipart': None})
        path = self.root / 'api' / ('tts-' + request_hash[:16] + '.response')
        path.parent.mkdir()
        path.write_bytes(b'existing paid result')
        p.state['requests']['tts'] = {'hash': request_hash, 'status': 'completed', 'output_hash': sha256(path)}
        with patch('pipeline.subprocess.run') as network:
            self.assertEqual(p.api('tts', '/test', payload), path)
            network.assert_not_called()

    def test_existing_uncertain_paid_call_is_not_reissued(self):
        p = self.pipeline()
        (self.root / 'editorial_qa.json').unlink()
        payload = {'text': 'test'}
        p.state['requests']['tts'] = {'hash': digest({'endpoint': '/test', 'payload': payload, 'multipart': None}),
                                    'status': 'uncertain'}
        with patch('pipeline.subprocess.run') as network:
            with self.assertRaisesRegex(RuntimeError, 'Uncertain API outcome'):
                p.api('tts', '/test', payload)
            network.assert_not_called()

    def test_images_block_before_generation_tasks(self):
        p = self.pipeline()
        (self.root / 'editorial_qa.json').unlink()
        with self.assertRaises(NeedsAgent):
            p.images([{'id': 'K1'}])
        self.assertEqual(read(self.root / 'agent_tasks.json')['stage'], 'story_editorial')
        self.assertFalse((self.root / 'Готовые_изображения').exists())


if __name__ == '__main__':
    unittest.main()
