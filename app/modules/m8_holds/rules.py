import hashlib
import json
from datetime import datetime
from decimal import Decimal, Decimal as D

from app.errors import DomainError
from app.modules.m4_conflicts.rules import PENDING, RELEASED, hold_summary
from app.modules.m5_calendar.capacity import CAPACITY, capacity_calendar, split_days

SCOPE = 'hospital'
ATTR_DECISIONS = ('真实双占', '重复登记', '错床更正')
BLAME_DECISIONS = ('重复登记', '错床更正')
ACTIVE_STATE = '生效'


def parse_time(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def dec(value):
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def fmt(value):
    return format(dec(value).quantize(Decimal('0.1')), 'f')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def hydrate_holds(holds):
    out = []
    for hold in holds or []:
        item = dict(hold)
        item['start'] = parse_time(hold['start'])
        item['end'] = parse_time(hold['end'])
        item['minutes'] = dec(hold['minutes'])
        claims = []
        for claim in hold.get('claims') or []:
            clone = dict(claim)
            clone['los_minutes'] = dec(claim['los_minutes'])
            clone['intervals'] = []
            for part in claim.get('intervals') or []:
                piece = dict(part)
                piece['start'] = parse_time(part['start'])
                piece['end'] = parse_time(part['end'])
                piece['minutes'] = dec(part.get('minutes', 0))
                clone['intervals'].append(piece)
            claims.append(clone)
        item['claims'] = claims
        out.append(item)
    return out


def hydrate_intervals(intervals):
    out = []
    for item in intervals or []:
        out.append({**item, 'start': parse_time(item['start']), 'end': parse_time(item['end'])})
    return out


def fingerprint(holds):
    """争用指纹：床号、双方患者、争用分钟或任一方住院时长变化都视为争议改变。"""
    canon = []
    for hold in sorted(holds or [], key=lambda h: (h['bed'], str(h['start']), str(h['end']), h['hold_id'])):
        claims = sorted((c['patient'], fmt(c['los_minutes'])) for c in hold.get('claims') or [])
        canon.append([
            hold['hold_id'], hold['bed'], hold.get('ward', ''), hold.get('contending', ''),
            str(hold['start']), str(hold['end']), fmt(hold['minutes']), claims,
        ])
    return hashlib.sha256(canonical(canon).encode('utf-8')).hexdigest()


def version_id(parent, dispute_fingerprint, releases, attributions):
    """版本号只由争用指纹与裁决内容决定：相同内容重试复用同一版本，不重复建版。

    父版本只记录谱系，不参与散列；否则重复提交时自动带入新父版本会凭空造出新版本。
    """
    decisions = canonical({'releases': releases or {}, 'attributions': attributions or {}})
    key = f'{SCOPE}|{dispute_fingerprint}|{decisions}'
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]


def apply_state(holds, releases, attributions):
    """叠加归属与释放：封床只改归属记录，患者住院区间始终原样保留。

    releases 以封床号存在即视为释放，值可为分钟（缺省释放整段封床）。
    """
    out = []
    releases = releases or {}
    for hold in holds or []:
        item = dict(hold)
        attribution = (attributions or {}).get(hold['hold_id'])
        item['attribution'] = attribution
        if hold['hold_id'] in releases:
            minutes = releases[hold['hold_id']]
            item['status'] = RELEASED
            item['released_minutes'] = dec(hold['minutes']) if minutes in (None, '') else dec(minutes)
            item['release_basis'] = None  # 由容量重算后写入
        else:
            item['status'] = PENDING
            item['released_minutes'] = Decimal(0)
            item['release_basis'] = None
        out.append(item)
    return out


def capacity_for(payload, releases=None, attributions=None):
    holds = apply_state(hydrate_holds(payload.get('holds')), releases, attributions)
    return capacity_calendar(hydrate_intervals(payload.get('intervals')), holds)


