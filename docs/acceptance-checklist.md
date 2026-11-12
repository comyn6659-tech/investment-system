# 历史风险验收清单（阶段一版）

来源：设计书 §10 保留的历史审计要求、§12 必须存在的验收场景，以及本轮实际施工中发现的问题。
每一项都必须能在新项目里找到对应实现与测试；不继承旧版的投资政策，只继承「检查项」。

图例：✅ 已在阶段一实现并有测试；🟡 已建立机制但只能部分验证（依赖阶段二/三）；
⬜ 明确留到后续阶段。


## 0-A. 第二轮返工（F1–F3）新增检查项

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| C14 | 阻断性原因码对所有正式价格判断（candidate/wait/avoid）一票否决 | `contracts/validation.py` | `test_f1_research_cannot_export_formal_price_decision`、复验脚本 `blocked_decision` | ✅ |
| C15 | research_signal 必须可追到已登记模型 + 版本一致 + 与 model_plan 一致 + 分析确实执行 | `validation._check_research_signal` + `models/registry.py` | `test_f1_research_signal_requires_registered_model` 等 4 项 | ✅ |
| C16 | 合法研究结果仍可输出（不因修约束而禁绝研究数字） | 同一校验器 | `test_f1_legitimate_research_result_is_allowed` | ✅ |
| C17 | 数据调查按真实市场前缀调用，并区分本地提前返回/远端空/远端异常/取到数据 | `tools/probe_data_sources.py::_classify_attempt` | 复验脚本 `real_adapter_routing`、`partial_probe`；`test_r7_*` | ✅ |
| C18 | 快照内容哈希覆盖所有字段（含 fetched_at / notes / producers），元信息不能绕开身份 | `data/snapshot.py::_content_payload` | `test_f3_metadata_changes_are_caught_by_content_hash` | ✅ |
| C19 | manifest 与实际快照交叉核对，目录可自检 | `SnapshotStore._check_manifest_entry` / `check_consistency` | `test_f3_manifest_must_match_snapshot`、`test_f3_snapshot_directory_is_self_consistent` | ✅ |
| C20 | 作废样例不进入生产数据目录，不用备注文字控制准入 | `data/snapshots/` 目录 + 移除 notes 隔离逻辑 | `test_f3_retired_sample_is_out_of_production` | ✅ |

## 0. 返工撤回与恢复（2026-11-11）

第一版清单里的下列 ✅ 被证明是**过头了**：机制存在，但可以被旁路绕过。
按返工指令 C.5，这些勾选先撤回，修复并有针对性测试后才恢复。
撤回原因与恢复依据：

| 检查项 | 撤回原因（对应问题） | 恢复依据（返工后测试） |
|---|---|---|
| A1 零与缺失分离 | `Fact.from_dict` 可构造 `status=missing, value=0` 而不报错（R10） | `test_r10_missing_status_cannot_smuggle_value_via_from_dict` |
| A5 NaN/Infinity 拦截 | 反序列化路径接受 `known + None/nan/inf`（R10） | `test_r10_from_dict_rejects_invalid_known_values` |
| B2 历史分析不读未来数据 | 用字符串比较时间；只有日期的公告在 00:00 就放行（R5） | `test_r5_utc_fact_is_not_released_before_local_time`、`test_r5_date_only_is_not_known_at_midnight` |
| B3 快照内容可校验 | 删除 `snapshot_id` 仍被接受；不校验请求 ID（R8） | `test_r8_missing_snapshot_id_is_rejected`、`test_r8_requested_id_must_match_file_content` |
| C6 文本与 JSON 同一校验 | 文本出口实际未调用校验器（R2） | `test_r2_research_cannot_export_formal_price_decision` |
| C9 unsupported 不携带定价 | 只靠流程内断言，载荷可注入 `valuation`（R2） | `test_r2_unsupported_cannot_carry_valuation_payload` |
| D5/D6 范围外与启发式 | 启发式范围排除没有 instrument 时直接 `AssertionError`（R4） | `test_r4_heuristic_scope_exclusion_is_structured` |
| D1 不用代码长度判断类型 | 显式交易所被静默改写（R3，同类问题） | `test_r3_wrong_exchange_is_rejected` |

