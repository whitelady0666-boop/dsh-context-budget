// 冒烟测试：不开 DSH，用假的 ctx 把插件跑一遍。
//   node test/smoke.mjs
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { apply, name } from '../index.js'

const dir = mkdtempSync(join(tmpdir(), 'ctxbudget-'))
const handlers = {}
let tool = null
let created = []
let prompted = []

const ctx = {
  logger: { info: () => {} },
  on: (evt, fn) => { handlers[evt] = fn },
  get: (svc) => ({
    sessionController: {
      create: async (opts) => {
        created.push(opts)
        return { sessionId: 'session-test-' + created.length }
      },
      prompt: async (opts) => { prompted.push(opts) },
    },
  })[svc],
  tools: { register: (def) => { tool = def } },
}

apply(ctx, { budgetTokens: 20000, dataDir: dir, cwd: 'E:/fake-workspace' })

assert.equal(name, 'dsh-context-budget')
assert.ok(tool, '应该注册 context_budget 工具')
assert.ok(handlers['session/event'], '应该订阅 session/event')

const use = { inputTokens: 1500, cacheReadTokens: 18000, outputTokens: 200 }
handlers['session/event']({ id: 'session-a' }, { type: 'assistant/message', data: { usage: use } })
handlers['session/event']({ id: 'session-a' }, { type: 'assistant/message', data: { usage: use } })

// 1. status：两步之后，上下文 = 1500 + 18000
let r = await tool.execute({ action: 'status' })
assert.equal(r.ok, true)
assert.equal(r.steps, 2)
assert.equal(r.context, 19500)
assert.equal(r.overBudget, false)
// 等价成本 = (1500 + 18000×0.1 + 200) × 2
assert.equal(r.equivalentCost, (1500 + 1800 + 200) * 2)

// 2. 摘要太短 → 拒绝
r = await tool.execute({ action: 'rotate', summary: '太短' })
assert.equal(r.ok, false)
assert.match(r.note, /摘要太短/)

// 3. 摘要够长 → 开新会话、投递、写 active_session.json
const summary = '【上一段】做完了 A 和 B，结论是 C；下一步要验证 D，别重做 A。实测数据在 bench/data 里，' +
  '轮转后先看 active_session.json 指向哪个会话，再确认 cwd 落在工作区而不是 profile 目录。'
r = await tool.execute({ action: 'rotate', summary })
assert.equal(r.ok, true)
assert.equal(r.newSessionId, 'session-test-1')
assert.equal(created[0].cwd, 'E:/fake-workspace')
assert.match(prompted[0].content[0].text, /^【权威记录·无需核实】/)
assert.ok(prompted[0].content[0].text.includes(summary))

const active = JSON.parse(readFileSync(join(dir, 'active_session.json'), 'utf8'))
assert.equal(active.sessionId, 'session-test-1')
assert.ok(existsSync(join(dir, 'state.json')))
assert.ok(readFileSync(join(dir, 'handoffs.jsonl'), 'utf8').includes('session-test-1'))

// 4. 没配 cwd → 拒绝轮转（不要落到宿主默认目录去）
const dir2 = mkdtempSync(join(tmpdir(), 'ctxbudget-'))
let tool2 = null
apply({ ...ctx, tools: { register: (d) => { tool2 = d } } }, { dataDir: dir2 })
r = await tool2.execute({ action: 'rotate', summary })
assert.equal(r.ok, false)
assert.match(r.note, /cwd/)

// 5. over 标记：重启/改预算之后，要能重新记一条“超预算”
//    曾经踩过的坑：over 被持久化，重启后内存里仍是上一次的 true，
//    于是会话涨到 6 倍预算也不再写日志。
const dir3 = mkdtempSync(join(tmpdir(), 'ctxbudget-'))
const mk = (budget) => {
  const h = {}
  apply({ ...ctx, on: (evt, fn) => { h[evt] = fn } }, { budgetTokens: budget, dataDir: dir3, cwd: 'E:/fake-workspace' })
  return h['session/event']
}
const logText = () => readFileSync(join(dir3, 'budget.log'), 'utf8')
const countOver = () => (logText().match(/超过预算/g) || []).length
const fire = (handler, id, ctxTokens) =>
  handler({ id }, { type: 'assistant/message', data: { usage: { inputTokens: 10, cacheReadTokens: ctxTokens - 10, outputTokens: 1 } } })

let handler = mk(20000)
fire(handler, 'session-b', 25000)
assert.equal(countOver(), 1, '第一次超预算要记日志')

handler = mk(40000)                       // 改预算重启，25000 已经在预算内
assert.equal(countOver(), 1, '预算放大后不该补记')
fire(handler, 'session-b', 50000)         // 再涨上去
assert.equal(countOver(), 2, '重启后再次超预算必须能记 —— 这条就是那个 bug 的回归测试')

handler = mk(40000)                       // 已经超预算的状态下重启
assert.equal(countOver(), 3, '启动时就已超预算，要提示一次')
assert.match(logText(), /启动检查/)

console.log('smoke OK —— 记账 / 状态 / 拒绝 / 轮转 / 交接 / over 标记都通过')
