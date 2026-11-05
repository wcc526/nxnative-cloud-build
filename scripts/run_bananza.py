"""Compile pinned NX2Apple inputs; publish only age-encrypted results."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import traceback
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PRIVATE = 'wcc526/nxnative-private-build'
TAG = 'bananza-320-20261105'
MANIFEST_SHA = '757a0aeffb3eac9b64884e92aa83ca872fc918579527c778c58f71fc0e3c00c6'
RECIPIENT = 'age102hu7yuyrkk7f2v28jy3lme9ekj25vwq25p0mhgeesmn75z4kqkqk4udqk'
TITLE = '040074A01BF12000'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def fetch(ticket_json, destination):
    opener = urllib.request.build_opener(NoRedirect())
    # These are asset-specific, expiring URLs minted locally. The runner receives
    # neither a general GitHub token nor console/signing credentials.
    assets=json.loads(ticket_json)
    assert set(assets) == {'build-manifest.json', 'engine-source.zip', 'compile-input.zip'}

    def download(name, maximum):
        asset = assets[name]
        assert 0 < asset['size'] <= maximum
        target=asset['url'];parsed=urllib.parse.urlsplit(target)
        assert parsed.scheme=='https' and parsed.hostname=='release-assets.githubusercontent.com'
        assert not parsed.username and not parsed.password and parsed.port in (None,443)
        response=opener.open(urllib.request.Request(target),timeout=180)
        path = destination / name
        size = 0
        with response, path.open('xb') as f:
            while chunk := response.read(4 * 1024**2):
                size += len(chunk)
                assert size <= maximum and size <= asset['size']
                f.write(chunk)
        assert size == asset['size']
        return path

    manifest_path = download('build-manifest.json', 16 * 1024**2)
    assert sha(manifest_path) == MANIFEST_SHA
    manifest = json.loads(manifest_path.read_text())
    assert manifest['schema'] == 1 and manifest['game'] == TITLE and manifest['package'] == 'v458752'
    for name in ('engine-source.zip', 'compile-input.zip'):
        expected = manifest['assets'][name]
        assert assets[name]['size'] == expected['bytes']
        path = download(name, 2 * 1024**3)
        assert sha(path) == expected['sha256']
    return manifest


def unpack(archive, root, limit, input_files=None):
    root.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        assert len(infos) <= 150000 and sum(i.file_size for i in infos) <= limit
        names = set()
        for info in infos:
            name = info.filename
            p = PurePosixPath(name)
            assert name not in names and not p.is_absolute() and '..' not in p.parts
            assert chr(92) not in name and ':' not in name and len(p.parts) < 40
            names.add(name)
            mode = info.external_attr >> 16
            assert not stat.S_ISLNK(mode)
            target = root.joinpath(*p.parts)
            assert target.resolve().is_relative_to(root.resolve())
            if info.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if input_files is not None:
                assert name in input_files and info.file_size == input_files[name]['bytes']
            with z.open(info) as src, target.open('xb') as dst: shutil.copyfileobj(src, dst, 4 * 1024**2)
            target.chmod(0o700 if mode & 0o111 else 0o600)
            if input_files is not None: assert sha(target) == input_files[name]['sha256']
        if input_files is not None: assert names == set(input_files)


def compile_game(source, inputs, work, report, manifest, log, clean_env):
    sys.path[:0] = [str(source / p) for p in ('scripts', 'dm/games', 'dm/tools')]
    import build_ipa_from_nsp as b
    import game
    # User chose the project's documented alternative-Metal path on 2026-11-05.
    b.ALLOW_OTHER_METAL = True
    profile=source/'dm/games'/TITLE
    rows=lambda p:[s for s in p.read_text().splitlines() if s.strip() and not s.startswith('#')]
    assert rows(profile/'game.tsv')==rows(inputs/'Profile/game.tsv')
    # Preparation already proved that only DLC-owned shaders differ. Recheck
    # that proof against the pinned base profile before using the new tables.
    g=game.load(TITLE)
    assert b.dlc_only_shader_change(g,inputs/'Profile')
    for name in ('shader_map.tsv','shader_facts.tsv','shader_pairs.tsv','pso_pairs.tsv'):
        shutil.copyfile(inputs/'Profile'/name,profile/name)
    g = game.load(TITLE)
    assert not b.build_mismatch(inputs, 'v458752', g)
    metadata = json.loads((inputs / 'metadata.json').read_text())
    assert metadata['version'] == '3.2.0'
    (work / 'tmp').mkdir(parents=True)
    (inputs / 'RomFS').mkdir()
    env = dict(clean_env, DM_GAME=TITLE, DM_ROOT=str(work), WORK=str(work / 'tmp'),
               J=str(min(os.cpu_count() or 3, 6)), PYTHONUTF8='1', DM_ALLOW_OTHER_METAL='1')
    compiler = subprocess.check_output(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], text=True, env=env)
    (report / 'compiler.txt').write_text(compiler)
    xlate = source / 'dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate'
    b.check_prereqs(env, xlate)
    b.check_metal(sys.executable, source, env)
    patched, logs = work / 'patched', work / 'logs'
    b.rewrite(sys.executable, source, inputs / 'ExeFS', patched, env, logs, g)
    sh = work / 'shader'
    sh.mkdir()
    shutil.move(str(inputs / 'Shader'), sh / 'pkg')
    wanted = {s.split('\t')[0] for s in g.table('shader_map.tsv').read_text().splitlines()
              if s.split('\t')[0].endswith('.bin')}
    assert {p.name for p in (sh / 'pkg').iterdir()} == wanted
    ios = b.build_shaders(sys.executable, source, sh, xlate, env, logs, g,
                          pkg_key=b.package_key(sh / 'pkg'))
    output = report / 'Bananza-intermediate.ipa'
    env.update(GAME=TITLE, W=str(work / 'ipa'), PATCHED=str(patched), ROMFS=str(inputs / 'RomFS'),
               MSL_IOS=str(ios), GFXAPI='nvn', FONT_PY=sys.executable, DM_LANG='zh-Hans',
               DM_DISPLAY_NAME='Donkey Kong Bananza', DM_APP_VERSION='3.2.0',
               DM_BUNDLE_ID='org.nx2apple.040074a01bf12000', DM_TEAM_ID='0000000000',
               DM_COPY_ROMFS='NO', AOC='')
    if (inputs / 'icon.jpg').exists(): env['DM_ICON'] = str(inputs / 'icon.jpg')
    b.run(['bash', source / 'dm/ios/tools/build_ipa.sh', output], logs / 'build_ipa.log', env=env)
    with zipfile.ZipFile(output, 'a') as z:
        z.writestr('Payload/DmIOS.app/dm/build-provenance.json', json.dumps(manifest['provenance']))
    with zipfile.ZipFile(output) as z: assert z.testzip() is None
    (report / 'ipa-sha256.txt').write_text(sha(output) + '\n')


def main():
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP']) / 'bananza-build'
    root.mkdir(mode=0o700)
    report = root / 'report'; report.mkdir()
    incoming = root / 'incoming'; incoming.mkdir()
    tickets = os.environ.pop('BANANZA_INPUT_URLS', '')
    env = {k:v for k,v in os.environ.items() if k not in
           ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_STEP_DEBUG', 'ACTIONS_RUNNER_DEBUG')}
    status = 1
    stop=threading.Event()
    public_output=sys.stdout
    def progress():
        started=time.monotonic()
        while not stop.wait(60):
            libraries=sum(1 for _ in (root/'work/shader/ios').glob('b-*/*_b.metallib'))
            print('Build progress: %d iOS base libraries; %d minutes elapsed.' %
                  (libraries,(time.monotonic()-started)//60),file=public_output,flush=True)
    threading.Thread(target=progress,daemon=True).start()
    with (report / 'build.log').open('w') as log:
        oldout, olderr = sys.stdout, sys.stderr
        try:
            print('Downloading verified private compilation inputs.', flush=True)
            manifest = fetch(tickets, incoming)
            tickets = ''
            unpack(incoming / 'engine-source.zip', root / 'engine', 2 * 1024**3)
            unpack(incoming / 'compile-input.zip', root / 'input', 4 * 1024**3, manifest['input_files'])
            print('Compiling Bananza with the pinned NX2Apple engine; detailed logs stay encrypted.', flush=True)
            sys.stdout = sys.stderr = log
            compile_game(root / 'engine', root / 'input', root / 'work', report, manifest, log, env)
            status = 0
        except Exception:
            traceback.print_exc(file=log)
        finally: sys.stdout, sys.stderr = oldout, olderr
    stop.set()
    (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    if (root / 'work/logs').exists(): shutil.copytree(root / 'work/logs', report / 'compiler-logs')
    archive = root / 'result.tar'
    with tarfile.open(archive, 'x') as t:
        for p in sorted(report.rglob('*')):
            assert not p.is_symlink()
            if p.is_file(): t.add(p, arcname=p.relative_to(report).as_posix(), recursive=False)
    output = Path(os.environ['RUNNER_TEMP']) / 'nx-public-output'; output.mkdir(mode=0o700)
    age = Path(os.environ['RUNNER_TEMP']) / 'verified-age/age'
    subprocess.run([str(age), '--encrypt', '--recipient', RECIPIENT, '--output',
                    str(output / 'build-result.tar.age'), str(archive)], check=True, timeout=1800, env=env)
    print('Encrypted IPA ready.' if status == 0 else '::error::Build failed; encrypted diagnostics are available.')
    return status


if __name__ == '__main__': sys.exit(main())