返工新增的检查项（第一版没有覆盖）：

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| B7 | 同一 as_of 多版本按抓取时间选最新，哈希不参与排序 | `SnapshotStore._version_key` + manifest `fetched_at` | `test_r9_latest_uses_fetch_time_not_hash_order` | ✅ |
| C10 | 必填状态字段缺失或为 null 一律违规 | `validation._REQUIRED_KEYS` / `_NOT_NULL_KEYS` | `test_r2_missing_status_fields_are_violations` | ✅ |
| C11 | 正式价格判断（candidate/wait/avoid）要求 validated + complete | `validate_result` | `test_r2_research_cannot_export_formal_price_decision` | ✅ |
| C12 | 其他序列化出口（dump_json、save）不得绕过校验 | `AnalysisResult.dump_json`、`SnapshotStore.save` | `test_dump_json_cannot_bypass_validation` | ✅ |
| D9 | 显式交易所冲突/前后缀冲突返回澄清 | `normalize.venue_conflict` + `resolver` | `test_r3_*` | ✅ |
| A8 | 直接构造 / 便捷构造 / 反序列化规则一致 | `Fact.__post_init__` | `test_r10_direct_and_helper_construction_agree` | ✅ |
| B8 | 采集不得把实时行情倒填到旧日期 | `tools/build_snapshots.py` 市场日期校验 | `test_r6_*` | ✅ |
| C13 | 数据调查器不得写死成败与频率 | `tools/probe_data_sources.py` | `test_r7_*` | ✅ |

## A. 数据状态

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| A1 | 零与缺失分离，缺失不得为 0 | `engine/data/facts.py`（`known_zero` vs `missing`，构造与反序列化统一校验） | `test_facts.py::test_zero_and_missing_are_different_states`、`test_r10_missing_status_cannot_smuggle_value_via_from_dict` | ✅ 返工后复核 |
| A2 | 不适用（如银行的净债务）不得当作 0 或缺失混同 | `Fact.not_applicable` + `tools/build_snapshots.py` 银行为银行模板标注 not_applicable | `test_facts.py::test_not_applicable_is_not_zero` | ✅ |
| A3 | 过期数据不得冒充最新 | `DataStatus.STALE` + `Fact.stale` | `test_facts.py::test_stale_is_not_treated_as_fresh` | ✅ |
| A4 | 多来源冲突不得静默取一个 | `Fact.conflict` 要求至少两个取值，`value=None` | `test_facts.py::test_conflict_requires_two_values` | ✅ |
| A5 | NaN / Infinity 不得进入计算与输出 | `engine/data/numeric.py`、`Fact.__post_init__`（含 from_dict）、出口 `_check_numbers` | `test_facts.py::test_nan_is_rejected`、`test_r10_from_dict_rejects_invalid_known_values`、`test_contract.py::test_nan_in_payload_is_rejected` | ✅ 返工后复核 |
| A6 | 指数点位不得标成人民币 | `payloads.amount()` 拒绝点位带币种；出口再校验 | `test_contract.py::test_index_point_with_currency_is_rejected` | ✅ |
| A7 | 金额必须带币种 | 出口 `_check_numbers` | `test_contract.py::test_money_without_currency_is_rejected` | ✅ |

## B. 时点与重放

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| B1 | 每条事实记录公告/可获得时间，抓取时间单独记录 | `Fact.available_at` / `fetched_at`；构建脚本逐条写入 | `test_snapshot_replay.py::test_point_in_time_filter_hides_later_disclosures` | ✅ |
| B2 | 历史分析不得读取当时尚未披露的数据 | `engine/data/snapshot.py::facts_available_by`（可获得时间未知的事实不放行） | `test_snapshot_replay.py::test_facts_without_availability_are_never_released` | ✅ |
| B3 | 快照内容可校验（篡改、缺 ID、请求 ID 不符均报错） | `Snapshot.from_dict(expected_id=...)` 必填 ID + 重算哈希 + 结构校验 | `test_r8_missing_snapshot_id_is_rejected`、`test_r8_requested_id_must_match_file_content`、`test_snapshot_replay.py::test_tampered_snapshot_is_rejected` | ✅ 返工后复核 |
| B4 | 同一快照 + 同一配置重放结果一致（生成时间除外） | `AnalysisResult.analysis_id` / `replay_fingerprint` 排除 `generated_at` | `test_pipeline.py::test_replay_is_byte_identical_except_generated_at` | ✅ |
| B5 | 快照不可用时明确报错，不返回旧的成功结果 | `pipeline._engine_error_result`，`analysis_status=engine_error` | `test_pipeline.py::test_unknown_snapshot_id_is_engine_error_not_fabrication` | ✅ |
| B6 | 重建快照会换 id（抓取时间进入内容哈希），旧结论不得被当作新数据 | `Snapshot._identity_payload` + `manifest.json` | `test_snapshot_replay.py::test_snapshot_id_is_content_addressed` | ✅ |

