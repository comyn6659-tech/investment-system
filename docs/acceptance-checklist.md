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

---

# 第四阶段 4a-R 验收清单（个股政策 v1.1，R1–R14 / N1–N9）

对应文件：`fable-review-judgement-and-batch4a-r.md` 第四节。范围只做 R1–R14、重跑 30 只；
不改 ERP、无风险利率规则、安全边际 25%/40%，不增加银行数据，不动指数与 ETF，不升级 validated。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| N1 | R1–R14 每项在政策文件中有参数与依据，代码无散落数字；每项至少一个测试（含边界） | `data/policies/equity-policy-v1.1-draft.json`、`engine/policy/*` | `test_policy_module_has_no_hardcoded_policy_numbers`；17 项 R 系列测试（`test_r1_*` … `test_r14_*`） | ✅ |
| N2 | 万华化学状态 partial、无法判断，原因码说明 R2 哪一条触发 | `engine/policy/trial.py::continuity_guardrail_reasons` | 600309：`TERMINAL_GUARDRAIL_BY_DEFAULT`、`..._EXPLICIT_CASH`、`..._TERMINAL_SHARE` 三项；`test_r2_wanhua_*` | ✅ |
| N3 | 茅台现金取 `END_CCE`、受限代理为 0；报告列出新等级与新价值 | `engine/policy/valuation.py::extract_inputs`、快照重建 | 1,264.3 亿元；非现金等价物 0；等级 B、基准 1,144.66 元；`test_r3_maotai_*` | ✅ |
| N4 | R1 后 FCFF 基准价值对终值增长 ±0.5pp 变化 < 1%（手算 + 报告） | `engine/policy/valuation.py::terminal_spec` | `test_r1_fcff_terminal_roi_is_wacc_and_growth_cancels`；报告 §3.5 最大 0.88% | ✅ |
| N5 | 比亚迪派息率总额口径，新旧并列；路线按新值重判 | `engine/financials/dividends.py::par_value`、`runner._payout_stats` | 64.7% → 22.9%；路线 股息折现 → FCFF；`test_r10_payout_total_basis_fixes_byd_split` | ✅ |
| N6 | 报告满足 R12 全部条目 | `tools/run_equity_policy_trial.py` | 报告 §1.1、§3.1/3.2 分表、§3.3 终值占比、§5 起约束、§6 倍数表、读法；`test_r12_report_presentation`、`test_r12_threshold_distance_alert_boundary` | ✅ |
| N7 | 试运行 30 只全部有结果；新旧逐只对照表含状态、等级、三档价值、结论、变化原因 | `data/policy-trial/trial-report.json`、`docs/phase4a-equity-policy-trial.md` §7 | 30 行对照；`test_trial_covers_all_thirty_codes` | ✅ |
| N8 | 指数、账本、CLI、回测不变；全部测试通过 | `engine/signals/*`、`engine/pipeline.py`（未改） | 账本 3 条 0 问题；CLI `research`/`undetermined`；**459 passed** | ✅ |
| N9 | DELIVERY.md 汇总、复现命令、提交 ID | `DELIVERY.md` | 入口文件 §1–§10 | ✅ |

---

# 第四阶段第二批验收清单（启用个股正式结论，P1–P8）

