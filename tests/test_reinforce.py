import copy
from dataclasses import replace

import pytest

from waveclerk.pipeline import open_fixture, reinforce, worksheet, calibrate_cell
from waveclerk.aisle_load import service as feature_service
from waveclerk.aisle_dispatch import service as dispatch_service
from waveclerk.order_mix import service as monitor_service
from waveclerk.reinforce import service as reinforce_service
from waveclerk.reinforce.contracts import (
    MAX_PICKERS, ReinforceLedger, ReinforceLine,
    STATE_SENT, STATE_MANUAL, STATE_PAUSED, STATE_SKIPPED,
    MANUAL_MISSING_PICKERS, MANUAL_MISSING_SLACK, MANUAL_OUT_OF_DOMAIN,
    MANUAL_BASE_REVIEW, MANUAL_SPARSE_CALIBRATION, PAUSE_BULKY_INTERLOCK,
    SKIP_NOT_LATE, SKIP_UNRESCUABLE, SKIP_FLEX_EXHAUSTED,
)


@pytest.fixture(scope='module')
def fitted():
    doc = open_fixture()
    intake, windows, state = worksheet(doc['records'], doc['as_of'])
    features = feature_service.transform_windows(windows, state)
    kernel, verification, (_recent, shift) = calibrate_cell(features, state)
    baseline = monitor_service.fit_baseline(features.train, state.fingerprint)
    drift = monitor_service.scan_current(baseline, features.live, state.fingerprint)
    policy = dispatch_service.Policy.select(12, 'balanced')
    jobs = {a.raw.key: dispatch_service.recommend(a, frozenset(drift.domain), policy)
            for a in shift[1]}
    vectors = {v.key: v for v in features.live}
    return doc, state, kernel, verification, vectors, jobs


def dataset_with(slack_map):
    doc = open_fixture()
    records = copy.deepcopy(doc['records'])
    for row in records:
        if row['key'] in slack_map:
            row['slack_min'] = slack_map[row['key']]
    return records


def line_by_key(payload, key):
    return next(row for row in payload['rows'] if row['单号'] == key)


# ---------------------------------------------------------------------------
# 单票增员推演：沿用本班冻结回归与校准
# ---------------------------------------------------------------------------

