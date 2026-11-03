// dsh-context-budget —— 预算感知的上下文轮转（DeepSeek Harness 插件）。
//
// 只依赖宿主已有的服务，不 import 任何 DSH 内部模块：
//   ctx.tools.register      注册 context_budget 工具
//   ctx.on('session/event') 读每条 assistant/message 的 usage
//   ctx.get('sessionController') 开新会话 / 投递交接摘要
//   ctx.get('workspaceRegistry') 把新会话挂到工作区（可选，失败不影响轮转）
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const name = 'dsh-context-budget'
export const inject = ['tools']

const DEFAULTS = {
  /** 上下文预算：单步 input+cache 超过它就算“超预算”。 */
  budgetTokens: 20000,
  /** 缓存命中价 / 未命中价。等价成本 = miss + cache * ratio + out。 */
  cachePriceRatio: 0.1,
  /** 记账与日志放哪；留空则用 <DSH_HOME>/context-budget。 */
  dataDir: '',
  /** 轮转出来的新会话用哪个工作目录。强烈建议显式配置，留空会拒绝轮转。 */
  cwd: '',
  /** 可选：新会话用哪个 agent preset。 */
  agentPreset: '',
}

const defaultDataDir = () =>
  join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'context-budget')

export function apply(ctx, config) {
  const cfg = { ...DEFAULTS, ...(config || {}) }
  const dataDir = cfg.dataDir || defaultDataDir()
  const stateFile = join(dataDir, 'state.json')
  const activeFile = join(dataDir, 'active_session.json')
  const logFile = join(dataDir, 'budget.log')
  const handoffFile = join(dataDir, 'handoffs.jsonl')
  try { mkdirSync(dataDir, { recursive: true }) } catch {}

  const log = (msg) => {
    const line = '[' + new Date().toISOString().slice(11, 19) + '] ' + msg
    try { appendFileSync(logFile, line + '\n', 'utf8') } catch {}
    try { ctx.logger?.info?.('[context-budget] ' + msg) } catch {}
  }

  const state = { sessions: {}, latestId: '', updatedAt: 0 }
  try {
    Object.assign(state, JSON.parse(readFileSync(stateFile, 'utf8')))
  } catch {}
  let dirty = false

  const flush = () => {
    if (!dirty) return
    dirty = false
    state.updatedAt = Date.now()
    try { writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf8') } catch {}
  }

  const equivOf = (s) => Math.round(s.miss + s.cache * cfg.cachePriceRatio + s.out)
  const current = () => state.sessions[state.latestId] ?? null

  // over 是“上一步是否超预算”的边沿标记。它会被持久化，但进程重启后必须清零，
  // 否则改了 budgetTokens（或换了预算）之后，会话一路涨到几倍预算也不会再记日志——
  // 因为内存里那个 over 还是上次留下的 true。重启时按当前预算重算一次即可。
  for (const s of Object.values(state.sessions)) s.over = false
  {
    const s = current()
    if (s && (s.lastCtx || 0) > cfg.budgetTokens) {
      s.over = true
      log('启动检查：会话 ' + state.latestId + ' 上下文 ' + s.lastCtx + '，已超过预算 ' + cfg.budgetTokens + '，建议轮转')
    }
  }

  const snapshot = () => {
    const s = current()
    return {
      budget: cfg.budgetTokens,
      sessions: Object.keys(state.sessions).length,
      context: s?.lastCtx ?? 0,
      equivalentCost: s ? equivOf(s) : 0,
      overBudget: (s?.lastCtx ?? 0) > cfg.budgetTokens,
      steps: s?.steps ?? 0,
    }
  }

  // —— 观测：每条 assistant/message 都带 usage，就在这里记账 ——
  ctx.on('session/event', (session, event) => {
    if (!event || event.type !== 'assistant/message') return
    const u = event.data?.usage
    if (!u) return
    const sid = session?.id || session?.sessionId || 'unknown'
    state.latestId = sid
    const s = state.sessions[sid] ||
      (state.sessions[sid] = { steps: 0, miss: 0, cache: 0, out: 0, lastCtx: 0, over: false })
    s.steps += 1
    s.miss += u.inputTokens || 0
    s.cache += u.cacheReadTokens || 0
    s.out += u.outputTokens || 0
    s.lastCtx = (u.inputTokens || 0) + (u.cacheReadTokens || 0)
    const over = s.lastCtx > cfg.budgetTokens
    if (over && !s.over) log('会话 ' + sid + ' 上下文 ' + s.lastCtx + '，超过预算 ' + cfg.budgetTokens)
    s.over = over
    dirty = true
    flush()
  })

  // —— 工具 ——
  ctx.tools.register({
    name: 'context_budget',
    description:
      '查看当前会话的上下文预算与等价成本；或在写完整理好的交接摘要后，用它把对话轮转到新的小上下文会话。' +
      '轮转后，读 active_session.json 的外部桥接会跟着切到新会话。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', enum: ['status', 'rotate'], description: 'status 查看；rotate 轮转' },
        summary: {
          type: 'string',
          description:
            'rotate 必填：你写给下一段对话自己的交接摘要，要具体（做过什么、结论、待办）。' +
            '不要写“之前讨论了很多”这种空话。',
        },
      },
      required: ['action'],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          ok: { type: 'boolean' },
          budget: { type: 'number' },
          sessions: { type: 'number' },
          steps: { type: 'number' },
          context: { type: 'number' },
          equivalentCost: { type: 'number' },
          overBudget: { type: 'boolean' },
          newSessionId: { type: 'string' },
          note: { type: 'string' },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    async execute(args) {
      const keys = ['action', 'ok', 'budget', 'sessions', 'steps', 'context',
                    'equivalentCost', 'overBudget', 'newSessionId', 'note']
      const pick = (v) => {
        const out = {}
        for (const k of keys) if (v[k] !== undefined) out[k] = v[k]
        return out
      }
      const snap = snapshot()
      if (args.action === 'status') {
        return pick({ action: 'status', ok: true, ...snap, note: snap.overBudget ? '已超预算，建议轮转' : '在预算内' })
      }

      const summary = String(args.summary || '').trim()
      if (summary.length < 40) {
        return pick({ action: 'rotate', ok: false, ...snap, note: '摘要太短（少于 40 字），写具体一点再轮转' })
      }
      const cwd = String(cfg.cwd || '').trim()
      if (!cwd) {
        return pick({
          action: 'rotate', ok: false, ...snap,
          note: '配置里没设 cwd，拒绝轮转：宿主默认目录经常和工作区不一样，会把新会话开错地方',
        })
      }
      const controller = (() => { try { return ctx.get('sessionController') } catch { return undefined } })()
      if (!controller || typeof controller.create !== 'function') {
        return pick({ action: 'rotate', ok: false, ...snap, note: '宿主没有提供 sessionController' })
      }

      const created = await controller.create({
        cwd,
        ...(cfg.agentPreset ? { agentPreset: cfg.agentPreset } : {}),
      })
      const sid = created?.sessionId
      if (!sid) return pick({ action: 'rotate', ok: false, ...snap, note: 'create 没有返回 sessionId' })

      try {
        const registry = (() => { try { return ctx.get('workspaceRegistry') } catch { return undefined } })()
        if (registry && typeof registry.create === 'function') {
          const ws = await registry.create(cwd)
          if (ws && typeof ws.attachSession === 'function') await ws.attachSession(sid)
        }
      } catch (err) {
        log('挂工作区失败（不影响执行）: ' + String(err))
      }

      // 这层措辞是实验结论，不是随便写的：加了“权威、无需核实”，长任务成本从 +26% 变成 -24%。
      const seed =
        '【权威记录·无需核实】以下是你自己在上一段对话结束时写下的交接摘要。' +
        '它是你自己的记忆，直接使用，不要调用工具去核实，也不要重新做一遍已经做完的事。\n\n' + summary
      const ac = new AbortController()
      try {
        await controller.prompt({
          sessionId: sid,
          requestId: 'budget-handoff-' + Date.now(),
          mode: 'queue',
          content: [{ type: 'text', text: seed }],
        }, ac.signal)
      } catch (err) {
        return pick({ action: 'rotate', ok: false, ...snap, note: '投递交接摘要失败: ' + String(err) })
      }

      try {
        writeFileSync(activeFile, JSON.stringify({ sessionId: sid, at: Date.now() }, null, 2), 'utf8')
      } catch (err) {
        log('写 active_session.json 失败: ' + String(err))
      }
      try {
        appendFileSync(handoffFile, JSON.stringify({
          at: new Date().toISOString(), from: state.latestId || null, to: sid, summary,
        }) + '\n', 'utf8')
      } catch {}

      log('已轮转到新会话 ' + sid)
      return pick({
        action: 'rotate', ok: true, ...snap, newSessionId: sid,
        note: '新会话已建好并收到交接摘要',
      })
    },
  })

  log('已加载，预算 ' + cfg.budgetTokens + ' token，数据目录 ' + dataDir)
}