对应文件：`decision2-and-batch4b-instructions.md` 第二节。范围：批准落地、模型状态、CLI 正式结论、
数据新鲜度、结论账本、30 只运行；不改政策参数、不扩股票池、不改指数/ETF、不做对话入口。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| P1 | approved 政策与 v1.1 草案逐参数相同；批准记录与复核日期在文件中 | `data/policies/equity-policy-v1.1.json`、`engine/policy/equity.py` | `test_p1_approved_policy_matches_draft_parameters`、`test_p1_policy_version_uses_approved_identifier`；`pol-920f936dbfefd436` | ✅ |
| P2 | 只有政策下的 FCFF / 股息折现为 `validated`；其余模型状态不变；有测试防止其他模型升级 | `engine/models/registry.py` | `test_p2_only_policy_routes_are_validated`、`test_p2_formal_result_requires_validated_policy_model` | ✅ |
| P3 | 30 只 CLI 结论与试运行 v1.1 预演结论 30/30 一致；试运行与 CLI 共用代码 | `engine/pipeline.py::_policy_equity_result` | `test_p3_thirty_cli_conclusions_match_trial`、`test_p3_cli_and_trial_share_evaluate_company`；报告 30/30 | ✅ |
| P4 | CLI 输出含结论、等级及含义、三档价值及用途、起约束条件、距门槛、未满足条件、政策与快照、免责声明；C/partial/不支持/资料不足仍 undetermined 且有原因 | `engine/policy/conclusion.py`、`engine/reporting/text_summary.py` | `test_p4_formal_output_contains_required_fields`、`test_p4_text_output_contains_all_fields`、`test_p4_c_partial_unsupported_insufficient_are_undetermined` | ✅ |
| P5 | 新鲜度三种情形各有测试，触发即 undetermined | `engine/policy/freshness.py`、政策 `runtime_parameters.freshness` | `test_p5_stale_price_hits`、`test_p5_stale_financials_hits`、`test_p5_missing_policy_field_hits`、`test_p5_stale_price_forces_undetermined_end_to_end` | ✅ |
| P6 | 账本追加、重复跳过、冲突、篡改检测各有测试；首次写入条数 = 30 只中正式结论数量 | `engine/policy/conclusion_ledger.py`、`engine/cli.py` | `test_p6_ledger_append_skip_conflict_and_check`、`test_p6_ledger_detects_tampering`、`test_p6_cli_check_detects_tampering`、`test_p6_first_write_has_seven_formal_conclusions` | ✅ |
| P7 | 指数信号、指数账本、ETF、回测不变；全部既有测试通过 | `engine/signals/*`、`engine/pipeline.py` | `test_p7_index_and_signal_ledger_unchanged`、`test_p7_backtest_file_is_read_only`；**480 passed** | ✅ |
| P8 | DELIVERY.md 汇总、复现命令、测试结果、提交 ID | `DELIVERY.md` | 入口文件 §1–§10 | ✅ |

---

# 第四阶段第三批验收清单（创业板指 399006，Q1–Q8）

对应文件：`batch4c-chinext-instructions.md` 第四节。范围：先做官方数据可行性，再按结果接入；
不用第三方估值、板块 PE 或拼接序列；不调候选 A 阈值，不给指数正式买点，不做 ETF 执行检查。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| Q1 | 可行性报告逐项给出来源、字段、起止、频率、是否官方，以及做了哪些、没做哪些及原因 | `docs/phase4c-chinext-feasibility.md`、`data/coverage/chinext-feasibility.json`、`tools/build_chinext_data.py` | `test_feasibility_record_is_explicit`；报告 §2–§3 六类探针 | ✅ |
| Q2 | 估值、信号、回测只用官方同源数据；有测试证明非官方来源不能进入分位 | `engine/series/profile.py::build_index_profile(official_only=…)`、`engine/signals/candidate_a.py` | `test_non_official_profile_series_is_rejected`、`test_non_official_signal_series_is_rejected`；`cnindex.*` / `csindex.*` 白名单 | ✅ |
| Q3 | 已做部分与前三只指数共用代码；399006 结果可由独立重算复现 | `engine/pipeline.py::_index_profile`、`build_candidate_a_signal`、`SeriesStore` | `test_shared_profile_and_signal_functions`、`test_series_hash_can_be_recomputed`、`test_official_short_history_is_used_by_profile` | ✅ |
| Q4 | 若做回测：字段与第三阶段第三批一致，含分段与逐笔明细 | 未做 | 官方 PE 历史不可得，回测不启动；`test_no_chinext_backtest_entry` 证明回测文件未新增 399006 | ✅（不适用） |
| Q5 | 账本既有 3 条哈希不变；399006 新记录符合只追加、重复跳过 | `data/ledger/candidate-a-signals.jsonl` 未改 | `test_ledger_has_no_chinext_record_and_existing_three_unchanged`；`SignalLedger.check()` 0 问题；无信号可写，未新增记录 | ✅（不适用新增） |
| Q6 | 名称识别三种情形各有测试（创业板指 / 创业板歧义 / 创业板50 范围外） | `engine/identity/resolver.py`、`data/registry/instruments.json` | `test_name_resolution_three_cases`、`test_cli_bare_board_needs_clarification` | ✅ |
| Q7 | 前三只指数、个股结论、结论账本、ETF 既有结果不变；全部测试通过 | 既有模块未改口径 | `test_existing_three_indices_unchanged`；个股 7 条账本不变；**497 passed** | ✅ |
| Q8 | DELIVERY.md 汇总、复现命令、测试结果、提交 ID | `DELIVERY.md` | 入口文件 §1–§9 | ✅ |

---

# 第四阶段第四批验收清单（好公司池，G1–G9）