## C. 契约与出口

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| C1 | 事实 / 模型状态 / 价格位置 / 决策四个维度互不替代 | `contracts/enums.py` + `contracts/result.py` | `test_contract.py::test_valid_unsupported_result_passes` | ✅ |
| C2 | 未完成/未实现/资料不足/需澄清/引擎错误 ⇒ decision=undetermined | `decision/policy.py::apply_guards` + 出口校验 | `test_contract.py::test_unsupported_must_be_undetermined` 等 | ✅ |
| C3 | research 模型不得产生正式 candidate | `apply_guards` + `validate_result` | `test_contract.py::test_research_model_cannot_be_candidate` | ✅ |
| C4 | candidate 必须是 validated + complete + attractive 且无阻断原因码 | `validate_result` | `test_contract.py::test_candidate_requires_validated_and_complete`、`test_blocking_reason_code_prevents_candidate` | ✅ |
| C5 | 规则不是「一律拒绝」：真正满足条件时可以通过 | 同一校验器 | `test_contract.py::test_validated_complete_attractive_candidate_is_allowed` | ✅ |
| C6 | 文本与 JSON 使用同一套校验 | `json_exporter.export_json`、`text_summary.render_text`、`AnalysisResult.dump_json` 均前置 `enforce` | `test_r2_research_cannot_export_formal_price_decision`、`test_contract.py::test_text_and_json_share_the_same_validation` | ✅ 返工后复核 |
| C7 | 未登记的原因码视为违规（防止文档与代码漂移） | `validate_result` 检查 `reason_codes` 注册表 | `test_contract.py::test_unknown_reason_code_is_rejected` | ✅ |
| C8 | 载荷类型必须与资产类别一致 | `payloads.PAYLOAD_SCHEMAS` + 出口校验 | `test_contract.py::test_payload_type_must_match_asset_class` | ✅ |
| C9 | unsupported 结果不得携带定价结果 | 出口校验：未完成/未 validated 时 `valuation/premium_discount/signal` 必须为空 | `test_r2_unsupported_cannot_carry_valuation_payload`、`test_pipeline.py::test_equity_result_is_unsupported_with_facts` | ✅ 返工后复核 |

## D. 身份与范围

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| D1 | 不再用代码长度判断类型；显式交易所不得被静默改写 | `identity/normalize.py`（不做类型推断、检测前后缀冲突）+ `resolver.py` | `test_identity.py::test_index_codes_resolve`、`test_r3_wrong_exchange_is_rejected` | ✅ 返工后复核 |
| D2 | 代码冲突时列候选，不猜 | `resolver._resolve_code` → `ambiguous` | `test_identity.py::test_code_conflict_is_ambiguous_not_guessed` | ✅ |
| D3 | 只说指数时不擅自选 ETF | 精确名称匹配优先；ETF 全名不注册为裸别名 | `test_identity.py::test_index_names_resolve_to_index_not_etf` | ✅ |
| D4 | 具体 ETF 必须核实真实跟踪指数 | `data/registry/instruments.json` 的 `underlying_index_id` + 基金概况页证据 | `test_identity.py::test_each_verified_etf_maps_to_correct_index` | ✅ |
| D5 | 行业/策略/增强/跨境对象正确判为范围外 | 注册表 `scope` 字段 + 名称排除规则 | `test_identity.py::test_out_of_scope_products_are_recognized` | ✅ |
| D6 | 名称启发式只能排除、不能纳入，且排除结果必须是合法契约 | `resolver._heuristic_out_of_scope` + `pipeline._scope_only_result` | `test_identity.py::test_heuristic_never_includes_unverified`、`test_r4_heuristic_scope_exclusion_is_structured` | ✅ 返工后复核 |
| D7 | 空字符串/乱码/未登记输入清晰报错 | `INPUT_EMPTY` / `INPUT_INVALID_CHARS` / `INPUT_UNRECOGNIZED` | `test_identity.py::test_empty_input_is_invalid` 等 | ✅ |
| D8 | 其他宽基先识别、不套用已验证标签 | `INDEX_NOT_IN_FIRST_BATCH` 原因码 | `test_pipeline.py::test_run_of_the_mill_broad_index_is_flagged_not_silently_modelled` | ✅ |

