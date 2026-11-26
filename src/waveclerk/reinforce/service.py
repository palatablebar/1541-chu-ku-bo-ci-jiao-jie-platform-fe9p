from dataclasses import replace
from hashlib import sha256
import json

from ..aisle_load.contracts import RAW_KEYS
from ..aisle_load.service import numeric_fields
from ..handoff_duration.service import predict_one
from ..completion_bands.service import check_point
from .contracts import (
    MAX_PICKERS, TRUCK_BUFFER_MIN, POLICY_VERSION,
    MANUAL_MISSING_PICKERS, MANUAL_MISSING_SLACK, MANUAL_OUT_OF_DOMAIN,
    MANUAL_BASE_REVIEW, MANUAL_SPARSE_CALIBRATION, PAUSE_BULKY_INTERLOCK,
    SKIP_NOT_LATE, SKIP_UNRESCUABLE, SKIP_FLEX_EXHAUSTED,
    STATE_SENT, STATE_MANUAL, STATE_PAUSED, STATE_SKIPPED,
    ReinforceOption, ReinforceLine, ReinforceLedger, ReinforceOrder,
)

PICKERS_POSITION = RAW_KEYS.index('pickers')
SLACK_POSITION = RAW_KEYS.index('slack_min')


def truck_buffer(raw_values):
    slack = raw_values[SLACK_POSITION]
    return slack - TRUCK_BUFFER_MIN


def _score_with_pickers(kernel, verification, feature_state, vector, total_pickers):
    """以冻结的回归与校准重算增员后的完工估计，返回校准后的评估与新增偏离。

    仅统计增员后相对基础单新引入的包络偏离；基础单自带的偏离已在主链路
    分布监测里登记，不在此重复判超域。
    """
    modified = tuple(total_pickers if slot == PICKERS_POSITION else value
                     for slot, value in enumerate(vector.input_values))
    normalized, factors, flags = numeric_fields(modified, feature_state)
    scenario = replace(vector, values=normalized, factors=factors,
                       input_values=modified, excursions=flags)
    assessment = check_point(predict_one(kernel, scenario), verification)
    new_excursions = tuple(name for name in flags if name not in vector.excursions)
    return assessment, new_excursions


def build_options(kernel, verification, feature_state, vector):
    """逐档推演增 1 人至训练域允许的上限，沿用本班回归与校准。

    超训练域仅指增员动作把特征推到训练包络外（新增 excursions）；基础单
    本就存在的偏离不算增员推演引入，不阻断在域救回路径。
    """
    base_pickers = vector.input_values[PICKERS_POSITION]
    options = []
    for extra in range(1, MAX_PICKERS - int(base_pickers) + 1):
        total = base_pickers + extra
        assessment, new_excursions = _score_with_pickers(
            kernel, verification, feature_state, vector, total)
        in_domain = not new_excursions
        buffer_value = truck_buffer(vector.input_values)
        rescues = in_domain and assessment.upper is not None and assessment.upper <= buffer_value
        options.append(ReinforceOption(
            extra, total, assessment.estimate, assessment.upper,
            new_excursions, in_domain, rescues))
    return tuple(options)


def _line(key, cohort, state, reason, vector, base_assessment, base_late, *,
          extra=0, total=None, revised_estimate=None, revised_upper=None,
          required=0, in_domain_path=True, new_excursions=(), options=(),
          base_state='', base_reason='', buffer_value=None):
    raw_pickers = None if 'pickers' in vector.missing else vector.input_values[PICKERS_POSITION]
    # 缺装车缓冲量测时，输入向量里只剩拟合补值，缓冲不可作为裁决依据。
    if 'slack_min' in vector.missing:
        buffer_value = None
    return ReinforceLine(
        key=key, cohort=cohort, state=state, reason=reason,
        base_pickers=raw_pickers,
        truck_buffer=buffer_value,
        base_estimate=base_assessment.estimate,
        base_upper=base_assessment.upper,
        base_late=base_late,
        extra_pickers=extra, total_pickers=total,
        revised_estimate=revised_estimate, revised_upper=revised_upper,
        required_pickers=required, in_domain_path=in_domain_path,
        new_excursions=tuple(new_excursions), options=tuple(options),
        base_state=base_state, base_reason=base_reason)


