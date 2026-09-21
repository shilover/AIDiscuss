# AIDiscuss

[English](README.en.md) | 中文

多模型代码方案讨论器。针对项目里的一个功能改动，组织多个 AI 角色基于**真实代码**讨论，产出一份可评审、可执行的落地方案。

支持两种调用方式，可按模型混用：

- **`cli`（默认）**  直接调用本机已登录的 CLI，**不消耗 API Key**
- **`api`**  调用云端 API（需要 Key）

## 模型端点

| 别名 | 通道 | 说明 |
| --- | --- | --- |
| `claude` | Claude Code CLI | `claude -p` |
| `deepseek` | dsh | `@deepseek-ai/dsh` |
| `gemini` | **agy** | Antigravity，默认 `gemini-3.8-flash-high` |
| `sonnet` | **agy** | `claude-sonnet-4-6` |
| `opus` | **agy** | `claude-opus-4-6-thinking` |
| `gpt` | **agy** | `gpt-oss-120b-medium` |

> `agy` 是 Antigravity CLI，一个 harness 里同时提供 Gemini / Claude / GPT 多个模型，
> 用 `--model` 切换。不需要单独安装各家 CLI。

## 角色分工

| 角色 | 默认模型 | 职责 |
| --- | --- | --- |
| scout | deepseek | 从文件列表挑相关文件 |
| architect | claude | 目标结构、接口/数据变更 |
| implementer | deepseek | 逐文件改动清单、兼容性 |
| skeptic | **gemini** | **只找反例**，不给方案、不下结论 |
| tester | deepseek | 测试用例与通过标准 |
| host | deepseek | 判断收敛、提炼未解决分歧 |
| judge | claude | 分歧裁决、反例判定、最终方案 |

设计要点：**提案方（claude/deepseek）与审查方（gemini）来自不同模型族**，避免自我认同；Gemini 只做窄任务，不做裁决。

角色分配可用 `.env` 覆盖：

```
ROLE_ARCHITECT=sonnet     # 改用 agy 的 Claude
ROLE_JUDGE=opus           # 让裁决更强
ROLE_SKEPTIC=gpt          # 改用 agy 的 GPT
```

## 快速开始

```powershell
cd D:\UEProject\AIDiscuss

.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # .venv 已建好
.\.venv\Scripts\python.exe -m app.cli doctor                    # 检查各端点
.\.venv\Scripts\python.exe -m app.cli discuss `
  --repo D:\path\to\project `
  --file requirement_demo.md
```

## 命令

| 命令 | 说明 |
| --- | --- |
| `discuss` | 执行讨论，产出方案 |
| `doctor` | 检查实际用到的端点是否可用 |
| `models` | 查看角色 → 模型 → 传输的分配 |

`discuss` 参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--repo` | `.` | 待讨论的代码仓库 |
| `--requirement` / `--file` |  | 需求描述 |
| `--rounds` | 3 | 最大讨论轮次（1~6） |
| `--out` | `sessions/` | 输出目录 |
| `--no-scout` | 关 | 跳过 LLM 选文件，只用关键词检索 |

## 工作流程

```
选文件(scout) → 材料包
      
第1轮：各角色独立出立场（互相不可见，防从众）
      
第2+轮：交叉质询 → 主持人判断收敛
      
