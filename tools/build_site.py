"""Build public website assets and history-free self-hosted source downloads.

Only tracked source and the explicitly named website inputs are eligible. Local
configuration, imported libraries, Git metadata and maintainer identity policy
are never part of a download. A generated config note preserves that directory
for fixture tests. Building the website requires a Git source checkout.
"""

import argparse
import gzip
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE_FILES = ('index.html', 'site.css', 'site.js', 'security.html')
SCREENS = tuple(name + '-' + theme + '.png'
                for name in ('welcome', 'scan', 'signin', 'connect')
                for theme in ('light', 'dark'))
FONTS = ('ibm-plex-sans-latin-400-normal.woff2',
         'ibm-plex-sans-latin-500-normal.woff2',
         'ibm-plex-sans-latin-600-normal.woff2',
         'ibm-plex-mono-latin-400-normal.woff2')
PACKAGE_CONFIG_NOTE = b'''# Source download configuration

This directory intentionally contains no maintainer commit-email allowlist.
Use `python3 hub.py doctor` to check a self-hosted installation.

The complete developer suite and website builder require the development Git
checkout, including its CI files and maintainer identity policy. Those inputs
are intentionally excluded from this download. Installation does not require
them. Runtime tests that copy this directory can still prepare their fixtures.
'''


def source_files(root):
    if not (root / '.git').exists():
        raise ValueError('Website packaging requires a development Git checkout; '
                         'use python3 hub.py doctor to verify an installed download')
    try:
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root,
                                          stderr=subprocess.PIPE).decode().split('\0')
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError('Website packaging requires a development Git checkout; '
                         'use python3 hub.py doctor to verify an installed download') from error
    website = ['site/' + name for name in SITE_FILES] + ['site/README.md']
    website += ['site/screens/' + name for name in SCREENS]
    names = set(tracked) | set(website) | {'CONTRIBUTING.md', 'tools/build_site.py', 'tests/test_build_site.py'}
    result = {}
    for name in sorted(names):
        if not name or name.startswith(('.github/', 'audit/', '.scratch/', '.hub-store/', '.agent-hub-ops/',
                                        '.worktrees/', 'secrets/', 'remote/public/', 'remote/node_modules/',
                                        'remote/.wrangler/')) or name in {
                                            'config/commit-email-allowlist.txt', 'config.json', 'config.local.json',
                                            'remote/wrangler.local.jsonc', 'remote/.dev.vars', '.env'}:
            continue
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Source path escapes repository')
        path = root / name
        if (any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents)
                or not path.is_file() or not path.resolve().is_relative_to(root.resolve())):
            raise ValueError('Source contains a missing or nonordinary file: ' + name)
        result[name] = path.read_bytes()
    result.setdefault('config/README.md', PACKAGE_CONFIG_NOTE)
    return result


def archives(files, output, source_revision=None):
    downloads = output / 'downloads'
    downloads.mkdir()
    with (downloads / 'agentlocker.tar.gz').open('xb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, content in sorted(files.items()):
                    entry = tarfile.TarInfo('agentlocker/' + name)
                    entry.size = len(content)
                    entry.mode = 0o644
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(content))
    with zipfile.ZipFile(downloads / 'agentlocker.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo('agentlocker/' + name, (1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    manifest = {
        'source_revision': source_revision,
        'source_revision_role': 'base revision; file hashes identify packaged working bytes',
        'file_count': len(files),
        'source_sha256': hashlib.sha256(''.join(name + '\0' + value + '\n'
                                            for name, value in sorted(hashes.items())).encode()).hexdigest(),
        'files': hashes,
        'excluded': ['Git history', 'local configuration and imported content',
                     'maintainer audit records', 'CI configuration', 'maintainer email allowlist'],
        'working_tree_snapshot': True,
    }
    (downloads / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    checksums = ''.join(hashlib.sha256((downloads / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                        for name in ('agentlocker.tar.gz', 'agentlocker.zip'))
    (downloads / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
    return manifest


def security_page(template, contact):
    if not contact:
        return template
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", contact):
        raise ValueError('A valid public security email address is required')
    safe = html.escape(contact, quote=True)
    return template.replace(
        'No private reporting inbox is configured for this build. Do not report credentials or private catalog content in public issues.',
        'Report suspected vulnerabilities privately to <a href="mailto:' + safe + '">' + safe +
        '</a>. Forwarding is configured to the maintainer. Please allow time for a response.')


DEFAULT_REPOSITORY = 'https://github.com/TayHCode/agentlocker'


def repository_page(template, url):
    if not url:
        return template
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', url):
        raise ValueError('Use a repository URL in the form https://github.com/owner/repository')
    return template.replace(DEFAULT_REPOSITORY, url)


def build(output, security_email=None, repo_url=None):
    if output.exists():
        raise ValueError('Output already exists; choose a new directory to preserve the prior build')
    # Read all inputs before creating output; a missing input never yields a
    # partially populated public directory.
    files = source_files(ROOT)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    output.mkdir(parents=True)
    for name in SITE_FILES:
        (output / name).write_bytes(files['site/' + name])
    if repo_url:
        (output / 'index.html').write_text(
            repository_page(files['site/index.html'].decode('utf-8'), repo_url), encoding='utf-8')
    if security_email:
        (output / 'security.html').write_text(
            security_page(files['site/security.html'].decode('utf-8'), security_email), encoding='utf-8')
    (output / 'screens').mkdir()
    for name in SCREENS:
        (output / 'screens' / name).write_bytes(files['site/screens/' + name])
    (output / 'fonts').mkdir()
    for name in FONTS:
        shutil.copyfile(ROOT / 'ui/fonts' / name, output / 'fonts' / name)
    for name in ('LICENSES.md', 'ibm-plex-OFL.txt', 'geist-mono-OFL.txt', 'inter-OFL.txt'):
        shutil.copyfile(ROOT / 'ui/fonts' / name, output / 'fonts' / name)
    shutil.copyfile(ROOT / 'ui/favicon.svg', output / 'favicon.svg')
    (output / '_headers').write_text(
        '/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n'
        "  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; font-src 'self'; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'\n"
        '/downloads/*.zip\n  Content-Disposition: attachment\n'
        '/downloads/*.gz\n  Content-Disposition: attachment\n', encoding='utf-8')
    return archives(files, output, revision)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--security-email', help='Verified public reporting alias; never use the private forwarding destination')
    parser.add_argument('--repo-url', help='Prepared source repository; visibility stays under the maintainer’s control')
    args = parser.parse_args()
    try:
        result = build(args.out.resolve(), args.security_email, args.repo_url)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({key: result[key] for key in ('source_revision', 'file_count', 'source_sha256')}))
