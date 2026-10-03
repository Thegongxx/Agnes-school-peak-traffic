import { createClient, memoryJournal } from '../.tools/agnes-harness/packages/sdk/src/index.node.js'
import { runResourceCommand } from '../.tools/agnes-harness/packages/resource-control-cli/src/resources.js'
import { readFileSync, writeFileSync, appendFileSync, existsSync, readdirSync } from 'node:fs'
import { join, basename } from 'node:path'

const root = process.cwd()
const owner = JSON.parse(readFileSync(join(process.env.AGH_HOME!, 'data/daemon/owner.json'), 'utf8'))
const client = createClient({ transport: { kind: 'unix', path: owner.socketPath, serverIdentity: { pid: owner.pid, processStartId: owner.processStartId } }, auth: { kind: 'local' }, journal: memoryJournal() })
await client.initialize()
try {
  const mode = process.argv[2]
  if (mode === 'register') {
    const args = process.argv.slice(3)
    await runResourceCommand('mcp', ['--profile', 'local-dev', ...args], client, { write: value => process.stdout.write(value), confirm: async summary => { console.error(summary); return true } })
  } else if (mode === 'status') {
    const config = await client.config.get()
    const mcp = await client.mcp.servers.status({ profile: 'local-dev', serverId: 'campus-signal' })
    console.log(JSON.stringify({ configured: config.configured, mcp }))
  } else {
    const folder = process.argv[3]
    if (!(await client.config.get()).configured) throw new Error('Provider 未配置：请在 AGH 工作台保存模型账户。')
    const session = mode === 'resume' ? await client.session.load(JSON.parse(readFileSync(join(folder, 'session.json'), 'utf8')).session_id, { cwd: root }) : await client.session.new({ cwd: root, sessionKey: 'campus-signal:' + basename(folder) })
    writeFileSync(join(folder, 'session.json'), JSON.stringify({ session_id: session.id }))
    session.onPermissionRequest(async (request, context) => {
      const id = String(request.toolCall.toolCallId || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '').slice(0,100)
      const path = join(folder, 'approval.json')
      writeFileSync(path, JSON.stringify({ id, pending: true, title: request.toolCall.title, input: request.toolCall.rawInput, options: request.options, deadline_ms: request.deadlineMs, created_at: new Date().toISOString() }))
      const decision = join(folder, 'approval-' + id + '.json')
      while (!context.signal.aborted) {
        if (existsSync(decision)) {
          const selected = JSON.parse(readFileSync(decision, 'utf8'))
          writeFileSync(path, JSON.stringify({ id, pending: false, decision: selected }))
          appendFileSync(join(folder, 'approvals.jsonl'), JSON.stringify({ id, title: request.toolCall.title, decision: selected, time: new Date().toISOString() }) + '\n')
          const option = request.options.find(o => o.optionId === selected.option_id && ['allow_once', 'reject_once'].includes(o.kind))
          return option ? { optionId: option.optionId } : { verdict: 'rejected' }
        }
        await new Promise(resolve => setTimeout(resolve, 300))
      }
      writeFileSync(path, JSON.stringify({ id, pending: false, expired: true }))
      return { verdict: 'rejected' }
    })
    const remainingTask = () => {
      for (const name of readdirSync(join(root, '.runtime/reports')).filter(n => /^[a-f0-9]{16}$/.test(n))) {
        const run = join(root, '.runtime/reports', name)
        if (!existsSync(join(run, 'input.json'))) continue
        const input = JSON.parse(readFileSync(join(run, 'input.json'), 'utf8'))
        if (input.request_id !== basename(folder) || !existsSync(join(run, 'comparison.json'))) continue
        const comp = JSON.parse(readFileSync(join(run, 'comparison.json'), 'utf8'))
        const history = JSON.parse(readFileSync(join(run, 'search-history.json'), 'utf8'))
        if (!comp.passed && history.length <= input.constraints.max_retries) {
          return `轮次核对：run_id=${name} 已完成初次搜索 round=0 及 ${history.length-1} 次修正。max_retries=2 指额外两次修正，还能修正 ${input.constraints.max_retries-history.length+1} 次。不得重新建立基线。现在调用 search_timing_plan(run_id="${name}", feedback=${JSON.stringify(comp.reasons)})，随后使用完整 candidate_ids 调用 run_candidate，再调用 verify_and_compare。只依据实际轮次和校验结果汇总。`
        }
      }
      return null
    }
    let task = mode === 'resume' ? remainingTask() : readFileSync(join(folder, 'prompt.txt'), 'utf8')
    let outcome: any = { reason: 'completed' }
    let lastSeq = 0
    if (existsSync(join(folder, 'agh-events.jsonl'))) {
      const lines = readFileSync(join(folder, 'agh-events.jsonl'), 'utf8').trim().split('\n')
      lastSeq = JSON.parse(lines.at(-1)!).seq
    }
    await session.attach({ filter: { acpUpdates: false } })
    for (let turn = 0; task && turn < 3; turn++) {
      if (turn || mode === 'resume') appendFileSync(join(folder, 'continuations.jsonl'), JSON.stringify({ time: new Date().toISOString(), prompt: task }) + '\n')
      const threshold = lastSeq
      const collected = (async () => { for await (const event of session.events()) { if (event.seq <= threshold) continue; lastSeq = event.seq; appendFileSync(join(folder, 'agh-events.jsonl'), JSON.stringify(event) + '\n'); if (event.type === 'turn/end') break } })()
      outcome = await session.prompt(task, { signal: AbortSignal.timeout(1_800_000) })
      await Promise.race([collected, new Promise(resolve => setTimeout(resolve, 2000))])
      if (outcome.reason !== 'completed') break
      task = remainingTask()
    }
    if (outcome.reason === 'completed' && remainingTask()) throw new Error('模型未完成要求的有限修正流程，请检查会话轨迹。')
    console.log(JSON.stringify({ sessionId: session.id, reason: outcome.reason, exitCode: outcome.reason === 'completed' ? 0 : 1 }))
    if (outcome.reason !== 'completed') process.exitCode = 1
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
} finally { await client.close() }
