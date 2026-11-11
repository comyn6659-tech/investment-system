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

## 阶段二遗留（不阻断本轮）

| # | 事项 | 处理建议 |
|---|---|---|
| G1 | 银行资本充足率/不良率/拨备覆盖率/风险加权资产无免费来源 | 需年报原文或持牌数据源；数学与约束已实现并有测试 |
| G2 | 600900 缺 FCFF 交叉检查（目标资本结构假设未批准） | 待 P11 政策决定 |
| G3 | 600519 两条路线差 24.3%，接近 25% 阈值 | 属终值增长与可持续增长口径问题，交设计方判断（P3） |
| G4 | 非经营性资产按 0、受限资金未拆分、少数股东按账面扣除 | 需要更细的报表科目后再收窄 |
| G5 | 业务证据由脚本整理，未做自动化财报文本阅读 | 后续阶段 |