裁决(judge) → 渲染 Markdown 方案 → 落盘
```

## 防幻觉设计

- 每条结论必须带 `文件:行号` 依据，**系统会校验文件与行号是否真实存在**
- 编造的引用会被剔除，对应结论标记 `未验证`
- 反例必须先被 judge 标为 `valid` 才进入最终方案
- 角色可申请补充文件（`request_context`），系统只满足一次，避免上下文无限膨胀

## 各 CLI 的适配差异

| | claude | dsh | agy |
| --- | --- | --- | --- |
| prompt 传递 | stdin | 临时文件 | 临时文件 + `-p` |
| system prompt | `--append-system-prompt` | 拼进正文 | 拼进正文 |
| 输出正文位置 | JSON 的 `result` | stdout | JSON 的 `response` |
| 选模型 | 默认 | 默认 | `--model` |
| 长度限制 | 无 | 无 | 无 |

### 两个必须踩过的坑

1. **`dsh` / `agy` 的 Windows 启动器会丢引号。** 直接调用会破坏 JSON 示例，
   因此 `dsh` 会自动定位到 `node .../lib/bin.js` 绕过 `.cmd` 包装。
2. **`dsh` / `agy` 都不读 stdin，且命令行有 32K 上限。**
   两者都能读本地文件，所以把完整 prompt 写进临时文件，只传一句「读取该文件并按要求作答」。

> `agy` 在 headless 下无法弹权限确认，读取临时文件需要
> `--dangerously-skip-permissions`；该参数与 `--mode plan`（只读模式）搭配使用，
> 保证它只能读、不能改。若你的环境不允许，可改用 api 传输。

## 输出产物

每次讨论写入 `sessions/<时间>_<需求摘要>/`：

| 文件 | 内容 |
| --- | --- |
| `plan.md` | **最终方案**（Markdown）：结论、决策记录、改动清单、测试、风险、反例判定、保留意见、讨论纪要 |
| `plan.html` | 同上内容的**单文件 HTML**：内联样式、明暗主题自适应、可离线打开、可直接转发评审 |
| `transcript.jsonl` | 全部发言（结构化），可回放 |
| `context.md` | 本次使用的代码材料包 |
| `judgment.json` | 裁决原始结果 |
| `meta.json` | 需求与仓库信息 |

## 需求文档

`requirements/_template.md` 是需求文档模板（只含格式，无具体内容）。用法：

```powershell
Copy-Item requirements\_template.md requirements\my-feature.md
```

填好后作为 `--file` 传给 `discuss`。模板的小节顺序对应多模型讨论的关注点，
其中「已经完成、不要重做的」和「已被否决、不要再提的路线」两节能显著减少
重复调研和绕路，建议认真填写。

`requirements/*` 默认被 git 忽略（只保留模板），因此真实需求文档不会被误提交。

## 作为 AI Agent Skill 使用

项目封装成了标准 Agent Skill（`SKILL.md` 格式），Claude Code 与 dsh 都能自动发现；
`agy` 无需单独安装 skill，它作为模型通道被 AIDiscuss 调用。

### 安装 / 卸载

```powershell
powershell -ExecutionPolicy Bypass -File skills\install.ps1              # 安装
powershell -ExecutionPolicy Bypass -File skills\install.ps1 -Uninstall  # 卸载
```

安装脚本会：

1. 写入 `skills/aidiscuss/runtime.conf`（记录项目根与解释器路径）
2. 在 `~/.agents/skills/aidiscuss` 与 `~/.claude/skills/aidiscuss` 建立 Junction，指向本仓库的 skill 源目录

因为是 Junction，改 `skills/aidiscuss/` 下的任何文件立即生效，无需重新安装。

### 调用

装好后在任意项目里对 agent 说「用 aidiscuss 讨论这个改动」即可，也可以直接跑：

```powershell
# 体检（可选，1~3 分钟）
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" doctor

# 后台启动讨论
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" start `
  --repo <目标仓库> --file <需求.md> --rounds 2 --out <输出目录>
```

Git Bash / WSL：

```bash
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" start \
  --repo /c/path/to/repo --file /c/path/to/req.md --rounds 2 --out /c/path/to/out
```

包装脚本的子命令：

| 子命令 | 说明 |
| --- | --- |
| `doctor` | 真实调用每个端点做连通性体检 |
| `models` | 查看角色 -> 模型 -> 传输的分配 |
| `config` | 打印解析到的项目根、解释器、配置文件路径（排障用） |
| `discuss` | 前台执行讨论 |
| `start` | 后台执行讨论并立即返回（必须给 `--out`） |

### 为什么用 `start`

完整讨论 `--rounds 3` 约 40~60 分钟，远超单条命令的超时。`start` 把讨论放到后台并立即返回：

- 日志：`<out>\run.out.log`、`<out>\run.err.log`（UTF-8）
- 完成标志：`<out>\plan.md` 出现
- 轮询时分次检查即可，不要用死循环阻塞

### 排障

| 现象 | 处理 |
| --- | --- |
| 找不到项目根 | 设置环境变量 `AIDISCUSS_HOME`，或重跑 `install.ps1` 刷新 `runtime.conf` |
| `agy` 找不到 | 项目会自动尝试 `~/.gemini\bin\agy.exe`，一般无需处理 |
| 某角色持续失败 | 改 `.env` 里的 `ROLE_*` 换别名 |
| 讨论中断 | 读 `<out>\run.err.log`，把错误原文反馈出来，不要反复重试 |

## Windows 环境注意事项

- 本机只有 **Windows PowerShell 5.1**，没有 `pwsh`；命令一律用 `powershell`，
  包装脚本内部在有 `pwsh` 时会优先使用它。
- `.ps1` 文件必须保存为 **UTF-8 with BOM**，否则 PS 5.1 会按 GBK 解析中文，直接语法报错。
- CLI 入口已强制 UTF-8 控制台输出；用 PowerShell 读日志或文件时建议加 `-Encoding UTF8`。
- `dsh` / `agy` 的 Windows 启动器会丢引号，项目内部已绕过（走 `node lib/bin.js` 与临时文件）。

## 目录结构

```
app/
  cli.py           命令行入口
  config.py        .env 加载 + 模型/角色注册表
  schemas.py       所有结构化数据（Pydantic）
  llm.py           统一调用层：CLI 与 API 两种传输
  prompts.py       角色 system prompt
  context.py       目录树/检索/片段读取/引用校验
  orchestrator.py  讨论编排
  render.py        Markdown + HTML 方案渲染
evals/
  smoke_test.py    无需任何模型调用的端到端冒烟测试
skills/
  install.ps1      安装/卸载 skill 到各 agent 的 skill 目录
  aidiscuss/
    SKILL.md       Agent Skill 主文件
    reference.md   参数、角色、产物、故障排查
    runtime.conf   项目根与解释器路径（install.ps1 生成，已 gitignore）
    scripts/       跨平台包装脚本（ps1 / cmd / sh）
requirements/
  _template.md     需求文档模板（真实需求文档默认被 gitignore）
AGENTS.md          项目级 agent 约定（PowerShell 5.1、UTF-8 BOM 等）
```
## 测试

```powershell
.\.venv\Scripts\python.exe evals\smoke_test.py
```

mock 掉模型调用，验证编排、引用校验、去重、渲染、落盘全链路，不消耗额度。

## 性能参考

走 CLI 时单次调用约 20~90 秒（CLI 有启动与探路开销）。一轮内 4 个角色并行，
`--rounds 3` 的完整讨论约 **40~60 分钟**。

## 已知限制

- 只出方案，不自动改代码
- 上下文按字符预算截断，超大仓库可能漏掉相关文件（调 `context.py` 的 `budget_chars`）
- `skeptic` 反例跨轮次去重按文本归一化，**语义相同但措辞不同**的反例不会合并，会各占一行（judge 虽会在理由里指出「与 CE-x 是同一问题」，但不会自动合并）
- 走 CLI 时无法控制 `temperature`
- `agy` 的 `gpt-oss-120b` 偶发服务端容量不足（503）

## 修复记录

### 1. 反例判定对不齐

**症状**：`_counterexample_rows()` 用 `ce.scenario.strip()` 精确匹配 judge 的判定，
但 judge 会用自己的话复述场景，两边文本不相等  实测 10 条反例**全部退化为 `unknown`**。

```
judgment.counterexample_verdicts[0].scenario
  = "render_html 抛出未捕获异常导致 run_discussion 中断、_persist 未执行"
skeptic.counterexamples[0].scenario
  = "render_html 渲染时抛异常，run_discussion 崩溃，已有产物丢失"
```

**修法**：`prompts.index_counterexamples()` 给每条反例编稳定编号 `CE-1`、`CE-2`，
`render_history()` 把编号渲染进发言（`- [CE-1] 场景: `），judge 按编号返回判定；
`_counterexample_rows()` 改为**编号优先、文本兜底**（旧数据没有 id 时仍能工作）。

**验证**：真实讨论中判定对齐从 `0/10` 提升到 `8/8`，判定有区分度（5 valid / 3 invalid）。

这轮讨论的 judge 自己就给出过这条判定 

> `scenario 转义后与 counterexample_verdicts 原始字典键失配导致 valid/invalid 全部退化为 unknown`  判定：**valid**

它预判了这个 bug，却因为同样的原因没能对上号。

### 2. 去重会丢掉后续轮次的判定

**症状**：`_counterexample_rows` 用 `scenario` 文本做 `seen` 去重。若同一反例在第二轮才被
judge 判定，而第一轮的同名记录先入为主，**带判定的新记录会被当作重复丢弃**。

**修法**：去重按**归一化文本**（折叠空白 + 小写）分组；同组内若后来的记录带了判定、
而先前的没有，就用新记录替换。

**顺带修的**：只要有任意一条判定带编号，就**禁用文本兜底**  否则同一 scenario 文本
会把判定误套给其它编号的记录。文本兜底现在只服务于无编号的历史数据。

### 3. 多行文本被浏览器折叠

**症状**：`_html_proposals` / `_html_claims` / `_html_bullets` 输出的自由文本里，
换行与缩进会被浏览器按默认 `white-space` 规则折叠成单行，多行方案和代码片段排版丢失。

这是由讨论里的 `CE-3` / `CE-7` 两条反例发现的（judge 判为 `valid`）。

**修法**：新增 `.freetext { white-space: pre-wrap; }`，三处自由文本容器都带上该类。

### 4. verdict 样式类是死代码

CSS 里定义了 `.verdict-valid` / `.verdict-invalid`，但 `_html_table()` 对所有单元格
一律只做 `_esc()`，两个类名从未被引用，判定只能以纯文本呈现。

**修法**：引入 `_Raw` 标记类型，`_cell()` 对已自行转义的片段直接注入；
判定列改为 `<span class="verdict-valid">valid</span>`。

### 5. 写盘异常保护不一致

`plan.html` 写入有 `try/except OSError`，但紧随其后的 `meta.json` 没有 
磁盘满或权限问题时，异常会从 `_persist` 抛出，留下一个缺文件的半成品目录。

**修法**：`_persist` 的所有写盘统一走 `_write_text()`，逐个隔离。

### 6. `_title()` 省略号失效

`cleaned[:limit] + ("" if len(cleaned) > limit else "")` 两个分支都是空串，
超过 60 字的标题被静默截断且无省略提示。这是早先用 PowerShell here-string
写文件时 `` 被吞掉导致的，已修复。