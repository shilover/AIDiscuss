# AIDiscuss

[中文](README.md) | English

A multi-model design-discussion tool. Given a single feature change in a project, it
organizes several AI roles to discuss it against the **real code**, then produces a
reviewable, actionable implementation plan.

Two transports are supported and can be mixed per model:

- **`cli` (default)** - calls the CLIs already logged in on this machine, **no API key required**
- **`api`** - calls cloud APIs (requires keys)

## Model endpoints

| Alias | Channel | Notes |
| --- | --- | --- |
| `claude` | Claude Code CLI | `claude -p` |
| `deepseek` | dsh | `@deepseek-ai/dsh` |
| `gemini` | **agy** | Antigravity, defaults to `gemini-3.8-flash-high` |
| `sonnet` | **agy** | `claude-sonnet-4-6` |
| `opus` | **agy** | `claude-opus-4-6-thinking` |
| `gpt` | **agy** | `gpt-oss-120b-medium` |

> `agy` is the Antigravity CLI: one harness exposing Gemini / Claude / GPT behind a
> `--model` switch. No separate per-vendor CLI installs are needed.

## Roles

| Role | Default model | Responsibility |
| --- | --- | --- |
| scout | deepseek | pick relevant files from the file list |
| architect | claude | target structure, interfaces and data changes |
| implementer | deepseek | per-file change list, compatibility |
| skeptic | **gemini** | **counterexamples only** - proposes nothing, concludes nothing |
| tester | deepseek | test cases and pass criteria |
| host | deepseek | decide convergence, surface unresolved disagreements |
| judge | claude | adjudicate disagreements, rule on counterexamples, final plan |

Design intent: **proposers (claude/deepseek) and the reviewer (gemini) come from different
model families**, which avoids self-agreement; Gemini only does narrow work and never
adjudicates.

Role assignment can be overridden in `.env`:

```
ROLE_ARCHITECT=sonnet     # use agy's Claude
ROLE_JUDGE=opus           # stronger adjudication
ROLE_SKEPTIC=gpt          # use agy's GPT
```

## Quick start

```powershell
cd D:\UEProject\AIDiscuss

.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # .venv already exists
.\.venv\Scripts\python.exe -m app.cli doctor                    # check endpoints
.\.venv\Scripts\python.exe -m app.cli discuss `
  --repo D:\path\to\project `
  --file requirement_demo.md
```

## Commands

| Command | Description |
| --- | --- |
| `discuss` | run a discussion and produce the plan |
| `doctor` | check whether the endpoints actually used are available |
| `models` | show the role -> model -> transport assignment |

`discuss` options:

| Option | Default | Description |
| --- | --- | --- |
| `--repo` | `.` | repository under discussion |
| `--requirement` / `--file` | | requirement text |
| `--rounds` | 3 | max discussion rounds (1-6) |
| `--out` | `sessions/` | output directory |
| `--no-scout` | off | skip LLM file selection, use keyword retrieval only |

## Workflow

```
file selection (scout) -> context pack
round 1: each role states a position independently (mutually invisible, avoids bandwagoning)
round 2+: cross-examination -> host judges convergence
verdict (judge) -> render Markdown plan -> persist to disk
```

## Anti-hallucination design

- Every claim must cite a `file:line`; **the system verifies that both the file and the line exist**
- Fabricated citations are dropped and the affected claim is marked `unverified`
- A counterexample only reaches the final plan after the judge rules it `valid`
- Roles may request extra files (`request_context`), which the system satisfies only once,
  so context cannot grow without bound

## CLI transport differences

| | claude | dsh | agy |
| --- | --- | --- | --- |
| prompt delivery | stdin | temp file | temp file + `-p` |
| system prompt | `--append-system-prompt` | folded into the body | folded into the body |
| result location | JSON `result` | stdout | JSON `response` |
| model selection | default | default | `--model` |
| length limit | none | none | none |

### Two traps worth knowing

1. **The Windows launchers of `dsh` / `agy` drop quotes.** Calling them directly corrupts
   embedded JSON examples, so `dsh` is resolved to `node .../lib/bin.js` to bypass the `.cmd` wrapper.
2. **Neither `dsh` nor `agy` reads stdin, and the command line is capped at 32K.**
   Both can read local files, so the full prompt is written to a temp file and only a short
   "read that file and answer as instructed" is passed on the command line.

> Under headless mode `agy` cannot show permission prompts; reading the temp file requires
> `--dangerously-skip-permissions`. It is combined with `--mode plan` (read-only) so agy can
> read but never write. If your environment disallows this, switch to api transport.

## Output artifacts

Each discussion writes to `sessions/<timestamp>_<requirement-slug>/`:

