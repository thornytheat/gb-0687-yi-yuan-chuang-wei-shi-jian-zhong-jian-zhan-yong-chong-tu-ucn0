import copy
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app import holds
from app.errors import DomainError
from app.modules.m4_conflicts.rules import PENDING, RELEASED, conflicts, generate_holds
from app.modules.m5_calendar.capacity import capacity_calendar
from app.modules.m8_holds.rules import parse_time
from app.normalize import instant
from app.pipeline import board, run

HOLD_KEYS = {
    'bed', 'ward', 'contending', 'start', 'end', 'minutes', 'los',
    'attribution', 'responsible', 'release_basis', 'state',
}


def part(patient, start, end, bed='001', ward='一病区'):
    return {'patient': patient, 'bed': bed, 'ward': ward,
            'start': instant('2026-11-16T' + start + ':00Z'), 'end': instant('2026-11-16T' + end + ':00Z')}


def later(value, hours=1):
    return (parse_time(value) + timedelta(hours=hours)).isoformat()


def extend(hold, hours=1):
    """补传改变争议：延长封床并同步争用分钟。"""
    hold['end'] = later(hold['end'], hours)
    hold['minutes'] = str(D(str(hold['minutes'])) + D(60 * hours))
    return hold


def snapshot(raw):
    result = run(raw, persist=False)
    return {
        'run_id': result['manifest']['run_id'],
        'rule_version': result['manifest']['rules'],
        'intervals': result['intervals'],
        'conflicts': result['conflicts'],
        'holds': result['holds'],
    }


@pytest.fixture
def snap(raw):
    return snapshot(raw)


# ---------- 争用 → 待裁决封床 ----------

def test_hold_from_contention_batch():
    parts = [part('A', '08:00', '10:00'), part('B', '09:00', '11:00')]
    rows = conflicts(parts)
    items = generate_holds(rows, parts)
    assert len(items) == 1
    assert items[0]['status'] == PENDING
    assert items[0]['patients'] == ['A', 'B']
    assert items[0]['minutes'] == D(60)


def test_hold_keeps_both_lengths():
    parts = [part('A', '08:00', '12:00'), part('B', '09:00', '10:00')]
    item = generate_holds(conflicts(parts), parts)[0]
    los = {c['patient']: c['los_minutes'] for c in item['claims']}
    assert los == {'A': D(240), 'B': D(60)}
    # 封床分钟不写进任何一方住院时长
    assert item['minutes'] == D(60)
    assert item['minutes'] not in los.values() or los['A'] == D(240)


def test_hold_id_is_stable():
    parts = [part('A', '08:00', '10:00'), part('B', '09:00', '11:00')]
    first = generate_holds(conflicts(parts), parts)[0]['hold_id']
    second = generate_holds(conflicts(parts), parts)[0]['hold_id']
    assert first == second


def test_touching_is_not_a_hold():
    assert generate_holds(conflicts([part('A', '08:00', '09:00'), part('B', '09:00', '10:00')]),
                          [part('A', '08:00', '09:00'), part('B', '09:00', '10:00')]) == []


def test_batch_board_lists_disputes(snap):
    assert len(snap['holds']) == len(snap['conflicts'])
    assert all(h['status'] == PENDING for h in snap['holds'])
    assert all(len(h['claims']) == 2 for h in snap['holds'])


# ---------- 暂停收治 / 收治容量 ----------

def test_pending_hold_pauses_admission():
    parts = [part('A', '08:00', '10:00'), part('B', '09:00', '11:00')]
    item = generate_holds(conflicts(parts), parts)[0]
    rows = capacity_calendar(parts, [item])
    assert rows[0]['state'] == '暂停收治'
    assert rows[0]['held'] == D(60)
    assert rows[0]['occupied'] == D(180)  # 并集，重叠只占一次


def test_released_hold_frees_capacity():
    parts = [part('A', '08:00', '10:00'), part('B', '09:00', '11:00')]
    item = generate_holds(conflicts(parts), parts)[0]
    item['status'] = RELEASED
    item['released_minutes'] = item['minutes']
    rows = capacity_calendar(parts, [item])
    assert rows[0]['state'] == '已释放'
    assert rows[0]['held'] == D(0) and rows[0]['released'] == D(60)
    assert rows[0]['available'] == D(1440) - D(180)


def test_partial_release_keeps_remainder_held():
    parts = [part('A', '08:00', '12:00'), part('B', '08:00', '10:00')]
    item = generate_holds(conflicts(parts), parts)[0]
    item['status'] = RELEASED
    item['released_minutes'] = D(30)
    rows = capacity_calendar(parts, [item])
    assert rows[0]['released'] == D(30)
    assert rows[0]['held'] == D(90)
    assert rows[0]['state'] == '暂停收治'


