---
name: aidiscuss
description: 多模型代码方案讨论器。针对某个代码仓库的一次功能改动，组织多个 AI 角色（scout/architect/implementer/skeptic/tester/host/judge）基于仓库真实代码独立出立场、交叉质询，产出可评审可执行的落地方案（plan.md + 单文件 plan.html）。当用户要求出方案 / 方案讨论 / 多模型评审 / 多个模型一起讨论 / 动手前先评估风险时使用。支持 claude、dsh、agy 三个本机 CLI 通道，不消耗 API Key。
whenToUse: 用户要求对一个代码改动做方案设计、技术评审、风险挑刺，或希望在动手写代码前拿到经过反例检验的实施计划时。
---

# AIDiscuss  多模型代码方案讨论器

把一次代码改动交给多个 AI 角色讨论，基于仓库里的**真实代码**产出可评审、可执行的方案。
角色：`scout` 选文件、`architect` 定结构、`implementer` 出改动清单、`skeptic` 只找反例、
`tester` 定测试标准、`host` 主持收敛、`judge` 裁决并出最终方案。

Skill 已安装在本机（项目源目录 `D:\UEProject\AIDiscuss\skills\aidiscuss`）：

- `C:\Users\<你>\.agents\skills\aidiscuss`（dsh 与 Claude Code 共用）
- `C:\Users\<你>\.claude\skills\aidiscuss`（Claude Code）

## 何时用 / 何时不用

**适合**：用户说先出个方案多模型讨论一下让几个模型一起评审动手前帮我评估风险；
改动跨多个文件、涉及兼容性或数据迁移，值得先讨论再写代码。

**不适合**：明确的单点小修（直接改更快）；用户只想让当前 agent 直接实现。

## 使用流程

### 1. 体检（每个会话第一次调用时做一次）

Windows PowerShell（本机没有 pwsh，用 `powershell`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" doctor
```

Git Bash / WSL：

```bash
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" doctor
```

`doctor` 会真实调用每个端点，约 1~3 分钟。只想知道角色分配用 `models`（秒级）。

### 2. 启动讨论

**关键：完整讨论很慢**`--rounds 3` 实测约 40~60 分钟，远超单条命令的超时。
所以默认用 `start` 子命令**后台启动**，然后轮询产物。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" start `
  --repo "C:\绝对路径\目标仓库" `
  --file "C:\绝对路径\需求.md" `
  --rounds 2 `
  --out "C:\绝对路径\输出目录"
```

| 参数 | 说明 |
| --- | --- |
| `--repo` | **被讨论的代码仓库**（不是 AIDiscuss 自己） |
| `--file` / `--requirement` | 需求描述：文件路径，或直接给文字 |
| `--rounds` | 讨论轮次，默认 3；小改动用 1~2 |
| `--out` | 输出目录；**`start` 必填**，产物落在这里 |
| `--no-scout` | 跳过 LLM 选文件阶段，只用关键词检索 |

Git Bash / WSL 下换成 `sh` 版脚本即可（`start` 会自动转交给 PowerShell 实现）：

```bash
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" start \
  --repo "/c/path/to/repo" \
  --file "/c/path/to/req.md" \
  --rounds 2 \
  --out "/c/path/to/out"
```

`start` 立即返回 PID 与输出目录，日志写到 `<out>\run.out.log` 和 `<out>\run.err.log`。

前台直接跑（仅适合 `--rounds 1`、并确认不会超时）：

```
... aidiscuss.ps1 discuss --repo <仓库> --file <需求.md> --rounds 1 --out <目录>
```

### 3. 等待与汇报

启动后先向用户报告：输出目录、预计耗时、可以先去忙别的。
然后**分次轮询**，每次调用只查一次状态，不要用死循环阻塞：

```powershell
Test-Path "<out>\plan.md"                    # True 即完成
Get-Content "<out>\run.err.log" -Encoding UTF8 -Tail 20   # 看是否有报错
Get-Content "<out>\run.out.log" -Encoding UTF8 -Tail 20   # 看进度
```

（日志是 UTF-8；Git Bash 下直接 `tail` 即可。）

完成后读取 `<out>\plan.md`，用中文向用户汇报：

1. 一句话结论
2. 决策记录与关键取舍
3. 改动清单（`文件:符号`）
4. 反例判定：哪些反例被判 `valid`、方案如何应对
5. 未解决分歧与风险
6. 产物路径：`plan.md`、`plan.html`（HTML 可直接打开或转发评审）

## 失败处理

- **某端点 FAIL**：`agy` 不在 PATH 时项目会自动去 `~/.gemini\bin` 找，通常无需处理。
- **某角色一直失败**：编辑 `D:\UEProject\AIDiscuss\.env` 换别名后重试，例如
  `ROLE_SKEPTIC=claude`（与提案方仍是不同模型族）、`ROLE_ARCHITECT=sonnet`。
- **讨论中途报错**：读 `<out>\run.err.log`，把错误原文交给用户，**不要自行反复重试**。
- 本工具**只出方案、不改代码**。用户要落地时，另起一个实现任务。

详细参数、角色/模型表、输出文件、已知限制见 [reference.md](reference.md)。