def _check_claims(hold, intervals):
    """住院凭据必须等于原始分段：不能靠封床归属篡改临床时长。"""
    errors = []
    label = f"{hold['bed']}:{hold.get('contending', '')}"
    actual = {}
    for item in intervals:
        if item['patient'] in (hold.get('patients') or []):
            actual.setdefault(item['patient'], []).append((item['start'], item['end']))
    for claim in hold.get('claims') or []:
        recorded = [(p['start'], p['end']) for p in claim.get('intervals') or []]
        expected = sorted(actual.get(claim['patient'], []))
        if sorted(recorded) != expected:
            errors.append(('CLAIM_MISMATCH', f"{label} {claim['patient']} 住院区间与事件分段不一致"))
            continue
        total = sum((dec(p.get('minutes', _gap(p))) for p in claim.get('intervals') or []), Decimal(0))
        if (total - dec(claim['los_minutes'])).copy_abs() > Decimal('0.05'):
            errors.append(('CLAIM_MISMATCH', f"{label} {claim['patient']} 住院分钟与分段不一致"))
    return errors


def _gap(part):
    return D(str((part['end'] - part['start']).total_seconds())) / 60


def validate_snapshot(payload):
    """切换前结构校验：封床须来自真实争用，双方住院凭据须完整且与分段一致，容量不得越界。"""
    errors = []
    holds = payload.get('holds') or []
    intervals = hydrate_intervals(payload.get('intervals'))
    ids = [h['hold_id'] for h in holds]
    if len(ids) != len(set(ids)):
        errors.append(('HOLD_DUPLICATE', '同批次封床号重复'))
    for hold in hydrate_holds(holds):
        label = f"{hold['bed']}:{hold.get('contending', '')}"
        if len(hold.get('patients') or []) < 2:
            errors.append(('HOLD_PATIENTS', f'{label} 争用患者不足两人'))
        if hold['end'] <= hold['start'] or hold['minutes'] <= 0:
            errors.append(('HOLD_RANGE', f'{label} 封床区间无效'))
        claims = {c['patient']: c for c in hold.get('claims') or []}
        missing = [p for p in hold.get('patients') or [] if p not in claims]
        if missing:
            errors.append(('CLAIM_MISSING', f"{label} 缺少 {'/'.join(missing)} 住院凭据"))
        if not any(c.get('intervals') for c in claims.values()):
            errors.append(('CLAIM_MISSING', f'{label} 双方住院区间为空'))
        for claim in claims.values():
            for part in claim.get('intervals') or []:
                if part['end'] <= part['start']:
                    errors.append(('CLAIM_RANGE', f"{label} {claim['patient']} 住院区间无效"))
        errors.extend(_check_claims(hold, intervals))
    try:
        capacity = capacity_calendar(intervals, hydrate_holds(holds))
    except (ValueError, TypeError):
        return errors + [('CAPACITY', '容量日历无法重算')]
    for row in capacity:
        label = f"{row['bed']}@{row['date']}"
        if dec(row['occupied']) > CAPACITY:
            errors.append(('CAPACITY', f'{label} 占床超过床位容量'))
        if dec(row['available']) < 0:
            errors.append(('CAPACITY', f'{label} 可用分钟为负'))
        if dec(row['held']) < 0 or dec(row['released']) < 0:
            errors.append(('CAPACITY', f'{label} 封床分钟为负'))
        if int(row['pending_holds']) + int(row['released_holds']) <= 0 and dec(row['held']) + dec(row['released']) > 0:
            errors.append(('CAPACITY', f'{label} 封床计数与分钟不一致'))
    return errors


def normalize_request(holds, releases, attributions):
    """把请求归一为 hold_id -> 分钟 与 hold_id -> 归属；未知封床号直接拒绝。"""
    known = {h['hold_id']: h for h in holds}
    norm_releases = {}
    if isinstance(releases, dict):
        pairs = releases.items()
    else:
        pairs = [(hold_id, None) for hold_id in (releases or [])]
    for hold_id, minutes in pairs:
        hold = known.get(hold_id)
        if hold is None:
            raise DomainError('HOLD_UNKNOWN', f'封床 {hold_id} 不存在', 422)
        value = dec(hold['minutes']) if minutes in (None, '') else dec(minutes)
        if value <= 0 or value > dec(hold['minutes']):
            raise DomainError('HOLD_RANGE', f'封床 {hold_id} 释放分钟越界', 422)
        norm_releases[hold_id] = format(value.quantize(Decimal('0.1')), 'f')
    norm_attrs = {}
    for hold_id, attr in (attributions or {}).items():
        if hold_id not in known:
            raise DomainError('HOLD_UNKNOWN', f'封床 {hold_id} 不存在', 422)
        if not isinstance(attr, dict):
            raise DomainError('ATTRIBUTION', '归属复核必须是对象', 422)
        norm_attrs[hold_id] = {
            'decision': str(attr.get('decision', '')).strip(),
            'responsible': (str(attr['responsible']).strip() if attr.get('responsible') not in (None, '') else None),
            'note': (str(attr['note']).strip() if attr.get('note') not in (None, '') else None),
        }
    return norm_releases, norm_attrs


