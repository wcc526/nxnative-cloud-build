"""Compile the reviewed GL startup fix; publish encrypted results only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from run_build import encrypt_result

SOURCE = 'wcc526/nxnative-private-build'
INPUT_TAG = 'cloud-input-slay-spire-231-20261023'


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
            print('Checking private source and inputs.', flush=True)
            for pattern in ('test_slay_spire.py', 'test_gl_reflection.py'):
                run([sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts', '-p', pattern, '-v'], 300)
            fetch_env = dict(clean_env, GH_TOKEN=token, GITHUB_REPOSITORY=SOURCE)
            try:
                run([sys.executable, 'scripts/cloud_ipa.py', 'fetch', '--slim-only',
                     '--tag', INPUT_TAG, '--out', str(root / 'nx-input')], 600, fetch_env)
            finally:
                fetch_env.clear()
                token = ''
            print('Compiling Apple code. Detailed logs remain private.', flush=True)
            run([sys.executable, 'scripts/slay_spire_ipa.py', 'build', '--input', str(root / 'nx-input'),
                 '--work', str(root / 'nx-build')], 1800)
            for name in ('SlayTheSpire-2.3.1-intermediate.ipa', 'SHA256SUMS.txt'):
                path = root / 'nx-build/output' / name
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError('Missing regular build result')
                shutil.copyfile(path, report / name)
            status = 0
        except Exception:
            log.write(b'\nBuild stage failed; inspect the preceding private log.\n')
        (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    try:
        detailed = root / 'nx-build/logs'
        if detailed.exists():
            detailed.rename(report / 'compiler-logs')
        encrypt_result(root / 'verified-age/age', report, root / 'nx-public-output')
    except Exception:
        print('::error::Result encryption failed. No plaintext will be uploaded.')
        return 1
    print('Encrypted result ready.' if status == 0 else
          '::error::Build failed. Download the encrypted diagnostics.')
    return status


if __name__ == '__main__':
    sys.exit(main())
