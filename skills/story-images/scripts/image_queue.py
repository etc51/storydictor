"""Local, transactional job ledger. Does not generate or visually judge images."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path


def digest(path):
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Project path escape')
    return path


def connect(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Project folder must exist')
    folder = within(root, 'image_jobs')
    folder.mkdir(exist_ok=True)
    db = sqlite3.connect(within(root, 'image_jobs/queue.sqlite3'), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, package TEXT NOT NULL, input_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', worker TEXT, lease TEXT,
        expires REAL, attempts INTEGER NOT NULL DEFAULT 0, artifact TEXT,
        artifact_hash TEXT, qa TEXT, error TEXT, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
        sequence INTEGER PRIMARY KEY, job_id TEXT, action TEXT, detail TEXT, at REAL);
    ''')
    if 'logical_id' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
        db.execute('ALTER TABLE jobs ADD COLUMN logical_id TEXT')
        db.execute('UPDATE jobs SET logical_id=id WHERE logical_id IS NULL')
    db.execute('CREATE INDEX IF NOT EXISTS logical_jobs ON jobs(logical_id)')
    return root, db


def event(db, job_id, action, detail=None):
    db.execute('INSERT INTO events(job_id,action,detail,at) VALUES(?,?,?,?)',
               (job_id, action, encoded(detail), time.time()))


def row(db, job_id):
    r = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if r is None:
        raise ValueError('Unknown job')
    return r


def verify_inputs(root, package):
    for ref in package['reference_images'] + package.get('input_files', []):
        path = within(root, ref['path'])
        if not path.is_file() or digest(path) != ref['sha256']:
            raise ValueError('Reference changed or missing; create a revised job id')


def verify_approved(root, db, job_id):
    r = row(db, job_id)
    if r['status'] != 'approved':
        raise ValueError('Dependency not approved')
    p = json.loads(r['package'])
    verify_inputs(root, p)
    path = within(root, r['artifact'])
    if not path.is_file() or digest(path) != r['artifact_hash']:
        raise ValueError('Approved dependency image changed or missing')
    for dependency in p['depends_on']:
        verify_approved(root, db, dependency)


def initialize(root, db, filename):
    packages = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    if not isinstance(packages, list) or not packages:
        raise ValueError('Jobs must be a nonempty JSON array')
    prepared = {}
    for original in packages:
        p = dict(original)
        job_id = p.get('id', '')
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', job_id):
            raise ValueError('Unsafe job id')
        if job_id in prepared:
            raise ValueError('Duplicate job id')
        p.setdefault('logical_id', job_id)
        if not isinstance(p['logical_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', p['logical_id']):
            raise ValueError('Unsafe logical id')
        for key in ('prompt', 'scene', 'world_version', 'bible_version'):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise ValueError('Missing package field: ' + key)
        for key in ('characters', 'source_ids', 'reference_images', 'input_files'):
            if not isinstance(p.get(key), list):
                raise ValueError('Missing package list: ' + key)
        if not p['source_ids'] or not all(isinstance(s, str) and s for s in p['source_ids']):
            raise ValueError('World source ids required')
        if not p['reference_images']:
            raise ValueError('Visual reference images required')
        if not p['input_files']:
            raise ValueError('Frozen bible/source input files required')
        for category in ('reference_images', 'input_files'):
            refs = []
            for ref in p[category]:
                if not isinstance(ref, dict) or not ref.get('role') or not ref.get('path'):
                    raise ValueError('Input path and role required')
                path = within(root, ref['path'])
                if not path.is_file():
                    raise ValueError('Input file missing')
                refs.append({**ref, 'path': path.relative_to(root).as_posix(), 'sha256': digest(path)})
            p[category] = refs
        p.setdefault('depends_on', [])
        if not isinstance(p['depends_on'], list) or not all(isinstance(d, str) for d in p['depends_on']):
            raise ValueError('Invalid dependencies')
        prepared[job_id] = p
    existing = {r['id']: json.loads(r['package']) for r in db.execute('SELECT * FROM jobs')}
    graph = {**existing, **prepared}
    visited, active = set(), set()
    def walk(job_id):
        if job_id in active:
            raise ValueError('Dependency cycle')
        if job_id in visited:
            return
        if job_id not in graph:
            raise ValueError('Unknown dependency: ' + job_id)
        active.add(job_id)
        for dependency in graph[job_id]['depends_on']:
            walk(dependency)
        active.remove(job_id)
        visited.add(job_id)
    for job_id in prepared:
        walk(job_id)
    for job_id, p in prepared.items():
        data = encoded(p)
        h = hashlib.sha256(data.encode()).hexdigest()
        old = db.execute('SELECT input_hash FROM jobs WHERE id=?', (job_id,)).fetchone()
        if old and old['input_hash'] != h:
            raise ValueError('Job input changed; use a revised id rather than overwrite')
        if not old:
            active_versions = list(db.execute("SELECT * FROM jobs WHERE logical_id=? AND status!='superseded'", (p['logical_id'],)))
            previous = p.get('supersedes')
            if active_versions:
                if len(active_versions) != 1 or active_versions[0]['id'] != previous:
                    raise ValueError('Revised logical image requires supersedes=current job id')
                prior = active_versions[0]
                if prior['status'] in ('generating', 'uncertain', 'recovered_existing'):
                    raise ValueError('Finish or reconcile running/uncertain job before revision')
                db.execute("UPDATE jobs SET status='superseded',updated=? WHERE id=?", (time.time(), previous))
                event(db, previous, 'superseded', {'replacement': job_id})
            elif previous:
                raise ValueError('Superseded job is not active for this logical image')
            db.execute('INSERT INTO jobs(id,logical_id,package,input_hash,updated) VALUES(?,?,?,?,?)',
                       (job_id, p['logical_id'], data, h, time.time()))
            event(db, job_id, 'init', {'input_hash': h})
    return {'registered': len(prepared)}


def owned(db, job_id, lease, allowed):
    r = row(db, job_id)
    if r['status'] not in allowed or not lease or r['lease'] != lease:
        raise ValueError('Wrong lease or state')
    return r


def execute(args):
    root, db = connect(args.project)
    try:
        db.execute('BEGIN IMMEDIATE')
        cmd = args.command
        result = None
        if cmd == 'init':
            result = initialize(root, db, args.jobs)
        elif cmd == 'claim':
            states = {r['id']: r['status'] for r in db.execute('SELECT id,status FROM jobs')}
            for r in db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY id"):
                p = json.loads(r['package'])
                if all(states.get(d) == 'approved' for d in p['depends_on']):
                    total = db.execute('SELECT sum(attempts) FROM jobs WHERE logical_id=?', (r['logical_id'],)).fetchone()[0] or 0
                    if total >= 3:
                        continue
                    verify_inputs(root, p)
                    for dependency in p['depends_on']:
                        verify_approved(root, db, dependency)
                    lease = uuid.uuid4().hex
                    db.execute("UPDATE jobs SET status='claimed',worker=?,lease=?,expires=?,attempts=attempts+1,updated=? WHERE id=?",
                               (args.worker, lease, time.time() + args.lease_seconds, time.time(), r['id']))
                    event(db, r['id'], 'claim', {'worker': args.worker})
                    result = {'id': r['id'], 'lease': lease, 'attempt': r['attempts'] + 1, 'package': p}
                    break
            if result is None:
                result = {'id': None, 'message': 'No ready jobs; inspect status and dependencies'}
        elif cmd == 'start':
            r = owned(db, args.id, args.lease, ('claimed',))
            p = json.loads(r['package'])
            verify_inputs(root, p)
            for dependency in p['depends_on']:
                verify_approved(root, db, dependency)
            db.execute("UPDATE jobs SET status='generating',updated=? WHERE id=?", (time.time(), args.id))
            event(db, args.id, 'start')
            result = {'id': args.id, 'status': 'generating'}
        elif cmd == 'result':
            r = owned(db, args.id, args.lease, ('generating', 'recovered_existing', 'generated'))
            src = Path(args.image).resolve()
            signature = b''
            if src.is_file():
                with src.open('rb') as f:
                    signature = f.read(8)
            if signature != b'\x89PNG\r\n\x1a\n':
                raise ValueError('Expected actual PNG output; this check does not prove visual quality')
            h = digest(src)
            if r['status'] == 'recovered_existing' and h != r['artifact_hash']:
                raise ValueError('Recovered result differs from reconciliation evidence')
            if r['status'] == 'generated':
                if r['artifact_hash'] != h:
                    raise ValueError('Different result already registered')
                dest = within(root, r['artifact'])
                if not dest.is_file() or digest(dest) != h:
                    raise ValueError('Registered artifact was changed or lost')
            else:
                folder = within(root, 'image_jobs/results/' + args.id)
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / ('attempt-' + str(r['attempts']) + '.png')
                if dest.exists() and digest(dest) != h:
                    raise ValueError('Conflicting artifact already exists; reconcile without new generation')
                if src != dest:
                    tmp = folder / (uuid.uuid4().hex + '.tmp')
                    try:
                        shutil.copyfile(src, tmp)
                        if digest(tmp) != h:
                            raise ValueError('Copy hash mismatch')
                        os.replace(tmp, dest)
                    finally:
                        tmp.unlink(missing_ok=True)
                db.execute("UPDATE jobs SET status='generated',artifact=?,artifact_hash=?,updated=? WHERE id=?",
                           (dest.relative_to(root).as_posix(), h, time.time(), args.id))
                event(db, args.id, 'result', {'sha256': h})
            result = {'id': args.id, 'status': 'generated', 'path': str(dest), 'sha256': h}
        elif cmd in ('approve', 'reject'):
            r = row(db, args.id)
            if r['status'] != 'generated':
                raise ValueError('QA requires generated result')
            verify_inputs(root, json.loads(r['package']))
            artifact = within(root, r['artifact'])
            if not artifact.is_file() or digest(artifact) != r['artifact_hash']:
                raise ValueError('Image changed or missing')
            qa = json.loads(Path(args.qa).read_text(encoding='utf-8-sig'))
            if qa.get('image_id') != args.id or qa.get('sha256') != r['artifact_hash']:
                raise ValueError('QA belongs to different image version')
            if not qa.get('reviewer') or qa.get('viewed') is not True or not qa.get('observations'):
                raise ValueError('Actual visual review and observations required')
            if not qa.get('bible_version') or not qa.get('source_ids'):
                raise ValueError('Bible version and lore sources required')
            package = json.loads(r['package'])
            for dependency in package['depends_on']:
                verify_approved(root, db, dependency)
            if qa['bible_version'] != package['bible_version'] or qa.get('world_version') != package['world_version']:
                raise ValueError('QA has different bible or world version')
            if not isinstance(qa['source_ids'], list) or not set(package['source_ids']).issubset(qa['source_ids']):
                raise ValueError('QA omits required lore sources')
            required = ('scene', 'identity', 'anatomy', 'lore', 'text')
            checks = qa.get('checks', {})
            if any(checks.get(k) not in ('passed', 'failed', 'unverified', 'not_applicable') for k in required):
                raise ValueError('Incomplete QA categories')
            if cmd == 'approve' and (checks['scene'] != 'passed' or checks['lore'] != 'passed' or
                                     any(v in ('failed', 'unverified') for v in checks.values())):
                raise ValueError('Unverified or failed image cannot be approved')
            status = 'approved' if cmd == 'approve' else 'rejected'
            db.execute('UPDATE jobs SET status=?,qa=?,updated=? WHERE id=?', (status, encoded(qa), time.time(), args.id))
            event(db, args.id, cmd, {'sha256': r['artifact_hash']})
            result = {'id': args.id, 'status': status}
        elif cmd == 'reconcile':
            r = row(db, args.id)
            if r['status'] != 'uncertain':
                raise ValueError('Reconciliation requires uncertain state')
            proof = json.loads(Path(args.evidence).read_text(encoding='utf-8-sig'))
            if proof.get('image_id') != args.id or not proof.get('checked_by') or not proof.get('details') or proof.get('operation_finished') is not True:
                raise ValueError('Confirmed completed operation and reconciliation evidence required')
            outcome = proof.get('outcome')
            if outcome == 'existing_output':
                path = Path(proof.get('output_path', '')).resolve()
                if not path.is_file() or digest(path) != proof.get('sha256'):
                    raise ValueError('Recovered output missing or different hash')
                status = 'recovered_existing'
            elif outcome == 'no_output':
                status = 'failed'
            else:
                raise ValueError('Unresolved operation cannot be reconciled')
            db.execute('UPDATE jobs SET status=?,artifact_hash=?,error=NULL,updated=? WHERE id=?',
                       (status, proof.get('sha256') if outcome == 'existing_output' else r['artifact_hash'], time.time(), args.id))
            event(db, args.id, 'reconcile', proof)
            result = {'id': args.id, 'status': status, 'lease': r['lease']}
        elif cmd == 'fail':
            owned(db, args.id, args.lease, ('claimed', 'generating'))
            status = 'uncertain' if args.uncertain else 'failed'
            db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?', (status, args.reason, time.time(), args.id))
            event(db, args.id, status, args.reason)
            result = {'id': args.id, 'status': status}
        elif cmd == 'retry':
            r = row(db, args.id)
            total = db.execute('SELECT sum(attempts) FROM jobs WHERE logical_id=?', (r['logical_id'],)).fetchone()[0] or 0
            if r['status'] not in ('failed', 'rejected') or total >= 3:
                raise ValueError('Retry forbidden: uncertain/state conflict or correction limit reached')
            db.execute("UPDATE jobs SET status='pending',lease=NULL,worker=NULL,expires=NULL,qa=NULL,error=NULL,updated=? WHERE id=?",
                       (time.time(), args.id))
            event(db, args.id, 'retry')
            result = {'id': args.id, 'status': 'pending'}
        elif cmd == 'stale':
            stale = list(db.execute("SELECT id FROM jobs WHERE status IN ('claimed','generating') AND expires<?", (time.time(),)))
            for r in stale:
                db.execute("UPDATE jobs SET status='uncertain',error='Expired lease; reconcile tool history and saved artifacts before retry',updated=? WHERE id=?", (time.time(), r['id']))
                event(db, r['id'], 'uncertain', 'expired lease')
            result = {'uncertain': [r['id'] for r in stale]}
        elif cmd == 'status':
            result = {'counts': {r['status']: r['n'] for r in db.execute('SELECT status,count(*) n FROM jobs GROUP BY status')},
                      'jobs': [dict(r) for r in db.execute('SELECT id,logical_id,status,worker,attempts,artifact,artifact_hash,error FROM jobs ORDER BY id')]}
        db.execute('COMMIT')
        return result
    except Exception:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'claim', 'start', 'result', 'approve', 'reject', 'fail', 'retry', 'stale', 'status', 'reconcile'):
        sub = subs.add_parser(name)
        sub.add_argument('--project', required=True)
        if name == 'init':
            sub.add_argument('--jobs', required=True)
        if name == 'claim':
            sub.add_argument('--worker', required=True)
            sub.add_argument('--lease-seconds', type=int, default=3600)
        if name in ('start', 'result', 'approve', 'reject', 'fail', 'retry', 'reconcile'):
            sub.add_argument('--id', required=True)
        if name in ('start', 'result', 'fail'):
            sub.add_argument('--lease', required=True)
        if name == 'result':
            sub.add_argument('--image', required=True)
        if name in ('approve', 'reject'):
            sub.add_argument('--qa', required=True)
        if name == 'reconcile':
            sub.add_argument('--evidence', required=True)
        if name == 'fail':
            sub.add_argument('--reason', required=True)
            sub.add_argument('--uncertain', action='store_true')
    args = parser.parse_args()
    if hasattr(args, 'lease_seconds') and args.lease_seconds < 60:
        parser.error('Lease must be at least 60 seconds')
    try:
        print(encoded(execute(args)))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(encoded({'error': str(exc)}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