## E. 留给后续阶段

| # | 检查项 | 计划阶段 | 状态 |
|---|---|---|---|
| E1 | 价格变化不改变内在估值与经营分类 | 阶段二 | ⬜ |
| E2 | FCFF/FCFE 口径桥接（利息、税项、租赁、少数股东、净债务） | 阶段二 | ⬜ |
| E3 | 特别分红不得永续化 | 阶段二 | ⬜ |
| E4 | 银行资本与资产质量缺口阻断正式买点 | 阶段二 | ⬜ |
| E5 | 指数分位无未来信息、无当前成分回填 | 阶段三 | ⬜ |
| E6 | 理论指数与 ETF 可执行回测分离、成本不重复扣除 | 阶段三 | ⬜ |
| E7 | 模型冲突时展示分歧而非挑最高估值 | 阶段二/三 | ⬜ |
| E8 | 自然语言入口与旧版对比 | 阶段四 | ⬜ |

## F. 本轮已知未解决

| # | 问题 | 影响 | 处理建议 |
|---|---|---|---|
| F1 | 官方指数估值历史只有 20 个交易日，本次第三方缓存为月度 | 阶段三「5 年周度分位」无法用**本次已测来源**完成 | 请设计方按 Q1 决定研究规格；本轮不降频、不插值 |
| F2 | 已测的东方财富接口对 H00300/H00905/H00852（带市场前缀）返回无数据 | 回测缺少股息口径 | 未穷举其他供应商；需要时另行调查，不购买数据 |
| F3 | 净值披露时间不可得 | 日终折溢价无法严格成立 | 阶段三改为「净值日期对齐 + 标注时点不确定性」 |
| F4 | 深交所 ETF 清单接口调用报错 | 深市 ETF 上市地缺少官方清单证据 | 以代码段 + 行情清单标注为推断，并在报告中写明 |
| F5 | 银行资本/资产质量字段未采集 | 阶段二银行模型前置数据不足 | 阶段二单独核实接口并补快照 |
| F6 | 个股价格未采集 | 阶段一不展示价格 | 设计方已答复接受；阶段二接入有日期与币种的价格事实 |
| F7 | ETF 跟踪误差序列未测试 | 暂无官方序列 | 需要时由净值与指数自行计算，并单列测试 |

> 以上缺口都限定在「本次已测来源与参数」范围内；不写成「所有免费数据均不可得」。

---

# 阶段二首轮验收清单（个股研究估值）

