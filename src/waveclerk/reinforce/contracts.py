from dataclasses import dataclass, field

# 增员上限与录入量测约束保持一致：可用拣货员有效区间为 2–15 人。
MAX_PICKERS = 15
# 装车交接缓冲沿用通道分派口径：距装车交接保留 6 分钟。
TRUCK_BUFFER_MIN = 6
POLICY_VERSION = 'waveclerk.reinforce.20261126.v1'

# 留人工判读原因码
MANUAL_MISSING_PICKERS = 'MISSING_PICKERS'
MANUAL_MISSING_SLACK = 'MISSING_TRUCK_SLACK'
MANUAL_OUT_OF_DOMAIN = 'REINFORCE_OUT_OF_DOMAIN'
MANUAL_BASE_REVIEW = 'BASE_MANUAL_REVIEW'
MANUAL_SPARSE_CALIBRATION = 'SPARSE_CALIBRATION'
# 安全暂停沿用基础处置
PAUSE_BULKY_INTERLOCK = 'BULKY_INTERLOCK'
# 不派人原因码
SKIP_NOT_LATE = 'ALREADY_CATCHES_TRUCK'
SKIP_UNRESCUABLE = 'CANNOT_CATCH_WITH_FLEX'
SKIP_FLEX_EXHAUSTED = 'FLEX_HEADCOUNT_EXHAUSTED'

# 增援单处置状态
STATE_SENT = '已增援'
STATE_MANUAL = '人工判读'
STATE_PAUSED = '暂停'
STATE_SKIPPED = '不增援'


@dataclass(frozen=True, slots=True)
class ReinforceOption:
    """单票增员推演的一档人手方案。"""
    extra_pickers: int
    total_pickers: float
    estimate: float
    upper: float | None
    new_excursions: tuple[str, ...]
    in_domain: bool
    rescues: bool

    def __post_init__(self):
        if self.extra_pickers <= 0:
            raise ValueError('增援人数必须为正整数')
        if not self.in_domain and self.rescues:
            raise ValueError('超训练域方案不能判定为可救回')


@dataclass(frozen=True, slots=True)
class ReinforceLine:
    """当班增援单上的一张票的裁决。"""
    key: str
    cohort: str
    state: str
    reason: str
    base_pickers: float | None
    truck_buffer: float | None
    base_estimate: float
    base_upper: float | None
    base_late: bool
    extra_pickers: int
    total_pickers: float | None
    revised_estimate: float | None
    revised_upper: float | None
    required_pickers: int
    in_domain_path: bool
    new_excursions: tuple[str, ...]
    options: tuple[ReinforceOption, ...]
    base_state: str
    base_reason: str

    def __post_init__(self):
        if self.state == STATE_SENT and self.extra_pickers <= 0:
            raise ValueError('已增援单必须登记正增援人数')
        if self.state == STATE_SENT and not self.base_late:
            raise ValueError('原本赶得上的单不能派人')
        if self.state == STATE_SENT and (self.revised_upper is None or self.truck_buffer is None
                                         or self.revised_upper > self.truck_buffer):
            raise ValueError('已增援单的完工区间上沿必须回到装车缓冲内')


@dataclass(frozen=True, slots=True)
class ReinforceLedger:
    flex_pool: int
    flex_used: int
    flex_balance: int
    waves_rescued: int
    sent: tuple[str, ...]
    manual: tuple[str, ...]
    paused: tuple[str, ...]
    skipped: tuple[str, ...]

    def __post_init__(self):
        if self.flex_used + self.flex_balance != self.flex_pool:
            raise ArithmeticError('机动人手账不平，不能签发增援凭证')
        if self.waves_rescued != len(self.sent):
            raise ArithmeticError('救回波次数与已增援名单不符')


@dataclass(frozen=True, slots=True)
class ReinforceOrder:
    lines: tuple[ReinforceLine, ...]
    ledger: ReinforceLedger
    feature_stamp: str
    model: str
    verification: str
    flex_pool: int
    as_of: int
    batch_hash: str
    policy_hash: str
    stamp: str