| File | Contents |
| --- | --- |
| `plan.md` | **the final plan** (Markdown): conclusion, decision log, change list, tests, risks, counterexample verdicts, reservations, discussion minutes |
| `plan.html` | the same content as a **single-file HTML**: inline styles, light/dark aware, opens offline, ready to forward for review |
| `transcript.jsonl` | every utterance (structured), replayable |
| `context.md` | the code context pack used for this run |
| `judgment.json` | raw verdict output |
| `meta.json` | requirement and repository info |

## Requirement documents

`requirements/_template.md` is the requirement template (format only, no content). Usage:

```powershell
Copy-Item requirements\_template.md requirements\my-feature.md
```

Fill it in and pass it to `discuss` via `--file`. The section order mirrors what the
multi-model discussion cares about; the "already done, do not redo" and "already rejected,
do not raise again" sections in particular cut down repeated research and detours, so fill
them in carefully.

`requirements/*` is git-ignored by default (only the template is kept), so real requirement
documents are never accidentally committed.

## Using as an AI Agent Skill

The project is packaged as a standard Agent Skill (`SKILL.md` format) that both Claude Code
and dsh discover automatically; `agy` needs no skill of its own because it is called as a
model transport.

### Install / uninstall

```powershell
powershell -ExecutionPolicy Bypass -File skills\install.ps1              # install
powershell -ExecutionPolicy Bypass -File skills\install.ps1 -Uninstall   # uninstall
```

The installer will:

1. write `skills/aidiscuss/runtime.conf` (project root and interpreter path)
2. create junctions at `~/.agents/skills/aidiscuss` and `~/.claude/skills/aidiscuss`
   pointing back at this repository's skill source directory

Because they are junctions, any edit under `skills/aidiscuss/` takes effect immediately with
no reinstall.

### Invocation

Once installed, just tell your agent "use aidiscuss to discuss this change" in any project,
or call the wrapper directly:

```powershell
# health check (optional, 1-3 min)
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" doctor

# start a discussion in the background
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" start `
  --repo <target-repo> --file <requirement.md> --rounds 2 --out <output-dir>
```

Git Bash / WSL:

```bash
"$HOME/.agents/skills/aidiscuss/scripts/aidiscuss.sh" start \
  --repo /c/path/to/repo --file /c/path/to/req.md --rounds 2 --out /c/path/to/out
```

Wrapper subcommands:

| Subcommand | Description |
| --- | --- |
| `doctor` | really call every endpoint as a connectivity check |
| `models` | show the role -> model -> transport assignment |
| `config` | print the resolved project root, interpreter and config path (for debugging) |
| `discuss` | run a discussion in the foreground |
| `start` | run a discussion in the background and return immediately (requires `--out`) |

### Why `start`

A full `--rounds 3` discussion takes about 40-60 minutes, far beyond a single command
timeout. `start` puts it in the background and returns immediately:

- logs: `<out>\run.out.log`, `<out>\run.err.log` (UTF-8)
- completion marker: `<out>\plan.md` appears
- poll in separate calls; never block in a loop

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| project root not found | set `AIDISCUSS_HOME`, or re-run `install.ps1` to refresh `runtime.conf` |
| `agy` not found | the project automatically tries `~/.gemini\bin\agy.exe`, usually nothing to do |
| one role keeps failing | change its `ROLE_*` alias in `.env` |
| discussion aborted | read `<out>\run.err.log` and report the raw error; do not retry blindly |

## Windows environment notes

- This machine has only **Windows PowerShell 5.1**, no `pwsh`; use `powershell` in commands.
  The wrappers still prefer `pwsh` when it exists.
- `.ps1` files must be saved as **UTF-8 with BOM**, otherwise PS 5.1 parses Chinese text as
  GBK and fails with a syntax error.
- The CLI entry point forces UTF-8 console output; when reading logs or files with
  PowerShell, prefer `-Encoding UTF8`.
- The Windows launchers of `dsh` / `agy` drop quotes; the project already routes around
  that (via `node lib/bin.js` and temp files).

## Repository layout

```
app/
  cli.py           CLI entry point
  config.py        .env loading + model/role registry
  schemas.py       all structured data (Pydantic)
  llm.py           unified call layer: CLI and API transports
  prompts.py       role system prompts
  context.py       directory tree / retrieval / snippet reading / citation validation
  orchestrator.py  discussion orchestration
  render.py        Markdown + HTML plan rendering
evals/
  smoke_test.py    end-to-end smoke test that needs no model calls