对应文件：`batch4d-quality-pool-instructions.md` 冻结验收条件。范围：数据可行性优先、财务覆盖率 95% 门槛、
规则版本化、缺数据不默认通过、固定快照可重放、报告 7 项、两本新账本、不改既有结论。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| G1 | 可行性报告逐项给出来源、覆盖率、起止；按 95% 门槛执行停或继续 | `docs/phase4d-quality-pool-feasibility.md`、`data/coverage/quality-pool-feasibility.json`、`tools/build_quality_pool_data.py --probe-only` | 财务 99.45%（3,234/3,252），gate_passed=true；审计/处罚/质押不可得逐项列探针 | ✅ |
| G2 | 规则在版本化文件中，每个参数带依据；代码无散落数字；每条条件有边界测试 | `data/policies/quality-pool-v1-draft.json`、`engine/quality/{policy,evaluator,metrics}.py` | `test_roe_average_exact_threshold_passes`、`test_roe_average_just_below_threshold_fails`、`test_roe_consistency_boundary`、`test_roe_recent_boundary`、`test_cash_conversion_boundary`、`test_free_cash_flow_must_be_strictly_positive`、`test_loss_year_fails`、`test_net_debt_to_ebitda_strict_boundary`、`test_dividend_payout_boundary` | ✅ |
| G3 | 缺数据规则按第一部分执行，有测试证明缺数据不会默认通过 | `engine/quality/metrics.py`、`evaluator.py`、政策 `missing_data` | `test_missing_year_does_not_default_pass`、`test_missing_field_does_not_default_pass`、`test_financial_condition_is_untested_not_passed`；缺年报 fail、缺字段 pending、未检验单列 | ✅ |
| G4 | 全市场快照可重放：同快照重跑结果逐家相同 | `engine/quality/{snapshot,build}.py`、`data/quality-pool/snapshot-qps-e6fd0031c30f1e01/` | `test_same_snapshot_replays_identically`、`test_snapshot_directory_round_trip_and_tamper_detection`；表级哈希校验 | ✅ |
| G5 | 报告含第二部分 C 的 7 项 | `docs/phase4d-quality-pool-report.md`、`data/quality-pool/report.json` | §1 首页、§2 入池 144、§3 只差一条 108、§4 漏斗、§5 敏感性、§6 30 只、§7 行业 | ✅ |
| G6 | 我抽查的公司成绩单可由快照独立复算 | `tools/run_quality_pool.py --show <code>` | 从 `*.csv.gz` 原始表重算逐年 ROE/现金流/分红；示例 600519 | ✅ |
| G7 | 入池账本与官方估值记录只追加、重复跳过、篡改可检出 | `tools/run_quality_pool.py`、`tools/update_official_valuation_ledger.py`、`data/ledger/*.jsonl` | `test_pool_ledger_append_skip_conflict_and_tamper`、`test_official_valuation_ledger_hash_and_tamper`；`--check` 各 0 问题 | ✅ |
| G8 | 个股结论、两本既有账本、指数与 ETF 结果不变；全部测试通过 | 既有模块未改口径 | 个股 7 条、候选 A 3 条 0 问题；快照/序列/回测未改；**524 passed** | ✅ |
| G9 | DELIVERY.md 汇总、复现命令、测试结果、提交 ID | `DELIVERY.md`、`docs/phase4d-delivery.md` | 入口 §1–§9；实现提交见 DELIVERY | ✅ |

---

# 第四阶段第五批验收清单（好公司池补治理检验，J1–J8）

对应文件：`batch4e-governance-instructions.md` 冻结验收条件。范围：252 家候选逐家查审计意见、处罚/立案/公开谴责、控股股东质押；不改阈值、不扩范围、不改个股/指数/ETF。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| J1 | 三项治理数据对 252 家的覆盖率逐项报告；来源与接口有记录 | `tools/build_governance_data.py`、`data/quality-pool/report.json`、报告 §1b | 审计 252/252、处罚 252/252、质押 227/252；表级元数据 | ✅ |
| J2 | 取不到 → 待定、不入池；有测试证明不会默认通过 | `engine/quality/metrics.py`、`evaluator.py` | `test_audit_condition_pass_fail_pending`、`test_penalty_condition_pass_fail_pending`、`test_pledge_condition_pass_fail_pending`；18 家待定 | ✅ |
| J3 | 每家因治理出池的公司都有可核对证据（事由、日期、文号或链接） | `tools/run_quality_pool.py::_governance_evidence`、报告 §1c | 26 条条件证据 / 24 家公司；审计类型、公告文号或链接、质押比例 | ✅ |
| J4 | 我抽查的公司治理判定可由快照复核 | `run_quality_pool.py --show`、`governance-*.csv.gz` | 600519 逐年审计意见、处罚检索、控股股东 0% 质押 | ✅ |
| J5 | 周期属性标记与个股政策企业族分类一致（测试） | `engine/quality/cycle.py` 复用 `classify_family` | `test_cycle_marker_matches_policy_family_classification` | ✅ |
| J6 | 报告措辞修正到位（测试扫描旧句式） | `engine/quality/evaluator.py`、`engine/quality/report.py` | `test_condition_reason_wording_is_not_misleading`、`test_4e_report_has_governance_sections_and_no_misleading_wording` | ✅ |
| J7 | 第四批快照、两本既有账本、个股结论、指数与 ETF 结果不变；入池账本只追加；全部测试通过 | 旧快照/`report-4d.json` 未改 | 个股 7 条、候选 A 3 条、官方估值 4 条 0 问题；池账本 2 条 0 问题；**532 passed** | ✅ |
| J8 | DELIVERY.md 一页以内汇总，含复现命令、测试结果、提交 ID | `DELIVERY.md`、`docs/phase4e-delivery.md` | 入口 §1–§8 | ✅ |

