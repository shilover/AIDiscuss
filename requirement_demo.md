# 需求：讨论结果导出 HTML

## 背景
当前 `app/render.py` 只提供 `render_markdown()`，产出 Markdown 方案；
`app/cli.py` 的 `discuss` 命令在结束时调用 `_persist()` 把结果写到 `sessions/<id>/plan.md`。

## 目标
增加把讨论结果导出为单文件 HTML 的能力，便于直接发给同事评审。

## 约束
- 不能破坏现有 Markdown 输出
- HTML 需要自带样式，单文件可离线打开
- 不引入新的第三方依赖