skills/
  install.ps1      install/uninstall the skill into each agent's skill directory
  aidiscuss/
    SKILL.md       Agent Skill entry
    reference.md   options, roles, artifacts, troubleshooting
    runtime.conf   project root and interpreter path (generated, git-ignored)
    scripts/       cross-platform wrappers (ps1 / cmd / sh)
requirements/
  _template.md     requirement template (real requirement docs are git-ignored)
AGENTS.md          project-level agent conventions (PowerShell 5.1, UTF-8 BOM, ...)
```

## Tests

```powershell
.\.venv\Scripts\python.exe evals\smoke_test.py
```

Model calls are mocked; it verifies orchestration, citation validation, deduplication,
rendering and persistence end to end without spending any quota.

## Performance

Over CLI transports a single call takes roughly 20-90 seconds (startup and exploration
overhead). Four roles run in parallel within a round, so a full `--rounds 3` discussion
takes about **40-60 minutes**.

## Known limitations

- Produces plans only; it never edits code
- Context is truncated by a character budget, so very large repos may drop relevant files
  (tune `budget_chars` in `context.py`)
- Counterexamples are deduplicated across rounds by normalized text; **semantically identical
  but differently worded** counterexamples are not merged and each takes a row (the judge
  points out "same issue as CE-x" in its rationale but does not merge them)
- `temperature` cannot be controlled over CLI transports
- `agy`'s `gpt-oss-120b` occasionally returns 503 (server-side capacity)

## Fix log

### 1. Counterexample verdicts never matched

**Symptom**: `_counterexample_rows()` matched the judge's verdicts against
`ce.scenario.strip()` exactly, but the judge paraphrases the scenario, so the two strings
never matched - in a real run all 10 counterexamples **degraded to `unknown`**.

```
judgment.counterexample_verdicts[0].scenario
  = "render_html 抛出未捕获异常导致 run_discussion 中断、_persist 未执行"
skeptic.counterexamples[0].scenario
  = "render_html 渲染时抛异常，run_discussion 崩溃，已有产物丢失"
```

**Fix**: `prompts.index_counterexamples()` assigns each counterexample a stable id
(`CE-1`, `CE-2`, ...), `render_history()` renders that id into the transcript
(`- [CE-1] scenario: `), the judge returns verdicts by id, and `_counterexample_rows()`
became **id-first with a text fallback** (still works for old data without ids).

**Verification**: in a real discussion, alignment went from `0/10` to `8/8`, with a real
split (5 valid / 3 invalid).

The judge in that very discussion had produced this verdict:

> `scenario 转义后与 counterexample_verdicts 原始字典键失配导致 valid/invalid 全部退化为 unknown` - verdict: **valid**

It predicted the bug but, for the same reason, could not match it up.

### 2. Deduplication dropped later verdicts

**Symptom**: `_counterexample_rows` deduplicated on the `scenario` text. If the same
counterexample was only ruled on in round two while an earlier record with the same text had
already won, **the new record carrying the verdict was discarded as a duplicate**.

**Fix**: deduplicate by **normalized text** (collapsed whitespace + lowercase); within a
group, if a later record carries a verdict and the earlier one does not, replace it.

**Fixed along the way**: once any verdict carries an id, the **text fallback is disabled**,
otherwise the same scenario text could be attributed to a different id. The text fallback now
only serves historical data without ids.

### 3. Multi-line text collapsed by the browser

**Symptom**: free text emitted by `_html_proposals` / `_html_claims` / `_html_bullets`
had its newlines and indentation collapsed into a single line by the browser's default
`white-space` rules, destroying the layout of multi-line plans and code snippets.

This was found by counterexamples `CE-3` / `CE-7` from that discussion (judged `valid`).

**Fix**: added `.freetext { white-space: pre-wrap; }` and applied the class to all three
free-text containers.

### 4. The verdict style classes were dead code

CSS defined `.verdict-valid` / `.verdict-invalid`, but `_html_table()` ran every cell
through `_esc()` only, so the two class names were never referenced and verdicts could only
be rendered as plain text.

**Fix**: introduced a `_Raw` marker type so `_cell()` can inject already-escaped fragments
directly, and changed the verdict column to `<span class="verdict-valid">valid</span>`.

### 5. Inconsistent write-failure protection

Writing `plan.html` had a `try/except OSError`, but the `meta.json` write right after it did
not - on a full disk or permission error the exception escaped `_persist`, leaving a
half-written session directory.

**Fix**: all writes in `_persist` go through `_write_text()` and are isolated one by one.

### 6. `_title()` never appended its ellipsis

`cleaned[:limit] + ("" if len(cleaned) > limit else "")` produced an empty string in both
branches, so titles longer than 60 characters were silently truncated with no ellipsis.
An ellipsis had been eaten by a PowerShell here-string earlier; this is now fixed.