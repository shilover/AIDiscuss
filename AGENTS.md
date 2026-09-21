# AGENTS.md

本仓库是 **AIDiscuss**  多模型代码方案讨论器（Python 3.11+ / typer / pydantic）。

## 常用命令

```powershell
.\.venv\Scripts\python.exe evals\smoke_test.py        # 冒烟测试，不调用模型
.\.venv\Scripts\python.exe -m app.cli doctor          # 真实调用各端点体检（1~3 分钟）
.\.venv\Scripts\python.exe -m app.cli models          # 角色 -> 模型分配
.\.venv\Scripts\python.exe -m app.cli discuss --repo <仓库> --file <需求.md>
powershell -ExecutionPolicy Bypass -File skills\install.ps1   # 安装 skill
```

## 约定

- 本机是 **Windows PowerShell 5.1（没有 pwsh）**；`.ps1` 文件必须存为 **UTF-8 with BOM**，
  否则中文会被按 GBK 解析导致语法错误。读写文本用 `-Encoding UTF8`。
- CLI 入口强制 UTF-8 控制台输出。
- `.env` 控制模型传输与角色分配，参考 `.env.example`；`ROLE_*` 只能填注册表里的别名。
- 改动 CLI / 编排 / 渲染后跑一次 `evals\smoke_test.py`。
- 本工具只产出方案，不改目标仓库的代码。