"""Build a complete secret-free plugin ZIP from explicit package paths."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATHS = ['plugin.json', '.codex-plugin', '.agents', 'skills', 'assets', 'docs',
                 'scripts', 'README.md', 'README.ru.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements.txt']


def package_files():
    for item in PACKAGE_PATHS:
        path = ROOT / item
        candidates = [path] if path.is_file() else sorted(path.rglob('*'))
        for p in candidates:
            if not p.is_file() or '__pycache__' in p.parts or p.suffix in ('.pyc', '.pyo'):
                continue
            yield p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'plugin.json').read_text(encoding='utf-8'))
    files = list(package_files())
    for p in files:
        if p.suffix in ('.md', '.json', '.yaml', '.py', '.ps1'):
            text = p.read_text(encoding='utf-8-sig')
            if re.search(r'[A-Za-z]:[/\\]Users[/\\][^/\\\s\"]+|(?<!\w)sk[-_][A-Za-z0-9_-]{30,}', text):
                raise ValueError('Private path or possible credential found in ' + str(p.relative_to(ROOT)))
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"storydictor-{manifest['version']}.zip"
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            archive.write(p, p.relative_to(ROOT).as_posix())
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    (args.output / 'SHA256SUMS').write_text(sha + '  ' + target.name + '\n', encoding='utf-8')
    print(json.dumps({'file': str(target), 'sha256': sha, 'files': len(files), 'bytes': target.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
