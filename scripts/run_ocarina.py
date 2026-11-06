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
TAG = 'ocarina-of-time-131072-20261106'
MANIFEST_SHA = '045447f98020569381af587974c41efa13982fe034d75adcfb99b020ccf43be7'
RECIPIENT = 'age12euq6uyzfgrv0x7mhzk8pq4exy8yz36zau2za6gs0efa03f0x4zsrw5g84'
TITLE = '0400486024DEC000'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def fetch(ticket_json, destination):
    opener = urllib.request.build_opener(NoRedirect())
    # These are asset-specific, expiring URLs minted locally. The runner receives
    # neither a general GitHub token nor console/signing credentials.
    assets=json.loads(ticket_json)
    assert {'build-manifest.json','engine-source.zip','compile-input.zip'}.issubset(assets)

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
    assert manifest['schema'] == 1 and manifest['game'] == TITLE and manifest['package'] == 'v131072'
    assert set(assets)=={'build-manifest.json',*manifest['assets']}
    for name,expected in manifest['assets'].items():
        assert PurePosixPath(name).name==name and chr(92) not in name and ':' not in name
        assert assets[name]['size'] == expected['bytes']
        path = download(name, 2 * 1024**3)
        assert sha(path) == expected['sha256']
    if manifest.get('host_cache'):
        cache=manifest['host_cache']
        with (destination/'host-inputs.zip').open('xb') as out:
            for name in cache['parts']:
                assert name in manifest['assets']
                with (destination/name).open('rb') as src:shutil.copyfileobj(src,out,4*1024**2)
        assert (destination/'host-inputs.zip').stat().st_size==cache['bytes']
        assert sha(destination/'host-inputs.zip')==cache['sha256']
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


