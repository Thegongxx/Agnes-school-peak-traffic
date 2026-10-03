"""Run official AGH SDK sessions, preserving permission requests and native exports."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from . import core
from .core import ROOT, load_scenario, read_json, utc_now, write_json

NODE = ROOT / '.tools/node-v24.10.0-win-x64/node.exe'
ENTRY = ROOT / '.tools/agnes-harness/packages/cli/dist/local/agnes.mjs'
SDK_ENTRY = ENTRY.parent / 'campus-runner.mjs'
PYTHON = ROOT / '.tools/python/python.exe'
HOME = ROOT / '.runtime/agh'
JOBS = ROOT / '.runtime/jobs'
_job_lock = threading.Lock()
_running = None


def environment():
    env = os.environ.copy()
    env.update(AGH_HOME=str(HOME), AGNES_PROFILE='local-dev', PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PIP_CACHE_DIR=str(ROOT / '.cache/pip'), TMP=str(ROOT / '.cache/temp'), TEMP=str(ROOT / '.cache/temp'))
    env['PATH'] = str(NODE.parent) + os.pathsep + str(PYTHON.parent) + os.pathsep + env.get('PATH', '')
    return env


def status():
    current = read_json(JOBS / 'current.json') if (JOBS / 'current.json').exists() else None
    approval = None
    if current:
        path = JOBS / current['job_id'] / 'approval.json'
        if path.exists():
            try:
                value = read_json(path)
                if value.get('pending') and (not value.get('deadline_ms') or value['deadline_ms'] > time.time() * 1000):
                    approval = value
            except ValueError:
                pass
    return {'installed': ENTRY.exists() and SDK_ENTRY.exists(), 'node_local': NODE.exists(), 'python_local': PYTHON.exists(), 'agh_home': str(HOME), 'current_job': current, 'approval': approval, 'workbench_url': 'http://127.0.0.1:4177'}


def answer_approval(job_id, approval_id, option_id):
    current = status()
    if not current['current_job'] or current['current_job']['job_id'] != job_id or current['current_job']['status'] != 'running':
        raise core.InputError('该任务没有正在等待的审批')
    pending = current['approval']
    if not pending or pending['id'] != approval_id:
        raise core.InputError('审批已过期，请刷新')
    if not any(o['optionId'] == option_id and o['kind'] in ('allow_once', 'reject_once') for o in pending['options']):
        raise core.InputError('只允许本次审批或本次拒绝')
    write_json(JOBS / job_id / f'approval-{approval_id}.json', {'option_id': option_id})
    return {'saved': True}


def prompt(scenario_id, request_id):
    return f'''你是校园高峰路口信号配时优化智能体。本轮业务操作只使用 campus-signal MCP（显示名 campus-traffic-signal）的五个业务工具。
若工具尚未加载，先用 tool_search 搜索 campus 或具体工具名，必要时用 tool_describe 读取参数。业务工具在 AGH 中带 mcp_campus_signal_ 前缀，请使用实际目录中的完整名称。
请求编号：{request_id}；场景：{scenario_id}；所有输入均为合成数据，仅作仿真。
先简短说明计划，再依次执行：
1. load_scenario(scenario_id="{scenario_id}")，读取硬约束。
2. run_baseline(scenario_id="{scenario_id}", request_id="{request_id}")，保留返回的 run_id。
3. search_timing_plan(run_id)，取得完整候选 ID 列表。
4. run_candidate(run_id, candidate_ids=上一工具返回的完整列表)，批量真实仿真。
5. verify_and_compare(run_id)，仅按工具返回判断是否通过。
6. 若失败，把工具返回 reasons 传给 search_timing_plan 的 feedback 后重复第4、5步；最多修正两次。仍失败就返回基线。
最终说明数据来源、配时、车辆延误、行人等待、排队、通过量、失败原因和 run_id。引用实际工具结果，不捏造数值、不宣称真实道路有效。
不要通过终端绕过这些工具执行仿真。'''


def start_job(scenario_id):
    global _running
    load_scenario(scenario_id)
    if not ENTRY.exists() or not SDK_ENTRY.exists():
        raise RuntimeError('AGH 尚未完成本地构建。请执行 scripts/build-agh.ps1 并查看 .runtime/agh-build.log。')
    with _job_lock:
        if _running and _running.is_alive():
            raise RuntimeError('已有 AGH 任务正在执行')
        job_id = uuid.uuid4().hex[:16]
        folder = JOBS / job_id
        folder.mkdir(parents=True)
        state = {'job_id': job_id, 'scenario_id': scenario_id, 'status': 'running', 'started_at': utc_now(), 'entry': 'official_AGH_SDK', 'model': 'AGH 当前配置的 Provider', 'message': 'AGH 已接收优化任务；工具审批会显示在本看板'}
        write_json(JOBS / 'current.json', state)
        write_json(folder / 'status.json', state)
        task_prompt = prompt(scenario_id, job_id)
        (folder / 'prompt.txt').write_text(task_prompt, encoding='utf-8')
        _running = threading.Thread(target=_execute, args=(state, folder, task_prompt), daemon=True)
        _running.start()
        return state


def _execute(state, folder, task_prompt, resume=False):
    try:
        with (folder / 'agh-output.jsonl').open('w', encoding='utf-8') as output, (folder / 'agh-stderr.log').open('w', encoding='utf-8') as error:
            process = subprocess.Popen([str(NODE), str(SDK_ENTRY), 'resume' if resume else 'run', str(folder)], cwd=ROOT, env=environment(), stdout=output, stderr=error, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                code = process.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                process.terminate()
                code = process.wait(timeout=20)
                state['message'] = '等待 AGH 超时；请在工作台检查会话运行或审批状态'
        state['exit_code'] = code
        state['status'] = 'completed' if code == 0 else 'failed'
        state['finished_at'] = utc_now()
        if (folder / 'session.json').exists():
            state['session_id'] = read_json(folder / 'session.json')['session_id']
        if code:
            state['message'] = (folder / 'agh-stderr.log').read_text(encoding='utf-8', errors='replace')[-1600:] or 'AGH 返回错误，请在工作台检查模型配置或 MCP 状态'
        else:
            result_line = None
            for line in (folder / 'agh-output.jsonl').read_text(encoding='utf-8').splitlines():
                try:
                    value = json.loads(line)
                    if value.get('sessionId'):
                        result_line = value
                except (ValueError, AttributeError):
                    continue
            if result_line:
                state['session_id'] = result_line['sessionId']
                exported = subprocess.run([str(NODE), str(ENTRY), 'export', state['session_id'], '--format', 'agnes', '-o', str(folder / 'agh-session.jsonl')], cwd=ROOT, env=environment(), capture_output=True, text=True, encoding='utf-8', timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                state['native_trace_exported'] = exported.returncode == 0 and (folder / 'agh-session.jsonl').exists()
            matches = [path.parent for path in core.RUNS.glob('*/input.json') if read_json(path).get('request_id') == state['job_id']]
            if matches and (matches[-1] / 'comparison.json').exists():
                run_id = matches[-1].name
                comparison = read_json(matches[-1] / 'comparison.json')
                state.update(run_id=run_id, accepted=comparison['passed'], message='AGH 已执行仿真与校验：' + ('方案通过' if comparison['passed'] else '保留基线；' + '；'.join(comparison['reasons'])))
                write_json(matches[-1] / 'agh-evidence.json', {'job_id': state['job_id'], 'session_id': state.get('session_id'), 'native_trace_exported': state.get('native_trace_exported', False)})
            else:
                state.update(status='failed', message='AGH 会话结束，但未找到本次请求完整的仿真与校验记录。请在 AGH 工作台检查工具调用和审批状态。')
    except Exception as exc:
        state.update(status='failed', message=str(exc), finished_at=utc_now())
    finally:
        write_json(folder / 'status.json', state)
        write_json(JOBS / 'current.json', state)
