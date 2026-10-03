from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / '.runtime' / 'reports'
CALLS = ROOT / '.runtime' / 'tool-calls.jsonl'
DIRECTIONS = ('N', 'S', 'E', 'W')
_write_lock = threading.RLock()


class InputError(ValueError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def constraints():
    return read_json(ROOT / 'config' / 'constraints.json')


def validate_scenario(value):
    missing = [key for key in ('id', 'vehicles_per_hour', 'pedestrians_per_hour', 'seed', 'duration_seconds', 'drain_seconds') if key not in value]
    if missing:
        raise InputError('缺少字段：' + ', '.join(missing))
    rates = value['vehicles_per_hour']
    if not isinstance(rates, dict) or set(rates) != set(DIRECTIONS):
        raise InputError('vehicles_per_hour 必须完整包含 N、S、E、W 四个方向')
    for key, number in [*rates.items(), ('pedestrians_per_hour', value['pedestrians_per_hour'])]:
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not 0 <= number <= 6000:
            raise InputError(f'{key} 必须是 0–6000 之间的有限数值')
    for key, minimum, maximum in [('duration_seconds', 60, 3600), ('drain_seconds', 0, 3600), ('seed', 0, 2**31-1)]:
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
            raise InputError(f'{key} 必须是 {minimum}–{maximum} 的整数')
    return value


def load_scenario(scenario_id: str):
    config = read_json(ROOT / 'config' / 'scenarios.json')
    selected = next((s for s in config['scenarios'] if s['id'] == scenario_id), None)
    if selected is None:
        raise InputError('未知场景：' + str(scenario_id))
    selected = copy.deepcopy(selected)
    for key in ('duration_seconds', 'drain_seconds', 'seed', 'data_source'):
        selected[key] = config[key]
    validate_scenario(selected)
    return {
        'scenario': selected, 'constraints': constraints(),
        'network': {'junctions': 1, 'approaches': 4, 'vehicle_movement': '仅直行；第一版固定无冲突相位', 'pedestrians': 'SUMO 真实步行过街对象，独立行人相位'},
        'engine': 'Eclipse SUMO / TraCI',
    }


def cycle_seconds(plan, rules=None):
    rules = rules or constraints()
    return plan['ns_green'] + plan['ew_green'] + plan['pedestrian_walk'] + 2 * (rules['yellow_seconds'] + rules['all_red_seconds']) + rules['pedestrian_clearance_seconds']


def validate_plan(plan, rules=None):
    rules = rules or constraints()
    errors = []
    allowed = {'id', 'ns_green', 'ew_green', 'pedestrian_walk'}
    if not isinstance(plan, dict):
        return {'passed': False, 'errors': ['配时必须是对象']}
    extra = set(plan) - allowed
    if extra:
        errors.append('拒绝未定义的相位或字段：' + ', '.join(sorted(extra)))
    for key in ('ns_green', 'ew_green', 'pedestrian_walk'):
        n = plan.get(key)
        if isinstance(n, bool) or not isinstance(n, int):
            errors.append(f'{key} 必须是整数秒')
        elif key.endswith('green') and not rules['min_green_seconds'] <= n <= rules['max_green_seconds']:
            errors.append(f'{key} 超过允许绿灯范围')
        elif key == 'pedestrian_walk' and n != rules['pedestrian_walk_seconds']:
            errors.append('行人放行时间在本版搜索中保持固定')
    if not errors:
        cycle = cycle_seconds(plan, rules)
        if not rules['min_cycle_seconds'] <= cycle <= rules['max_cycle_seconds']:
            errors.append('总周期超过允许范围')
    return {'passed': not errors, 'errors': errors, 'phase_conflicts': False, 'cycle_seconds': cycle_seconds(plan, rules) if not errors else None, 'clearing_times_fixed': True}


def run_folder(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{16}', run_id):
        raise InputError('无效 run_id')
    folder = RUNS / run_id
    if not (folder / 'input.json').exists():
        raise InputError('找不到运行记录')
    return folder


def log_call(name, args, result, caller='local_validation'):
    event = {'time': utc_now(), 'tool': name, 'arguments': args, 'caller': caller, 'result': result}
    CALLS.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, CALLS.open('a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')


def demand(scenario, folder):
    rng = random.Random(scenario['seed'])
    root = ET.Element('routes')
    ET.SubElement(root, 'vType', id='campus_car', vClass='passenger', accel='2.3', decel='4.5', length='4.5', minGap='2.5', maxSpeed='8.33', sigma='0.25')
    ET.SubElement(root, 'vType', id='campus_walker', vClass='pedestrian', maxSpeed='1.3', speedFactor='1', speedDev='0')
    routes = {'N': ('n_in', 's_out'), 'S': ('s_in', 'n_out'), 'E': ('e_in', 'w_out'), 'W': ('w_in', 'e_out')}
    vehicles, persons, elements = {}, {}, []
    for direction in DIRECTIONS:
        arrival, rate, index = 0., scenario['vehicles_per_hour'][direction] / 3600, 0
        while rate > 0:
            arrival += rng.expovariate(rate)
            if arrival >= scenario['duration_seconds']:
                break
            item_id = f'{direction}_{index}'
            vehicles[item_id] = {'depart': arrival, 'direction': direction}
            v = ET.Element('vehicle', id=item_id, type='campus_car', depart=f'{arrival:.3f}', departLane='1', departSpeed='max')
            ET.SubElement(v, 'route', edges=' '.join(routes[direction]))
            elements.append((arrival, v))
            index += 1
    # Each person crosses one arm from its incoming sidewalk to its outgoing sidewalk.
    arrival, rate, index = 0., scenario['pedestrians_per_hour'] / 3600, 0
    while rate > 0:
        arrival += rng.expovariate(rate)
        if arrival >= scenario['duration_seconds']:
            break
        arm = rng.choice(('n', 's', 'e', 'w'))
        item_id = f'ped_{index}'
        persons[item_id] = {'depart': arrival, 'arm': arm}
        p = ET.Element('person', id=item_id, type='campus_walker', depart=f'{arrival:.3f}', departPos='-3')
        ET.SubElement(p, 'walk', edges=f'{arm}_in {arm}_out', arrivalPos='3')
        elements.append((arrival, p))
        index += 1
    for _, element in sorted(elements, key=lambda x: x[0]):
        root.append(element)
    path = folder / 'demand.rou.xml'
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'vehicles': vehicles, 'persons': persons, 'sha256': fingerprint, 'file': str(path)}


def run_baseline(scenario_id: str, request_id: str = ''):
    from .simulator import simulate
    loaded = load_scenario(scenario_id)
    folder = RUNS / uuid.uuid4().hex[:16]
    folder.mkdir(parents=True)
    loaded['request_id'] = request_id[:80]
    loaded['created_at'] = utc_now()
    write_json(folder / 'input.json', loaded)
    manifest = demand(loaded['scenario'], folder)
    write_json(folder / 'demand.json', manifest)
    result = simulate(loaded['scenario'], loaded['constraints']['baseline'], folder, capture=True)
    write_json(folder / 'baseline.json', result)
    write_json(ROOT / '.runtime' / 'latest.json', {'run_id': folder.name})
    return {'run_id': folder.name, 'scenario': loaded['scenario']['id'], 'data_source': loaded['scenario']['data_source'], 'baseline': summarise_result(result), 'demand_sha256': manifest['sha256']}


def search_timing_plan(run_id: str, feedback: list[str] | None = None):
    folder = run_folder(run_id)
    inp = read_json(folder / 'input.json')
    rules, scenario = inp['constraints'], inp['scenario']
    history_file = folder / 'search-history.json'
    history = read_json(history_file) if history_file.exists() else []
    round_index = len(history)
    if round_index > rules['max_retries']:
        return {'run_id': run_id, 'candidate_ids': [], 'retries_exhausted': True, 'recommended_plan': rules['baseline'], 'reason': '已完成初次搜索和最多两次修正，返回基线。'}
    if round_index:
        comparison_file = folder / 'comparison.json'
        if not comparison_file.exists() or read_json(comparison_file)['passed']:
            raise InputError('仅能在实际校验失败后修正搜索')
        if not feedback:
            raise InputError('修正搜索必须提供上一轮工具返回的失败原因')
    ns = scenario['vehicles_per_hour']['N'] + scenario['vehicles_per_hour']['S']
    ew = scenario['vehicles_per_hour']['E'] + scenario['vehicles_per_hour']['W']
    share = ns / (ns + ew) if ns + ew else .5
    # Explore a bounded finite set around demand-proportional allocation and symmetric allocation.
    overhead = 2 * (rules['yellow_seconds'] + rules['all_red_seconds']) + rules['pedestrian_walk_seconds'] + rules['pedestrian_clearance_seconds']
    pool, seen = [], set()
    cycle_options = ((72, 84, 96, 108), (66, 78, 90, 102), (64, 70, 80, 88))[round_index]
    for cycle in cycle_options:
        available = cycle - overhead
        for fraction in (share - .08, share, share + .08, .5):
            ng = round(available * fraction / 3) * 3
            eg = available - ng
            plan = {'id': f'r{round_index}_p{cycle}_{ng}_{eg}', 'ns_green': ng, 'ew_green': eg, 'pedestrian_walk': rules['pedestrian_walk_seconds']}
            if validate_plan(plan, rules)['passed'] and (ng, eg) not in seen:
                seen.add((ng, eg))
                pool.append(plan)
    write_json(folder / 'candidates.json', pool)
    history.append({'round': round_index, 'feedback': feedback or [], 'cycle_options': list(cycle_options), 'candidates': pool})
    write_json(history_file, history)
    return {'run_id': run_id, 'round': round_index, 'retries_remaining': rules['max_retries'] - round_index, 'method': '有限枚举：4 个周期 × 需求比例附近的绿信比；校验失败后缩短周期重新搜索，合法性过滤先于仿真', 'feedback_received': feedback or [], 'candidates': pool, 'candidate_ids': [p['id'] for p in pool], 'instructions': '调用 run_candidate，candidate_ids 传入该列表批量执行真实 SUMO；再调用 verify_and_compare。'}


def summarise_result(result):
    return {k: v for k, v in result.items() if k not in ('snapshots', 'timeseries')}


def run_candidate(run_id: str, candidate_ids: list[str] | None = None, plan: dict | None = None):
    from .simulator import simulate
    folder = run_folder(run_id)
    inp = read_json(folder / 'input.json')
    pool = read_json(folder / 'candidates.json') if (folder / 'candidates.json').exists() else []
    if plan is not None:
        check = validate_plan(plan, inp['constraints'])
        if not check['passed']:
            return {'run_id': run_id, 'passed': False, 'rejected_before_simulation': True, 'reasons': check['errors']}
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,60}', str(plan.get('id', ''))):
            raise InputError('plan.id 必须是安全的字母数字标识')
        if plan['id'] == 'baseline':
            raise InputError('baseline 是保留标识，不允许候选覆盖基线原始记录')
        selected = [plan]
    else:
        ids = candidate_ids or [p['id'] for p in pool]
        if not ids or len(ids) > 24 or len(set(ids)) != len(ids):
            raise InputError('候选列表必须包含 1–24 个不同 ID')
        by_id = {p['id']: p for p in pool}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise InputError('未知候选：' + ', '.join(unknown))
        selected = [by_id[i] for i in ids]
    results = []
    for candidate in selected:
        result = simulate(inp['scenario'], candidate, folder)
        write_json(folder / f"candidate-{candidate['id']}.json", result)
        results.append(result)
    baseline_metrics = read_json(folder / 'baseline.json')['metrics']
    feasible = [r for r in results if r['metrics']['max_pedestrian_wait'] <= inp['constraints']['max_pedestrian_wait_seconds'] and r['metrics']['mean_pedestrian_wait'] <= baseline_metrics['mean_pedestrian_wait'] + .5 and max(r['metrics']['approach_max_wait'].values(), default=0) <= inp['constraints']['max_approach_wait_seconds'] and r['metrics']['collision_count'] == 0 and r['metrics']['unfinished_vehicles'] <= baseline_metrics['unfinished_vehicles'] and r['metrics']['unfinished_persons'] <= baseline_metrics['unfinished_persons']]
    chosen = min(feasible or results, key=lambda r: (r['metrics']['unfinished_vehicles'], r['metrics']['mean_vehicle_delay']))
    # Record replay of the chosen plan using the identical demand and seed.
    chosen = simulate(inp['scenario'], chosen['plan'], folder, capture=True)
    write_json(folder / f"candidate-{chosen['plan']['id']}.json", chosen)
    write_json(folder / 'selected.json', chosen)
    return {'run_id': run_id, 'evaluated': len(results), 'best_candidate_id': chosen['plan']['id'], 'best': summarise_result(chosen), 'results': [summarise_result(r) for r in results], 'next_step': '必须调用 verify_and_compare；本工具选优尚不表示通过验收。'}


