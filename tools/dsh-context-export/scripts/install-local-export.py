"""Install or restore the local client-only export extension, with byte-for-byte backups."""
# Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.context-export-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


parser = argparse.ArgumentParser()
parser.add_argument('--target', type=Path, default=Path.home() / '.dsh/profiles/desktop/node_modules/dsh-context')
parser.add_argument('--restore', type=Path)
args = parser.parse_args()
target = args.target.resolve()
files = ['lib/client.js', 'package.json']

if args.restore:
    backup = args.restore.resolve()
    saved = json.loads((backup / 'installation.json').read_text())
    if Path(saved['target']) != target:
        raise SystemExit('Restore target does not match the saved installation.')
    for name in files:
        if digest((target / name).read_bytes()) != saved['patched_sha256'][name]:
            raise SystemExit('Installed files have changed since this patch; refusing to overwrite: ' + name)
        if digest((backup / name).read_bytes()) != saved['original_sha256'][name]:
            raise SystemExit('Backup verification failed: ' + name)
    for name in files:
        atomic_write(target / name, (backup / name).read_bytes())
    print(json.dumps({'restored': True, 'target': str(target), 'backup': str(backup)}, ensure_ascii=False, indent=2))
else:
    original = {name: (target / name).read_bytes() for name in files}
    package = json.loads(original['package.json'])
    if package.get('name') != 'dsh-context' or package.get('version') != '0.72.0':
        raise SystemExit('This patch is for the installed dsh-context 0.72.0 only.')
    if package.get('x-local-context-export'):
        raise SystemExit('Local export patch already installed; restore it before installing another build.')
    built = (ROOT / 'lib/client.js').read_bytes()
    if b'dsh-context.selected-context' not in built or b'context-export-1' not in built:
        raise SystemExit('Build the export-enabled client before installing.')
    baseline = json.loads((ROOT / 'UPSTREAM.json').read_text())['commit']
    package['x-local-context-export'] = {'extension': 'context-export-1', 'upstreamCommit': baseline}
    modules = package['dsh']['client']['inject']
    if '@deepseek-ai/dsh-session-log-export' not in modules:
        modules.append('@deepseek-ai/dsh-session-log-export')
    patched = {'lib/client.js': built, 'package.json': (json.dumps(package, ensure_ascii=False, indent=2) + '\n').encode()}
    now = datetime.datetime.now().astimezone()
    backup = ROOT / 'local-backups' / now.strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, exist_ok=False)
    for name, data in original.items():
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    host_sha = digest((target / 'lib/index.js').read_bytes())
    saved = {'target': str(target), 'time': now.isoformat(), 'upstreamCommit': baseline,
             'original_sha256': {name: digest(data) for name, data in original.items()},
             'patched_sha256': {name: digest(data) for name, data in patched.items()},
             'unchanged_host_sha256': host_sha, 'scope': 'client bundle and client module declaration only'}
    (backup / 'installation.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    try:
        for name, data in patched.items():
            atomic_write(target / name, data)
        assert digest((target / 'lib/index.js').read_bytes()) == host_sha
        assert all((target / name).read_bytes() == data for name, data in patched.items())
    except BaseException:
        for name, data in original.items():
            atomic_write(target / name, data)
        raise
    print(json.dumps({'installed': True, 'target': str(target), 'backup': str(backup),
                      'host_unchanged': True, 'patched_sha256': saved['patched_sha256']}, ensure_ascii=False, indent=2))
