"""Real SUMO checks. This is local validation, not an AGH competition run."""
import argparse
import copy
import json
import time
from pathlib import Path
from traffic_agent import core
from traffic_agent.simulator import build_network, simulate, sumo_paths

parser = argparse.ArgumentParser()
parser.add_argument('--quick', action='store_true')
parser.add_argument('--scenario', default='peak')
args = parser.parse_args()
results = []

def record(name, passed, detail):
    value = {'name': name, 'passed': passed, 'detail': detail}
    results.append(value)
    print(json.dumps(value, ensure_ascii=False), flush=True)

def invoke(name, **kwargs):
    result = getattr(core, name)(**kwargs)
    core.log_call(name, kwargs, result, caller='local_validation')
    return result

print('Local validation: actual SUMO, synthetic inputs. Not an AGH run.', flush=True)
print(str(sumo_paths()[1]), flush=True)
build_network()
scenario_ids = [args.scenario] if args.quick else ['low', 'normal', 'peak', 'one_direction', 'pedestrian_peak', 'zero']
for scenario_id in scenario_ids:
    invoke('load_scenario', scenario_id=scenario_id)
    base = invoke('run_baseline', scenario_id=scenario_id)
    run_id = base['run_id']
    search = invoke('search_timing_plan', run_id=run_id)
    candidates = invoke('run_candidate', run_id=run_id, candidate_ids=search['candidate_ids'])
    comparison = invoke('verify_and_compare', run_id=run_id)
    record(scenario_id, comparison['constraints_check']['passed'] and comparison['candidate']['metrics']['collision_count'] == 0, {'run_id': run_id, 'accepted': comparison['passed'], 'improvement_percent': comparison['improvement_percent'], 'reasons': comparison['reasons'], 'baseline_metrics': comparison['baseline']['metrics'], 'candidate_metrics': comparison['candidate']['metrics']})
    if scenario_id == 'peak':
        folder = core.run_folder(run_id)
        inp = core.read_json(folder / 'input.json')
        repeated = simulate(inp['scenario'], candidates['best']['plan'], folder)
        record('same_seed_reproducible', repeated['metrics'] == candidates['best']['metrics'], {'run_id': run_id, 'metrics_equal': repeated['metrics'] == candidates['best']['metrics']})
        illegal = invoke('run_candidate', run_id=run_id, plan={'id': 'bad', 'ns_green': 1, 'ew_green': 90, 'pedestrian_walk': 10, 'conflicting_phase': 'GGGG'})
        record('reject_conflicting_candidate', illegal.get('rejected_before_simulation') is True, illegal)
        incomplete = copy.deepcopy(inp['scenario'])
        del incomplete['vehicles_per_hour']['N']
        try:
            core.validate_scenario(incomplete)
            record('missing_direction', False, 'unexpectedly accepted')
        except core.InputError as exc:
            record('missing_direction', True, str(exc))
        invalid = copy.deepcopy(inp['scenario'])
        invalid['vehicles_per_hour']['N'] = 'many'
        try:
            core.validate_scenario(invalid)
            record('wrong_type', False, 'unexpectedly accepted')
        except core.InputError as exc:
            record('wrong_type', True, str(exc))
        try:
            simulate(inp['scenario'], inp['constraints']['baseline'], folder, timeout_seconds=.0001)
            record('simulation_timeout', False, 'unexpectedly completed')
        except TimeoutError as exc:
            record('simulation_timeout', True, str(exc))
            # Timeout may overwrite only raw tripinfo; restore baseline raw evidence.
            simulate(inp['scenario'], inp['constraints']['baseline'], folder)
summary = {'created_at': core.utc_now(), 'execution_source': 'local_validation', 'engine': 'SUMO / TraCI', 'results': results, 'all_checks_passed': all(v['passed'] for v in results)}
core.write_json(core.ROOT / '.runtime/validation/summary.json', summary)
if not summary['all_checks_passed']:
    raise SystemExit(1)
