"""Validate evidence for a human/agent editorial decision; never judge prose quality."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def clean(text):
    return re.sub(r'\[[^\]]*\]', '', text).strip()


def narrative_content(text):
    # Chunk boundaries and supported direction tags do not alter spoken content.
    # Preserve words, punctuation and order; only formatting whitespace is ignored.
    return ' '.join(clean(text).split())


def spoken_snapshot(text, headers, story, errors):
    """Exclude explicitly declared structural headers, never arbitrary narration.

    The immutable snapshot and its line numbers/hash remain unchanged. Only the
    spoken-content comparison uses this projection. Do not infer exclusions.
    """
    lines = text.splitlines()
    excluded = set()
    if not isinstance(headers, list):
        errors.append('nonspoken_headers must be a list')
        return text
    chunks = story.get('chunks', []) if story is not None else None
    for header in headers:
        if not isinstance(header, dict):
            errors.append('invalid nonspoken header declaration')
            continue
        line, exact = header.get('line'), header.get('text')
        if (type(line) is not int or not 1 <= line <= len(lines)
                or not isinstance(exact, str) or exact != lines[line - 1]):
            errors.append('nonspoken header line/text mismatch: ' + str(line))
            continue
        if line in excluded:
            errors.append('duplicate nonspoken header line: ' + str(line))
            continue
        chunk_header = re.fullmatch(r'## (C[0-9]+)', exact)
        ladder_header = re.fullmatch(r'@([1-9][0-9]*) ([^\r\n]+)', exact)
        if not chunk_header and not ladder_header:
            errors.append('not a supported structural nonspoken header: ' + str(line))
            continue
        if chunk_header and chunks is not None and chunk_header.group(1) not in {c['id'] for c in chunks}:
            errors.append('nonspoken header refers to unknown chunk: ' + chunk_header.group(1))
            continue
        if ladder_header and chunks is not None:
            ordinal = int(ladder_header.group(1))
            if ordinal > len(chunks):
                errors.append('nonspoken ladder header ordinal exceeds chunk count: ' + str(line))
                continue
            title = chunks[ordinal - 1].get('title')
            if title is not None and title != ladder_header.group(2):
                errors.append('nonspoken ladder header differs from chunk title: ' + str(line))
                continue
        excluded.add(line)
    return '\n'.join(line for i, line in enumerate(lines, 1) if i not in excluded)


def validate(root, story=None):
    """Return structured blockers without mutation, network or creative approval.

    Reports bind to the exact bytes of narration_file. A supplied story must have
    exactly the same spoken content. Different whitespace is harmless; words and
    punctuation changes are not. JSON declarations are provenance, not an identity
    authentication system: the coordinator must record real subagent execution.
    """
    root = Path(root).resolve()
    errors = []

    def local(value):
        path = Path(value)
        return path if path.is_absolute() else root / path

    try:
        qa = read(root / 'editorial_qa.json')
        if qa.get('schema_version') != 1:
            errors.append('unsupported editorial schema_version')
        if qa.get('status') != 'passed':
            errors.append('editorial status is not passed: ' + str(qa.get('status')))
        writer = qa.get('writer_id')
        if not isinstance(writer, str) or not writer.strip():
            errors.append('writer_id is required')
        source = local(qa['narration_file'])
        source_hash = sha256(source)
        text = source.read_text(encoding='utf-8-sig')
        if qa.get('input_sha256') != source_hash:
            errors.append('editorial decision has stale narration hash')
        projected = spoken_snapshot(text, qa.get('nonspoken_headers', []), story, errors)
        if story is not None:
            spoken = '\n\n'.join(clean(c['text']) for c in story['chunks'])
            if narrative_content(projected) != narrative_content(spoken):
                errors.append('reviewed narration differs from current story chunks')
        lines = text.splitlines()
        resolutions = {}
        for item in qa.get('findings_resolution', []):
            key = (item.get('report_role'), item.get('finding_id'))
            if key in resolutions:
                errors.append('duplicate finding resolution: ' + str(key))
            resolutions[key] = item
        reviewers = []
        for role in ('critic', 'reader'):
            try:
                path = local(qa['reports'][role])
                report = read(path)
                if qa.get('reports_sha256', {}).get(role) != sha256(path):
                    errors.append(role + ' report hash missing or stale')
                if report.get('schema_version') != 1 or report.get('role') != role:
                    errors.append(role + ' report schema/role mismatch')
                if report.get('input_sha256') != source_hash:
                    errors.append(role + ' report has stale narration hash')
                if report.get('scope') != 'full_script':
                    errors.append(role + ' review is incomplete')
                reviewer = report.get('reviewer_id')
                if not isinstance(reviewer, str) or not reviewer.strip():
                    errors.append(role + ' reviewer_id is required')
                elif reviewer == writer or report.get('independent') is not True:
                    errors.append(role + ' is self-review or not independent')
                reviewers.append(reviewer)
                if not isinstance(report.get('summary'), str) or not report['summary'].strip():
                    errors.append(role + ' summary is required')
                if role == 'reader' and not report.get('persona'):
                    errors.append('reader persona is required')
                findings = report.get('findings')
                if not isinstance(findings, list):
                    errors.append(role + ' findings must be a list')
                    continue
                seen = set()
                for finding in findings:
                    fid = finding.get('id')
                    if not isinstance(fid, str) or not fid or fid in seen:
                        errors.append(role + ' missing/duplicate finding ID')
                    seen.add(fid)
                    severity = finding.get('severity')
                    if severity not in ('blocking', 'major', 'minor'):
                        errors.append(role + ' invalid severity: ' + str(fid))
                    location = finding.get('location', {})
                    start, end, quote = location.get('line_start'), location.get('line_end'), location.get('quote')
                    if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines)
                            or not isinstance(quote, str) or not quote.strip()
                            or quote not in '\n'.join(lines[start - 1:end])):
                        errors.append(role + ' invalid quotation/location: ' + str(fid))
                    if not finding.get('issue') or not finding.get('reason'):
                        errors.append(role + ' finding lacks issue/reason: ' + str(fid))
                    # Writer resolution cannot overrule a current blocking review.
                    # Revision requires a NEW full report of the current input.
                    if severity == 'blocking':
                        errors.append(role + ' open blocking finding: ' + str(fid))
                    elif severity == 'major':
                        resolution = resolutions.get((role, fid), {})
                        status = resolution.get('status')
                        rationale = resolution.get('rationale', '')
                        if status not in ('resolved', 'accepted') or not isinstance(rationale, str) or not rationale.strip():
                            errors.append(role + ' unaddressed major finding: ' + str(fid))
                        elif status == 'resolved' and resolution.get('verified_by') != reviewer:
                            errors.append(role + ' major resolution not independently verified: ' + str(fid))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                errors.append(role + ' report unavailable/invalid: ' + str(exc))
        if len(reviewers) == 2 and reviewers[0] == reviewers[1]:
            errors.append('critic and reader must be different agents')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append('editorial decision unavailable/invalid: ' + str(exc))
    return {'status': 'needs_agent' if errors else 'passed', 'stage': 'story_editorial',
            'errors': errors, 'ask_user': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--story', type=Path)
    args = parser.parse_args()
    result = validate(args.project, read(args.story) if args.story else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
