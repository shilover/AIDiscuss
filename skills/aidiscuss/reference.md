# AIDiscuss 参考手册

配合 [SKILL.md](SKILL.md) 使用。这里放详细参数、角色表、产物说明和故障排查。

## 工作流程

```
选文件(scout)  材料包
第1轮：各角色独立出立场（互相不可见，防从众）
第2+轮：交叉质询 + 主持人判断收敛
裁决(judge)  渲染 Markdown 方案  落盘
```

## 命令

| 命令 | 说明 |
| --- | --- |
| `discuss` | 执行讨论，产出方案 |
| `start` | 同 `discuss`，但后台运行并立即返回（skill 包装层提供） |
| `doctor` | 真实调用每个端点，检查是否可用 |
| `models` | 查看角色  模型  传输的分配 |
| `config` | 打印 skill 解析到的项目根、解释器与配置文件路径（排障用） |

`discuss` 参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--repo` | `.` | 待讨论的代码仓库路径 |
| `--requirement` / `-r` | | 需求文字 |
| `--file` / `-f` | | 从文件读取需求（UTF-8） |
| `--rounds` | 3 | 最大讨论轮次，1~6 |
| `--out` | `sessions/` | 输出目录 |
| `--no-scout` | 开 | 跳过 LLM 选文件，只用关键词检索 |

## 角色与模型

| 角色 | 默认模型 | 职责 |
| --- | --- | --- |
| scout | deepseek | 从文件列表挑相关文件 |
| architect | claude | 目标结构、接口、数据变更 |
| implementer | deepseek | 逐文件改动清单、兼容性 |
| skeptic | gemini (agy) | **只找反例**，不给方案、不下结论 |
| tester | deepseek | 测试用例与通过标准 |
| host | deepseek | 判断收敛、提点未解决分歧 |
| judge | claude | 分歧裁决、反例判定、最终方案 |

设计要点：提案方（claude/deepseek）与审查方（gemini）来自不同模型族，避免自我认同；
Gemini 只做窄任务、不做裁决。

### 模型端点

| 别名 | 通道 | 说明 |
| --- | --- | --- |
| `claude` | Claude Code CLI | `claude -p` |
| `deepseek` | dsh | `@deepseek-ai/dsh --profile headless` |
| `gemini` | agy | Antigravity，默认 `gemini-3.8-flash-high` |
| `sonnet` | agy | `claude-sonnet-4-6` |
| `opus` | agy | `claude-opus-4-6-thinking` |
| `gpt` | agy | `gpt-oss-120b-medium` |

`agy` 是 Antigravity CLI，一个 harness 里同时提供 Gemini / Claude / GPT，用 `--model` 切换。
若 `agy` 不在 PATH，`app/config.py::resolve_cli` 会自动尝试 `~/.gemini/bin/agy.exe`。

角色分配可用 `.env` 覆盖：

```
ROLE_ARCHITECT=sonnet     # 改用 agy 的 Claude
ROLE_JUDGE=opus           # 让裁决更强
ROLE_SKEPTIC=gpt          # 改用 agy 的 GPT
```

## 输出产物

写入 `<out>/`（默认 `D:\UEProject\AIDiscuss\sessions\<时间>_<需求摘要>\`）：

| 文件 | 内容 |
| --- | --- |
| `plan.md` | **最终方案**：结论、决策记录、改动清单、测试、风险、反例判定、保留意见、讨论纪要 |
| `plan.html` | 同上的单文件 HTML，内联样式、明暗主题自适应、可离线打开、可直接转发评审 |
| `transcript.jsonl` | 全部发言（结构化），可回放 |
| `context.md` | 本次使用的代码材料包 |
| `judgment.json` | 裁决原始结果 |
| `meta.json` | 需求与仓库信息 |

`start` 子命令另外写 `run.out.log` / `run.err.log`。

## CLI 适配差异

| | claude | dsh | agy |
| --- | --- | --- | --- |
| prompt 传递 | stdin | 临时文件 | 临时文件 + `-p` |
| system prompt | `--append-system-prompt` | 拼进正文 | 拼进正文 |
| 输出正文位置 | JSON 的 `result` | stdout | JSON 的 `response` |
| 选模型 | 默认 | 默认 | `--model` |

两个必须知道的坑：

1. **`dsh` / `agy` 的 Windows 启动器会丢引号**，直接调用会破坏 JSON 示例。
   因此 `dsh` 会自动定位到 `node .../lib/bin.js` 绕过 `.cmd` 包装。
2. **`dsh` / `agy` 都不读 stdin，且命令行有 32K 上限**，所以把完整 prompt 写进临时文件，
   只传一句读取该文件并按它要求作答。`agy` 在 headless 下读取临时文件需要
   `--dangerously-skip-permissions`，并与 `--mode plan`（只读模式）搭配，保证只读不改。

## 防幻觉设计

- 每条结论必须带 `文件:行号` 依据，系统会**校验文件与行号是否真实存在**。
- 编造的引用会被剔除，对应结论标记 `未验证`。
- 反例必须先被 judge 标为 `valid` 才进入最终方案。
- 角色可申请补充文件（`request_context`），系统只满足一次，避免上下文无限膨胀。

## 性能参考

走 CLI 时单次调用约 20~90 秒（CLI 有启动与探路开销）。一轮内 4 个角色并行，
`--rounds 3` 的完整讨论约 **40~60 分钟**。所以 skill 默认用 `start` 后台跑。

## 已知限制

- 只出方案，不自动改代码。
- 上下文按字符预算截断，超大仓库可能漏掉相关文件（见 `app/context.py` 的 `budget_chars`）。
- `skeptic` 反例去重按文本归一化，语义相同但措辞不同的反例不会合并。
- 走 CLI 时无法控制 `temperature`。
- `agy` 的 `gpt-oss-120b` 偶发服务端 503。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `doctor` 报 `找不到命令` | 确认对应 CLI 在 PATH；`agy` 会自动找 `~/.gemini\bin` |
| 缺少 API Key | 检查 `D:\UEProject\AIDiscuss\.env`，或把 `*_TRANSPORT` 改成 `cli` |
| 角色配置引用了未知模型 | `.env` 里 `ROLE_*` 只能填上表中的别名 |
| 讨论超时/中断 | 看 `<out>\run.err.log`；把原文交给用户，别反复重试 |
| 中文乱码 | CLI 入口已强制 UTF-8；仍乱码时设 `NO_COLOR=1` 并用 `-Encoding UTF8` 读文件 |

## 在 bash（Git Bash / WSL）里调用

```bash
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" doctor
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" discuss --repo /d/path/to/repo --file ./req.md --out ./out
```

`aidiscuss.sh` 会自动用项目 `.venv` 的 Python；`start` 子命令会转交给 PowerShell 版实现。