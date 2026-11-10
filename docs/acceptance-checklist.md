# 历史风险验收清单（阶段一版）

来源：设计书 §10 保留的历史审计要求、§12 必须存在的验收场景，以及本轮实际施工中发现的问题。
每一项都必须能在新项目里找到对应实现与测试；不继承旧版的投资政策，只继承「检查项」。

图例：✅ 已在阶段一实现并有测试；🟡 已建立机制但只能部分验证（依赖阶段二/三）；
⬜ 明确留到后续阶段。

## A. 数据状态

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| A1 | 零与缺失分离，缺失不得为 0 | `engine/data/facts.py`（`known_zero` vs `missing`，构造即校验） | `test_facts.py::test_zero_and_missing_are_different_states` | ✅ |
| A2 | 不适用（如银行的净债务）不得当作 0 或缺失混同 | `Fact.not_applicable` + `tools/build_snapshots.py` 银行为银行模板标注 not_applicable | `test_facts.py::test_not_applicable_is_not_zero` | ✅ |
| A3 | 过期数据不得冒充最新 | `DataStatus.STALE` + `Fact.stale` | `test_facts.py::test_stale_is_not_treated_as_fresh` | ✅ |
| A4 | 多来源冲突不得静默取一个 | `Fact.conflict` 要求至少两个取值，`value=None` | `test_facts.py::test_conflict_requires_two_values` | ✅ |
| A5 | NaN / Infinity 不得进入计算与输出 | `engine/data/numeric.py`、`Fact.__post_init__`、出口 `_check_numbers` | `test_facts.py::test_nan_is_rejected`、`test_contract.py::test_nan_in_payload_is_rejected` | ✅ |
| A6 | 指数点位不得标成人民币 | `payloads.amount()` 拒绝点位带币种；出口再校验 | `test_contract.py::test_index_point_with_currency_is_rejected` | ✅ |
| A7 | 金额必须带币种 | 出口 `_check_numbers` | `test_contract.py::test_money_without_currency_is_rejected` | ✅ |

## B. 时点与重放

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| B1 | 每条事实记录公告/可获得时间，抓取时间单独记录 | `Fact.available_at` / `fetched_at`；构建脚本逐条写入 | `test_snapshot_replay.py::test_point_in_time_filter_hides_later_disclosures` | ✅ |
| B2 | 历史分析不得读取当时尚未披露的数据 | `engine/data/snapshot.py::facts_available_by`（可获得时间未知的事实不放行） | `test_snapshot_replay.py::test_facts_without_availability_are_never_released` | ✅ |
| B3 | 快照内容可校验（篡改即报错） | `Snapshot.from_dict` 重算内容哈希 | `test_snapshot_replay.py::test_tampered_snapshot_is_rejected` | ✅ |
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
| C6 | 文本与 JSON 使用同一套校验 | `reporting/json_exporter.py`、`reporting/text_summary.py` 前置 `enforce` | `test_contract.py::test_text_and_json_share_the_same_validation`、`test_cli.py` | ✅ |
| C7 | 未登记的原因码视为违规（防止文档与代码漂移） | `validate_result` 检查 `reason_codes` 注册表 | `test_contract.py::test_unknown_reason_code_is_rejected` | ✅ |
| C8 | 载荷类型必须与资产类别一致 | `payloads.PAYLOAD_SCHEMAS` + 出口校验 | `test_contract.py::test_payload_type_must_match_asset_class` | ✅ |
| C9 | unsupported 结果不得携带定价结果 | `decision/policy.py::assert_not_fabricated` | `test_pipeline.py::test_equity_result_is_unsupported_with_facts` | ✅ |

## D. 身份与范围

| # | 检查项 | 实现位置 | 测试 | 状态 |
|---|---|---|---|---|
| D1 | 不再用代码长度判断类型 | `identity/normalize.py`（不做类型推断）+ `resolver.py` | `test_identity.py::test_index_codes_resolve` | ✅ |
| D2 | 代码冲突时列候选，不猜 | `resolver._resolve_code` → `ambiguous` | `test_identity.py::test_code_conflict_is_ambiguous_not_guessed` | ✅ |
| D3 | 只说指数时不擅自选 ETF | 精确名称匹配优先；ETF 全名不注册为裸别名 | `test_identity.py::test_index_names_resolve_to_index_not_etf` | ✅ |
| D4 | 具体 ETF 必须核实真实跟踪指数 | `data/registry/instruments.json` 的 `underlying_index_id` + 基金概况页证据 | `test_identity.py::test_each_verified_etf_maps_to_correct_index` | ✅ |
| D5 | 行业/策略/增强/跨境对象正确判为范围外 | 注册表 `scope` 字段 + 名称排除规则 | `test_identity.py::test_out_of_scope_products_are_recognized` | ✅ |
| D6 | 名称启发式只能排除、不能纳入 | `resolver._heuristic_out_of_scope` 只返回范围外 | `test_identity.py::test_heuristic_never_includes_unverified` | ✅ |
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
| F1 | 官方指数估值历史只有 20 个交易日，第三方只有月度 | 阶段三「5 年周度分位」无法用现有免费数据完成 | 请设计方决定：降频到月度、放宽周度要求，或另找数据源 |
| F2 | 全收益指数行情不可得 | 回测缺少股息收益 | 阶段三前确认是否接受价格指数 + 显式股息调整 |
| F3 | 净值披露时间不可得 | 日终折溢价无法严格成立 | 阶段三改为「净值日期对齐 + 明确标注时点不确定性」 |
| F4 | 深交所 ETF 清单接口报错 | 深市 ETF 上市地缺少官方清单证据 | 以代码段 + 行情清单标注为推断，并在报告中写明 |
| F5 | 银行资本/资产质量字段未采集 | 阶段二银行模型前置数据不足 | 阶段二单独核实接口并补快照 |
