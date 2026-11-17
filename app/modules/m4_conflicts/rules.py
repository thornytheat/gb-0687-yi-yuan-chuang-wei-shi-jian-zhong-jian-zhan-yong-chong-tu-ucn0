import hashlib
from collections import defaultdict
from decimal import Decimal as D

PENDING = '待裁决'
RELEASED = '已释放'


def _minutes(start, end):
    return D(str((end - start).total_seconds())) / 60


def conflicts(intervals):
    beds = defaultdict(list)
    for item in intervals:
        beds[item['bed']].append(item)
    result = []
    for bed, items in sorted(beds.items()):
        ordered = sorted(items, key=lambda x: (x['start'], x['end'], x['patient']))
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                if right['start'] >= left['end']:
                    break
                start = max(left['start'], right['start'])
                end = min(left['end'], right['end'])
                if start < end and left['patient'] != right['patient']:
                    result.append({
                        'bed': bed,
                        'ward': left.get('ward', ''),
                        'patient': left['patient'] + ' / ' + right['patient'],
                        'patients': [left['patient'], right['patient']],
                        'start': start,
                        'end': end,
                        'minutes': _minutes(start, end),
                        'state': '双占',
                    })
    return result


def dispute_id(bed, patients):
    key = bed + '|' + '|'.join(sorted(patients))
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]


def hold_id(dispute, start, end):
    key = dispute + '|' + start.isoformat() + '|' + end.isoformat()
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]


def _claim(patient, intervals):
    """保留争用双方在冲突床及全院的住院时长，不因封床改写临床分钟。"""
    mine = [x for x in intervals if x['patient'] == patient]
    total = sum((_minutes(x['start'], x['end']) for x in mine), D(0))
    return {
        'patient': patient,
        'los_minutes': total,
        'intervals': [{
            'patient': x['patient'], 'bed': x['bed'], 'ward': x.get('ward', ''),
            'start': x['start'], 'end': x['end'], 'open': bool(x.get('open')),
            'source': x.get('source', ''), 'minutes': _minutes(x['start'], x['end']),
        } for x in sorted(mine, key=lambda r: (r['start'], r['bed']))],
    }


def generate_holds(rows, intervals):
    """把每个双占交集转成一张待裁决封床，双方住院时长原样保留。"""
    holds = []
    for row in rows:
        patients = list(row.get('patients') or [p.strip() for p in row['patient'].split('/')])
        did = dispute_id(row['bed'], patients)
        holds.append({
            'hold_id': hold_id(did, row['start'], row['end']),
            'dispute_id': did,
            'bed': row['bed'],
            'ward': row['ward'],
            'patients': patients,
            'contending': ' / '.join(patients),
            'start': row['start'],
            'end': row['end'],
            'minutes': row['minutes'],
            'status': PENDING,
            'attribution': None,
            'released_minutes': D(0),
            'release_basis': None,
            'rule': '同一床号半开区间交集，临床时长不改写',
            'claims': [_claim(p, intervals) for p in patients],
        })
    return sorted(holds, key=lambda h: (h['bed'], h['start'], h['hold_id']))


def hold_summary(holds):
    """批次封床摘要：待裁决/已释放张数与分钟，及争用患者清单。"""
    pending = [h for h in holds if h['status'] == PENDING]
    released = [h for h in holds if h['status'] == RELEASED]
    return {
        'total': len(holds),
        'pending': len(pending),
        'released': len(released),
        'held_minutes': sum((h['minutes'] for h in pending), D(0)),
        'released_minutes': sum((h['released_minutes'] for h in released), D(0)),
        'contending_patients': sorted({p for h in holds for p in (h.get('patients') or [])}),
    }