def test_board_heads_contention_columns(raw, isolate):
    data = board(raw, persist=False)
    assert set(data['hold_board'][0]) == HOLD_KEYS
    row = data['hold_board'][0]
    assert ':' in row['los']  # 双方住院分钟并列
    assert row['minutes']  # 封床分钟并列
    assert row['release_basis'] == '待裁决'


# ---------- 归属复核 → 重算日历 → 释放 ----------

def test_release_requires_attribution(snap, isolate):
    hold = snap['holds'][0]
    with pytest.raises(DomainError) as info:
        holds.publish(snap, releases={hold['hold_id']: None})
    assert info.value.code == 'ATTRIBUTION'


def test_blame_requires_contending_patient(snap, isolate):
    hold = snap['holds'][0]
    with pytest.raises(DomainError) as info:
        holds.publish(snap, releases={hold['hold_id']: None},
                      attributions={hold['hold_id']: {'decision': '重复登记', 'responsible': 'X'}})
    assert info.value.code == 'ATTRIBUTION'


def test_attribution_never_rewrites_clinical_minutes(snap, isolate):
    hold = snap['holds'][0]
    before = {c['patient']: D(str(c['los_minutes'])) for c in hold['claims']}
    version = holds.publish(snap, releases={hold['hold_id']: None},
                            attributions={hold['hold_id']: {'decision': '重复登记',
                                                            'responsible': hold['patients'][0]}})
    released = next(h for h in version['holds'] if h['hold_id'] == hold['hold_id'])
    after = {c['patient']: D(str(c['los_minutes'])) for c in released['claims']}
    assert after == before
    assert released['attribution']['responsible'] == hold['patients'][0]
    assert '仅封床归属' in released['release_basis']


def test_release_recomputes_calendar(snap, isolate):
    preventing = holds.publish(snap)
    assert any(r['state'] == '暂停收治' for r in preventing['capacity'])
    hold_id = snap['holds'][0]['hold_id']
    released = holds.publish(snap, releases={hold_id: None},
                             attributions={hold_id: {'decision': '真实双占'}}, parent=preventing['id'])
    row = next(r for r in released['capacity'] if r['bed'] == snap['holds'][0]['bed']
               and r['date'] == snap['holds'][0]['start'][:10])
    assert D(str(row['released'])) == D(snap['holds'][0]['minutes'])
    assert '日历重算' in released['board'][0]['release_basis']


def test_release_basis_is_auditable(snap, isolate):
    hold = snap['holds'][0]
    version = holds.publish(snap, releases={hold['hold_id']: None},
                            attributions={hold['hold_id']: {'decision': '真实双占'}})
    basis = version['board'][0]['release_basis']
    assert hold['bed'] in basis and '归属复核' in basis and '释放' in basis


def test_release_minutes_bounded(snap, isolate):
    hold = snap['holds'][0]
    with pytest.raises(DomainError) as info:
        holds.publish(snap, releases={hold['hold_id']: str(float(hold['minutes']) + 1)},
                      attributions={hold['hold_id']: {'decision': '真实双占'}})
    assert info.value.code == 'HOLD_RANGE'


def test_unknown_hold_rejected(snap, isolate):
    with pytest.raises(DomainError) as info:
        holds.publish(snap, releases={'nope': None}, attributions={'nope': {'decision': '真实双占'}})
    assert info.value.code == 'HOLD_UNKNOWN'


# ---------- 补传改变争议 → 关联版本、保留原证据 ----------