---

# 第四阶段第六批验收清单（好公司池收尾，K1–K5）

对应文件：`batch4f-closeout-instructions.md` 冻结验收条件。范围：按 K1–K3 重跑 252 家候选，
出第六批快照与报告、第五批→第六批对照表、入池账本追加；不改阈值、不扩候选、不改个股/指数/ETF。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| K1 | 14 家质押表解析失败逐家给出最终来源或无法取得原因；剩余待定 ≤ 14 | `tools/build_governance_data.py`、`data/policy-inputs/governance-review-v1.json`、报告 §1e | 18 行来源表（14 家第四批入池 + 4 家只差一条），逐家 URL/接口；剩余待定 **0** | ✅ |
| K2 | 无控股股东按第一大股东（含一致行动人合计）口径；50% 等于/略高有边界测试；格力附数据与来源 | `engine/quality/metrics.py`、`tools/build_governance_data.py`、政策 `pledge_ratio.no_controller` | `test_v12_pledge_boundary_exactly_50_and_above`；格力：珠海明骏 859,566,532/859,566,532 = 100%，含董明珠合计 87.38%，来源 2026 三季报 PDF | ✅ |
| K3 | 每条计入处罚标明主体与身份；药明康德按身份重判；主体不明 → 待定（测试） | 政策 `definitions.penalty`、`engine/quality/metrics.py`、报告 §1g | `test_v12_penalty_subject_unknown_is_pending`、`test_v12_penalty_other_shareholder_is_recorded_not_counted`；9 条计入记录均有主体/身份/文号；药明康德 3 条股东事项改只记录，回池 | ✅ |
| K4 | 对照表齐全；第五批快照与报告不变；既有账本不变、池账本只追加；全部测试通过 | 报告 §1d、`data/quality-pool/report-4e.json`、`snapshot-qps-1c1edb9a5fc01bc3/` | 第五批 120 家/18 家待定、快照 id 不变；池账本 3 条 0 问题；个股 7 条/候选 A 3 条/官方估值 4 条 0 问题；**541 passed** | ✅ |
| K5 | DELIVERY.md 一页以内，含复现命令、测试结果、提交 ID | `DELIVERY.md`、`docs/phase4f-delivery.md` | 入口 §1–§7；实现提交与交付提交见文件 | ✅ |

---

# 第四阶段第七批验收清单（稳定收息型 + 所有制，M1–M8）

