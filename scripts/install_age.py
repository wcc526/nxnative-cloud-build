"""Install the pinned, hash-verified age executable from its official release."""
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import tarfile

VERSION = '1.3.2'
HASHES = {
    'arm64': 'e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5',
    'amd64': '1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539',
}


def main():
    if platform.system() != 'Darwin':
        raise RuntimeError('This installer is for macOS runners only')
    arch = {'arm64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    root = Path(os.environ['RUNNER_TEMP']) / 'verified-age'
    root.mkdir(mode=0o700)
    archive = root / 'age.tar.gz'
    url = f'https://github.com/FiloSottile/age/releases/download/v{VERSION}/age-v{VERSION}-darwin-{arch}.tar.gz'
    subprocess.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                    '--max-time', '180', '--max-filesize', '30000000', '--output', str(archive), url],
                   check=True, timeout=190)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != HASHES[arch]:
        raise RuntimeError('age release checksum mismatch')
    # Copy only the bounded regular executable, never extract arbitrary paths.
    with tarfile.open(archive, 'r:gz') as package:
        member = package.getmember('age/age')
        if not member.isfile() or not 0 < member.size < 30_000_000:
            raise RuntimeError('Unexpected age executable')
        with package.extractfile(member) as source, (root / 'age').open('xb') as target:
            target.write(source.read(member.size))
    (root / 'age').chmod(0o700)
    subprocess.run([str(root / 'age'), '--version'], check=True, timeout=10)


if __name__ == '__main__':
    main()
