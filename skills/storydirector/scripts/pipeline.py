"""Codex-owned video production: durable local stages and bounded ElevenLabs calls.

Creative input is produced by Codex, not by an invented external image/LLM API.
Run `init --topic ...`, let Codex populate story.json, then `run --project ...`.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from editorial_guard import validate as validate_editorial

HOME = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def clean(text):
    return re.sub(r'\[[^\]]*\]', '', text).strip()


def normalized(text):
    return re.findall(r'\w+', text.lower().replace('ё', 'е').replace('\u0301', ''))


class NeedsAgent(Exception):
    pass


class Pipeline:
    def __init__(self, root, profile):
        self.root = Path(root).resolve()
        self.profile = profile
        self.state_path = self.root / 'project_state.json'
        self.state = read(self.state_path)
        self.ff = shutil.which(profile['tools']['ffmpeg']) or profile['tools']['ffmpeg']
        self.probe = shutil.which(profile['tools']['ffprobe']) or profile['tools']['ffprobe']

    def event(self, stage, status, **fields):
        self.state['stages'][stage] = {'status': status, **fields}
        self.state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        save(self.state_path, self.state)
        with (self.root / 'ACTIONS.md').open('a', encoding='utf-8') as stream:
            stream.write(f"\n- {self.state['updated_at']} {stage}: {status}\n")

    def media(self, path):
        return json.loads(subprocess.check_output([self.probe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))

    def execute(self, command):
        result = subprocess.run(command, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-3000:])
        return result

    def tasks(self, stage, tasks):
        save(self.root / 'agent_tasks.json', {'stage': stage, 'tasks': tasks, 'ask_user': False})
        self.event(stage, 'pending', agent_tasks='agent_tasks.json')
        raise NeedsAgent(stage)

    def preflight(self):
        for name, executable in (('ffmpeg', self.ff), ('ffprobe', self.probe)):
            if not Path(executable).is_file():
                raise FileNotFoundError('Configure ' + name + ' in production_profile.json or install it on PATH')
        if not self.profile.get('tts', {}).get('key_env'):
            raise ValueError('No configured key source')
        self.event('preflight', 'completed')

    def api(self, identity, endpoint, payload=None, multipart=None, paid=True, estimated_credits=None):
        """Never automatically resend an ambiguous paid call; no secret arguments."""
        request_hash = digest({'endpoint': endpoint, 'payload': payload, 'multipart': multipart})
        output = self.root / 'api' / (identity + '-' + request_hash[:16] + '.response')
        entry = self.state.setdefault('requests', {}).get(identity)
        if entry and entry['hash'] == request_hash:
            if entry['status'] == 'completed' and output.is_file() and file_hash(output) == entry['output_hash']:
                return output
            if entry['status'] in ('running', 'uncertain'):
                raise RuntimeError('Uncertain API outcome; reconcile before retry: ' + identity)
        if paid and not self.state.get('quota_verified'):
            raise RuntimeError('Subscription quota not verified; run account-check first')
        if paid:
            # Preserve cache retrieval/uncertain reconciliation above this guard.
            # Only a NEW dependent request requires a current editorial decision.
            self.editorial(read(self.root / 'story.json') if (self.root / 'story.json').exists() else None)
            self.account_check()
            if estimated_credits is not None:
                estimate = math.ceil(estimated_credits * 1.25)
            elif payload and 'text' in payload:
                estimate = math.ceil(len(payload['text']) * 1.25)
            else:
                estimate = math.ceil(float(self.media(multipart['file'][1:])['format']['duration']) / 60 * 330 * 1.25)
            if estimate > self.state['quota_verified']['remaining_credits']:
                raise RuntimeError('Insufficient included quota for request and reserve: ' + identity)
        key = os.environ.get(self.profile['tts']['key_env'])
        if not key and self.profile['tts'].get('key_file'):
            key = Path(self.profile['tts']['key_file']).read_text(encoding='utf-8-sig').strip()
        if not key:
            raise RuntimeError('Set the configured ElevenLabs key environment variable; never commit credentials')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
            raise ValueError('Invalid key format')
        output.parent.mkdir(exist_ok=True)
        command = ['curl.exe', '--config', '-', '--ssl-revoke-best-effort', '-sS', '--max-time', '240', '--output', str(output), '--write-out', '%{http_code}', self.profile['tts']['api_base'] + endpoint]
        body_file = None
        if payload is not None:
            body_file = output.with_suffix('.request.json')
            save(body_file, payload)
            command += ['-H', 'Content-Type: application/json', '--data-binary', '@' + str(body_file)]
        if multipart:
            for field, value in multipart.items():
                command += ['-F', field + '=' + value]
        config = 'header = "xi-api-key: ' + key + '"\n'
        self.state['requests'][identity] = {'hash': request_hash, 'status': 'running', 'paid': paid}
        save(self.state_path, self.state)
        result = subprocess.run(command, input=config.encode(), capture_output=True)
        code = result.stdout.decode().strip()
        entry = self.state['requests'][identity]
        entry['http_status'] = code
        if result.returncode or not code.isdigit() or int(code) >= 500:
            entry['status'] = 'uncertain' if paid else 'failed'
            save(self.state_path, self.state)
            raise RuntimeError('API outcome uncertain: ' + identity)
        if int(code) != 200:
            entry['status'] = 'failed'
            save(self.state_path, self.state)
            raise RuntimeError('API HTTP ' + code + '; details saved at ' + str(output))
        entry.update(status='completed', output_hash=file_hash(output))
        save(self.state_path, self.state)
        return output

    def account_check(self):
        # Fresh account state, never reuse a prior quota snapshot.
        identity = 'account-' + str(time.time_ns())
        data = read(self.api(identity, '/v1/user/subscription', paid=False))
        remaining = data['character_limit'] - data['character_count']
        overage = data.get('max_credit_limit_extension', 0)
        if overage not in (0, None):
            raise RuntimeError('Usage-based extension is enabled; included-only production requires disabling it')
        if data.get('tier') == 'free' or remaining <= 0:
            raise RuntimeError('Paid subscription and remaining quota required')
        self.state['quota_verified'] = {'remaining_credits': remaining, 'tier': data['tier'], 'checked_at': time.time()}
        save(self.state_path, self.state)
        self.event('account', 'completed', remaining_credits=remaining)
        return data

    def script(self):
        path = self.root / 'story.json'
        if not path.exists():
            self.tasks('script', [{'action': 'write_story', 'file': str(path), 'topic': self.state['topic'], 'target_duration_seconds': self.profile['target_duration_seconds'], 'schema': 'references/pipeline.md'}])
        story = read(path)
        chunks, shots = story.get('chunks', []), story.get('shots', [])
        if not chunks or not shots:
            raise ValueError('story.json requires chunks and shots')
        ids = set()
        for chunk in chunks:
            name = chunk['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in ids:
                raise ValueError('Invalid chunk ID')
            ids.add(name)
            if not chunk.get('text', '').strip() or len(chunk['text']) > 4500:
                raise ValueError('Chunk must contain 1-4500 characters')
        shot_ids = set()
        for shot in shots:
            if not re.fullmatch(r'[A-Za-z0-9_-]+', shot['id']) or shot['id'] in shot_ids or shot['chunk_id'] not in ids:
                raise ValueError('Invalid shot ID or chunk reference')
            shot_ids.add(shot['id'])
        jokes = sum(bool(s.get('humorous_object_text') or s.get('visual_gag')) for s in shots)
        if jokes != math.floor(len(shots) * .5):
            raise ValueError('Visual humor quota violated: 50% of unique shots, rounded down, required')
        self.event('script', 'completed', input_hash=file_hash(path))
        return story

    def voices(self, story):
        self.editorial(story)
        audio = self.root / 'audio'
        audio.mkdir(exist_ok=True)
        records = []
        for chunk in story['chunks']:
            name = chunk['id']
            path = audio / (name + '.wav')
            supplied = chunk.get('audio_file')
            if supplied:
                source = self.local(supplied)
            else:
                if not self.state.get('quota_verified') or time.time() - self.state['quota_verified']['checked_at'] > 300:
                    self.account_check()
                model = chunk.get('model', self.profile['tts']['preferred_model'])
                payload = {'text': chunk['text'], 'model_id': model, 'language_code': self.profile['language']}
                voice = chunk.get('voice_id', self.profile['tts']['default_voice_id'])
                source = self.api('tts-' + name, '/v1/text-to-speech/' + voice + '?output_format=mp3_44100_128', payload)
            self.execute([self.ff, '-v', 'error', '-y', '-i', str(source), '-ar', '48000', '-ac', '1', str(path)])
            records.append({'id': name, 'audio': str(path), 'seconds': float(self.media(path)['format']['duration']), 'text': clean(chunk['text'])})
        offset = 0.0
        for record in records:
            record['offset'] = offset
            offset += record['seconds']
        save(audio / 'chunks.json', records)
        # Relative filenames are generated IDs, not user paths in concat syntax.
        listing = audio / 'concat.txt'
        listing.write_text(''.join("file '" + r['id'] + ".wav'\n" for r in records), encoding='utf-8')
        self.execute([self.ff, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c:a', 'pcm_s16le', str(audio / 'narration.wav')])
        self.event('voice', 'completed', duration_seconds=offset)
        return records

    def local(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def alignment(self, story, records):
        all_words, problems, transcript_parts = [], [], []
        for chunk, record in zip(story['chunks'], records):
            supplied = chunk.get('transcript_file')
            if supplied:
                data = read(self.local(supplied))
            else:
                if not self.state.get('quota_verified') or time.time() - self.state['quota_verified']['checked_at'] > 300:
                    self.account_check()
                data = read(self.api('stt-' + chunk['id'] + '-' + file_hash(record['audio'])[:12], '/v1/speech-to-text', multipart={'model_id': self.profile['tts']['stt_model'], 'language_code': self.profile['language'], 'file': '@' + record['audio']}))
            score = difflib.SequenceMatcher(None, normalized(record['text']), normalized(data.get('text', '')), autojunk=False).ratio()
            approval_path = self.root / 'speech_qa_approval.json'
            approvals = read(approval_path) if approval_path.exists() else {}
            comparison_hash = digest({'expected': record['text'], 'recognized': data.get('text', ''), 'audio_hash': file_hash(record['audio'])})
            if score < .85 and approvals.get(chunk['id']) != comparison_hash:
                problems.append({'chunk_id': chunk['id'], 'similarity': score, 'expected': record['text'], 'recognized': data.get('text'), 'comparison_hash': comparison_hash})
            words = [w for w in data.get('words', []) if w.get('type', 'word') == 'word']
            if not words:
                raise ValueError('No word timestamps: ' + chunk['id'])
            previous = 0.0
            for word in words:
                if not 0 <= word['start'] <= word['end'] <= record['seconds'] + .1 or word['start'] < previous:
                    raise ValueError('Invalid word timing')
                previous = word['start']
                all_words.append({'chunk_id': chunk['id'], 'text': word['text'], 'start': word['start'] + record['offset'], 'end': word['end'] + record['offset']})
            transcript_parts.append(data.get('text', ''))
        save(self.root / 'audio/words.json', all_words)
        save(self.root / 'audio/text_qa.json', {'issues': problems, 'perceptual_audio_review': False})
        if problems:
            self.tasks('speech_qa', [{'action': 'review_transcript_difference', **issue} for issue in problems])
        fps = self.profile['fps']
        boundaries = []
        for record in records:
            planned = [s for s in story['shots'] if s['chunk_id'] == record['id']]
            words = [w for w in all_words if w['chunk_id'] == record['id']]
            if not planned:
                raise ValueError('No shots for chunk ' + record['id'])
            starts = [s.get('start_word', 0) for s in planned]
            if starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= len(words):
                raise ValueError('Shot start_word must be ordered, unique, start at zero, and refer to recognized words')
            for index, shot in enumerate(planned):
                start = record['offset'] if index == 0 else words[starts[index]]['start']
                end = record['offset'] + record['seconds'] if index == len(planned)-1 else words[starts[index+1]]['start']
                boundaries.append({**shot, 'start_s': round(start * fps) / fps, 'end_s': round(end * fps) / fps})
        save(self.root / 'РАСКАДРОВКА.json', {'shots': boundaries, 'timing_source': 'recognized_word_timestamps'})
        self.subtitles(all_words)
        self.event('alignment', 'completed', words=len(all_words))
        return boundaries

    def subtitles(self, words):
        def stamp(value):
            ms = round(value * 1000)
            return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
        blocks = []
        group = []
        for word in words:
            if group and (len(group) >= 7 or word['start'] - group[0]['start'] > 4 or word['chunk_id'] != group[0]['chunk_id']):
                blocks.append(group)
                group = []
            group.append(word)
        if group:
            blocks.append(group)
        (self.root / 'subtitles.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(g[0]['start'])} --> {stamp(g[-1]['end'])}\n" + ' '.join(w['text'] for w in g) for i, g in enumerate(blocks)) + '\n', encoding='utf-8')

    def images(self, shots):
        self.editorial(read(self.root / 'story.json'))
        directory = self.root / 'Готовые_изображения'
        directory.mkdir(exist_ok=True)
        missing = []
        for shot in shots:
            path = directory / (shot['id'] + '.png')
            if not path.is_file():
                missing.append({'action': 'generate_and_review_image', 'file': str(path), 'shot': shot, 'reference_bible': str(self.root / 'visual_bible.json')})
            else:
                self.media(path)
        if missing:
            self.tasks('images', missing)
        qa_path = self.root / 'image_qa.json'
        qa = read(qa_path).get('reviewed', {}) if qa_path.exists() else {}
        unreviewed = []
        for shot in shots:
            path = directory / (shot['id'] + '.png')
            review = qa.get(shot['id'], {})
            if review.get('status') != 'approved' or review.get('sha256') != file_hash(path):
                unreviewed.append({'action': 'visually_review_current_image', 'id': shot['id'], 'file': str(path), 'write': str(qa_path), 'required_sha256': file_hash(path)})
        if unreviewed:
            self.tasks('image_qa', unreviewed)
        self.event('images', 'completed', count=len(shots))

    def soundtrack(self, story):
        self.editorial(story)
        for index, item in enumerate(story.get('soundtrack', [])):
            if item.get('anchor_end'):
                duration = float(self.media(self.root / 'audio/narration.wav')['format']['duration'])
                item['start_s'] = duration - float(item['duration_s'])
            if item.get('anchor_shot_id'):
                shots = read(self.root / 'РАСКАДРОВКА.json')['shots']
                anchor = next(s for s in shots if s['id'] == item['anchor_shot_id'])
                item['start_s'] = anchor['start_s'] + float(item.get('anchor_offset_s', 0))
            if item.get('file') and self.local(item['file']).is_file():
                continue
            if not item.get('prompt') or item.get('kind') not in ('music', 'sfx'):
                self.tasks('soundtrack', [{'action': 'select_or_describe_audio', 'item': item}])
            if not self.state.get('quota_verified'):
                self.account_check()
            length = float(item.get('source_duration_s', item['duration_s']))
            if item['kind'] == 'music':
                if not 3 <= length <= 300:
                    raise ValueError('Generate music in 3-300 second source themes')
                endpoint = '/v1/music'
                payload = {'prompt': item['prompt'], 'music_length_ms': round(length * 1000), 'force_instrumental': True, 'model_id': 'music_v2_5'}
                credits = length / 60 * 900
            else:
                if not 0.5 <= length <= 30:
                    raise ValueError('SFX source duration must be 0.5-30 seconds')
                endpoint = '/v1/sound-generation'
                payload = {'text': item['prompt'], 'duration_seconds': length}
                credits = max(200, length * 40)
            source = self.api('sound-' + str(index), endpoint, payload, estimated_credits=credits)
            destination = self.root / 'assets' / ('generated_' + str(index) + '.wav')
            destination.parent.mkdir(exist_ok=True)
            self.execute([self.ff, '-v', 'error', '-y', '-i', str(source), '-ar', '48000', '-c:a', 'pcm_s16le', str(destination)])
            item['file'] = str(destination)
            item['provenance'] = {'provider': 'ElevenLabs', 'input_hash': digest(payload), 'request_record': 'sound-' + str(index), 'license_confirmation': self.profile['audio_assets'].get('music_license_status')}
        save(self.root / 'soundtrack_manifest.json', story.get('soundtrack', []))
        self.event('soundtrack', 'completed', assets=len(story.get('soundtrack', [])))

    def mix(self, story):
        narration = self.root / 'audio/narration.wav'
        duration = float(self.media(narration)['format']['duration'])
        command = [self.ff, '-v', 'error', '-y', '-i', str(narration)]
        filters = ['[0:a]asplit=2[voice][key]']
        backgrounds = []
        for index, item in enumerate(story.get('soundtrack', []), 1):
            source = self.local(item['file'])
            if not source.exists():
                self.tasks('soundtrack', [{'action': 'create_or_select_audio', 'item': item}])
            start, length = float(item['start_s']), float(item['duration_s'])
            if start < 0 or length <= 0 or start + length > duration + .05:
                raise ValueError('Soundtrack outside narration')
            if item.get('kind') not in ('music', 'sfx') or not item.get('provenance'):
                raise ValueError('Soundtrack requires kind and provenance')
            if item.get('loop', False):
                command += ['-stream_loop', '-1']
            command += ['-i', str(source)]
            delay = round(start * 1000)
            gain = float(item.get('gain_db', -20 if item['kind'] == 'music' else -12))
            fade = min(.5, length / 4)
            label = 'bg' + str(index)
            filters.append(f'[{index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,volume={gain}dB,afade=t=in:d={fade},afade=t=out:st={length-fade}:d={fade},adelay={delay}:all=1[{label}]')
            backgrounds.append('[' + label + ']')
        if backgrounds:
            filters.append(''.join(backgrounds) + f'amix=inputs={len(backgrounds)}:normalize=0,apad,atrim=duration={duration}[background]')
            filters.append('[background][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=300[ducked]')
            filters.append('[voice][ducked]amix=inputs=2:normalize=0[mixed]')
        else:
            filters = ['[0:a]anull[mixed]']
        filters.append('[mixed]loudnorm=I=-16:TP=-1:LRA=11,aresample=48000[out]')
        target = self.root / 'audio/mix.wav'
        command += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-t', str(duration), '-c:a', 'pcm_s24le', str(target)]
        self.execute(command)
        self.event('mix', 'completed', music_and_sfx=bool(backgrounds))
        return target

    def render(self, audio):
        self.editorial(read(self.root / 'story.json'))
        self.execute([sys.executable, str(HOME / 'scripts/render_video.py'), '--project', str(self.root), '--audio', str(audio), '--profile', str(self.root / 'production_profile.json')])
        if not (self.root / 'cover_generation.json').exists():
            self.execute([self.ff, '-v', 'error', '-y', '-i', str(self.root / 'render/film.mp4'), '-frames:v', '1', str(self.root / 'cover.png')])
        save(self.root / 'manifest.json', {'topic': self.state['topic'], 'story_hash': file_hash(self.root / 'story.json'), 'film': 'render/film.mp4', 'subtitles': 'subtitles.srt', 'audio': 'audio/mix.wav', 'shots': read(self.root / 'РАСКАДРОВКА.json')['shots'], 'technical_qa': read(self.root / 'render/qa.json'), 'creative_qa': 'Agent review required'})
        self.event('render', 'completed', film='render/film.mp4')
        self.tasks('final_review', [{'action': 'review_video_audio_and_story', 'film': str(self.root / 'render/film.mp4'), 'write': str(self.root / 'creative_qa.json'), 'requirements': ['continuity', 'emotions', 'readable_object_jokes', 'audio_balance', 'no_missing_content']}])

    def editorial(self, story=None):
        result = validate_editorial(self.root, story)
        if result['errors']:
            self.tasks('story_editorial', [{
                'action': 'complete_independent_editorial_review',
                'requirements': str(HOME / 'references/story-editorial.md'),
                'write': str(self.root / 'editorial_qa.json'),
                'errors': result['errors'],
                'preserve_existing_materials': True,
            }])
        self.event('story_editorial', 'completed', review='editorial_qa.json',
                   input_hash=read(self.root / 'editorial_qa.json')['input_sha256'])

    def run(self):
        self.event('execution', 'running')
        self.state['stages'].pop('delivery', None)
        save(self.state_path, self.state)
        self.preflight()
        story = self.script()
        self.editorial(story)
        records = self.voices(story)
        shots = self.alignment(story, records)
        self.images(shots)
        self.soundtrack(story)
        audio = self.mix(story)
        self.render(audio)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'run', 'status', 'account-check', 'finish'])
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--topic')
    parser.add_argument('--profile', type=Path, default=HOME / 'references/local-profile.json')
    args = parser.parse_args()
    profile = read(args.profile)
    if args.command == 'init':
        if not args.topic or (args.project / 'project_state.json').exists():
            raise ValueError('Topic required; existing projects cannot be overwritten')
        args.project.mkdir(parents=True, exist_ok=True)
        save(args.project / 'project_state.json', {'schema_version': 1, 'topic': args.topic, 'stages': {}, 'requests': {}})
        save(args.project / 'production_profile.json', profile)
        (args.project / 'ACTIONS.md').write_text('# Production actions\n', encoding='utf-8')
        print(json.dumps({'project': str(args.project), 'next': 'Codex writes story.json and visual_bible.json'}, ensure_ascii=False))
        return
    if args.profile == HOME / 'references/local-profile.json' and (args.project / 'production_profile.json').exists():
        profile = read(args.project / 'production_profile.json')
    pipeline = Pipeline(args.project, profile)
    if args.command == 'status':
        print(json.dumps(pipeline.state, ensure_ascii=False, indent=2))
        return
    lock = args.project / '.pipeline.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('Project locked; verify previous process has stopped before removing lock')
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        if args.command == 'account-check':
            pipeline.account_check()
        elif args.command == 'finish':
            pipeline.editorial(read(args.project / 'story.json'))
            review = read(args.project / 'creative_qa.json')
            if not review.get('passed') or not review.get('reviewed_story_hash') == file_hash(args.project / 'story.json') or not review.get('reviewed_film_hash') == file_hash(args.project / 'render/film.mp4'):
                raise ValueError('A passing creative review of current story and film is required')
            pipeline.event('final_review', 'completed', review='creative_qa.json')
            pipeline.event('delivery', 'completed', film='render/film.mp4')
            pipeline.event('execution', 'completed')
        else:
            pipeline.run()
    except NeedsAgent as exc:
        pipeline.event('execution', 'waiting_agent', waiting_for=str(exc))
        print(json.dumps({'status': 'needs_agent', 'stage': str(exc), 'tasks': str(args.project / 'agent_tasks.json'), 'ask_user': False}))
    except Exception as exc:
        pipeline.event('execution', 'failed', error=str(exc), resumable=True)
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