对应文件：`outputs/phase1-acceptance-and-phase2-instructions.md` §4 的六项冻结条件。
本轮只按这六项交付，不回头扩大阶段一审计。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| P2-1 模型适用性 | 三条路线有模型规格、必需数据和选择解释；未实现路线不被默认送入 DCF；选择不随当前股价改变 | `engine/evidence/applicability.py`、`docs/equity-model-spec.md` §1/§6 | `test_applicability.py`（25 项，含 `test_cyclical_needs_industry_and_volatility_together`、`test_unimplemented_families_are_not_sent_to_dcf`、`test_selection_does_not_depend_on_price`）；`docs/phase2-samples.md` 的 601318/601088/600030 三例 | ✅ |
| P2-2 数学与口径 | FCFF 与股息内核可运行；银行专用约束可测试；折现恒等式、期数、单位与股权价值桥接有独立验证 | `engine/valuation/{discounting,fcff,ddm,bank}.py`、`engine/financials/bridge.py` | `test_valuation_kernel.py`（48 项：单期/多期手算、零增长终值退化为永续年金、折现率单调性、负现金流保留、终值边界、桥接符号/单位/重复计数、每 10 股转换、银行资本与拨备约束）；`docs/equity-model-spec.md` §0/§2/§3/§4 | ✅ |
| P2-3 真实样本 | 至少一只真实非金融公司完成「证据→适用性→计算→研究报告」；同时交银行及股息路线的样本或具体缺口 | `engine/valuation/runner.py`、`data/snapshots/`、`data/evidence/`、`data/assumptions/` | `test_phase2_pipeline.py::test_real_non_financial_company_completes_full_chain`、`test_second_real_company_completes_dividend_route`；600519（FCFF+股息交叉检查）与 600900（股息）均 `complete`；601398 银行路线交具体缺口表 | ✅ |
| P2-4 反例 | 覆盖低 ROE、超额/特别分红、亏损或周期不适用、关键字段缺失；验证理由与处理而非一律拒绝 | `engine/valuation/ddm.py`、`engine/financials/dividends.py`、`engine/valuation/bank.py`、`engine/evidence/applicability.py` | `test_low_roe_with_positive_growth_is_refused_with_explanation`、`test_low_roe_with_zero_growth_is_still_valued_and_flagged`、`test_payout_above_earnings_*`、`test_special_dividend_never_enters_ordinary_dps`、`test_loss_making_company_is_distressed_not_fcff`、`test_bank_route_stops_with_concrete_data_gap`；`docs/phase2-delivery.md` §4.2 | ✅ |
| P2-5 研究与正式决策分离 | 本轮生产输出没有 candidate/wait/avoid；真实研究结果可导出；未运行模型、必要数据缺失不能夹带价格；原阶段一回归通过 | `contracts/validation.py::_check_research_valuation*`、`decision/policy.py`、`pipeline._equity_result` | `test_no_equity_or_product_output_is_ever_candidate`（9 个标的）、`test_research_payload_cannot_claim_a_price_position`、`test_research_payload_requires_research_model_status`、`test_bank_route_stops_with_concrete_data_gap`（`research_valuation is None`）、原阶段一 142 项全部保留通过；文本与 JSON 出口对每种状态都有渲染测试 | ✅ |
| P2-6 可复现与交付 | 固定数据、假设及版本可复现；提交参数来源与敏感性表、示例、测试输出和未解决事项 | `data/assumptions/`（内容哈希）、`docs/assumptions-and-sensitivity.md`、`docs/phase2-samples.md`、`examples/outputs/`、`docs/phase2-delivery.md` | `test_same_inputs_replay_identical_values`、`test_assumption_id_is_content_addressed_and_stable`、`test_changed_one_number_changes_the_assumption_id`；`tools/render_phase2_report.py` 与 `tools/render_examples.py` 从真实运行生成 | ✅ |

## 阶段二 S1 / S2 收尾（复验报告）

| 编号 | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| S1-1 | 缺失/冲突的少数股东权益不得默认零；桥接信息不足时 `insufficient_data`，不产出每股价值 | `financials/bridge.py::_resolve_item` | `test_missing_minority_equity_cannot_be_treated_as_zero`、`test_conflicting_minority_equity_is_rejected`、`test_s1_*`（含真实样本单字段缺失的完整运行） | ✅ |
| S1-2 | 有来源的已知零仍可计算 | 同上（`sourced_zero`） | `test_known_zero_minority_equity_still_computes`、`test_s1_zero_minority_interest_is_still_valued` | ✅ |
| S1-3 | 零声明必须逐项、带依据，不能用一个全局开关授权所有项 | `financials/bridge.py::parse_bridge_policy` / `BridgeItemPolicy` | `test_there_is_no_blanket_absent_as_zero_switch_left`、`test_minority_equity_assertion_requires_a_basis`、`test_bridge_policy_rejects_unknown_items` | ✅ |
| S1-4 | 同一桥接函数的同类缺失回退一并处理（现金、优先股、非经营性资产、有息负债） | 同上 | `test_missing_preferred_equity_is_refused_without_a_declaration`、`test_bridge_policy_covers_debt_and_structural_items_but_not_minority` | ✅ |
| S2-1 | 无持续资金来源的超额 DPS 不得作为自动永续终值，且限制与终值增长正负无关 | `valuation/ddm.py::check_growth_consistency` | `test_excess_payout_with_zero_growth_terminal_is_refused`、`test_s2_excess_payout_scenario_is_refused_end_to_end` | ✅ |
| S2-2 | 不静默截断派息率；有证据时拆分有限期超额分配与可持续终值 | `valuation/ddm.py::value_dividend`（`terminal_dps` + 依据） | `test_supported_finite_excess_distribution_is_not_blanket_rejected`、`test_s2_supported_terminal_split_computes_end_to_end` | ✅ |
| S2-3 | 正常 100% 派息 + 零增长仍退化为永续年金 | 同上 | `test_full_payout_with_zero_growth_still_degenerates_to_annuity` | ✅ |
| S2-4 | 可持续终值分配必须 ≤ 当年每股收益且附依据 | 同上 | `test_sustainable_terminal_dps_above_earnings_is_rejected`、`test_sustainable_terminal_dps_requires_a_basis` | ✅ |
| S2-5 | 银行路线复用同一内核，同步受约束 | `valuation/bank.py` | `test_bank_route_inherits_terminal_sustainability_check` | ✅ |

