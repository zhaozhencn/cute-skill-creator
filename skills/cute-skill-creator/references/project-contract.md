# project.json 契约（schema_version = 1）

Python 3.9+，无需安装第三方依赖。工作目录由用户任务决定，交付 Skill 与运行证据分开。

```text
work/
  project.json             可修订的需求、流程、生成内容和测试计划
  src/                     编写好的业务脚本/参考资料（名称可自定）
  adapters/                测试执行器（不放入 resources）
  delivery/<sha256>/
    <skill-name>/SKILL.md   可复制安装的完整 Skill
    requirements.md        业务需求说明
    flow.md                流程设计
    project.json           本版契约快照
    manifest.json          Skill 文件摘要
  build.json               当前交付指针
  revisions.json           问题、修改和前一轮测试关联
  runs/<run-id>/            每次测试独立保存，历史不覆盖
    project.json
    <case-id>/attempts-N/   输入、预期、argv、stdout、stderr
    <case-id>/baseline-N/   对照证据
    results.json
    summary.json
    report.md
  latest-run.json
```

## 项目字段

- `schema_version`: 固定为 `1`。
- `name`: 小写字母、数字和单连字符，少于 64 字符。
- `version`: 用户维护的非空版本号；内容摘要用于区分实际版本。
- `description`: Skill 触发说明，最多 1024 字符，不含尖括号。
- `instructions`: Markdown 正文，由 Creator 根据业务流程编写；生成器添加 frontmatter。
- `requirements`: 需求对象数组；六类澄清维度均须存在。
- `nodes`: 至少一个节点，有向无环依赖；可以由多种执行方式组成。
- `resources`: `{ "source": "src/calculate.py", "target": "scripts/calculate.py" }` 数组。源必须为工作目录内文件，目标仅允许 scripts、references、assets 内的文件。空数组适用于无需附加资源的 AI Skill。
- `tests`: 下述测试对象数组。所有需求、节点、声明的分支须由必测案例覆盖。
- `test_resources`: 可选工作目录相对路径数组，列出测试适配器及夹具。参与版本摘要并保存到测试快照，变更后必须重新生成和测试。执行的脚本参数还会单独保存源码副本和文件摘要。

## 需求对象

`id`（唯一字母/数字/下划线/连字符）、`category`（goal/io/flow/rules/environment/acceptance）、`text`、`status`（confirmed/pending/assumed）、`critical`（布尔）、`source`、`acceptance`。

`source` 记录用户原话、文档路径或本轮默认假设依据。关键项必须 confirmed，非关键 pending/assumed 可继续但保留在交付说明中。不要把影响权限、业务结果或验收的未知条件标为非关键。

## 节点对象

所有节点都需 `id`、`input`、`output`、`mode`（ai/code/hybrid）、`reason`、`constraints`、`validation`、`failure`、`human_intervention`、`depends_on`（前置节点 id 数组，可空）、`requirements`（需求 id 非空数组）、`branches`（非空唯一字符串数组）。

code/hybrid 另需 `scripts`：交付中的脚本路径数组。hybrid 另需 `handoff`：候选数据格式和处理规则；`validator`：已声明的校验脚本资源路径。

## 测试对象

每项必需 `id`、`purpose`、`required`（布尔）、`layer`（structure/trigger/node/hybrid/regression）、`mode`、`nodes`、`requirements`、`branches`。

`branches` 引用格式 `node-id:branch-name`。必测测试层至少包含结构、触发、节点和异常回归；混合流程还须混合交接测试。触发层必须包含 expected=true 和 false 的 AI 案例。AI/hybrid 节点须有相应语义测试。

从 Creator 1.1.0 起移除 `e2e` 层。旧项目须删除对应测试对象，并确认必要的节点、需求和分支仍由其他必测用例覆盖；历史运行证据保留。新报告不再输出端到端任务成功率 `task_success_rate`。

执行字段：

- `input`: JSON 值，写入执行器 stdin。
- `command`: argv 字符串数组或 null；只做 `{python}`、`{workspace}`、`{skill}` 替换，不经过 shell。执行目录为本次用例证据目录；使用绝对路径或占位符。
- `timeout`: 0 至 3600 秒之间，默认 60（不含 0）。
- `repeats`: 1..20；AI/hybrid 至少为 2。
- `blocked_reason`: command=null 时的具体阻塞原因。
- code 模式必须有 `expected`：对执行器 stdout 的 JSON 做精确断言。
- AI/hybrid 必须有 `rubric`：如 `{ "evidence_grounding": 0.8, "usefulness": 0.8 }`，阈值范围 0..1。
- AI/hybrid 必须有 `baseline`: `{ "kind": "no_skill", "command": [...] }` 或 `previous_skill`。执行器负责确实不加载 Skill 或加载指定旧版。缺少真实执行条件时 command=null。
- AI/hybrid 可指定 `expected`，用于检查输出中的 result。触发测试必须是布尔 expected。

CLI 返回码：0=操作成功或全部必测通过；1=检查未就绪或测试未全部验证；2=契约、文件或调用错误。定向 `test --case ID` 不合并旧版/旧轮结果，其他测试保留为未执行，交付前须全套执行。