对应文件：`batch4g-income-pool-instructions.md` 冻结验收条件。范围：新增 `income-pool-v1-draft`、
分红补 2016–2020、收息池试运行、所有制标记与新账本；不改好公司池、不做银行、不评券商保险。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| M1 | 规则版本化、每个参数带依据；代码无散落数字；每条条件有边界测试（等于、略差、缺年份） | `data/policies/income-pool-v1-draft.json`、`engine/income/{metrics,evaluator}.py` | `test_default_income_company_passes_all_conditions`、`test_roe_average_boundary`、`test_roe_min_boundary`、`test_loss_years_zero_required`、`test_profit_decline_years_boundary`、`test_profit_decline_missing_year_and_field`、`test_dividend_every_year_boundary_and_missing`、`test_dividend_payout_boundary`、`test_fcf_to_dividend_boundary_and_zero_dividend`、`test_net_debt_to_ebitda_strict_boundary`、`test_audit_penalty_pledge_conditions`、`test_missing_window_year_fails_financial_conditions`、`test_missing_recent_year_fails_dividend_and_fcf` | ✅ |
| M2 | 分红 2016–2020 补齐覆盖率报告；缺失按规则处理，不默认通过 | `tools/build_income_pool_data.py`、报告 §1c | 20 个报告期、2016–2020 共 13,610 行；各年有分红记录 78.0%/81.4%/75.6%/73.9%/73.5%；空派现 213 行为送转；缺失财年→待定测试 `test_missing_dividend_data_pends_dividend_and_fcf` | ✅ |
| M3 | 治理三项对候选覆盖率报告；复用口径与 v1.2 一致（测试） | `data/policy-inputs/governance-review-v1.json`、`engine/income/evaluator.py`、报告 §1b | 审计/处罚/质押 402/402（100%）；`test_income_governance_conditions_reuse_v12` 比对条件定义、参数与判定 | ✅ |
| M4 | 报告含第二部分 B 的全部内容 | `docs/phase4g-income-pool-report.md`、`data/income-pool/report.json` | 首页、入池 142、只差一条 244、漏斗、敏感性、30 只、行业分布、两个池交集 59、三种敏感性 | ✅ |
| M5 | 所有制标记覆盖率与来源报告；只标记不影响判定（测试） | `tools/build_income_pool_data.py`、`data/policy-inputs/ownership-review-v1.json`、报告 §1e | 439 家 100% 标记；好公司池/收息池分布 10/22/93/6/7 与 18/38/76/3/7；`test_ownership_does_not_change_judgement` | ✅ |
| M6 | 抽查公司可由快照独立复算 | `tools/run_income_pool.py --show`、`engine.quality.snapshot` | `test_income_snapshot_and_report_and_ledger` 同快照重放逐家状态一致；600519 逐年复算 | ✅ |
| M7 | 好公司池快照、报告、账本不变；其他账本不变；新账本只追加；全部测试通过 | `data/quality-pool/*`、`data/ledger/*` | 好公司池快照/报告/账本 3 条未改；个股 7、候选 A 3、官方估值 4 条 0 问题；新账本 1 条 0 问题；**563 passed** | ✅ |
| M8 | DELIVERY.md 一页以内，含复现命令、测试结果、提交 ID | `DELIVERY.md`、`docs/phase4g-delivery.md` | 入口 §1–§7 | ✅ |

---

# 第四阶段第八批验收清单（银行标准 + 估值预演，N1–N7）

对应文件：`batch4h-bank-instructions.md` 冻结验收条件。范围：新增 `bank-pool-v1-draft`、
银行专项数据、银行池试运行、所有制标记、银行估值预演与新账本；不做券商/保险，不改既有池与结论。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| N1 | 规则版本化、每个参数带依据；每条条件有边界测试 | `data/policies/bank-pool-v1-draft.json`、`engine/bank/{metrics,evaluator}.py` | `test_policy_versioned_and_all_parameters_have_basis`、`test_roe_boundaries`、`test_roe_recent_boundary`、`test_loss_npl_provision_core_boundaries`、`test_dividend_boundaries_and_missing`、`test_audit_penalty_pledge_conditions` | ✅ |
| N2 | 各项银行指标来源与覆盖率逐项报告；缺失不默认通过 | `tools/build_bank_pool_data.py`、报告 §1b | 1,773 行银行指标；36 家评价银行 ROE/不良/5 年变化/拨备/核心一级/分红/审计/处罚/质押均 36/36；缺银行指标→待定测试 `test_missing_bank_metric_and_annual_report` | ✅ |
| N3 | 报告含 B 节全部内容 | `docs/phase4h-bank-pool-report.md` | 首页、名单 11、只差一条 13、漏斗、敏感性、两家测试银行、所有制、预演摘要 | ✅ |
| N4 | 估值预演每家有路线/状态/等级/价值/预演结论；正式账本与 CLI 不变（测试） | `tools/run_bank_valuation_rehearsal.py`、`docs/phase4h-bank-valuation-rehearsal.md` | 36 家：B 31、C 5、资料不足 0；`test_bank_valuation_rehearsal_report`、`test_bank_ledger_and_other_ledgers_unchanged`、`test_cli_bank_conclusion_unchanged` | ✅ |
| N5 | 我抽查的银行指标可由快照与原始来源核对 | `data/bank-pool/snapshot-.../bank-indicators.csv.gz`、`--show` | 600036 2025 ROE 13.44、不良 0.94%、拨备 391.79%、核心一级 14.16% 与源逐项一致（测试断言） | ✅ |
| N6 | 既有池、账本、个股结论、指数结果不变；新账本只追加；全部测试通过 | 既有模块未改口径 | 好公司池 138、收息池 142、个股 7/候选 A 3/官方估值 4/池 3/收息 1 条 0 问题；银行账本 1 条 0 问题；**579 passed** | ✅ |
| N7 | DELIVERY.md 一页以内，含复现命令、测试结果、提交 ID | `DELIVERY.md`、`docs/phase4h-delivery.md` | 入口 §1–§7 | ✅ |

