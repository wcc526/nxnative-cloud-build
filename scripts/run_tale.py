"""Rebuild only the host; reuse the verified Tale of Immortal Metal libraries."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import zipfile

import run_build

GAME = '0100FB701C48C000'
PRIVATE = 'wcc526/nxnative-private-build'
INPUT_TAG = 'cloud-input-tale-of-immortal-109-r2'
RUNTIME_TAG = 'cloud-runtime-tale-tap-haptics-20261028'
RUNTIME_SHA = 'ca5069e79fb054968329e249ecb42c8eeeafb324db185e4b14eab1050cc68b5f'


def check(ok, message):
    if not ok:
        raise ValueError(message)


def libraries(template, output, chain):
    check(hashlib.file_digest(template.open('rb'), 'sha256').hexdigest() == RUNTIME_SHA,
          'Runtime template checksum mismatch')
    prefix = 'Payload/DmIOS.app/msl/'
    seen, total = set(), 0
    with zipfile.ZipFile(template) as archive:
        entries = [e for e in archive.infolist() if e.filename.startswith(prefix)]
        check(0 < len(entries) <= 25000, 'Invalid library count')
        for e in entries:
            name = e.filename[len(prefix):]
            if not name:
                continue
            path = PurePosixPath(name)
            check(not path.is_absolute() and '..' not in path.parts and ':' not in name
                  and '\\' not in name and len(path.parts) <= 4
                  and path.parts[0] == 'b-' + chain, 'Unsafe library path')
            check(name not in seen and not stat.S_ISLNK(e.external_attr >> 16), 'Invalid library entry')
            seen.add(name)
            total += e.file_size
            check(total <= 1024**3 and e.file_size <= 64 * 1024**2, 'Library data exceeds limits')
            dest = output.joinpath(*path.parts)
            check(dest.resolve().is_relative_to(output.resolve()), 'Library escaped output')
            if e.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(e) as inp, dest.open('xb') as out:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
        check(any(name.endswith('.metallib') for name in seen), 'Metal libraries missing')
        marker = json.loads(archive.read('Payload/DmIOS.app/dm/cloud-input.json'))
        exclusions = archive.read('Payload/DmIOS.app/dm/experimental_shader_exclusions.json')
    check(marker['game'] == GAME and marker['version'] == 'v1' and marker['chain'] == chain,
          'Runtime template identity mismatch')
    return marker, exclusions


def main():
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP'])
    source = Path(os.environ['GITHUB_WORKSPACE']) / 'private-source'
    report = root / 'nx-private-report'
    report.mkdir(mode=0o700)
    token = os.environ.pop('PRIVATE_BUILD_READ_TOKEN', '')
    clean = dict(os.environ)
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_STEP_DEBUG', 'ACTIONS_RUNNER_DEBUG'):
        clean.pop(key, None)
    status = 1
    with (report / 'build.log').open('wb') as log:
        def run(args, timeout, env=None, output=log):
            return subprocess.run(args, cwd=source, env=env or clean, stdout=output,
                                  stderr=subprocess.STDOUT, check=True, timeout=timeout)
        try:
            check(bool(token), 'Missing private input credential')
            fetch = dict(clean, GH_TOKEN=token, GITHUB_REPOSITORY=PRIVATE)
            print('Fetching verified private code and existing Metal libraries.', flush=True)
            run([sys.executable, 'scripts/cloud_ipa.py', 'fetch', '--local-only', '--tag',
                 INPUT_TAG, '--out', str(root / 'nx-input')], 1800, fetch)
            metadata = json.loads(subprocess.check_output(
                ['gh', 'api', f'repos/{PRIVATE}/releases/tags/{RUNTIME_TAG}'], env=fetch, timeout=60))
            assets = metadata['assets']
            check(len(assets) == 1 and assets[0]['name'] == 'Runtime.ipa'
                  and assets[0]['size'] == 210978539 and isinstance(assets[0]['id'], int),
                  'Unexpected runtime asset')
            template = root / 'Runtime.ipa'
            with template.open('xb') as out:
                run(['gh', 'api', '-H', 'Accept: application/octet-stream',
                     f'repos/{PRIVATE}/releases/assets/{assets[0]["id"]}'], 1800, fetch, out)
            fetch.clear(); token = ''
            sys.path.insert(0, str(source / 'scripts'))
            import cloud_ipa as cloud
            work = root / 'nx-build'
            work.mkdir()
            inp = work / 'input'
            manifest = cloud.unpack(root / 'nx-input', inp)
            check(manifest['game'] == GAME and manifest['version'] == 'v1', 'Wrong prepared game')
            game = cloud.profile(GAME)
            cloud.version_check(inp, game, 'v1', require_romfs=False)
            chain = re.search(r'DMSH_CHAIN "([0-9a-f]{12})"', game.table('dmsh_chain.h').read_text()).group(1)
            old_manifest, exclusions = libraries(template, work / 'msl', chain)
            for key in ('module_sha256', 'resource_sha256', 'archive_sha256', 'chain'):
                check(old_manifest[key] == manifest[key], 'Template differs from prepared input')
            for name in ('c1_full.tsv', 'cb_extent.tsv'):
                shutil.copyfile(inp / 'Profile' / name, game.dir / name)
            romfs = inp / 'RomFS'
            romfs.mkdir(exist_ok=True)
            env_file = work / 'env.txt'
            env_file.write_text('DM_OPMODE=0\n')
            env = dict(clean, GAME=GAME, W=str(work / 'ipa'), PATCHED=str(inp / 'Patched'),
                       ROMFS=str(romfs), MSL_IOS=str(work / 'msl'), GFXAPI='nvn',
                       FONT_PY=sys.executable, DM_LANG='zh-Hans',
                       DM_DISPLAY_NAME='Tale of Immortal Tap', DM_APP_VERSION='1.0.9',
                       DM_BUNDLE_ID='org.nxnative.' + GAME.lower(), DM_ICON=str(inp / 'icon.jpg'),
                       AOC='', DM_TEAM_ID='0000000000', DM_OMIT_ROMFS='1', DM_ENV_FILE=str(env_file))
            print('Testing haptics and compiling the updated iOS host.', flush=True)
            run(['make', '-C', 'dm/host/tests', 'haptics'], 300, env)
            output = report / 'Cloud-intermediate.ipa'
            run(['bash', 'dm/ios/tools/build_ipa.sh', str(output)], 3600, env)
            with zipfile.ZipFile(output, 'a') as archive:
                archive.writestr('Payload/DmIOS.app/dm/cloud-input.json', json.dumps(manifest))
                archive.writestr('Payload/DmIOS.app/dm/experimental_shader_exclusions.json', exclusions)
            with zipfile.ZipFile(output) as archive:
                check(archive.testzip() is None, 'Intermediate CRC failure')
            output.with_suffix('.ipa.sha256').write_text(cloud.sha(output) + '  ' + output.name + '\n')
            status = 0
        except Exception:
            import traceback
            log.write(traceback.format_exc().encode('utf-8'))
        (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    run_build.encrypt_result(root / 'verified-age/age', report, root / 'nx-public-output')
    print('Encrypted result ready.' if status == 0 else '::error::Build failed; private diagnostics encrypted.')
    return status


if __name__ == '__main__':
    sys.exit(main())
