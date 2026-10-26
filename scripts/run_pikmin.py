"""Run reviewed code without publishing private logs or plaintext artifacts."""
import os
from pathlib import Path
import subprocess
import sys
import tarfile

RECIPIENT = 'age1ekkajc8ml47v2yak0s5zvef26r39m78yl329dm9l9qcvfcfvyexqvq9edn'
SOURCE = 'wcc526/nxnative-private-build'
INPUT_TAG = 'cloud-input-pikmin4-102-20261026'


def encrypt_result(age, report, output):
    archive = report.parent / 'result.tar'
    total = 0
    with tarfile.open(archive, 'x') as package:
        files = list(report.rglob('*'))
        if len(files) > 100_000:
            raise RuntimeError('Too many result entries')
        for path in sorted(files):
            if path.is_symlink():
                raise RuntimeError('Result links are not allowed')
            if path.is_file():
                total += path.stat().st_size
                if total > 30 * 1024**3:
                    raise RuntimeError('Result exceeds size limit')
                package.add(path, arcname=path.relative_to(report).as_posix(), recursive=False)
    output.mkdir(mode=0o700, exist_ok=False)
    partial = output / 'result.partial'
    subprocess.run([str(age), '--encrypt', '--recipient', RECIPIENT,
                    '--output', str(partial), str(archive)], check=True, timeout=1800)
    partial.rename(output / 'build-result.tar.age')
    archive.unlink()


def main():
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP'])
    source = Path(os.environ['GITHUB_WORKSPACE']) / 'private-source'
    age = root / 'verified-age' / 'age'
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
            run([sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts',
                 '-p', 'test_cloud*.py', '-v'], 300)
            fetch_env = dict(clean_env, GH_TOKEN=token, GITHUB_REPOSITORY=SOURCE)
            run([sys.executable, 'scripts/cloud_ipa.py', 'fetch', '--slim-only',
                 '--tag', INPUT_TAG, '--out', str(root / 'nx-input')], 1800, fetch_env)
            fetch_env.clear()
            token = ''
            print('Compiling Apple code. Detailed logs are kept private.', flush=True)
            args = [sys.executable, 'scripts/cloud_ipa.py', 'build',
                    '--input', str(root / 'nx-input'), '--work', str(root / 'nx-build'),
                    '--out', str(report / 'Cloud-intermediate.ipa'), '--language', 'zh-Hans']
            if clean_env.get('VALIDATE_MAC') == 'true':
                args.append('--validate-mac')
            run(args, 18000)
            status = 0
        except Exception:
            log.write(b'\nBuild stage failed; inspect the preceding private log.\n')
        (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    try:
        detailed = root / 'nx-build' / 'logs'
        if detailed.exists():
            detailed.rename(report / 'compiler-logs')
        encrypt_result(age, report, root / 'nx-public-output')
    except Exception:
        print('::error::Result encryption failed. No plaintext will be uploaded.')
        return 1
    print('Encrypted result ready.' if status == 0 else
          '::error::Build failed. Download the encrypted result for private diagnostics.')
    return status


if __name__ == '__main__':
    sys.exit(main())
