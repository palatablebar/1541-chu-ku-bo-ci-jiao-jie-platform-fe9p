# 出库波次交接台验证目录

AI/ML · 传统ML · 中型

修改业务链路时先检查受影响模块的测试，再运行整套 `pytest`。回归记录须能够定位到具体单据或计算值。

## 功能模块

| 编号 | 功能 | 核对内容 | 核心文件 | 独立测试 |
|---|---|---|---|---|
| M1 | 波次入站 | 单据版次与隔离 | `src/waveclerk/pick_manifests/service.py` | `tests/test_pick_manifests.py` |
| M2 | 交班记录 | 截止日和成熟标签 | `src/waveclerk/shift_vintage/service.py` | `tests/test_shift_vintage.py` |
| M3 | 通道负荷 | 原始数据到特征 | `src/waveclerk/aisle_load/service.py` | `tests/test_aisle_load.py` |
| M4 | 完工推算 | 训练及可重复推断 | `src/waveclerk/handoff_duration/service.py` | `tests/test_handoff_duration.py` |
| M5 | 回放误差 | 回放误差及可信区间 | `src/waveclerk/completion_bands/service.py` | `tests/test_completion_bands.py` |
| M6 | 货型变化 | 差异定位和外推审查 | `src/waveclerk/order_mix/service.py` | `tests/test_order_mix.py` |
| M7 | 通道分派 | 硬规则与额度分配 | `src/waveclerk/aisle_dispatch/service.py` | `tests/test_aisle_dispatch.py` |
| M8 | 交班凭证 | 单据及额度证据 | `src/waveclerk/wave_recap/service.py` | `tests/test_wave_recap.py` |
| M9 | 当班增援 | 机动人数与救回波次 | `src/waveclerk/reinforce/service.py` | `tests/test_reinforce.py` |

## 深度登记

| 编号 | 边界 | 判断 | 处理位置 | 测试定位 |
|---|---|---|---|---|
| TRAP-14 | 同版不同读数 | 两条都隔离，换序结论一致 | `src/waveclerk/pick_manifests/service.py` | `tests/test_pick_manifests.py::test_conflicting_same_version_is_order_independent` |
| TRAP-19 | 多个规则或外推标记重叠 | 硬约束优先，保留全部命中码 | `src/waveclerk/aisle_dispatch/service.py` | `tests/test_aisle_dispatch.py::test_hard_rule_precedes_outside_reference` |
| TRAP-23 | 标签落在窗口的终点 | 终点进入隔离段，窗口为半开区间 | `src/waveclerk/shift_vintage/service.py` | `tests/test_shift_vintage.py::test_half_open_days` |
| TRAP-32 | 大偏移细方差与常数列 | 中心化后累加，常数列尺度为一 | `src/waveclerk/aisle_load/service.py` | `tests/test_aisle_load.py::test_cancellation_safe_scaling` |
| TRAP-42 | 拟合窗口后才回传结果 | 依标签成熟时间屏蔽，未来数据不参与拟合 | `src/waveclerk/shift_vintage/service.py` | `tests/test_shift_vintage.py::test_mature_label_cannot_cross_partition` |
| TRAP-43 | 回放没有成熟记录 | 记录未定义状态，避免以零误差替代 | `src/waveclerk/completion_bands/service.py` | `tests/test_completion_bands.py::test_no_outcome_gets_undefined_metrics` |
| TRAP-44 | 校准样本不足 | 分派前转人工判读并保留原因 | `src/waveclerk/completion_bands/service.py` | `tests/test_completion_bands.py::test_empty_calibration_requires_review` |
| TRAP-53 | 单条推断与混批 | 复用冻结参数，各条估计完全一致 | `src/waveclerk/handoff_duration/service.py` | `tests/test_handoff_duration.py::test_input_batch_invariance` |
| TRAP-54 | 特征版本与模型不配对 | 拒绝继续推断并要求重拟合 | `src/waveclerk/handoff_duration/service.py` | `tests/test_handoff_duration.py::test_mismatched_model_input_version_fails` |
| TRAP-61 | 增员救回用点估计而非区间上沿 | 以上沿回到装车缓冲内为派人门槛，点估计只作展示 | `src/waveclerk/reinforce/service.py` | `tests/test_reinforce.py::test_sent_ticket_always_within_truck_buffer` |
| TRAP-62 | 机动人手被最紧迫单吃光 | 先最大化救回波次数，同救回数下最少派人，按需求人数升序贪心 | `src/waveclerk/reinforce/service.py` | `tests/test_reinforce.py::test_greedy_prefers_more_waves_over_urgency` |
| TRAP-63 | 缺测或超域单被默默派人 | 缺人数/缺缓冲/超训练域/基础人工一律留人工，暂停照旧 | `src/waveclerk/reinforce/service.py` | `tests/test_reinforce.py::test_missing_pickers_requires_manual_review` |
| TRAP-64 | 重跑增援名单漂移 | 同批数据与机动人数重跑戳记、名单、人数一致，与入站顺序无关 | `src/waveclerk/reinforce/service.py` | `tests/test_reinforce.py::test_input_order_cannot_change_reinforce_order` |