---

# 第四阶段第九批验收清单（批准落地 + 恒生科技，Q1–Q7）

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| Q1 | 三个 approved 文件参数与草案逐项相同；批准记录与复核日在文件中 | `data/policies/{quality-pool-v1.2,income-pool-v1,bank-pool-v1}.json` | `test_approved_policy_matches_draft_and_has_approval_record`（parameters/groups/sensitivity/scope 逐项相等；approval.next_review_date=2027-11-13） | ✅ |
| Q2 | 三池以 approved 规则重跑，名单与草案运行逐家相同；账本只追加 | `tools/run_{quality,income,bank}_pool.py`、三本 `*-pool.jsonl` | `test_approved_pool_run_matches_draft_list_and_appends`；质量 138 / 收息 142 / 银行 11，名单逐家相同，各追加 1 条 | ✅ |
| Q3 | 银行估值未启用（CLI 与正式结论账本无银行结论） | `data/policy-inputs/bank-check-v1-draft.json` | `test_bank_valuation_not_enabled`：approved=false、decision3 不启用；CLI 600036 `insufficient_data`、valuation null；个股账本 7 条无银行 | ✅ |
| Q4 | HSTECH 可行性报告逐项给出来源与判定；只用官方数据 | `tools/build_hstech_data.py`、`docs/phase4i-hstech-feasibility.md`、`data/coverage/hstech-feasibility.json` | `test_hstech_feasibility_uses_official_data_only`；全部 probes publisher=恒生指数公司、URL=hsi.com.hk；PE/DY/PB 历史 probes status=missing；第三方明列未采用 | ✅ |
| Q5 | HSTECH 已接入部分与其他指数共用代码；名称识别三种情形有测试 | `engine/series/profile.py`、`engine/pipeline.py`、`data/registry/instruments.json` | `test_hstech_name_resolution_and_other_hang_seng_out_of_scope`（三种名称 resolved；恒生指数/恒生国企/恒生科技ETF 范围外）；`test_hstech_uses_shared_profile_and_signal` | ✅ |
| Q6 | 既有指数、个股、ETF 结果与账本不变；全部测试通过 | 既有模块未改口径 | `test_existing_indices_and_ledgers_unchanged`：三大指数信号 complete/exit；候选 A 账本 3 条、官方估值账本 4 条、个股账本 7 条；**600 passed** | ✅ |
| Q7 | DELIVERY.md 一页以内 | `DELIVERY.md` | 入口 §1–§7 | ✅ |

---

# 第四阶段第十批验收清单（池成员估值，R1–R8）

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| R1 | 字段映射表齐全；新增数据 ≤ 200 MB；快照可重放 | `data/pool-valuation/snapshot-qps-f140a9790e4015ea`、`docs/phase4j-field-mapping.md` | 50 张表、15.08 MB；`test_batch_snapshot_size_and_replay`、`test_field_mapping_document_covers_policy_fields` | ✅ |
| R2 | 政策与估值代码未改（diff 证明）；适配层有测试 | `engine/pool_valuation/adapter.py`、`tools/build_pool_valuation_data.py` | `test_policy_and_valuation_code_unchanged`（`git diff e0da6d2 -- engine/policy engine/valuation` 为空）；`test_batch_snapshot_600519_recomputes_policy_inputs` | ✅ |
| R3 | 30 只回归：状态、等级、结论 30/30 相同，基准价值差 ≤ 1% | `tools/run_pool_valuation_regression.py`、`docs/phase4j-regression-30.md` | 30/30 通过；最大基准价值相对差 **0.0000%**；`test_regression_30_matches_original_paths` | ✅ |
| R4 | 池成员全部有结果；正式结论账本只追加，冲突规则有测试 | `tools/run_pool_valuation.py`、`data/ledger/equity-conclusions.jsonl` | 221 家全部有结果；首次写入 88 条，旧 7 条不变，账本 95 条；`test_p6_ledger_append_skip_conflict_and_check` 覆盖冲突 | ✅ |
| R5 | 报告含第 5 条全部内容 | `docs/phase4j-pool-valuation-report.md` | 结论分布、原因分组、88 家按距门槛排序（现价/门槛/起约束/等级/池/所有制/周期）、账本口径 | ✅ |
| R6 | 抽查公司可由快照独立复算 | `tools/run_pool_valuation.py --show 600519` | `test_show_recomputes_from_snapshot`；打印输入、三档价值、等级、结论 | ✅ |
| R7 | 指数、ETF、三池名单与账本不变；全部测试通过 | 既有模块未改 | 三池/指数/ETF 测试全过；旧 7 条结论前缀不变；**608 passed** | ✅ |
| R8 | DELIVERY.md 一页以内 | `DELIVERY.md` | 入口 §1–§7 | ✅ |