def test_backfill_change_creates_linked_version(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    assert holds.fingerprint(snap['holds']) != holds.fingerprint(changed['holds'])
    second = holds.publish(changed, parent=first['id'])
    assert second['id'] != first['id']
    assert second['parent'] == first['id']
    assert second['state'] == '生效'


def test_original_evidence_preserved(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    holds.publish(changed, parent=first['id'])
    original = holds.fetch(first['id'])
    assert original['holds'][0]['end'] == parse_time(snap['holds'][0]['end'])
    assert original['digest'] == first['digest']


def test_retry_does_not_duplicate_version(snap, isolate):
    first = holds.publish(snap)
    again = holds.publish(snap)
    assert again['id'] == first['id'] and again['idempotent'] is True
    assert len(holds.history()) == 1


def test_backfill_retry_keeps_single_version(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    a = holds.publish(changed, parent=first['id'])
    b = holds.publish(changed, parent=first['id'])
    assert a['id'] == b['id'] and b['idempotent'] is True
    assert len(holds.history()) == 2


# ---------- 切换失败 → 沿用旧容量 ----------

def test_failed_switch_keeps_old_capacity(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    failed = holds.publish(changed, parent=first['id'], fail_switch=True)
    assert failed['state'] == '切换失败'
    assert failed['error'] == '模拟切换失败'
    assert failed['fallback']['id'] == first['id']
    assert holds.active_view()['id'] == first['id']


def test_failed_switch_retry_can_activate(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    bad = holds.publish(changed, parent=first['id'], fail_switch=True)
    good = holds.publish(changed, parent=first['id'])
    assert good['id'] == bad['id']
    assert good['state'] == '生效'
    assert holds.active_view()['id'] == good['id']


def test_stale_parent_falls_back(snap, isolate):
    first = holds.publish(snap)
    changed = copy.deepcopy(snap)
    extend(changed['holds'][0])
    second = holds.publish(changed, parent=first['id'])
    assert second['id'] != first['id']
    other = copy.deepcopy(snap)
    extend(other['holds'][-1])
    stale = holds.publish(other, parent=first['id'])
    assert stale['state'] == '切换失败'
    assert stale['error'] == '父版本已过期'
    assert stale['fallback']['id'] == second['id']
    assert holds.active_view()['id'] == second['id']


# ---------- 防篡改 ----------

def test_doctored_los_rejected(snap, isolate):
    bad = copy.deepcopy(snap)
    bad['holds'][0]['claims'][0]['los_minutes'] = '999.0'
    with pytest.raises(DomainError) as info:
        holds.publish(bad)
    assert info.value.code == 'CLAIM_MISMATCH'


def test_doctored_interval_rejected(snap, isolate):
    bad = copy.deepcopy(snap)
    bad['holds'][0]['claims'][0]['intervals'][0]['end'] = '2026-11-16T23:00:00+00:00'
    with pytest.raises(DomainError) as info:
        holds.publish(bad)
    assert info.value.code == 'CLAIM_MISMATCH'


# ---------- 并列导出 ----------

def test_export_lists_contention_holds_and_basis(client):
    version = client.post('/api/holds/from-run', json={}).json
    hold_id = version['holds'][0]['hold_id']
    client.post('/api/holds/from-run', json={
        'parent': version['id'], 'releases': {hold_id: None},
        'attributions': {hold_id: {'decision': '真实双占'}},
    })
    text = client.get('/api/holds.csv').text
    assert '争用患者' in text and '封床 min' in text and '释放依据' in text
    assert '归属复核' in text and '住院 min' in text
    assert '容量日历' in text


def test_board_page_shows_columns(client):
    page = client.get('/m/holds')
    assert page.status_code == 200
    assert '待裁决封床' in page.text and '释放依据' in page.text


def test_paused_bed_is_not_bookable():
    parts = [part('A', '08:00', '10:00'), part('B', '09:00', '11:00')]
    item = generate_holds(conflicts(parts), parts)[0]
    row = capacity_calendar(parts, [item])[0]
    assert row['state'] == '暂停收治' and row['bookable'] == D(0)
    released = capacity_calendar(parts, [{**item, 'status': RELEASED, 'released_minutes': D(60)}])[0]
    assert released['bookable'] == released['available'] == D(1440) - D(180)


def test_hold_across_midnight_splits_days():
    start = instant('2026-11-16T22:00:00+08:00')
    end = instant('2026-11-17T02:00:00+08:00')
    intervals = [
        {'patient': 'A', 'bed': '001', 'ward': '一病区', 'start': start, 'end': instant('2026-11-17T02:00:00+08:00')},
        {'patient': 'B', 'bed': '001', 'ward': '一病区', 'start': start, 'end': instant('2026-11-17T02:00:00+08:00')},
    ]
    rows = conflicts(intervals)
    holds_rows = generate_holds(rows, intervals)
    board_rows_ = capacity_calendar(intervals, holds_rows)
    assert [r['date'] for r in board_rows_] == ['2026-11-16', '2026-11-17']
    assert sum(D(r['held']) for r in board_rows_) == D(240)


def test_http_publish_and_retry(client):
    first = client.post('/api/holds/from-run', json={}).json
    assert first['state'] == '生效'
    retry = client.post('/api/holds/from-run', json={}).json
    assert retry['id'] == first['id'] and retry['idempotent'] is True
    hold_id = first['holds'][0]['hold_id']
    missing = client.post('/api/holds/from-run', json={'parent': first['id'], 'releases': {hold_id: None}})
    assert missing.status_code == 422 and missing.json['code'] == 'ATTRIBUTION'
    ok = client.post('/api/holds/from-run', json={
        'parent': first['id'], 'releases': {hold_id: None},
        'attributions': {hold_id: {'decision': '真实双占'}},
    }).json
    assert ok['state'] == '生效' and ok['summary']['released'] == 1


def test_http_failed_switch_keeps_active(client):
    first = client.post('/api/holds/from-run', json={}).json
    batch = client.get('/api/m8').json
    changed = copy.deepcopy({'run_id': batch['manifest']['run_id'], 'intervals': batch['intervals'],
                             'conflicts': batch['conflicts'], 'holds': batch['holds']})
    extend(changed['holds'][0])
    failed = client.post('/api/holds', json={'payload': changed, 'parent': first['id'],
                                             'fail_switch': True}).json
    assert failed['state'] == '切换失败'
    assert failed['fallback']['id'] == first['id']
    assert client.get('/api/holds').json['id'] == first['id']
