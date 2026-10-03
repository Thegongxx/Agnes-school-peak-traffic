"""Extract Microsoft's compiler packages locally; never install VS into Windows."""
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
import zlib
from pathlib import Path
import httpx
import win32com.client
from traffic_agent.core import ROOT, read_json, write_json

CACHE = ROOT / '.cache/msvc'
TOOLS = ROOT / '.tools/msvc'
SDK = ROOT / '.tools/windows-sdk'
CACHE.mkdir(parents=True, exist_ok=True)
TOOLS.mkdir(parents=True, exist_ok=True)
SDK.mkdir(parents=True, exist_ok=True)
packages = read_json(ROOT / '.cache/vs-manifest.json')['packages']


def download(payload, sub=''):
    path = CACHE / sub / Path(payload['fileName'].replace('\\', '/')).name
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = payload['sha256'].lower()
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        return path
    print('Downloading ' + path.name + f" ({payload['size']/1024/1024:.1f} MB)", flush=True)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for attempt in range(3):
            try:
                with client.stream('GET', payload['url']) as response:
                    response.raise_for_status()
                    with path.open('wb') as output:
                        for block in response.iter_bytes(1024 * 1024):
                            output.write(block)
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise RuntimeError('Microsoft payload checksum mismatch: ' + path.name)
                return path
            except Exception:
                if attempt == 2:
                    raise


for name in ('Microsoft.VC.14.44.17.14.Tools.HostX64.TargetX64.base', 'Microsoft.VC.14.44.17.14.Tools.HostX64.TargetX64.Res.base', 'Microsoft.VC.14.44.17.14.Tools.HostX86.TargetX64.base', 'Microsoft.VC.14.44.17.14.Tools.HostX64.TargetX86.base', 'Microsoft.VC.14.44.17.14.CRT.Source.base', 'Microsoft.VC.14.44.17.14.CRT.Headers.base', 'Microsoft.VC.14.44.17.14.CRT.x64.Desktop.base', 'Microsoft.VC.14.44.17.14.CRT.x64.Store.base'):
    package = next(p for p in packages if p['id'] == name and (not p.get('language') or p['language'] == 'en-US'))
    for payload in package['payloads']:
        archive = download(payload)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = info.filename.replace('\\', '/')
                if name.startswith('Contents/') and not info.is_dir():
                    target = (TOOLS / name[len('Contents/'):]).resolve()
                    if not target.is_relative_to(TOOLS.resolve()):
                        raise RuntimeError('Unsafe Microsoft archive path')
                    if target.exists() and target.stat().st_size == info.file_size and zlib.crc32(target.read_bytes()) == info.CRC:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)

sdk = next(p for p in packages if p['id'] == 'Win11SDK_10.0.22621')
payloads = {Path(p['fileName'].replace('\\', '/')).name: p for p in sdk['payloads']}
msi_names = (
    'Windows SDK Desktop Headers x86-x86_en-us.msi',
    'Windows SDK Desktop Headers x64-x86_en-us.msi',
    'Windows SDK Desktop Libs x64-x86_en-us.msi',
    'Windows SDK for Windows Store Apps Headers-x86_en-us.msi',
    'Windows SDK for Windows Store Apps Libs-x86_en-us.msi',
    'Universal CRT Headers Libraries and Sources-x86_en-us.msi',
)
installer = win32com.client.Dispatch('WindowsInstaller.Installer')
for name in msi_names:
    msi = download(payloads[name], 'sdk')
    database = installer.OpenDatabase(str(msi), 0)
    view = database.OpenView('SELECT `Cabinet` FROM `Media`')
    view.Execute()
    row = view.Fetch()
    while row:
        cabinet = row._oleobj_.Invoke(row._oleobj_.GetIDsOfNames('StringData'), 0, 2, True, 1)
        if cabinet and not cabinet.startswith('#'):
            download(payloads[cabinet], 'sdk')
        row = view.Fetch()
    view.Close()
    log = CACHE / ('extract-' + name + '.log')
    # Administrative image extraction only: no /i, registration, or system deployment.
    proc = subprocess.run(['msiexec.exe', '/a', str(msi), '/qn', 'TARGETDIR=' + str(SDK), '/l*v', str(log)], env={**os.environ, 'TEMP': str(ROOT / '.cache/temp'), 'TMP': str(ROOT / '.cache/temp')}, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode not in (0, 3010):
        raise RuntimeError(f'SDK administrative extraction failed: {name}, {proc.returncode}; {log}')

cl = next(TOOLS.rglob('Hostx64/x64/cl.exe'))
vc = cl.parents[3]
windows_h = next(SDK.rglob('um/Windows.h'))
include = windows_h.parent.parent
sdk_root = include.parent.parent
lib = sdk_root / 'Lib' / include.name
configuration = {
    'vc_tools': str(vc), 'cl': str(cl),
    'include': ';'.join(str(p) for p in (vc / 'include', include / 'ucrt', include / 'shared', include / 'um', include / 'winrt')),
    'lib': ';'.join(str(p) for p in (vc / 'lib/x64', lib / 'ucrt/x64', lib / 'um/x64')),
    'sdk_root': str(sdk_root), 'source': 'Microsoft official VS channel manifest; SHA256-verified payloads',
}
write_json(ROOT / '.tools/compiler-env.json', configuration)
print(json.dumps(configuration, indent=2), flush=True)
