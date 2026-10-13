"""Validate the distributable package without accounts or paid production."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    p = json.loads((ROOT / 'plugin.json').read_text(encoding='utf-8'))
    ext = p['extensions']['com.openai']
    ui = ext['interface']
    for key, limit in [('displayName', 30), ('shortDescription', 30), ('longDescription', 4000), ('developerName', 80)]:
        if not isinstance(ui.get(key), str) or not 0 < len(ui[key]) <= limit:
            errors.append('Invalid listing field: ' + key)
    for prompt in ui.get('defaultPrompt', []):
        if len(prompt) > 128:
            errors.append('Starter prompt too long')
    for key in ('composerIcon', 'logo'):
        path = ROOT / ui[key]
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            errors.append('Invalid icon: ' + key)
        else:
            from PIL import Image
            with Image.open(path) as image:
                if image.width != image.height or not 48 <= image.width <= 4096:
                    errors.append('Icon dimensions invalid: ' + key)
    if ext.get('requires_local_executor') is not True:
        errors.append('Local execution must be declared')
    if not (ROOT / ext['onboardingSkill']).is_file():
        errors.append('Missing onboarding skill')
    ru = ext['publication']['translations']['ru-RU']
    if not 0 < len(ru['subtitle']) <= 30 or not 0 < len(ru['description']) <= 4000:
        errors.append('Invalid Russian listing text')
    for skill in (ROOT / 'skills').iterdir():
        if not (skill / 'SKILL.md').is_file() or not (skill / 'LICENSE').is_file():
            errors.append('Incomplete skill: ' + skill.name)
    for path in (ROOT / 'skills').rglob('*'):
        if path.is_file() and path.suffix in ('.md', '.json', '.py', '.ps1', '.yaml'):
            text = path.read_text(encoding='utf-8-sig')
            if re.search(r'[A-Za-z]:[/\\]Users[/\\][^/\\\s\"]+', text):
                errors.append('Private installation residue: ' + str(path.relative_to(ROOT)))
    profile = json.loads((ROOT / 'skills/storydirector/references/local-profile.json').read_text(encoding='utf-8'))
    if profile['tts'].get('key_file') or profile['tts'].get('verified'):
        errors.append('Profile must not inherit private credentials or permissions')
    if profile['budget']['allow_overage'] or profile['delivery']['publish']:
        errors.append('Unsafe production defaults')
    print(json.dumps({'status': 'failed' if errors else 'passed', 'errors': errors, 'skills': len(list((ROOT/'skills').iterdir()))}, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == '__main__':
    main()