## 阶段二遗留（不阻断本轮）

| # | 事项 | 处理建议 |
|---|---|---|
| G1 | 银行资本充足率/不良率/拨备覆盖率/风险加权资产无免费来源 | 需年报原文或持牌数据源；数学与约束已实现并有测试 |
| G2 | 600900 缺 FCFF 交叉检查（目标资本结构假设未批准） | 待 P11 政策决定 |
| G3 | 600519 两条路线差 24.3%，接近 25% 阈值 | 属终值增长与可持续增长口径问题，交设计方判断（P3） |
| G4 | 非经营性资产按 0、受限资金未拆分、少数股东按账面扣除 | 已是逐项声明、可审计的已知近似；需要更细的报表科目后再收窄 |
| G5 | 业务证据由脚本整理，未做自动化财报文本阅读 | 后续阶段 |

---

# 第三阶段首轮验收清单（指数画像 + ETF 资料检查）

对应文件：`outputs/phase2-acceptance-and-phase3-instructions.md` §7 的六项冻结条件。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| I1 真实画像 | 三只首批指数都有真实运行报告；可计算的指标实际计算，缺失项明确说明；不能用 unsupported 代替已具备数据的实现 | `engine/series/profile.py`、`engine/pipeline.py::_index_result` | 三只指数均 `complete`：PE 分位 75.8/75.0/65.0，PB 分位 39.2/76.7/34.2，有效样本 60/60（`docs/phase3-samples.md`） | ✅ |
| I2 分位正确 | 手算小样本、并列值、当前值排除、窗口边界、每月去重、覆盖不足、非正 PE、未来日期记录 | `engine/series/window.py` | `test_index_profile.py` 24 项（含 `test_percentile_counts_ties_as_half`、`test_comparison_inside_window_is_removed_from_pool`、`test_insufficient_coverage_gives_no_percentile`、`test_non_positive_pe_*`、`test_future_dated_records_are_dropped`） | ✅ |
| I3 来源与重放 | 同源同列、无不同口径拼接；固定序列/快照/配置可复算；未填造历史公开时刻或版本 | `engine/series/models.py`（内容哈希）、`data/series/` | `test_series_id_is_content_addressed_and_stable`、`test_tampered_series_file_is_rejected`、`test_profile_is_deterministic_for_same_inputs`、`test_real_series_cover_the_60_month_window`；官方短序列单独成序列且不入池 | ✅ |
| I4 ETF 分离 | 三只 ETF 对应指数正确；同一指数画像一致；同日有效折溢价、跨日拒算、NAV 缺失/非正、交易状态未知均按约定处理 | `engine/pipeline.py::_etf_result`、`engine/reporting/product_check.py` | `test_etf_maps_to_the_right_index`、`test_index_profile_is_identical_from_index_and_etf_entry`、`test_cross_day_premium_is_refused`、`test_missing_nav_blocks_premium`、`test_non_positive_nav_blocks_premium`、`test_trading_status_and_spread_are_explicitly_missing` | ✅ |
| I5 输出与回归 | CLI 文本/JSON 同语义；全部研究决策为 undetermined；无目标点位/目标价/正式买点；个股及阶段一相关回归通过 | `engine/reporting/text_summary.py`、`engine/contracts/validation.py` | `test_etf_exports_validate_and_have_no_formal_decision`、`test_index_profile_cannot_be_a_trading_signal`、`test_index_profile_without_model_result_is_rejected`；个股 286 项回归全部通过 | ✅ |
| I6 交付完整 | DELIVERY.md 汇总真实示例、测试结果、数据覆盖、口径与剩余缺口，并给出可复现命令 | `DELIVERY.md`、`docs/phase3-delivery.md`、`docs/index-profile-spec.md`、`docs/phase3-samples.md` | 入口文件 §1–§6；样例由 `tools/render_phase3_report.py` 自动生成 | ✅ |

## 第三阶段收尾（W1 / W2 / E1）