def verify_and_compare(run_id: str, candidate_id: str = ''):
    folder = run_folder(run_id)
    inp = read_json(folder / 'input.json')
    baseline = read_json(folder / 'baseline.json')
    if candidate_id:
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,60}', candidate_id):
            raise InputError('候选 ID 无效')
        candidate = read_json(folder / f'candidate-{candidate_id}.json')
    else:
        candidate = read_json(folder / 'selected.json')
    bm, cm, rules = baseline['metrics'], candidate['metrics'], inp['constraints']
    check = validate_plan(candidate['plan'], rules)
    reasons = list(check['errors'])
    improvement = 100 * (bm['mean_vehicle_delay'] - cm['mean_vehicle_delay']) / bm['mean_vehicle_delay'] if bm['mean_vehicle_delay'] else 0
    if baseline['demand_sha256'] != candidate['demand_sha256']:
        reasons.append('候选与基线需求文件不同，比较无效')
    if cm['collision_count'] > 0:
        reasons.append('仿真报告碰撞，方案拒绝')
    if cm['max_pedestrian_wait'] > rules['max_pedestrian_wait_seconds']:
        reasons.append('行人最大等待超过项目上限')
    if cm['mean_pedestrian_wait'] > bm['mean_pedestrian_wait'] + .5:
        reasons.append('行人平均等待相对基线恶化超过 0.5 秒')
    if max(cm['approach_max_wait'].values(), default=0) > rules['max_approach_wait_seconds']:
        reasons.append('至少一个进口的最大等待超过项目上限')
    if cm['unfinished_vehicles'] > bm['unfinished_vehicles'] or cm['unfinished_persons'] > bm['unfinished_persons']:
        reasons.append('未完成需求多于基线，拒绝只统计已完成者产生的偏差')
    if bm['vehicle_count'] == 0:
        reasons.append('零车辆需求，没有可证明的车辆延误改善，保留基线')
    elif improvement < rules['target_delay_improvement_percent']:
        reasons.append(f"延误改善 {improvement:.2f}% 未达到项目 {rules['target_delay_improvement_percent']}% 目标")
    differences = {k: round(cm[k] - bm[k], 4) for k in ('mean_vehicle_delay', 'mean_pedestrian_wait', 'max_pedestrian_wait', 'max_queue', 'throughput', 'unfinished_vehicles', 'unfinished_persons')}
    result = {'run_id': run_id, 'passed': not reasons, 'status': 'accepted' if not reasons else 'fallback_baseline', 'reasons': reasons, 'improvement_percent': round(improvement, 4), 'differences': differences, 'constraints_check': check, 'recommended_plan': candidate['plan'] if not reasons else baseline['plan'], 'baseline': summarise_result(baseline), 'candidate': summarise_result(candidate), 'data_source': inp['scenario']['data_source'], 'verified_at': utc_now()}
    write_json(folder / 'comparison.json', result)
    write_json(ROOT / '.runtime' / 'latest.json', {'run_id': run_id})
    return result
