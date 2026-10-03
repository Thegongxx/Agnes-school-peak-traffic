"""Register, review and enable exactly the five local business tools in AGH."""
import json
import re
import subprocess
from traffic_agent import agh, core

profile = agh.HOME / 'profiles/local-dev/profile.yaml'
if not profile.exists():
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(core.ROOT / 'scripts/prepare-agh-home.ps1')], check=True)
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text('name: local-dev\ncomputerUse:\n  enabled: false\n', encoding='utf-8')

def cli(*arguments, check=True):
    # The user's project setup authorizes these five local tools. Use the official
    # resource controller; CLI stdin without a TTY deliberately cancels mutations.
    result = subprocess.run([str(agh.NODE), str(agh.SDK_ENTRY), 'register', *arguments[1:]], cwd=core.ROOT, env=agh.environment(), text=True, encoding='utf-8', capture_output=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW)
    if check and result.returncode:
        raise RuntimeError(f'AGH {arguments[0]} failed: {result.stderr[-1800:]} {result.stdout[-1800:]}')
    if result.stdout:
        print(result.stdout.strip(), flush=True)
    return result

server = 'campus-signal'
listed = cli('mcp', 'list')
if server not in listed.stdout:
    args = ['mcp', 'add', server, '--name', 'campus-traffic-signal', '--stdio', str(agh.PYTHON), '--arg', str(core.ROOT / 'scripts/mcp-entry.py')]
    for name in ('load_scenario', 'run_baseline', 'search_timing_plan', 'run_candidate', 'verify_and_compare'):
        args.extend(['--allow-tool', name])
    cli(*args)

for action, field, expected in [('trust', 'trust', 'trusted'), ('enable', 'desired', 'enabled')]:
    current = cli('mcp', 'get', server).stdout
    if f'{field}={expected}' not in current or (action == 'enable' and 'actual=ready' not in current):
        revision = re.search(r'\brevision=([^\s]+)', current)
        if not revision:
            raise RuntimeError('No current MCP revision in AGH response')
        cli('mcp', action, server, '--expected-revision', revision.group(1))

status = cli('mcp', 'status', server).stdout
tools = cli('mcp', 'tools', server).stdout
report = {'server': server, 'registered_in_AGH': True, 'status': status.strip(), 'tools': tools.strip(), 'real_model_run': False}
core.write_json(core.ROOT / '.runtime/validation/agh-mcp-registration.json', report)
