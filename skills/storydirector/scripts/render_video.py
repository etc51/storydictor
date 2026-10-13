"""Render a project storyboard and narration with resumable, input-hashed clips."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--storyboard', default='РАСКАДРОВКА.json')
    parser.add_argument('--images', default='Готовые_изображения')
    parser.add_argument('--workers', type=int, default=max(1, min(6, (os.cpu_count() or 4) // 2)))
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--profile', type=Path, default=Path(__file__).parent.parent / 'references/local-profile.json')
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8-sig'))
    root = args.project.resolve()
    ff, probe = profile['tools']['ffmpeg'], profile['tools']['ffprobe']
    fps, width, height = profile['fps'], profile['width'], profile['height']

    def inspect(path):
        return json.loads(subprocess.check_output([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))

    def run(command):
        result = subprocess.run(command, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-3000:])

    duration = float(inspect(args.audio)['format']['duration'])
    shots = json.loads((root / args.storyboard).read_text(encoding='utf-8-sig'))['shots']
    if not shots or not 1 <= args.workers <= 8:
        raise ValueError('Empty storyboard or invalid workers')
    previous, ids = 0, set()
    for shot in shots:
        name = shot['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or name in ids:
            raise ValueError('Invalid or duplicate shot ID')
        ids.add(name)
        start, end = round(shot['start_s'] * fps), round(shot['end_s'] * fps)
        if start != previous or end <= start:
            raise ValueError('Storyboard must be contiguous, positive, and start at zero: ' + name)
        previous = end
        if not (root / args.images / (name + '.png')).is_file():
            raise FileNotFoundError(name + '.png')
        if shot.get('foreground_file') and not (root / shot['foreground_file']).is_file():
            raise FileNotFoundError(shot['foreground_file'])
    if abs(previous / fps - duration) > 1 / fps:
        raise ValueError('Storyboard does not cover narration duration')
    if args.check_only:
        print(json.dumps({'shots': len(shots), 'audio_seconds': duration, 'inputs_valid': True}))
        return
    out = root / 'render'
    clips = out / 'clips'
    clips.mkdir(parents=True, exist_ok=True)

    def render(item):
        index, shot = item
        image = root / args.images / (shot['id'] + '.png')
        frames = round(shot['end_s'] * fps) - round(shot['start_s'] * fps)
        settings = {'shot': shot, 'fps': fps, 'width': width, 'height': height, 'renderer': 3}
        foreground = root / shot['foreground_file'] if shot.get('foreground_file') else None
        inputs = image.read_bytes() + (foreground.read_bytes() if foreground else b'')
        digest = hashlib.sha256(inputs + json.dumps(settings, sort_keys=True).encode()).hexdigest()
        target, stamp = clips / (shot['id'] + '.mp4'), clips / (shot['id'] + '.sha256')
        if target.exists() and stamp.exists() and stamp.read_text() == digest:
            info = inspect(target)['streams'][0]
            if int(info.get('nb_frames', 0)) == frames:
                return
        phase = f'(0.5-0.5*cos(PI*on/{max(1, frames-1)}))'
        zoom = f'1.025+0.035*{phase}' if index % 2 else f'1.060-0.035*{phase}'
        vf = f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},zoompan=z='{zoom}':x='(iw-iw/zoom)*0.5':y='(ih-ih/zoom)*0.5':d={frames}:s={width}x{height}:fps={fps},setsar=1"
        if shot.get('light_pulse', False):
            vf += ",eq=brightness='0.004*sin(2*PI*t/3.7)':eval=frame"
        title_image = None
        if shot.get('chapter_title'):
            from PIL import Image, ImageDraw, ImageFont
            title_image = clips / (shot['id'] + '.title.png')
            canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            font_name = profile.get('tools', {}).get('font_file') or 'DejaVuSans-Bold.ttf'
            try:
                font = ImageFont.truetype(font_name, 42)
            except OSError as exc:
                raise RuntimeError('Configure tools.font_file with a TrueType font supporting the story language') from exc
            text = shot['chapter_title']
            while draw.textlength(text, font=font) > width - 120:
                font = ImageFont.truetype(font_name, font.size - 1)
            right = 80 + draw.textlength(text, font=font)
            draw.rounded_rectangle((40, height-112, right, height-38), radius=8, fill=(0, 0, 0, 170))
            draw.text((60, height-97), text, font=font, fill=(255, 247, 225, 255))
            canvas.save(title_image)
        temporary = target.with_suffix('.partial.mp4')
        command = [ff, '-v', 'error', '-y', '-i', str(image)]
        if foreground or title_image:
            filters = [f'[0:v]{vf}[bg]']
            base, stream = 'bg', 1
            if foreground:
                command += ['-loop', '1', '-framerate', str(fps), '-i', str(foreground)]
                motion = f'12*sin(PI*t/{max(1/fps, frames/fps)})'
                filters.append(f'[{stream}:v]scale={width}:{height},format=rgba[fg]')
                filters.append(f"[{base}][fg]overlay=x='{motion}':y=0:shortest=1:eval=frame[parallax]")
                base, stream = 'parallax', stream+1
            if title_image:
                command += ['-loop', '1', '-framerate', str(fps), '-i', str(title_image)]
                filters.append(f'[{stream}:v]format=rgba[title]')
                filters.append(f"[{base}][title]overlay=0:0:shortest=1:enable='lt(t,5)'[captioned]")
                base = 'captioned'
            filters.append(f'[{base}]format=yuv420p[v]')
            command += ['-filter_complex', ';'.join(filters), '-map', '[v]']
        else:
            command += ['-vf', vf]
        command += ['-frames:v', str(frames), '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p', '-threads', '2', str(temporary)]
        run(command)
        if int(inspect(temporary)['streams'][0]['nb_frames']) != frames:
            raise ValueError('Invalid clip: ' + shot['id'])
        temporary.replace(target)
        stamp.write_text(digest)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(render, enumerate(shots)))
    listing = out / 'concat.txt'
    listing.write_text(''.join("file 'clips/" + s['id'] + ".mp4'\n" for s in shots), encoding='utf-8')
    target = out / 'film.mp4'
    temporary = out / 'film.partial.mp4'
    run([ff, '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-i', str(args.audio.resolve()), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration), '-movflags', '+faststart', str(temporary)])
    info = inspect(temporary)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    if abs(float(info['format']['duration']) - duration) > 0.1 or (video['width'], video['height']) != (width, height):
        raise ValueError('Final output validation failed')
    run([ff, '-v', 'error', '-i', str(temporary), '-f', 'null', '-'])
    temporary.replace(target)
    report = {'shots': len(shots), 'duration': duration, 'decode_passed': True, 'perceptual_review': False, 'character_motion': False, 'file': str(target)}
    (out / 'qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
