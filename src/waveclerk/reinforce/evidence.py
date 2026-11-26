from collections import Counter
from .contracts import (
    STATE_SENT, STATE_MANUAL, STATE_PAUSED, STATE_SKIPPED,
    SKIP_FLEX_EXHAUSTED,
)

REASON_TEXT = {
    'MISSING_PICKERS': '缺实测可用拣货员，增援人数无法起算',
    'MISSING_TRUCK_SLACK': '缺距装车交接量测，装车缓冲不可算',
    'REINFORCE_OUT_OF_DOMAIN': '增员推演超出训练包络，需人工判读',
    'BASE_MANUAL_REVIEW': '基础处置已转人工，增援沿用人工判读',
    'SPARSE_CALIBRATION': '校准样本不足，完工区间不可用',
    'BULKY_INTERLOCK': '大件通道互锁，安全暂停照旧',
    'ALREADY_CATCHES_TRUCK': '原本即赶得上装车，不予增援',
    'CANNOT_CATCH_WITH_FLEX': '机动人手上限内仍赶不上装车',
    'FLEX_HEADCOUNT_EXHAUSTED': '机动人手不足，留候补',
    '已增援': '增员后完工区间上沿回到装车缓冲内',
}


def option_rows(line):
    return [{
        '增援人数': option.extra_pickers,
        '合计拣货员': option.total_pickers,
        '替换估计': round(option.estimate, 4),
        '区间上沿': None if option.upper is None else round(option.upper, 4),
        '新增偏离': '、'.join(option.new_excursions) or '—',
        '在训练域内': option.in_domain,
        '可救回': option.rescues,
    } for option in line.options]


def line_row(line):
    return {
        '单号': line.key,
        '作业区': line.cohort,
        '处置': line.state,
        '原因码': line.reason,
        '处理依据': REASON_TEXT.get(line.reason, line.reason),
        '原拣货员': line.base_pickers,
        '装车缓冲': None if line.truck_buffer is None else round(line.truck_buffer, 4),
        '原估计': round(line.base_estimate, 4),
        '原区间上沿': None if line.base_upper is None else round(line.base_upper, 4),
        '原本赶不上': line.base_late,
        '增援人数': line.extra_pickers,
        '增援后拣货员': line.total_pickers,
        '增援后估计': None if line.revised_estimate is None else round(line.revised_estimate, 4),
        '增援后区间上沿': None if line.revised_upper is None else round(line.revised_upper, 4),
        '基础处置': line.base_state,
        '基础原因': line.base_reason,
    }


def order_payload(order):
    counts = Counter(line.state for line in order.lines)
    metrics = [
        {'label': '机动人数', 'value': order.ledger.flex_pool},
        {'label': '已派机动人手', 'value': order.ledger.flex_used},
        {'label': '机动结余', 'value': order.ledger.flex_balance},
        {'label': '救回波次数', 'value': order.ledger.waves_rescued},
        {'label': '人工判读', 'value': len(order.ledger.manual)},
        {'label': '安全暂停', 'value': len(order.ledger.paused)},
        {'label': '不增援', 'value': len(order.ledger.skipped)},
    ]
    return {
        'stamp': order.stamp,
        'batch_hash': order.batch_hash,
        'policy_hash': order.policy_hash,
        'feature_stamp': order.feature_stamp,
        'model': order.model,
        'verification': order.verification,
        'flex_pool': order.flex_pool,
        'as_of': order.as_of,
        'metrics': metrics,
        'flow_counts': sorted(counts.items()),
        'rows': [line_row(line) for line in order.lines],
        'options': {line.key: option_rows(line) for line in order.lines if line.options},
        'ledger': {
            '机动人数': order.ledger.flex_pool,
            '已用机动人手': order.ledger.flex_used,
            '机动结余': order.ledger.flex_balance,
            '救回波次': order.ledger.waves_rescued,
            '已增援名单': list(order.ledger.sent),
            '人工判读名单': list(order.ledger.manual),
            '暂停名单': list(order.ledger.paused),
            '不增援名单': list(order.ledger.skipped),
        },
    }


def reconciliation_evidence(order):
    """交班凭证可对账：人手守恒与名单一致。"""
    ledger = order.ledger
    sent_total = sum(line.extra_pickers for line in order.lines if line.state == STATE_SENT)
    if sent_total != ledger.flex_used:
        raise ArithmeticError('增援名单人手合计与机动账不符')
    if sorted(ledger.sent) != sorted(line.key for line in order.lines if line.state == STATE_SENT):
        raise ArithmeticError('增援名单与单据裁决不一致')
    rows = [
        {'核对项': '机动人数', '数量': ledger.flex_pool},
        {'核对项': '已派机动人手', '数量': ledger.flex_used},
        {'核对项': '机动结余', '数量': ledger.flex_balance},
        {'核对项': '当班单据', '数量': len(order.lines)},
        {'核对项': '救回波次', '数量': ledger.waves_rescued},
    ]
    rows.extend({'核对项': state, '数量': count}
                for state, count in sorted(Counter(line.state for line in order.lines).items()))
    return rows
