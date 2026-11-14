#!/usr/bin/env python3
"""Fork-Waechter fuer KreTiX-IT/rdgen.

Stellt sicher, dass die eigenen Absicherungen dieses Forks nach einem Abgleich
mit bryangerlach/rdgen erhalten bleiben:

- Workflows laden Patches und den printer-adapter nur aus diesem Repo,
  festgenagelt auf den Commit des laufenden Workflows (github.sha), nicht live
  vom master-Zweig des Originalautors.
- Die Endpunkte, die die Workflows aufrufen, verlangen das Upload-Token.
- Hide-CM (unsichtbare Sitzungen) bleibt deaktiviert.

Aufruf:
    python3 .github/scripts/fork_guard.py --check   # nur pruefen, Exit 1 bei Fund
    python3 .github/scripts/fork_guard.py --fix     # Workflow-URLs umschreiben, dann pruefen
"""
import argparse
import glob
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# Absichtlich zusammengesetzt, damit GitHub Actions den Ausdruck nicht schon
# beim Lesen einer Workflow-Datei auswertet; in die Workflows soll er woertlich.
EXPR_REPO = '$' + '{{ github.repository }}'
EXPR_SHA = '$' + '{{ github.sha }}'

UPSTREAM_RAW = 'bryangerlach/rdgen/refs/heads/master'
UPSTREAM_REPO_LINE = re.compile(r'^(\s*)repository:\s*bryangerlach/rdgen\s*$')
REF_MASTER_LINE = re.compile(r'^(\s*)ref:\s*master\s*$')


def workflow_files():
    return sorted(glob.glob(os.path.join(ROOT, '.github', 'workflows', '*.yml')))


def rel(path):
    return os.path.relpath(path, ROOT)


def fix_workflows():
    changed = []
    for path in workflow_files():
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        out = []
        pending_ref = False
        for line in lines:
            line = line.replace(UPSTREAM_RAW, EXPR_REPO + '/' + EXPR_SHA)
            m = UPSTREAM_REPO_LINE.match(line)
            if m:
                line = f'{m.group(1)}repository: {EXPR_REPO}'
                pending_ref = True
            elif pending_ref and line.strip():
                # Die ref-Zeile folgt im Original direkt auf die repository-Zeile.
                m = REF_MASTER_LINE.match(line)
                if m:
                    line = f'{m.group(1)}ref: {EXPR_SHA}'
                pending_ref = False
            out.append(line)
        new = '\n'.join(out)
        with open(path, encoding='utf-8') as fh:
            old = fh.read()
        if new != old:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new)
            changed.append(rel(path))
    return changed


def check():
    problems = []

    for path in workflow_files():
        with open(path, encoding='utf-8') as fh:
            for no, line in enumerate(fh, 1):
                if UPSTREAM_RAW in line or UPSTREAM_REPO_LINE.match(line.rstrip('\n')):
                    problems.append(
                        f'{rel(path)}:{no}: laedt Code live vom Original-Repo statt aus diesem Fork')

    views_path = os.path.join(ROOT, 'rdgenerator', 'views.py')
    with open(views_path, encoding='utf-8') as fh:
        views = fh.read()
    if 'def _has_upload_token' not in views:
        problems.append('rdgenerator/views.py: _has_upload_token() fehlt')
    for func in ('update_github_run', 'save_custom_client', 'cleanup_secrets'):
        m = re.search(r'^def ' + func + r'\(request\):\n((?:[ \t].*\n|\n)*?)(?=^\S)', views, re.M)
        body = m.group(1) if m else ''
        head = '\n'.join(body.split('\n')[:3])
        if '_has_upload_token(request)' not in head:
            problems.append(f'rdgenerator/views.py: {func}() prueft das Upload-Token nicht mehr')
    if not re.search(r'^\s*hidecm = False\s*$', views, re.M):
        problems.append('rdgenerator/views.py: hidecm ist nicht mehr fest auf False gesetzt')
    m = re.search(r'^def startgh\(request\):\n((?:[ \t].*\n|\n)*?)(?=^\S)', views, re.M)
    if not m or 'HttpResponseForbidden' not in '\n'.join(m.group(1).split('\n')[:5]):
        problems.append('rdgenerator/views.py: startgh() ist nicht mehr deaktiviert')

    for name in ('forms.py', 'api_views.py'):
        path = os.path.join(ROOT, 'rdgenerator', name)
        with open(path, encoding='utf-8') as fh:
            if re.search(r'\bhidecm\b', fh.read()):
                problems.append(f'rdgenerator/{name}: hidecm ist wieder vorhanden')

    for path in workflow_files():
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        if re.search(r'/(cleanzip|save_custom_client|updategh)\b', text) and 'UPLOAD_TOKEN' not in text:
            problems.append(f'{rel(path)}: ruft Upload-Endpunkte ohne UPLOAD_TOKEN auf')

    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='nur pruefen')
    group.add_argument('--fix', action='store_true', help='Workflow-URLs umschreiben, dann pruefen')
    args = parser.parse_args()

    if args.fix:
        for path in fix_workflows():
            print(f'umgeschrieben: {path}')

    problems = check()
    if problems:
        print('Fork-Waechter: Absicherungen verletzt:')
        for p in problems:
            print(f'  - {p}')
        return 1
    print('Fork-Waechter: alle Absicherungen vorhanden.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