def evaluate_ticket(kernel, verification, feature_state, vector, base_job):
    """对一张当班单给出增援裁决（不含机动额度分配）。"""
    base_assessment = check_point(predict_one(kernel, vector), verification)
    base_late = (base_assessment.upper is not None
                 and base_assessment.upper > truck_buffer(vector.input_values))
    common = dict(vector=vector, base_assessment=base_assessment, base_late=base_late,
                  base_state=base_job.state, base_reason=base_job.reason,
                  buffer_value=truck_buffer(vector.input_values))

    # 安全暂停照旧：大件互锁单不进入增援推演。
    if base_job.state == '暂停' or PAUSE_BULKY_INTERLOCK in base_job.rule_hits:
        return _line(vector.key, vector.cohort, STATE_PAUSED, PAUSE_BULKY_INTERLOCK, **common)

    # 缺实测人数或缺装车缓冲的单留人工。
    if 'pickers' in vector.missing:
        return _line(vector.key, vector.cohort, STATE_MANUAL, MANUAL_MISSING_PICKERS, **common)
    if 'slack_min' in vector.missing:
        return _line(vector.key, vector.cohort, STATE_MANUAL, MANUAL_MISSING_SLACK, **common)

    # 校准不足、区间不可用，以及基础处置已转人工（未知作业区/缺测/偏离）的单留人工。
    if base_assessment.upper is None or base_assessment.uncertain:
        return _line(vector.key, vector.cohort, STATE_MANUAL, MANUAL_SPARSE_CALIBRATION, **common)
    if base_job.state == '人工判读':
        return _line(vector.key, vector.cohort, STATE_MANUAL, MANUAL_BASE_REVIEW, **common)

    options = build_options(kernel, verification, feature_state, vector)
    rescue = next((option for option in options if option.rescues), None)

    # 只有原本赶不上的单才有增援资格。
    if not base_late:
        return _line(vector.key, vector.cohort, STATE_SKIPPED, SKIP_NOT_LATE,
                     options=options, **common)

    if rescue is None:
        # 增员把特征推到训练包络外（推演超出训练范围）的单留人工，而非直接放弃。
        out_of_domain = any(not option.in_domain for option in options)
        if out_of_domain:
            first_bad = next(option for option in options if not option.in_domain)
            return _line(vector.key, vector.cohort, STATE_MANUAL, MANUAL_OUT_OF_DOMAIN,
                         options=options, in_domain_path=False,
                         new_excursions=first_bad.new_excursions, **common)
        return _line(vector.key, vector.cohort, STATE_SKIPPED, SKIP_UNRESCUABLE,
                     options=options, **common)

    return _line(vector.key, vector.cohort, STATE_SKIPPED, SKIP_FLEX_EXHAUSTED,
                 extra=rescue.extra_pickers, total=rescue.total_pickers,
                 revised_estimate=rescue.estimate, revised_upper=rescue.upper,
                 required=rescue.extra_pickers, options=options, **common)


def plan_reinforcement(kernel, verification, feature_state, live_vectors, base_jobs, flex_pool):
    """按机动人数生成当班增援单：先让最多波次赶上装车，救回一样多时少派人。

    取每张可救回单的最少增援人数，按需求人数升序（同额按单号）贪心；该顺序
    对“先最大化救回数、再最小化派人”的字典序目标最优。
    """
    if type(flex_pool) is not int or not 1 <= flex_pool <= 30:
        raise ValueError('机动人数须是 1 到 30 的整数')
    job_by_key = {job.key: job for job in base_jobs}
    lines = [evaluate_ticket(kernel, verification, feature_state, vector, job_by_key[vector.key])
             for vector in live_vectors]

    eligible = {line.key for line in lines if line.required_pickers > 0}
    admission = {line.key: line.required_pickers
                 for line in sorted(lines, key=lambda item: (item.required_pickers, item.key))
                 if line.key in eligible}
    remaining = flex_pool
    sent_keys = set()
    for key, required in admission.items():
        if required <= remaining:
            remaining -= required
            sent_keys.add(key)

    resolved = []
    for line in lines:
        if line.key in sent_keys:
            resolved.append(replace(line, state=STATE_SENT, reason=STATE_SENT))
        else:
            resolved.append(line)
    lines = tuple(sorted(resolved, key=lambda line: line.key))

    sent = tuple(line.key for line in lines if line.state == STATE_SENT)
    ledger = ReinforceLedger(
        flex_pool=flex_pool,
        flex_used=flex_pool - remaining,
        flex_balance=remaining,
        waves_rescued=len(sent),
        sent=sent,
        manual=tuple(line.key for line in lines if line.state == STATE_MANUAL),
        paused=tuple(line.key for line in lines if line.state == STATE_PAUSED),
        skipped=tuple(line.key for line in lines if line.state == STATE_SKIPPED),
    )
    return lines, ledger


def _compact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def issue_order(kernel, verification, feature_state, live_vectors, base_jobs,
                flex_pool, as_of, batch_hash):
    lines, ledger = plan_reinforcement(
        kernel, verification, feature_state, live_vectors, base_jobs, flex_pool)
    policy_seed = (('flex', flex_pool), ('form', POLICY_VERSION),
                   ('model', kernel.name), ('x', feature_state.fingerprint),
                   ('verify', verification.method))
    policy_hash = sha256(_compact(policy_seed).encode()).hexdigest()[-16:]
    path = tuple((line.key, line.state, line.reason, line.extra_pickers,
                  line.total_pickers, line.revised_upper) for line in lines)
    stamp_seed = '|'.join((batch_hash, policy_hash, _compact(path),
                           _compact((ledger.flex_pool, ledger.flex_used, ledger.flex_balance))))
    stamp = sha256(stamp_seed.encode()).hexdigest()[4:20]
    return ReinforceOrder(
        lines=lines, ledger=ledger,
        feature_stamp=feature_state.fingerprint, model=kernel.name,
        verification=verification.method, flex_pool=flex_pool, as_of=as_of,
        batch_hash=batch_hash, policy_hash=policy_hash, stamp=stamp)