---

# 第四阶段第十一批验收清单（对话入口 + 端到端验收，S1–S9）

对应文件：`batch4k-dialog-instructions.md` 冻结验收条件。范围：周期行业清单补全、标签与小问题、
创业板指/159915 移出、前向记录 `update` 与定时脚本、`ask` 对话入口、端到端验收。
不改政策/池规则阈值；不启用银行估值；不加新标的类型。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| S1 | 周期清单版本化、带依据；重跑结果与变化清单齐全；君正集团判定附说明 | `data/policies/cyclical-industries-v1.json`、`tools/run_cyclical_rerun.py`、`docs/phase4k-cyclical-rerun.md` | 申万 2021 版依据与 URL；221 池成员 + 30 测试股重跑；池成员改变 2 家（君正集团、扬农化工）、测试股 0 家；君正专节说明（化学原料、波动 18.3%、旧关键词漏判）；`test_rerun_changes_are_recorded`、`test_list_is_versioned_with_shenwan_basis`、`test_matching_is_exact_for_chemical_subindustries` | ✅ |
| S2 | 正式与研究标签正确（测试） | `engine/policy/trial.py`、`engine/ask/service.py`、`engine/models/registry.py` | 正式结论标签 `正式结论（政策 equity-policy-v1.1，已批准）`、`is_trial_only=false`；validated 版本 `0.2.0`；指数/ETF/银行卡片无「正式结论」；`test_live_policy_conclusion_label_is_formal`、`test_formal_and_research_labels`、`test_research_never_has_formal_conclusion`、`test_validated_model_versions_have_no_research_suffix` | ✅ |
| S3 | 创业板指与 159915 移出；其余指数、ETF 结果不变 | `data/registry/instruments.json`、`engine/contracts/reason_codes.py` | 399006 scope out_of_scope + `INDEX_REMOVED_OFFICIAL_HISTORY_UNAVAILABLE`；159915 scope out_of_scope + `ETF_TRACKING_INDEX_REMOVED`；官方估值账本旧 4 条（含 399006 2026-11-11）不变；沪深300/中证500/中证1000/恒生科技/510300/510500/512100 不变；`test_cli_chinext_is_out_of_scope`、`test_etf_159915_registry_and_product_check`、`test_existing_official_valuation_ledger_records_unchanged`、`test_other_indices_and_etfs_still_available` | ✅ |
| S4 | `update` 只追加、重复跳过；定时脚本可安装可卸载，未经股东执行不自动安装 | `engine/forward/*`、`tools/update.py`、`scripts/install-daily-update.sh`、`scripts/uninstall-daily-update.sh` | 行情/官方估值/恒生科技月度/周信号均内容一致跳过、不同冲突；`tools/update.py --check` 0 问题；plist 经 `plistlib` 校验（18:30、RunAtLoad=false）；dry-run 不创建 LaunchAgents；引擎代码无自动调用安装脚本；`test_quote_snapshot_is_append_only_and_deduped`、`test_official_append_skips_duplicates_and_conflicts`、`test_hstech_parse_and_append`、`test_update_light_skips_heavy_series_and_logs`、`test_launchd_scripts_are_manual_only` | ✅ |
| S5 | `ask` 覆盖第二节全部意图，每类 ≥3 种说法有测试 | `engine/ask/*`、`data/ask/questions.json`、`tests/test_dialog.py` | 46 条问题覆盖 10 类（单股 6、银行 4、指数 4、ETF 4、池名单 4、是否在池 5、排行 4、历史 5、状态 4、范围外/歧义 6）；`test_question_set_has_at_least_40_and_covers_every_intent`、`test_every_question_matches_expected` | ✅ |
| S6 | 卡片结构符合第二节第 4 条；默认 ≤25 行 | `engine/ask/service.py`、`docs/phase4k-samples.md` | 第一行结论；现价/门槛/距门槛/三档价值及用途；起约束与中文未满足条件；池/所有制/周期属性/等级含义；局限；来源日期与政策版本；末行免责声明；全部问题集 8–10 行；`test_card_structure`、`test_detail_expands_and_keeps_disclaimer` | ✅ |
| S7 | 端到端问题集 ≥40 条全部通过；禁用词扫描、日期来源检查通过 | `tools/run_ask_acceptance.py`、`docs/phase4k-acceptance.md` | 46/46 问题、30/30 测试股、4/4 指数（含 HSTECH）、2/2 银行、3/3 ETF；禁用词 0；日期/来源/免责声明齐全；研究模型无正式结论；`test_forbidden_words_absent`、`test_every_question_matches_expected` | ✅ |
| S8 | 全部既有测试通过；所有账本只追加 | 既有模块；`data/ledger/*` | 本地 **660 passed**；`equity-conclusions.jsonl` 98 条 `--check` 0 问题；候选 A 3、官方估值 4、恒生科技月度 1、三池/银行账本 0 问题；`test_pool_valuation.py::test_policy_and_valuation_code_unchanged` 已更新为「仅允许结论标签与账本改判能力变化」；账本 `check` 全部通过 | ✅ |
| S9 | DELIVERY.md 一页以内，附 5 个真实问答样例 | `DELIVERY.md`、`docs/phase4k-samples.md` | 入口 §1–§8；5 个真实样例（茅台、招商银行、沪深300、好公司池、比亚迪） | ✅ |