| 编号 | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| W1-1 | 先按有效性过滤，再取每月最后一个有效观测；同月有有效值时该月不丢 | `engine/series/window.py::build_window` | `test_w1_month_keeps_last_valid_observation_when_month_end_is_invalid`（60 月 / 50%）、`test_w1_same_month_multiple_valid_values_keep_the_last` | ✅ |
| W1-2 | 当前最新 PE 非正时仍不给分位，不回退旧正值 | 同上 | `test_w1_invalid_last_value_does_not_change_the_current_comparison` | ✅ |
| W2-1 | 官方短序列只展示不晚于 `as_of` 的观测，`latest/数量/覆盖` 自洽 | `engine/series/profile.py::build_index_profile` | `test_w2_official_short_series_respects_as_of`、`test_w2_official_short_history_normal_case_is_unchanged` | ✅ |
| W2-2 | 官方序列全在未来时只报缺口，不硬取未来值 | 同上 | `test_w2_official_series_entirely_in_future_is_reported_as_gap` | ✅ |
| E1-1 | 派生折溢价在使用处核验价格可用/收盘、NAV 可用且为正、NAV 事实日期与 `nav_date` 一致、同日 | `engine/reporting/product_check.py::build_product_check` | `test_e1_nav_fact_period_mismatch_is_refused`、`test_e1_intraday_price_is_refused`、`test_e1_missing_price_is_refused` | ✅ |
| E1-2 | 缓存值只作一致性核对；与重算不一致时改用重算值并留痕 | 同上 | `test_e1_changed_price_recomputes_instead_of_reusing_stale_cache`、`test_e1_pipeline_never_exports_a_stale_premium` | ✅ |
| E1-3 | 正常三只 ETF 折溢价数字不变 | 同上 | `test_e1_normal_samples_are_consistent_with_cache`、`test_same_day_premium_is_computed_from_price_and_nav` | ✅ |

## 第三阶段遗留（不阻断本轮）

| # | 事项 | 影响 |
|---|---|---|
| H1 | 历史发布时间/修订版本未知 | 允许描述性统计；**阻断**作为严格可交易信号与回测 |
| H2 | 月度而非周度采样 | 周度策略与回测仍缺数据，禁止插值扩频 |
| H3 | 供应商聚合方法/分母/亏损处理 unknown | 只能同源同列比较 |
| H4 | 停复牌与盘口价差未取得 | 不能断言可交易 |
| H5 | 净值披露时刻未知 | 同日折溢价只能事后计算 |
| H6 | 非首批宽基无序列 | 返回 `insufficient_data` + 缺口 |

---

# 第四阶段第一批验收清单（个股政策试运行 + 指数研究信号）

对应文件：`outputs/equity-policy-v1-draft-and-batch4a.md` 第二部分与冻结验收条件 L1–L8。
本批只写试运行报告的「预演结论」，不改 CLI 的 research/undetermined，不升级 validated，不调指数阈值。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| L1 | 政策文件集中、版本化；每个参数带来源或理由；代码无散落阈值 | `data/policies/equity-policy-v1-draft.json`、`engine/policy/equity.py` | `test_policy_is_versioned_draft_and_cannot_self_approve`、`test_every_parameter_has_source_or_reason`、`test_policy_module_has_no_hardcoded_policy_numbers`、`test_changing_policy_changes_behavior` | ✅ |
| L2 | 折现率、终值、再投资一致性、情景按第一部分实现，各有手算测试；600519 两模型分歧报告并解释 | `engine/policy/valuation.py`、`docs/phase4a-equity-policy-trial.md` §1/§6 | `test_normalized_risk_free_*`、`test_discount_rate_build_matches_hand_calculation`、`test_cost_of_debt_floors_at_risk_free`、`test_growth_paths_*`、`test_terminal_growth_is_capped_by_*`、`test_fcff_value_matches_hand_calculation`、`test_dividend_value_matches_hand_calculation`、`test_terminal_payout_equals_one_minus_g_over_roi`、`test_maotai_two_model_divergence_under_new_rules` | ✅ |
| L3 | 确定性等级与结论映射按第 5、6 节实现，边界条件有测试；C 级、partial 一律无法判断 | `engine/policy/grading.py` | `test_grade_a_*`、`test_grade_b_*`、`test_grade_c_*`、`test_grade_boundary_net_debt_to_ebitda`、`test_bank_without_risk_evidence_cannot_exceed_c`、`test_conclusion_boundaries`、`test_conclusion_requires_pessimistic_condition`、`test_grade_c_and_partial_are_always_undetermined`、`test_margin_sensitivity_grid_*` | ✅ |
| L4 | 30 只测试集全部有结果（含资料不足等如实状态），快照与来源可重放 | `data/policy-trial/snapshots/`、`data/policy-trial/evidence/`、`engine/policy/trial.py` | `test_trial_covers_all_thirty_codes`、`test_trial_snapshots_replay`；报告 §2 数据表逐只列出快照与状态 | ✅ |
| L5 | 试运行报告字段齐全，含全集分布与安全边际敏感性 | `tools/run_equity_policy_trial.py`、`data/policy-trial/trial-report.json` | `test_trial_complete_records_have_full_fields`、`test_trial_distribution_and_margin_sensitivity`、`test_trial_markdown_contains_required_sections` | ✅ |
| L6 | 指数研究信号与只追加账本按 B 节实现，有测试；重复运行不重复写入、不改旧记录 | `engine/signals/candidate_a.py`、`engine/signals/ledger.py`、`tools/update_signal_ledger.py` | `test_candidate_a_signal_for_csi300`、`test_classify_signal_boundaries`、`test_ledger_append_is_idempotent`、`test_ledger_refuses_to_rewrite_history`、`test_ledger_detects_tampered_record`、`test_ledger_update_command_is_idempotent`、`test_index_report_contains_research_signal` | ✅ |
| L7 | CLI、research/undetermined 不变；全部既有测试通过 | `engine/pipeline.py`（指数分支新增信号）、`engine/valuation/runner.py`（未改） | `test_index_signal_cannot_become_price_judgement`、`test_research_payload_cannot_be_combined_with_candidate`；全部既有回归通过 | ✅ |
| L8 | DELIVERY.md 汇总并链接上述内容、复现命令、测试结果、提交 ID | `DELIVERY.md` | 入口文件 §1–§8 | ✅ |