def validate_adjudication(holds, releases, attributions):
    """释放前必须完成归属复核；责任只落在封床上，绝不写回患者临床时长。"""
    errors = []
    known = {h['hold_id']: h for h in holds}
    for hold_id, attr in (attributions or {}).items():
        hold = known[hold_id]
        decision = attr.get('decision')
        if decision not in ATTR_DECISIONS:
            errors.append(('ATTRIBUTION', f'封床 {hold_id} 归属结论须为 {"、".join(ATTR_DECISIONS)}'))
        responsible = attr.get('responsible')
        if decision in BLAME_DECISIONS and not responsible:
            errors.append(('ATTRIBUTION', f'封床 {hold_id} 的 {decision} 必须指明责任患者'))
        if responsible and responsible not in hold.get('patients', []):
            errors.append(('ATTRIBUTION', f'封床 {hold_id} 责任患者 {responsible} 不在争用双方中'))
    for hold_id in releases or {}:
        if hold_id not in (attributions or {}):
            errors.append(('ATTRIBUTION', f'封床 {hold_id} 未完成归属复核，不能释放'))
    return errors


def release_basis(hold, attribution, capacity, minutes):
    """释放依据并列床号、日期、重算后的占床/封床/可用分钟与归属复核结论。"""
    hold_days = {day for day, _, _ in split_days(hold['start'], hold['end'])}
    detail = [
        f"{row['bed']}@{row['date']} 占床 {fmt(row['occupied'])}/封床 {fmt(row['held'])}/可用 {fmt(row['available'])}/可收治 {fmt(row.get('bookable', 0))}"
        for row in capacity if row['bed'] == hold['bed'] and row['date'] in hold_days
    ]
    decision = attribution.get('decision', '')
    responsible = attribution.get('responsible')
    owner = f"；责任 {responsible}（仅封床归属，不改写临床时长）" if responsible else ''
    note = f"；说明 {attribution['note']}" if attribution.get('note') else ''
    return (f"归属复核：{decision}{owner}{note}；日历重算 {'；'.join(detail) or '无受影响床日'}；"
            f"释放 {fmt(minutes)} min")


def attach_release_basis(holds, capacity):
    for hold in holds:
        if hold['status'] != RELEASED:
            continue
        attribution = hold.get('attribution') or {}
        hold['release_basis'] = release_basis(hold, attribution, capacity, hold['released_minutes'])
    return holds


def board_rows(holds):
    """看板与导出并列口径：争用患者、封床分钟、双方住院分钟、归属复核、释放依据。"""
    rows = []
    for hold in holds:
        los = ' / '.join(f"{c['patient']}:{fmt(c['los_minutes'])}" for c in hold.get('claims') or [])
        attr = hold.get('attribution') or {}
        rows.append({
            'bed': hold['bed'],
            'ward': hold.get('ward', ''),
            'contending': hold.get('contending') or ' / '.join(hold.get('patients') or []),
            'start': hold['start'],
            'end': hold['end'],
            'minutes': fmt(hold['minutes']),
            'los': los,
            'attribution': attr.get('decision') or '待复核',
            'responsible': attr.get('responsible') or '—',
            'release_basis': hold.get('release_basis') or '待裁决',
            'state': hold['status'],
        })
    return rows


def capacity_digest(capacity):
    canon = [[r['date'], r['ward'], r['bed'], fmt(r['capacity']), fmt(r['occupied']), fmt(r['held']),
              fmt(r['released']), fmt(r['available']), fmt(r.get('bookable', 0)), r['state']] for r in capacity]
    return hashlib.sha256(canonical(canon).encode('utf-8')).hexdigest()