---

# 第四阶段第十一批收尾验收清单（T1–T3 / U1–U4）

对应文件：`batch4k-r-closeout-instructions.md`。范围：只读已提交快照、账本改判记录、launchd 测试。

| 编号 | 通过标准 | 实现位置 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| T1 | `ask`、`update`、池成员估值及测试不读 `data/cache/` 或被忽略路径；所有制改读已提交快照；扫描测试 | `engine/ask/service.py`、`tools/run_pool_valuation.py`、`tools/run_income_pool.py`、`tests/test_clean_inputs.py` | 所有制/分红改读 `data/income-pool/snapshot-qps-d8fb7e3718675b93/`；`update` 状态改读 `data/ledger/update-log.jsonl`；运行时扫描 0 命中；`test_runtime_code_has_no_cache_directory_reference`、`test_ownership_comes_from_committed_snapshot` | ✅ |
| T2 | 最新正式结论遇无法判断时追加改判记录；原因码/中文原因/政策/快照/分析日；旧记录不改；从未有正式结论不写 | `engine/policy/conclusion_ledger.py`、`engine/policy/conclusion.py`、`engine/cli.py`、`engine/ask/service.py`、`tools/run_cyclical_rerun.py` | 君正 601216、扬农 600486 各 1 条 reversal（`MODEL_FAMILY_NOT_IMPLEMENTED` + `CYCLICAL_INDUSTRY_LIST_MATCH`）；账本 98 条 0 问题；历史问答 2 条且最新为无法判断；`test_reversal.py` 7 项 | ✅ |
| T3 | 非 macOS 跳过 launchd 测试并注明原因 | `tests/test_forward_update.py` | `@pytest.mark.skipif(sys.platform != "darwin", reason="定时任务只支持 macOS（launchd）")`；macOS 下 plist 校验通过 | ✅ |
| U1 | 干净目录（`git archive`）中全部测试与 46 条问题集通过；代码扫描通过 | `scripts/verify-clean-archive.sh`、`tests/test_clean_inputs.py` | `git archive HEAD` 到临时目录（无 `.venv`/`data/cache`）：**659 passed + 1 skipped**（无 `.git` 的 diff 测试跳过）、问题集 **46/46**、30 股/4 指数/2 银行/3 ETF 全过 | ✅ |
| U2 | 君正、扬农各一条改判；“君正集团以前的结论”显示两条，最新为无法判断及原因；旧记录不变 | `data/ledger/equity-conclusions.jsonl`、`engine/ask/service.py` | 601216/600486 各 2 条；最新 `record_type=reversal`、`conclusion=undetermined`、原因码/中文原因/分析日/快照/`reversal_of` 齐全；旧记录 record_id 未变；`test_real_ledger_has_two_reversals_for_junzheng_and_yangnong`、`test_history_question_shows_two_records_and_reason` | ✅ |
| U3 | 其他账本、结论、池名单不变；全部测试通过 | 既有模块 | 候选 A 3、官方估值 4、三池/银行账本不变；池名单 138/142/11 不变；本地 660 passed | ✅ |
| U4 | DELIVERY.md 一页以内 | `DELIVERY.md` | 81 行；含 U1–U4、复现命令、干净归档结果 | ✅ |
