# 插件设计

## 目标 / 非目标

**目标**：让"会话变长 → 每步变贵"这条曲线变平。做法不是压缩历史，是**到点换一个小的**，
并把上文的结论以**静态、权威、够用**的形式带过去。

**非目标**：
- 不做摘要。摘要质量决定下一段成本，那是模型用 `summary` 参数自己写的。
- 不碰模型调用、不改系统提示、不代理请求。
- 不自动触发轮转。什么时候切，应该由知道任务边界的那一方决定。

## 数据流

```text
session/event(assistant/message)
        │  usage: inputTokens / cacheReadTokens / outputTokens
        ▼
   state.json ──► snapshot() ──► context_budget(status)
        │
        │  lastCtx > budgetTokens
        ▼
   日志行（budget.log，每个会话只记一次"超预算"）
        │
        └─► context_budget(rotate, summary)
                │
                ├─ sessionController.create({ cwd, agentPreset? })
                ├─ workspaceRegistry.create(cwd).attachSession(id)   ← 失败只记日志
                ├─ sessionController.prompt({ sessionId, mode:'queue', content:[权威摘要] })
                ├─ active_session.json   ← 外部桥接读它，跟着切会话
                └─ handoffs.jsonl        ← 审计：什么时候、从哪个会话、切到哪、带了什么摘要
```

## 记账口径

```js
等价成本 = miss + cache * cachePriceRatio + out
lastCtx  = inputTokens + cacheReadTokens        // 一步的上下文规模
```

`cachePriceRatio` 是唯一和价格相关的旋钮，默认 0.1。换模型/换价目表就改它，
历史数据不做回溯换算——`state.json` 里只有原始 token 数，等价成本是每次现算的。

## 三条不变量

**① 前缀稳定。** 系统提示、工具定义、摘要文件每一步完全一致：不塞时间戳、不塞随机 id、不塞"现在是几点"。
任何变动都会让缓存前缀失效，一步的差价能把整个实验省下来的钱吃光。

**② 注入必须静态 + 有可信度标签。** 见 [`docs/FINDINGS.md`](FINDINGS.md) 实验三。
自然语言描述「你去看看……」会诱导模型自己去翻，成本从 −24% 变成 +26%。

**③ 摘要够用就行。** 记忆多 13 倍，成本贵 2.6 倍，正确率一样（实验一 C 组）。

## 踩过的坑

**cwd 落错目录。** 最早版本不传 `cwd`，`controller.create` 就用了宿主默认目录——
轮转出来的会话落在 profile 目录，不在工作区。表现是"新会话能说话，但工具全在错的目录里跑"，
排查花了半天。现在 `cwd` 没配就直接拒绝轮转，并给出可读的 `note`。

**patch 必须用 `insert:`。** 直接写 `- id: dsh-context-budget` 会被 loader 当成"对未知目标的补丁"静默跳过，
日志里什么都没有，插件就是不加载。

**Windows 控制台是 GBK。** 日志里写中文会乱码甚至抛错。插件里所有写文件都显式 `encoding: 'utf8'`。

**摘要太短等于没摘要。** 少于 40 字直接拒绝——"之前讨论了很多"这种交接，下一段会重新做一遍，比不轮转更贵。

## 接口

只依赖宿主已有服务，不 import DSH 内部模块：

```js
ctx.tools.register({ name, description, parameters, output, execute })  // 工具
ctx.on('session/event', (session, event) => {})                        // usage 观测
ctx.get('sessionController').create({ cwd, agentPreset })              // 开会话
ctx.get('sessionController').prompt({ sessionId, requestId, mode, content }, signal)
ctx.get('workspaceRegistry').create(cwd).attachSession(sessionId)      // 可选
```

## 下一步

1. **记忆进系统级前缀。** 现在摘要当对话注入，模型仍会概率性去核实（D 组 1、1、2、2、4、5 步）。
   写进系统提示/固定前缀文件，才是稳定的。
2. **摘要由插件起草。** 用便宜模型把最老的 N 轮压成固定格式的摘要，人只审不写。
3. **预算自适应。** 不是固定 20K：观测"最近几轮有没有因为上下文不足多走步"，动态上下调。
4. **每步落地价的真实账单对照。** 现在全是 token 推算，没有对过实际账单。
