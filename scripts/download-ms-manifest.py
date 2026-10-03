import json
from pathlib import Path
import httpx
from traffic_agent.core import ROOT

with httpx.Client(follow_redirects=True, timeout=60) as client:
    result = client.get('https://aka.ms/vs/17/release/channel')
    result.raise_for_status()
    (ROOT / '.cache/vs-channel.json').write_bytes(result.content)
    channel = result.json()
    manifest = next(p for p in channel['channelItems'] if p['id'] == 'Microsoft.VisualStudio.Manifests.VisualStudio')
    result = client.get(manifest['payloads'][0]['url'])
    result.raise_for_status()
    (ROOT / '.cache/vs-manifest.json').write_bytes(result.content)
    packages = result.json()['packages']
    for p in packages:
        if p['id'] in ('Microsoft.VisualStudio.Component.VC.Tools.x86.x64',) or 'VC.Tools.HostX64' in p['id'] or 'Windows11SDK' in p['id']:
            print(json.dumps({k: p.get(k) for k in ('id', 'version', 'dependencies', 'payloads')}, ensure_ascii=False), flush=True)
