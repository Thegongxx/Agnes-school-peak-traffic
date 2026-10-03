"""Check the actual stdio MCP transport, separate from AGH evidence."""
import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from traffic_agent import agh, core

async def main():
    params = StdioServerParameters(command=str(agh.PYTHON), args=[str(core.ROOT / 'scripts/mcp-entry.py')], cwd=str(core.ROOT), env=agh.environment())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            catalog = await session.list_tools()
            names = [t.name for t in catalog.tools]
            assert set(names) == {'load_scenario', 'run_baseline', 'search_timing_plan', 'run_candidate', 'verify_and_compare'}
            result = await session.call_tool('load_scenario', {'scenario_id': 'peak'})
            assert not result.isError
            value = json.loads(result.content[0].text)
            assert value['scenario']['id'] == 'peak'
            report = {'source': 'official_python_MCP_client', 'tools': names, 'load_scenario_passed': True, 'agh_session': False}
            core.write_json(core.ROOT / '.runtime/validation/mcp-transport.json', report)
            print(json.dumps(report, ensure_ascii=False))

asyncio.run(main())
