from __future__ import annotations

import functools
import contextlib
import sys
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from . import core

server = FastMCP('campus-traffic-signal', instructions='校园单路口合成数据仿真。必须按 load_scenario → run_baseline → search_timing_plan → run_candidate → verify_and_compare 执行。指标只能引用真实 SUMO 工具返回；校验失败最多重试2次，再返回基线。')


def receipt(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = fn(*args, **kwargs)
            core.log_call(fn.__name__, kwargs, result, caller='mcp_stdio')
            return result
        except Exception as exc:
            core.log_call(fn.__name__, kwargs, {'error': str(exc)}, caller='mcp_stdio')
            raise
    return wrapped


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
@receipt
def load_scenario(scenario_id: str) -> dict:
    """读取 low/normal/peak/one_direction/pedestrian_peak/zero 合成场景、流量、种子和安全约束。"""
    return core.load_scenario(scenario_id)


@server.tool()
@receipt
def run_baseline(scenario_id: str, request_id: str = '') -> dict:
    """真实运行 SUMO 固定配时基线，保存不可由模型改写的结果并返回 run_id。"""
    return core.run_baseline(scenario_id, request_id)


@server.tool()
@receipt
def search_timing_plan(run_id: str, feedback: list[str] | None = None) -> dict:
    """生成有限合法候选，不编造仿真得分；反馈失败原因后可进行最多两轮修正。"""
    return core.search_timing_plan(run_id, feedback)


@server.tool()
@receipt
def run_candidate(run_id: str, candidate_ids: list[str] | None = None, plan: dict | None = None) -> dict:
    """用同一需求与种子真实批量仿真候选，返回最佳候选和实际指标；非法配时在仿真前拒绝。"""
    return core.run_candidate(run_id, candidate_ids, plan)


@server.tool()
@receipt
def verify_and_compare(run_id: str, candidate_id: str = '') -> dict:
    """从保存的原始仿真结果验证约束、行人等待、公平性和5%开发目标；失败返回基线。"""
    return core.verify_and_compare(run_id, candidate_id)


if __name__ == '__main__':
    server.run(transport='stdio')