**不作为通过条件**：候选买点数量、哪些股票被判为便宜。本批按事实交付，分布见试运行报告 §4。

## 第四阶段第一批收尾（M1–M5，2026-11-12）

| 编号 | 检查项 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| M1-1 | 指数报告历史覆盖拆成「快照实时抓取路径」与「序列库」，字段与说明不得与序列库事实相反 | `engine/reporting/payload_builders.py::build_index_payload`、`engine/pipeline.py::_series_library_coverage`、`engine/reporting/text_summary.py::_render_history_coverage` | 三只指数文本/JSON 扫描：`snapshot_fetch_*` 前缀、`series_library` 起止与观测数、`supports_weekly_5y_history: true`；`test_history_coverage_separates_snapshot_path_from_series_library` | ✅ |
| M2-1 | 周度分位显示保留两位，且从**未舍入值**格式化 | `engine/signals/candidate_a.py`、`engine/reporting/text_summary.py` | 沪深300 2026-W45：67.7165% → 67.72%（原 67.70%）；`test_signal_percentile_display_is_two_decimals` | ✅ |
| M3-1 | 000858 FY2025 两个数字用独立来源核对并写入交付文档 | `docs/phase4a-delivery.md` §9 M3 | 同花顺年度摘要 405.29 亿元 / 89.54 亿元，与东方财富一致；结论「已核对，数据属实」 | ✅ |
| M3-2 | 试运行报告新增「基期异常变动清单」，阈值版本化，不改结论 | `engine/policy/trial.py`、`tools/run_equity_policy_trial.py`、政策 `reporting.base_year_anomaly` | 报告 §4.2 列出 000858（−54.6% / −71.9%）；`test_trial_base_year_anomaly_list`、`test_policy_carries_designer_decisions_and_guardrail` | ✅ |
| M4-1 | 杠杆护栏 50% 版本化；超过时 partial / C / 无法判断，价值仅研究参考，新增原因码 | 政策 `quality.leverage_guardrail`、`engine/policy/valuation.py::leverage_guardrail`、`engine/contracts/reason_codes.py` | 601668 83.3% 触发；刚好 50% 不触发、50.01% 触发；`test_leverage_guardrail_boundary`、`test_trial_leverage_guardrail_forces_c` | ✅ |
| M5-1 | 30 只预演结论不变；账本 3 条哈希不变；CLI 仍 research/undetermined；全部测试通过 | `data/policy-trial/trial-report.json`、`data/ledger/` | 结论逐只比对 0 差异；`update_signal_ledger.py --check` 0 问题；全套测试通过 | ✅ |
