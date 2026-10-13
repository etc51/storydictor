"""Technical ledger tests only; tiny PNG fixture does not represent visual QA."""
import argparse
import base64
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).with_name('image_queue.py')
spec = importlib.util.spec_from_file_location('image_queue', HELPER)
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'project'
        self.root.mkdir()
        (self.root / 'reference.png').write_bytes(PNG)
        (self.root / 'bible.json').write_text('{}', encoding='utf-8')
        self.image = Path(self.tmp.name) / 'technical_fixture.png'
        self.image.write_bytes(PNG)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, command, **kwargs):
        return queue.execute(argparse.Namespace(project=str(self.root), command=command, **kwargs))

    def package(self, job_id='a', deps=None):
        return dict(id=job_id, prompt='Technical fixture prompt', scene='Scene 1',
                    world_version='world-v1', bible_version='world-v1', characters=['character-1'], source_ids=['source-1'],
                    reference_images=[dict(path='reference.png', role='environment')],
                    input_files=[dict(path='bible.json', role='frozen_bible')], depends_on=deps or [])

    def init(self, packages):
        path = Path(self.tmp.name) / 'jobs.json'
        path.write_text(json.dumps(packages), encoding='utf-8')
        return self.run_cmd('init', jobs=str(path))

    def claim(self, worker='worker'):
        return self.run_cmd('claim', worker=worker, lease_seconds=3600)

    def generated(self, job_id='a'):
        task = self.claim()
        self.assertEqual(task['id'], job_id)
        self.run_cmd('start', id=job_id, lease=task['lease'])
        return self.run_cmd('result', id=job_id, lease=task['lease'], image=str(self.image))

    def qa(self, job_id, artifact_hash, **changes):
        # This is synthetic schema data for ledger tests. No claim of visual review.
        qa = dict(image_id=job_id, sha256=artifact_hash, reviewer='synthetic-test', viewed=True,
                  observations=['Synthetic ledger fixture; no real visual QA'], bible_version='world-v1', world_version='world-v1',
                  source_ids=['source-1'], checks=dict(scene='passed', identity='not_applicable',
                  anatomy='not_applicable', lore='passed', text='not_applicable'))
        qa.update(changes)
        path = Path(self.tmp.name) / f'qa-{job_id}.json'
        path.write_text(json.dumps(qa), encoding='utf-8')
        return str(path)

    def test_concurrent_claims_have_no_duplicates(self):
        self.init([self.package(f'job-{i:03}') for i in range(40)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda i: self.claim(f'worker-{i}'), range(55)))
        ids = [r['id'] for r in results if r['id']]
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)

    def test_dependency_waits_for_approval(self):
        self.init([self.package('a'), self.package('b', ['a'])])
        generated = self.generated()
        self.assertIsNone(self.claim()['id'])
        self.run_cmd('approve', id='a', qa=self.qa('a', generated['sha256']))
        self.assertEqual(self.claim()['id'], 'b')

    def test_stale_uncertain_never_automatic_retry(self):
        self.init([self.package()])
        self.claim()
        db = sqlite3.connect(self.root / 'image_jobs' / 'queue.sqlite3')
        db.execute('UPDATE jobs SET expires=0')
        db.commit()
        db.close()
        self.assertEqual(self.run_cmd('stale')['uncertain'], ['a'])
        self.assertIsNone(self.claim()['id'])
        with self.assertRaises(ValueError):
            self.run_cmd('retry', id='a')

    def test_qa_rejects_wrong_image_hash(self):
        self.init([self.package()])
        self.generated()
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', '0' * 64))

    def test_qa_rejects_mutated_artifact(self):
        self.init([self.package()])
        result = self.generated()
        Path(result['path']).write_bytes(PNG + b'mutated')
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))

    def test_qa_rejects_mutated_reference(self):
        self.init([self.package()])
        result = self.generated()
        (self.root / 'reference.png').write_bytes(PNG + b'mutated')
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))

    def test_repeated_init_is_idempotent(self):
        packages = [self.package()]
        self.init(packages)
        self.claim()
        self.init(packages)
        state = self.run_cmd('status')['jobs']
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0]['attempts'], 1)
        self.assertEqual(state[0]['status'], 'claimed')

    def test_job_inputs_immutable_and_init_rollback(self):
        self.init([self.package()])
        changed = self.package()
        changed['prompt'] = 'Different prompt'
        with self.assertRaises(ValueError):
            self.init([self.package('new'), changed])
        self.assertEqual([r['id'] for r in self.run_cmd('status')['jobs']], ['a'])

    def test_reference_path_escape_rejected(self):
        p = self.package()
        p['reference_images'][0]['path'] = '../technical_fixture.png'
        with self.assertRaises(ValueError):
            self.init([p])

    def test_unsafe_id_rejected(self):
        with self.assertRaises(ValueError):
            self.init([self.package('../escape')])

    def test_cycles_and_missing_dependencies_rejected(self):
        with self.assertRaises(ValueError):
            self.init([self.package('a', ['b']), self.package('b', ['a'])])
        with self.assertRaises(ValueError):
            self.init([self.package('a', ['missing'])])

    def test_three_attempt_limit(self):
        self.init([self.package()])
        for attempt in range(1, 4):
            task = self.claim()
            self.assertEqual(task['attempt'], attempt)
            self.run_cmd('fail', id='a', lease=task['lease'], reason='Synthetic failure', uncertain=False)
            if attempt < 3:
                self.run_cmd('retry', id='a')
        with self.assertRaises(ValueError):
            self.run_cmd('retry', id='a')

    def test_qa_wrong_world_version_must_be_rejected(self):
        self.init([self.package()])
        result = self.generated()
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256'], bible_version='wrong-world'))

    def test_qa_wrong_source_ids_must_be_rejected(self):
        self.init([self.package()])
        result = self.generated()
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256'], source_ids=['unrelated-source']))

    def test_mutated_approved_dependency_must_block_child(self):
        self.init([self.package('a'), self.package('b', ['a'])])
        result = self.generated()
        self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))
        Path(result['path']).write_bytes(PNG + b'changed after approval')
        with self.assertRaises(ValueError):
            self.claim()

    def test_result_directory_symlink_escape_must_be_rejected(self):
        self.init([self.package()])
        task = self.claim()
        self.run_cmd('start', id='a', lease=task['lease'])
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        link = self.root / 'image_jobs' / 'results'
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            command = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(outside).replace("'", "''") + "' | Out-Null"
            created = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True)
            if created.returncode or not link.exists():
                self.skipTest(f'Symlink/junction unavailable: {exc}')
        with self.assertRaises(ValueError):
            self.run_cmd('result', id='a', lease=task['lease'], image=str(self.image))
        self.assertFalse((outside / 'a' / 'attempt-1.png').exists())

    def evidence(self, **changes):
        data = dict(image_id='a', checked_by='synthetic-test', details='Technical reconciliation fixture',
                    operation_finished=True, outcome='no_output')
        data.update(changes)
        path = Path(self.tmp.name) / 'evidence.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return str(path)

    def uncertain(self):
        self.init([self.package()])
        task = self.claim()
        self.run_cmd('start', id='a', lease=task['lease'])
        self.run_cmd('fail', id='a', lease=task['lease'], reason='Synthetic uncertain outcome', uncertain=True)
        return task

    def test_reconcile_existing_output_without_new_attempt(self):
        task = self.uncertain()
        result = self.run_cmd('reconcile', id='a', evidence=self.evidence(outcome='existing_output',
                    output_path=str(self.image), sha256=queue.digest(self.image)))
        self.assertEqual(result['status'], 'recovered_existing')
        self.assertEqual(result['lease'], task['lease'])
        self.assertIsNone(self.claim()['id'])
        self.run_cmd('result', id='a', lease=task['lease'], image=str(self.image))
        self.assertEqual(self.run_cmd('status')['jobs'][0]['attempts'], 1)

    def test_reconcile_no_output_allows_explicit_retry(self):
        self.uncertain()
        result = self.run_cmd('reconcile', id='a', evidence=self.evidence())
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(self.claim()['id'])
        self.run_cmd('retry', id='a')
        self.assertEqual(self.claim()['attempt'], 2)

    def test_reconcile_unfinished_operation_rejected(self):
        self.uncertain()
        with self.assertRaises(ValueError):
            self.run_cmd('reconcile', id='a', evidence=self.evidence(operation_finished=False))

    def test_reconcile_wrong_output_hash_rejected(self):
        self.uncertain()
        with self.assertRaises(ValueError):
            self.run_cmd('reconcile', id='a', evidence=self.evidence(outcome='existing_output',
                        output_path=str(self.image), sha256='0' * 64))

    def test_recovered_output_cannot_be_replaced(self):
        task = self.uncertain()
        self.run_cmd('reconcile', id='a', evidence=self.evidence(outcome='existing_output',
                    output_path=str(self.image), sha256=queue.digest(self.image)))
        self.image.write_bytes(PNG + b'different output')
        with self.assertRaises(ValueError):
            self.run_cmd('result', id='a', lease=task['lease'], image=str(self.image))

    def test_start_revalidates_dependency_after_claim(self):
        self.init([self.package('a'), self.package('b', ['a'])])
        result = self.generated()
        self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))
        task = self.claim()
        Path(result['path']).write_bytes(PNG + b'changed after child claim')
        with self.assertRaises(ValueError):
            self.run_cmd('start', id='b', lease=task['lease'])

    def revision(self, new_id='a-v2', supersedes='a'):
        p = self.package(new_id)
        p.update(logical_id='a', supersedes=supersedes, prompt='Corrected technical fixture prompt')
        return p

    def test_revision_rejected_image_never_reclaims_old(self):
        self.init([self.package()])
        result = self.generated()
        self.run_cmd('reject', id='a', qa=self.qa('a', result['sha256'],
            checks=dict(scene='failed', identity='not_applicable', anatomy='not_applicable', lore='passed', text='not_applicable')))
        self.init([self.revision()])
        state = {r['id']: r for r in self.run_cmd('status')['jobs']}
        self.assertEqual(state['a']['status'], 'superseded')
        self.assertEqual(self.claim()['id'], 'a-v2')
        with self.assertRaises(ValueError):
            self.run_cmd('retry', id='a')

    def test_old_claim_lease_cannot_start_after_revision(self):
        self.init([self.package()])
        task = self.claim()
        self.init([self.revision()])
        with self.assertRaises(ValueError):
            self.run_cmd('start', id='a', lease=task['lease'])
        with self.assertRaises(ValueError):
            self.run_cmd('result', id='a', lease=task['lease'], image=str(self.image))
        self.assertEqual(self.claim()['id'], 'a-v2')

    def test_prompt_revision_chain_cannot_bypass_attempt_budget(self):
        self.init([self.package()])
        current = 'a'
        for i in range(3):
            task = self.claim()
            self.assertEqual(task['id'], current)
            self.run_cmd('fail', id=current, lease=task['lease'], reason='Synthetic correction', uncertain=False)
            new_id = f'a-v{i + 2}'
            self.init([self.revision(new_id, current)])
            current = new_id
        self.assertIsNone(self.claim()['id'])
        state = self.run_cmd('status')['jobs']
        self.assertEqual(sum(r['attempts'] for r in state), 3)

    def test_running_uncertain_and_recovered_cannot_be_superseded(self):
        self.init([self.package()])
        task = self.claim()
        self.run_cmd('start', id='a', lease=task['lease'])
        with self.assertRaises(ValueError):
            self.init([self.revision()])
        self.run_cmd('fail', id='a', lease=task['lease'], reason='Synthetic uncertain', uncertain=True)
        with self.assertRaises(ValueError):
            self.init([self.revision()])
        self.run_cmd('reconcile', id='a', evidence=self.evidence(outcome='existing_output',
                    output_path=str(self.image), sha256=queue.digest(self.image)))
        with self.assertRaises(ValueError):
            self.init([self.revision()])

    def test_changed_bible_blocks_claim(self):
        self.init([self.package()])
        (self.root / 'bible.json').write_text('{"changed": true}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.claim()

    def test_changed_bible_blocks_approval(self):
        self.init([self.package()])
        result = self.generated()
        (self.root / 'bible.json').write_text('{"changed": true}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))

    def test_superseded_approved_dependency_blocks_child_claim(self):
        self.init([self.package('a'), self.package('b', ['a'])])
        result = self.generated()
        self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))
        revised = self.revision()
        revised['depends_on'] = ['b']  # Holds revised root pending too, no automatic dependency remap.
        self.init([revised])
        self.assertIsNone(self.claim()['id'])

    def test_approval_revalidates_superseded_dependency(self):
        self.init([self.package('a'), self.package('b', ['a'])])
        result = self.generated()
        self.run_cmd('approve', id='a', qa=self.qa('a', result['sha256']))
        child = self.generated('b')
        self.init([self.revision()])
        with self.assertRaises(ValueError):
            self.run_cmd('approve', id='b', qa=self.qa('b', child['sha256']))

    def test_wrong_supersedes_rolls_back_entire_init(self):
        self.init([self.package()])
        with self.assertRaises(ValueError):
            self.init([self.package('new'), self.revision(supersedes='not-current')])
        state = self.run_cmd('status')['jobs']
        self.assertEqual(len(state), 1)
        self.assertEqual(state[0]['id'], 'a')
        self.assertEqual(state[0]['status'], 'pending')


if __name__ == '__main__':
    unittest.main(verbosity=2)
