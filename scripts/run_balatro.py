"""Build Balatro diagnostics with private logs and encrypted output only."""
import os
from pathlib import Path
import subprocess
import sys

from run_build import encrypt_result


def main():
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP'])
    source = Path(os.environ['GITHUB_WORKSPACE']) / 'private-source'
    report = root / 'nx-private-report'
    report.mkdir(mode=0o700)
    token = os.environ.pop('PRIVATE_BUILD_READ_TOKEN', '')
    clean_env = dict(os.environ)
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_STEP_DEBUG', 'ACTIONS_RUNNER_DEBUG'):
        clean_env.pop(key, None)
    status = 1
    with (report / 'build.log').open('wb') as log:
        def run(args, timeout, env=None):
            subprocess.run(args, cwd=source, env=env or clean_env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout)
        try:
            if not token:
                raise RuntimeError('Missing read-only input token')
            print('Checking private build inputs.', flush=True)
            run([sys.executable, 'scripts/test_gl_texture_storage.py'], 120)
            fetch_env = dict(clean_env, GH_TOKEN=token, GITHUB_REPOSITORY='wcc526/nxnative-private-build')
            run([sys.executable, 'scripts/cloud_ipa.py', 'fetch',
                 '--tag', 'cloud-input-balatro-113-probe', '--out', str(root / 'nx-input')], 900, fetch_env)
            fetch_env.clear()
            token = ''
            print('Compiling Apple code. Detailed logs are kept private.', flush=True)
            run([sys.executable, 'scripts/balatro_probe.py', '--input', str(root / 'nx-input'),
                 '--work', str(root / 'nx-build'), '--device'], 1800)
            status = 0
        except Exception:
            log.write(b'\nBuild stage failed; inspect the preceding private log.\n')
        (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    try:
        for name in ('diagnostics', 'output'):
            directory = root / 'nx-build' / name
            if directory.exists():
                directory.rename(report / name)
        encrypt_result(root / 'verified-age' / 'age', report, root / 'nx-public-output')
    except Exception:
        print('::error::Result encryption failed. No plaintext will be uploaded.')
        return 1
    print('Encrypted result ready.' if status == 0 else
          '::error::Build failed. Download the encrypted result for private diagnostics.')
    return status


if __name__ == '__main__':
    sys.exit(main())
