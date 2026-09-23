# 验证记录

以下为移除端到端验证前的历史记录及快照，不代表当前版本的测试范围。当前 `make verify` 仅执行单元/回归测试与静态检查，不再执行 CLI 完整流程验证；最新结果见生成的 `artifacts/verification/latest.json`。当前订单汇总示例预期为 5 项通过、2 项模型触发测试阻塞。

公开仓库保留可复现的源码、测试和精简验证快照：[创建器软件验证](examples/creator-validation.json)、[订单分析案例](order-analysis-case-study.md)。下文标注为“本地证据”的路径指创建时保留的运行记录，不包含在 GitHub 源码中；执行 `make verify` 可生成新的本地软件验证证据。

验证日期：2026-09-23（Asia/Shanghai）。实现采用 Creator Skill + Python 3.9 标准库工具。软件验证通过；真实模型行为评估单独记录，未配置业务模型适配器的案例仍保持阻塞。

## 软件测试

运行 `make verify`，**56 项单元/回归测试全部通过**，静态检查和 CLI 端到端检查通过。运行环境为 macOS / Python 3.9.6。

- 完整验证报告（本地证据：`artifacts/verification/20260922T213408Z-aaa713/report.md`）
- 逐项测试日志（本地证据：`artifacts/verification/20260922T213408Z-aaa713/unittest.log`）
- 源文件摘要（本地证据：`artifacts/verification/20260922T213408Z-aaa713/source-hashes.json`）
- 官方 Skill 校验结果（本地证据：`artifacts/verification/20260922T213408Z-aaa713/official-skill-validation.json`）：`Skill is valid!`

覆盖需求充分性、关键假设阻断、非关键假设保留、流程环和无效引用、节点/分支/需求覆盖、正反触发测试契约、混合协议、资源越界、生成与版本保留、脚本和引用有效性、精确金额、异常输入、进程错误和超时、AI 评分和对照协议、部分重测、修订记录、报告及证据变更检测。

CLI 集成真实执行 check → build → test → report。订单示例 **6 项代码测试通过、2 项模型触发测试阻塞**；test/report 返回 1，正确保持 `business_verified=false`。阻塞原因是示例未配置真实模型适配器。软件测试验证的是“此情形必须阻塞”，不把这两项计为业务通过。

框架单元测试中的 AI 适配器是显式标注的 synthetic test doubles，只证明协议、断言与状态逻辑，不能用于宣称模型效果。

## 独立前向验证

独立 agent 按技能处理真实的“文章标题推荐栏目 Skill”请求，并实际使用 CLI 和生成脚本：

1. 信息不充分：将唯一/多栏目规则标为关键待确认；check 返回 1，build 返回 2，继续完成可独立设计和校验的部分。原始记录（本地证据：`artifacts/live/clarification/forward-test-report.md`）
2. 测试夹具补齐明确业务事实后，成功生成 Skill。真实模型候选将“Python 编程指南”推荐 tech、“现代诗歌赏析”推荐 culture；“用 Python 分析现代诗歌”保留两项并请求用户选一个；天气标题拒绝。原始候选（本地证据：`artifacts/live/complete-business-original/evidence/raw-model-candidates.json`）
3. 候选实际交给生成后的代码校验。最终完整项目框架测试 **6 passed、4 blocked**；模型自动化执行器及基线未配置，保持未验证。最终复测总表（本地证据：`artifacts/live/forward-retest/retest-summary.json`）

候选来自实际 Codex 子 agent，并非固定响应适配器。确切模型标识和 Token 不可观测，均如实标为 unknown/null。真实 agent 的候选交接证明已观察到的行为，不替代尚未完成的业务模型自动化全套验收。

## 发现、修订与重测

| 发现 | 修改 | 重测证据 |
| --- | --- | --- |
| 无运行记录时 report 只返回底层缺文件错误 | 返回先 check/build/test 的可操作说明 | 自动化测试 `test_report_before_tests_has_actionable_message` |
| Skill 指令遗漏 delivery 的内容摘要目录层 | 修正交付路径说明 | 当前 SKILL.md 与项目契约一致 |
| Python 相等判断将 true 与 1 混淆 | 严格 JSON 比较；触发结果要求 bool | 自动化测试 `test_exact_json_distinguishes_boolean_and_number` |
| 修改 results.json 状态能将阻塞伪装通过 | 最近运行指针保存并核对结果摘要 | 独立复测（本地证据：`artifacts/live/forward-retest/tampered-report-retest.json`）：返回 1、验证不通过 |
| 无效对照已评分就被当成有效比较 | 评分与协议有效性分开，比较要求 protocol_valid | 独立复测（本地证据：`artifacts/live/forward-retest/malformed-baseline-retest.json`）：触发案例阻塞 |
| 候选行为把未请求的外发授权列为关键缺口 | 明确仅分析目标所需权限；本地任务不新增发送节点 | 模型行为重测见下节 |
| Decimal 默认精度可能影响大额示例汇总 | 使用整数分计算金额 | 大额超过 28 位有效数字的回归测试通过 |

## 模型对照评估

无 Skill 基线和使用 Skill 的候选在不同子 agent 上下文中处理相同请求。每个条件各两次答复，评估创建器路由、关键澄清、节点分类、混合边界、权限范围和验证真实性。

- 无 Skill 原始答复（本地证据：`artifacts/live/baseline/response.json`）
- 首次使用 Skill 的原始答复（本地证据：`artifacts/live/candidate/response.json`）
- 修订后权限专项重测的两次原始答复（本地证据：`artifacts/live/candidate/response-retest.json`）
- 评分标准、逐项依据与限制（本地证据：`artifacts/live/evaluation.json`）

初轮基线与候选均正确区分“创建技能”和“直接计算”请求，并正确拆分精确代码与模型语义工作。候选存在上表权限范围偏差，已据此修订，保留初轮证据。

专项重测两次均明确：金额列含义是关键待确认项；未请求外发，发送授权不适用，不新增发送节点或关键权限缺口。两次均满足修订目标。专项重测没有重新评价其他维度，不据此计算新的全量平均分。此次评估帮助发现并修复偏差，未证明总体质量优于无 Skill 基线。

每个条件两次答复来自同一会话，不能视为独立统计样本；评审由主 agent 根据设计要求完成，并非盲评。模型精确 ID、Token 与模型调用耗时不可观测，未编造值。该样本不能证明 Skill 相对通用模型有统计显著提升。

上述触发观察是 agent 对真实请求的路由判定；未采集安装后宿主自动发现及隐式调用的运行遥测，不能将其表述为该宿主集成已经通过。

## 可复现范围与未解决项

`make test` / `make verify` 可完整重跑离线软件验证。真实模型评估依赖可用宿主和实际评审，不在离线测试中自动重放成新模型执行。原始证据保存在独立的 artifacts/，不进入交付 Skill；目录在 .gitignore 中，分享验证结论时应一并归档。

业务示例的模型适配器和对照仍未配置：影响是不能标记这些生成技能的全部业务测试通过。下一步是在目标宿主/模型环境中接入真实执行与评审适配器，使用相同输入和 rubric 全套执行。已观察到的真实模型行为、代码测试通过和未执行的业务测试在报告中分别呈现。
