import json
from datetime import datetime, timezone

from app import store
from app.errors import DomainError
from app.normalize import serial
from app.modules.m8_holds.rules import (
    ACTIVE_STATE, apply_state, attach_release_basis, board_rows, capacity_digest,
    capacity_for, dec, fingerprint, hold_summary, hydrate_holds, normalize_request, version_id,
    validate_adjudication, validate_snapshot,
)

SCOPE = 'hospital'
CANDIDATE = '待切换'
FAILED = '切换失败'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS hold_versions ("
        "id TEXT PRIMARY KEY, scope TEXT NOT NULL, parent TEXT, dispute TEXT NOT NULL, "
        "state TEXT NOT NULL, payload TEXT NOT NULL, snapshot TEXT NOT NULL, "
        "digest TEXT NOT NULL, source TEXT, error TEXT, created TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS hold_active ("
        "scope TEXT PRIMARY KEY, version TEXT NOT NULL, updated TEXT NOT NULL)"
    )


def _active_version(db=None):
    def query(conn):
        _schema(conn)
        row = conn.execute("SELECT version FROM hold_active WHERE scope=?", (SCOPE,)).fetchone()
        return row['version'] if row else None
    if db is not None:
        return query(db)
    with store.connection() as conn:
        return query(conn)


def _shape(row):
    snapshot = json.loads(row['snapshot'])
    holds = hydrate_holds(snapshot['holds'])
    for hold in holds:
        hold['released_minutes'] = dec(hold.get('released_minutes') or 0)
    capacity = snapshot['capacity']
    return {
        'id': row['id'], 'version': row['id'], 'parent': row['parent'], 'dispute': row['dispute'],
        'state': row['state'], 'digest': row['digest'], 'source': row['source'], 'error': row['error'],
        'created': row['created'], 'holds': holds, 'capacity': capacity,
        'board': board_rows(holds), 'summary': hold_summary(holds),
    }


def active_view():
    version = _active_version()
    if version is None:
        return {'id': None, 'version': None, 'state': '未切换', 'holds': [], 'capacity': [],
                'board': [], 'summary': hold_summary([]), 'active': False, 'parent': None,
                'dispute': None, 'digest': None, 'source': None, 'error': None, 'created': None}
    view = fetch(version)
    view['active'] = True
    return view


def fetch(version):
    with store.connection() as db:
        _schema(db)
        row = db.execute("SELECT * FROM hold_versions WHERE id=? AND scope=?", (version, SCOPE)).fetchone()
        if row is None:
            raise DomainError('HOLD_VERSION', '封床版本不存在', 404)
        return {**_shape(row), 'active': _active_version(db) == row['id']}


def history():
    with store.connection() as db:
        _schema(db)
        rows = db.execute(
            "SELECT id,parent,dispute,state,source,error,created FROM hold_versions "
            "WHERE scope=? ORDER BY rowid DESC LIMIT 100", (SCOPE,)).fetchall()
        active = _active_version(db)
    return [{**dict(row), 'active': row['id'] == active} for row in rows]


def _compose(payload, releases, attributions):
    """在原始封床证据上叠加归属与释放，重算容量并写入释放依据。"""
    holds = apply_state(hydrate_holds(payload.get('holds')), releases, attributions)
    capacity = capacity_for(payload, releases, attributions)
    attach_release_basis(holds, capacity)
    return holds, capacity


def publish(payload, releases=None, attributions=None, parent=None, source=None, fail_switch=False):
    """生成候选封床版本（关联父版本）并原子切换。

    - 争用或裁决内容改变才产生新版本；相同父版本与相同内容重试复用旧版本；
    - 切换失败保留待切换版本与错误，生效容量沿用旧版本；
    - 原始证据（payload）只追加不改写，封床归属不写回患者住院时长。
    """
    if not isinstance(payload, dict):
        raise DomainError('HOLDS', '封床数据必须是对象', 422)
    if not isinstance(payload.get('holds'), list):
        raise DomainError('HOLDS', '封床数据缺少 holds 数组', 422)
    releases = releases or {}
    attributions = attributions or {}
    releases, attributions = normalize_request(payload['holds'], releases, attributions)

    errors = validate_snapshot(payload)
    if errors:
        raise DomainError(errors[0][0], errors[0][1], 422)
    errors = validate_adjudication(hydrate_holds(payload['holds']), releases, attributions)
    if errors:
        raise DomainError(errors[0][0], errors[0][1], 422)

    dispute = fingerprint(payload['holds'])
    version = version_id(parent, dispute, releases, attributions)
    holds, capacity = _compose(payload, releases, attributions)
    digest = capacity_digest(capacity)

    with store.connection() as db:
        _schema(db)
        db.execute(
            "INSERT OR IGNORE INTO hold_versions"
            "(id,scope,parent,dispute,state,payload,snapshot,digest,source,error,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (version, SCOPE, parent, dispute, CANDIDATE, json.dumps(serial(payload), ensure_ascii=False),
             json.dumps(serial({'holds': holds, 'capacity': capacity}), ensure_ascii=False),
             digest, source, None, _now()),
        )

    existing = fetch(version)
    if existing['state'] == ACTIVE_STATE:
        return {**existing, 'switched': False, 'idempotent': True, 'fallback': None}

    with store.connection() as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        current = _active_version(db)
        if parent is not None and parent != current:
            db.execute("UPDATE hold_versions SET state=?,error=? WHERE id=?", (FAILED, '父版本已过期', version))
            db.commit()
            failed = fetch(version)
            return {**failed, 'switched': False, 'idempotent': False, 'fallback': active_view()}
        problem = '模拟切换失败' if fail_switch else None
        if problem is None and any(dec(r['available']) < 0 or dec(r['occupied']) > dec(r['capacity'])
                   or dec(r.get('bookable', 0)) < 0 for r in capacity):
            problem = '重算容量越界'
        if problem is not None:
            db.execute("UPDATE hold_versions SET state=?,error=? WHERE id=?", (FAILED, problem, version))
            db.commit()
            failed = fetch(version)
            return {**failed, 'switched': False, 'idempotent': False, 'fallback': active_view()}
        db.execute(
            "INSERT INTO hold_active(scope,version,updated) VALUES(?,?,?) "
            "ON CONFLICT(scope) DO UPDATE SET version=excluded.version,updated=excluded.updated",
            (SCOPE, version, _now()),
        )
        db.execute("UPDATE hold_versions SET state=?,error=NULL WHERE id=?", (ACTIVE_STATE, version))
        db.commit()
    return {**fetch(version), 'switched': True, 'idempotent': False, 'fallback': None}