def compile_game(source, inputs, work, report, manifest, log, clean_env, cache=None, public_output=None):
    sys.path[:0] = [str(source / p) for p in ('scripts', 'dm/games', 'dm/tools')]
    import build_ipa_from_nsp as b
    import game
    # The xcode-27 runner supplies the SDK used by the pinned engine. Build the
    # original source and keep the project's Metal compiler checks enabled.
    build_provenance=dict(manifest['provenance'],host_sdk_compatibility={
        'description':'Compiled with native Xcode 27.2 SDK declarations'})
    b.ALLOW_OTHER_METAL = False
    profile=source/'dm/games'/TITLE
    rows=lambda p:[s for s in p.read_text().splitlines() if s.strip() and not s.startswith('#')]
    assert sha(profile/'game.tsv')==manifest['provenance']['base_profile_sha256']
    proof = manifest['provenance']
    changes = proof['profile_changes']
    assert set(changes) == {'ipa', 'build', 'package', 'shader_versions', 'romfs_marker', 'mem_alias'}
    stable = lambda path: [row for row in rows(path) if row.split('\t')[0] not in changes]
    assert stable(profile/'game.tsv') == stable(inputs/'Profile/game.tsv')
    for key, (old, new) in changes.items():
        assert key+'\t'+old in rows(profile/'game.tsv')
        assert key+'\t'+new in rows(inputs/'Profile/game.tsv')
    for name, digest in proof['shader_tables'].items():
        assert sha(inputs/'Profile'/name) == digest
        if name in ('shader_pairs.tsv', 'pso_pairs.tsv'):
            assert rows(profile/name) == rows(inputs/'Profile'/name)
    review = proof['shader_review']
    expected_keys = {'d17eefaff4f0bc23', '1fdc85178b60f6ce', '92918e9b7e2f89a4'}
    assert set(review['added_shader_keys']) == expected_keys
    def shader_map(path):
        records = [row.split('\t') for row in rows(path)[1:]]
        mapping = {key: (file, index, stage, source) for file, index, stage, key, source in records}
        assert len(mapping) == len(records)
        return mapping
    old = shader_map(profile/'shader_map.tsv')
    new = shader_map(inputs/'Profile/shader_map.tsv')
    assert len(old) == 42851 and len(new) == 42854 and old.keys() <= new.keys()
    assert new.keys() - old.keys() == expected_keys
    assert all((item[0], *item[2:]) == (new[key][0], *new[key][2:]) for key, item in old.items())
    old_facts = set(rows(profile/'shader_facts.tsv'))
    new_facts = set(rows(inputs/'Profile/shader_facts.tsv'))
    assert old_facts <= new_facts and {row.split('\t')[0] for row in new_facts - old_facts} == expected_keys
    for path in (inputs/'Profile').iterdir():
        assert path.is_file() and path.suffix == '.tsv'
        shutil.copyfile(path,profile/path.name)
    g = game.load(TITLE)
    assert not b.build_mismatch(inputs, 'v131072', g)
    reserve, alias, stack = [int(g.get(key), 0) for key in ('mem_reserve', 'mem_alias', 'mem_stack')]
    heap = reserve - alias - stack - 0x10000000
    assert heap >= 0x18ce00000 and int(g.get('app_pool'), 0) == 0x240000000
    build_provenance['memory_layout'] = dict(reserve=reserve, alias=alias, stack=stack, heap=heap)
    metadata = json.loads((inputs / 'metadata.json').read_text())
    assert all(metadata[key] == proof[key] for key in metadata)
    bundle_version = '1.0' if metadata['metadata_source'] == 'NUP numeric update ID' else metadata['version']
    build_provenance['ios_bundle_version'] = bundle_version
    (work / 'tmp').mkdir(parents=True)
    (inputs / 'RomFS').mkdir(exist_ok=True)
    env = dict(clean_env, DM_GAME=TITLE, DM_ROOT=str(work), WORK=str(work / 'tmp'),
               J=str(min(os.cpu_count() or 3, 6)), PYTHONUTF8='1')
    env.pop('DM_ALLOW_OTHER_METAL', None)
    compiler = subprocess.check_output(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], text=True, env=env)
    (report / 'compiler.txt').write_text(compiler)
    build_provenance['metal_compiler']=compiler.splitlines()[0]
    build_provenance['xcode']=subprocess.check_output(['xcodebuild','-version'],text=True,env=env).strip()
    (report/'build-provenance.json').write_text(json.dumps(build_provenance,indent=2))
    xlate = source / 'dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate'
    b.check_prereqs(env, xlate)
    b.check_metal(sys.executable, source, env)
    patched, logs = work / 'patched', work / 'logs'
    b.run([sys.executable, source/'dm/gfx/tools/tool_lock.py', '--check-tools'],
          logs/'tool_lock.log', env=env)
    assert b.chain_of(source) == proof['translator_chain'] == 'cd1da67c132d'
    b.run([sys.executable,source/'dm/tools/romfs.py',inputs/'RomFS',g.get('romfs_marker')],
          logs/'romfs_preflight.log',env=env)
    print('Checking guest memory services.', file=public_output, flush=True)
    test_log = logs / 'guest_memory_tests.log'
    b.run(['make', '-C', source / 'dm/host/tests', 'memory', 'memory-file'], test_log, env=env)
    test_output = test_log.read_text()
    assert 'host page=16384, guest files 6)' in test_output
    assert 'guest files 1)' in test_output and 'guest files 0)' in test_output
    build_provenance['memory_svc_tests_passed'] = True
    if cache is not None:
        assert json.loads((cache/'provenance.json').read_text())==manifest['provenance']
        # These are game shader IR libraries, loaded at runtime, not object files
        # linked into the host. Their verified chain/profile stamp remains the
        # packer's authority. Preserve the actual shader compiler independently
        # from the compiler used for the host and its built-in shaders.
        build_provenance['game_shader_compiler']=manifest['host_cache']['compiler'].splitlines()[0]
        build_provenance['game_shader_cache']={k:manifest['host_cache'][k] for k in ('sha256','source_run')}
        (report/'build-provenance.json').write_text(json.dumps(build_provenance,indent=2))
        patched=cache/'Patched';ios=cache/'IOS'
        assert len(list(ios.glob('b-*.img'))) == 1
        for name in ('c1_full.tsv','cb_extent.tsv'):
            shutil.copyfile(cache/'Profile'/name,g.table(name))
    else:
        build_provenance['game_shader_compiler']=compiler.splitlines()[0]
        (report/'build-provenance.json').write_text(json.dumps(build_provenance,indent=2))
        b.rewrite(sys.executable, source, inputs / 'ExeFS', patched, env, logs, g)
        cuts = (patched/'sdk.cuts.tsv').read_text()
        assert '0x16ba48\t08fca752\t087ca052\t' in cuts
        assert '0x16bad4\t09fca752\t097ca052\t' in cuts
        # Exercise initialization on an arm64 Mac without graphics or shaders.
        # Only the real marker is present: this cannot validate game assets or
        # gameplay, but records early SDK allocator failures before shader work.
        print('Running the headless startup probe.',file=public_output,flush=True)
        host=work/'tools/dmhost'
        b.build_mac_host(source,host,env,logs,g)
        rd=work/'startup-probe'
        for name in ('save','temp','msl'):(rd/name).mkdir(parents=True,exist_ok=True)
        probe_log=logs/'startup_memory.log'
        henv=dict(env,DM_ROMFS=str(inputs/'RomFS'),DM_SAVE=str(rd/'save'),
                  DM_TEMP=str(rd/'temp'),DM_MSL_DIR=str(rd/'msl'),DM_OPMODE='0',
                  DM_NVN_SHMAP=str(g.table('shader_map.tsv')),DM_REGIONS=str(rd/'regions.tsv'))
        henv.pop('DM_WINDOW',None);henv.pop('DM_AUDIO',None)
        first_main=[]
        def probe_done():
            if probe_log.exists() and '>>> nnMain' in probe_log.read_text(errors='replace'):
                if not first_main:first_main.append(time.monotonic())
            return bool(first_main and time.monotonic()-first_main[0]>2)
        rc=b.boot_mac(host,patched,henv,rd,probe_log,35,probe_done)
        probe_text=probe_log.read_text(errors='replace')
        probe=dict(reached_nnmain='>>> nnMain' in probe_text,return_code=rc,
                   full_assets_available=False,device_runtime_verified=False)
        (report/'startup-memory-probe.json').write_text(json.dumps(probe,indent=2))
        build_provenance['startup_memory_probe']=probe
        (report/'build-provenance.json').write_text(json.dumps(build_provenance,indent=2))
        if (rd/'regions.tsv').exists():shutil.copyfile(rd/'regions.tsv',report/'startup-regions.tsv')
        if any(s in probe_text for s in ('reserve guest AS','SetHeapSize OOM','duplicate hook')):
            raise b.BuildError('The compact memory layout failed the early allocation probe; inspect encrypted startup_memory.log')
        sh = work / 'shader'
        sh.mkdir()
        shutil.move(str(inputs / 'Shader'), sh / 'pkg')
        wanted = {s.split('\t')[0] for s in g.table('shader_map.tsv').read_text().splitlines()
                  if s.split('\t')[0].endswith('.bin')}
        assert {p.name for p in (sh / 'pkg').iterdir()} == wanted
        print('Compiling game shaders with Apple Metal.', file=public_output, flush=True)
        ios = b.build_shaders(sys.executable, source, sh, xlate, env, logs, g,
                              pkg_key=b.package_key(sh / 'pkg'), pull=False)
    # Preserve completed Apple shader libraries and rewritten modules before
    # entering packaging, so a packaging-only repair can reuse this expensive
    # work. This archive is included only in the age-encrypted result.
    if cache is None:
        with zipfile.ZipFile(report/'host-inputs.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as saved:
            for label,directory in (('Patched',patched),('IOS',ios)):
                for path in sorted(directory.rglob('*')):
                    assert not path.is_symlink()
                    if path.is_file() and path.suffix!='.metal':
                        saved.write(path,label+'/'+path.relative_to(directory).as_posix())
            for name in ('c1_full.tsv','cb_extent.tsv'):
                assert g.table(name).is_file()
                saved.write(g.table(name),'Profile/'+name)
            saved.writestr('provenance.json',json.dumps(manifest['provenance']))
    from shader_lib import has_library
    images = sorted(ios.glob('b-*.img'))
    assert len(images) == 1 and has_library(images[0].with_suffix(''))
    build_provenance['shader_library_image'] = images[0].name
    output = report / 'OcarinaOfTime-intermediate.ipa'
    diagnostics=work/'ios-diagnostics.env'
    runtime_defaults = {'DM_OPMODE':'0', 'DM_ALIAS_WINDOWS':'256'}
    diagnostics.write_text(''.join(f'{key}={value}\n' for key, value in runtime_defaults.items()))
    build_provenance['runtime_defaults'] = runtime_defaults
    (report/'build-provenance.json').write_text(json.dumps(build_provenance,indent=2))
    env.update(GAME=TITLE, W=str(work / 'ipa'), PATCHED=str(patched), ROMFS=str(inputs / 'RomFS'),
               MSL_IOS=str(ios), GFXAPI='nvn', FONT_PY=sys.executable, DM_LANG='zh-Hans',
               DM_DISPLAY_NAME='Ocarina of Time', DM_APP_VERSION=bundle_version, DM_PSO_GPUS='none',
               DM_BUNDLE_ID='org.nx2apple.0400486024DEC000', DM_TEAM_ID='0000000000',
               DM_COPY_ROMFS='NO', AOC='',DM_ENV_FILE=str(diagnostics),DM_OPMODE='0')
    if (inputs / 'icon.jpg').exists(): env['DM_ICON'] = str(inputs / 'icon.jpg')
    print('Building the iOS host and intermediate IPA.', file=public_output, flush=True)
    b.run(['bash', source / 'dm/ios/tools/build_ipa.sh', output], logs / 'build_ipa.log', env=env)
    with zipfile.ZipFile(output, 'a') as z:
        z.writestr('Payload/DmIOS.app/dm/build-provenance.json', json.dumps(build_provenance))
    with zipfile.ZipFile(output) as z: assert z.testzip() is None
    (report / 'ipa-sha256.txt').write_text(sha(output) + '\n')


def main():
    os.umask(0o077)
    root = Path(os.environ['RUNNER_TEMP']) / 'ocarina-build'
    root.mkdir(mode=0o700)
    report = root / 'report'; report.mkdir()
    incoming = root / 'incoming'; incoming.mkdir()
    tickets = os.environ.pop('OCARINA_INPUT_URLS', '')
    env = {k:v for k,v in os.environ.items() if k not in
           ('GH_TOKEN', 'GITHUB_TOKEN', 'ACTIONS_STEP_DEBUG', 'ACTIONS_RUNNER_DEBUG')}
    status = 1
    stop=threading.Event()
    public_output=sys.stdout
    def progress():
        started=time.monotonic()
        while not stop.wait(60):
            libraries=sum(1 for folder in (root/'work/shader/ios',root/'cache/IOS') for _ in folder.glob('b-*/*_b.metallib'))
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
            cache=None
            if manifest.get('host_cache'):
                unpack(incoming/'host-inputs.zip',root/'cache',8*1024**3)
                cache=root/'cache'
                print('Reusing verified iOS game shader libraries; compiling the host with Xcode 27.2.',flush=True)
            print('Compiling Ocarina of Time with the pinned NX2Apple engine; detailed logs stay encrypted.', flush=True)
            sys.stdout = sys.stderr = log
            compile_game(root / 'engine', root / 'input', root / 'work', report, manifest, log, env, cache, public_output)
            status = 0
        except Exception:
            traceback.print_exc(file=log)
        finally: sys.stdout, sys.stderr = oldout, olderr
    stop.set()
    (report / 'status.txt').write_text('success\n' if status == 0 else 'failed\n')
    if (root / 'work/logs').exists(): shutil.copytree(root / 'work/logs', report / 'compiler-logs')
    xcode_log=root/'work/ipa/build/ios/pkg/build.log'
    if xcode_log.exists():shutil.copyfile(xcode_log,report/'xcode-build.log')
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
