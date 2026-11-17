from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.modules.m4_conflicts.rules import PENDING, RELEASED

ZONE = timezone(timedelta(hours=8))
CAPACITY = D(1440)


def _minutes(start, end):
    return D(str((end - start).total_seconds())) / 60


def split_days(start, end):
    """把区间按 +08:00 当地午夜切分为 (日期, 起, 止)。"""
    cursor = start
    while cursor < end:
        local = cursor.astimezone(ZONE)
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), ZONE)
        stop = min(end, midnight)
        yield local.date().isoformat(), cursor, stop
        cursor = stop


def union_minutes(segments):
    """同一床同一日物理占用取并集，重叠只计一次。"""
    total = D(0)
    cursor = stop = None
    for start, end in sorted(segments):
        if cursor is None or start >= stop:
            if cursor is not None:
                total += _minutes(cursor, stop)
            cursor, stop = start, end
        elif end > stop:
            stop = end
    if cursor is not None:
        total += _minutes(cursor, stop)
    return total


def hold_day_minutes(hold):
    """把封床分钟按当地午夜切日；部分释放自封床起点顺次扣除。"""
    released = hold.get('released_minutes') or D(0)
    released = D(released)
    rows = []
    for day, start, stop in split_days(hold['start'], hold['end']):
        span = _minutes(start, stop)
        freed = min(span, released)
        released -= freed
        rows.append((day, span, freed, span - freed))
    return rows


def capacity_calendar(intervals, holds):
    """放床日历：临床区间只按并集占一次，封床分钟单列并暂停收治。

    待裁决封床分钟标记暂停收治；已释放部分转入 released 并不再阻断收治。
    患者住院区间本身不因封床或归属复核而改写。
    """
    segments = defaultdict(list)
    for item in intervals:
        for day, start, stop in split_days(item['start'], item['end']):
            segments[(day, item['ward'], item['bed'])].append((start, stop))
    held = defaultdict(lambda: D(0))
    released = defaultdict(lambda: D(0))
    counts = defaultdict(lambda: {'pending': 0, 'released': 0})
    for hold in holds:
        for day, span, freed, pending in hold_day_minutes(hold):
            key = (day, hold['ward'], hold['bed'])
            held[key] += pending
            released[key] += freed
            if pending > 0:
                counts[key]['pending'] += 1
            if freed > 0:
                counts[key]['released'] += 1
    rows = []
    for key in sorted(set(segments) | set(held) | set(released)):
        day, ward, bed = key
        occupied = union_minutes(segments.get(key, []))
        pending = held[key]
        freed = released[key]
        # 物理余量 = 容量 - 临床并集（重叠只计一次）。
        available = CAPACITY - occupied
        # 封床不改写物理占床，但争用未裁决前这些床日不可收治。
        bookable = D(0) if pending > 0 else available
        state = '暂停收治' if pending > 0 else ('已释放' if freed > 0 else '可收治')
        rows.append({
            'date': day, 'ward': ward, 'bed': bed,
            'capacity': CAPACITY,
            'occupied': occupied,
            'held': pending,
            'released': freed,
            'available': available,
            'bookable': bookable,
            'pending_holds': counts[key]['pending'],
            'released_holds': counts[key]['released'],
            'state': state,
        })
    return rows