def test_rescue_uses_frozen_model_and_calibration(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    # slack=38 的合成单原本晚点，+4 人后区间上沿回到缓冲内，且无新增偏离。
    vector = replace(vectors['WAV-1253'],
                     input_values=vectors['WAV-1253'].input_values[:5] + (38.0,))
    line = reinforce_service.evaluate_ticket(kernel, verification, state, vector, jobs['WAV-1253'])
    assert line.base_late is True
    assert line.required_pickers == 4
    rescue = next(opt for opt in line.options if opt.rescues)
    assert rescue.extra_pickers == 4
    assert rescue.new_excursions == () and rescue.in_domain
    assert rescue.upper <= line.truck_buffer
    # 更早的档位救不回，确保取的是最少增援人数。
    assert all(not opt.rescues for opt in line.options if opt.extra_pickers < 4)


def test_scenario_prediction_matches_pipeline_scoring(fitted):
    _doc, state, kernel, verification, vectors, _jobs = fitted
    vector = replace(vectors['WAV-1253'],
                     input_values=vectors['WAV-1253'].input_values[:5] + (38.0,))
    options = reinforce_service.build_options(kernel, verification, state, vector)
    # 与既有单票推演完全相同的重算路径。
    from waveclerk.handoff_duration.service import predict_one
    from waveclerk.completion_bands.service import check_point
    opt = options[0]
    modified = tuple(opt.total_pickers if i == 2 else x
                     for i, x in enumerate(vector.input_values))
    values, factors, _flags = feature_service.numeric_fields(modified, state)
    alt = replace(vector, values=values, factors=factors, input_values=modified)
    assessment = check_point(predict_one(kernel, alt), verification)
    assert opt.estimate == assessment.estimate
    assert opt.upper == assessment.upper


def test_picker_headcount_never_exceeds_trained_limit(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1273'], jobs['WAV-1273'])
    assert line.options
    assert max(opt.total_pickers for opt in line.options) <= MAX_PICKERS
    assert all(opt.extra_pickers >= 1 for opt in line.options)


# ---------------------------------------------------------------------------
# 缺实测人数 / 缺装车缓冲 -> 人工
# ---------------------------------------------------------------------------

def test_missing_pickers_requires_manual_review(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    # WAV-1270 在样例里 pickers 缺测（补值为 9）。
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1270'], jobs['WAV-1270'])
    assert line.state == STATE_MANUAL and line.reason == MANUAL_MISSING_PICKERS
    assert line.options == () and line.extra_pickers == 0


def test_missing_slack_requires_manual_review(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    vector = replace(vectors['WAV-1273'],
                     missing=vectors['WAV-1273'].missing + ('slack_min',))
    line = reinforce_service.evaluate_ticket(kernel, verification, state, vector, jobs['WAV-1273'])
    assert line.state == STATE_MANUAL and line.reason == MANUAL_MISSING_SLACK
    assert line.truck_buffer is None


# ---------------------------------------------------------------------------
# 推演超出训练范围 -> 人工
# ---------------------------------------------------------------------------

def test_out_of_domain_rescue_path_requires_manual(fitted, monkeypatch):
    # 增员在数学上只会降低人均类特征，真实量测下不会引入新偏离；这里用注入式
    # 构造验证守卫：当某档增员把特征推出训练包络且无在域救回路径时，必须留人工。
    _doc, state, kernel, verification, vectors, jobs = fitted
    from waveclerk.reinforce import contracts as rc

    vector = replace(vectors['WAV-1253'],
                     input_values=vectors['WAV-1253'].input_values[:5] + (38.0,),
                     excursions=(), missing=())
    real_options = reinforce_service.build_options(kernel, verification, state, vector)

    def fake_options(k, cal, fs_state, vec):
        # 所有增员档位都推出训练包络，且无在域救回路径。
        return tuple(replace(opt, new_excursions=('line_load',), in_domain=False, rescues=False)
                     for opt in real_options)

    monkeypatch.setattr(reinforce_service, 'build_options', fake_options)
    line = reinforce_service.evaluate_ticket(kernel, verification, state, vector, jobs['WAV-1253'])
    assert line.state == STATE_MANUAL and line.reason == MANUAL_OUT_OF_DOMAIN
    assert line.in_domain_path is False and line.new_excursions == ('line_load',)
    assert line.required_pickers == 0


# ---------------------------------------------------------------------------
# 安全暂停照旧
# ---------------------------------------------------------------------------

def test_bulky_interlock_remains_paused(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1258'], jobs['WAV-1258'])
    assert line.state == STATE_PAUSED and line.reason == PAUSE_BULKY_INTERLOCK
    assert line.options == ()


def test_base_manual_review_is_inherited(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    # WAV-1255 来自未登记作业区，基础处置即人工判读。
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1255'], jobs['WAV-1255'])
    assert line.state == STATE_MANUAL and line.reason == MANUAL_BASE_REVIEW


def test_sparse_calibration_requires_manual(fitted):
    from waveclerk.completion_bands.contracts import Verification
    _doc, state, kernel, verification, vectors, jobs = fitted
    sparse = Verification('sparse-interval', (), 0, None)
    line = reinforce_service.evaluate_ticket(
        kernel, sparse, state, vectors['WAV-1260'], jobs['WAV-1260'])
    assert line.state == STATE_MANUAL and line.reason == MANUAL_SPARSE_CALIBRATION
    assert line.base_upper is None


# ---------------------------------------------------------------------------
# 只有原本赶不上、增员后上沿回缓冲内的单才派人
# ---------------------------------------------------------------------------

def test_on_time_ticket_is_never_reinforced(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1260'], jobs['WAV-1260'])
    assert line.base_late is False
    assert line.state == STATE_SKIPPED and line.reason == SKIP_NOT_LATE


def test_unrescuable_late_ticket_is_skipped_not_sent(fitted):
    _doc, state, kernel, verification, vectors, jobs = fitted
    # 样例 slack=24 的晚点单，增到上限仍赶不上。
    line = reinforce_service.evaluate_ticket(
        kernel, verification, state, vectors['WAV-1253'], jobs['WAV-1253'])
    assert line.base_late is True
    assert line.state == STATE_SKIPPED and line.reason == SKIP_UNRESCUABLE
    assert line.required_pickers == 0


def test_end_to_end_rescue_and_greedy_headcount():
    # 三张同型晚点单：两张 +1 可救回，一张 +4 可救回。
    records = dataset_with({'WAV-1253': 42, 'WAV-1254': 42, 'WAV-1256': 38})
    tight = reinforce(records, 3)
    sent = [row['单号'] for row in tight['rows'] if row['处置'] == STATE_SENT]
    assert sorted(sent) == ['WAV-1253', 'WAV-1254']
    waiting = line_by_key(tight, 'WAV-1256')
    assert waiting['处置'] == STATE_SKIPPED and waiting['原因码'] == SKIP_FLEX_EXHAUSTED
    assert waiting['增援人数'] == 4  # 保留最少需求，只是机动不够
    assert tight['ledger']['机动结余'] == 1
    assert tight['metrics'][3] == {'label': '救回波次数', 'value': 2}

    funded = reinforce(records, 6)
    sent_funded = [row['单号'] for row in funded['rows'] if row['处置'] == STATE_SENT]
    assert sorted(sent_funded) == ['WAV-1253', 'WAV-1254', 'WAV-1256']
    assert funded['ledger']['机动结余'] == 0
    # 救回一样多（3 张）时只用最少人手 6，而不是把机动额度花满。
    assert funded['ledger']['已用机动人手'] == 6


def test_greedy_prefers_more_waves_over_urgency():
    # 一张要 +4 的高紧迫单（slack=38），对两张各 +1 的单（slack=42）。
    # 机动 4 人时，贪心应救两张 +1 的单，而不是只救一张 +4 的单。
    records = dataset_with({'WAV-1256': 38, 'WAV-1254': 42, 'WAV-1262': 42})
    payload = reinforce(records, 4)
    sent = sorted(row['单号'] for row in payload['rows'] if row['处置'] == STATE_SENT)
    assert sent == ['WAV-1254', 'WAV-1262']
    assert line_by_key(payload, 'WAV-1256')['处置'] == STATE_SKIPPED
    assert payload['metrics'][3]['value'] == 2


def test_sent_ticket_always_within_truck_buffer():
    records = dataset_with({'WAV-1253': 42})
    payload = reinforce(records, 10)
    for row in payload['rows']:
        if row['处置'] == STATE_SENT:
            assert row['原本赶不上'] is True
            assert row['增援后区间上沿'] <= row['装车缓冲']
            assert row['增援人数'] >= 1


# ---------------------------------------------------------------------------
# 交班凭证可对账 + 同批数据和人手重跑一致
# ---------------------------------------------------------------------------

def test_ledger_conservation_and_reconciliation():
    records = dataset_with({'WAV-1253': 42, 'WAV-1254': 42, 'WAV-1256': 38})
    payload = reinforce(records, 6)
    sent_heads = sum(row['增援人数'] for row in payload['rows']
                     if row['处置'] == STATE_SENT)
    assert sent_heads == payload['ledger']['已用机动人手']
    assert (payload['ledger']['已用机动人手'] + payload['ledger']['机动结余']
            == payload['ledger']['机动人数'])
    assert payload['ledger']['已增援名单'] == sorted(
        row['单号'] for row in payload['rows'] if row['处置'] == STATE_SENT)
    rows_check = {r['核对项']: r['数量'] for r in payload['reconciliation']}
    assert rows_check['机动人数'] == 6
    assert rows_check['已派机动人手'] == 6
    assert rows_check['机动结余'] == 0
    assert rows_check['救回波次'] == 3


def test_rerun_same_batch_and_flex_is_identical():
    records = dataset_with({'WAV-1253': 42, 'WAV-1256': 38})
    first = reinforce(records, 7)
    second = reinforce(copy.deepcopy(records), 7)
    assert first['stamp'] == second['stamp']
    assert first['rows'] == second['rows']
    assert first['ledger'] == second['ledger']


def test_input_order_cannot_change_reinforce_order():
    records = dataset_with({'WAV-1253': 42, 'WAV-1254': 42, 'WAV-1256': 38})
    forward = reinforce(records, 6)
    backward = reinforce(list(reversed(records)), 6)
    assert forward['stamp'] == backward['stamp']
    assert forward['rows'] == backward['rows']


def test_flex_change_changes_stamp_but_not_model_identity():
    records = dataset_with({'WAV-1253': 42})
    small = reinforce(records, 2)
    large = reinforce(records, 9)
    assert small['stamp'] != large['stamp']
    assert small['feature_stamp'] == large['feature_stamp']
    assert small['batch_hash'] == large['batch_hash']
    assert small['policy_hash'] != large['policy_hash']


def test_reinforce_does_not_mutate_base_disposition():
    records = dataset_with({'WAV-1253': 42})
    from waveclerk.pipeline import preview
    base = preview()
    base_run_id = base['run_id']
    payload = reinforce(records, 5)
    reinforced = line_by_key(payload, 'WAV-1253')
    # 增援单独立产出，记录基础规则层原因，但不改动原工作台对同批数据的处置结论。
    assert reinforced['基础原因'] in {'HANDOFF_AT_RISK', 'MERGE_BOTTLENECK', 'ABOVE_TRIGGER'}
    assert reinforced['基础处置'] in {'候选', '已分派', '候补'}
    assert preview()['run_id'] == base_run_id


# ---------------------------------------------------------------------------
# 入参校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad', [0, 31, -1, True, None, '8', 3.5])
def test_flex_workers_validation(bad):
    with pytest.raises(ValueError, match='机动人数'):
        reinforce(open_fixture()['records'], bad)


def test_ledger_rejects_unbalanced_account():
    with pytest.raises(ArithmeticError, match='人手账不平'):
        ReinforceLedger(flex_pool=5, flex_used=3, flex_balance=1, waves_rescued=1,
                        sent=('WAV-1',), manual=(), paused=(), skipped=())


def test_sent_line_must_be_late_and_within_buffer():
    doc = open_fixture()
    intake, windows, state = worksheet(doc['records'], doc['as_of'])
    features = feature_service.transform_windows(windows, state)
    kernel, verification, _ = calibrate_cell(features, state)
    vector = features.live[0]
    from waveclerk.completion_bands.service import check_point
    from waveclerk.handoff_duration.service import predict_one
    assessment = check_point(predict_one(kernel, vector), verification)
    with pytest.raises(ValueError, match='原本赶得上'):
        ReinforceLine(
            key=vector.key, cohort=vector.cohort, state=STATE_SENT, reason=STATE_SENT,
            base_pickers=5.0, truck_buffer=99.0, base_estimate=assessment.estimate,
            base_upper=assessment.upper, base_late=False, extra_pickers=1, total_pickers=6.0,
            revised_estimate=assessment.estimate, revised_upper=10.0, required_pickers=1,
            in_domain_path=True, new_excursions=(), options=(),
            base_state='观察', base_reason='BELOW_TRIGGER')
