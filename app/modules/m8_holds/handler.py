from flask import Blueprint, jsonify, request

from app import holds
from app.errors import DomainError
from app.pipeline import board
from app.modules.m8_holds.service import metrics

bp = Blueprint('m8', __name__)


@bp.route('/api/m8')
def board_view():
    result = board()
    return jsonify(result)


@bp.route('/api/holds')
def active():
    return jsonify(holds.active_view())


@bp.route('/api/holds/history')
def versions():
    return jsonify(holds.history())


@bp.route('/api/holds/<version>')
def one(version):
    return jsonify(holds.fetch(version))


@bp.route('/api/holds', methods=['POST'])
def publish():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DomainError('BODY', '请求必须是对象')
    snapshot = payload.get('payload') or payload.get('snapshot')
    if snapshot is None:
        raise DomainError('HOLDS', '缺少封床数据')
    return jsonify(holds.publish(
        snapshot,
        releases=payload.get('releases'),
        attributions=payload.get('attributions'),
        parent=payload.get('parent'),
        source=payload.get('source') or payload.get('run_id'),
        fail_switch=bool(payload.get('fail_switch')),
    ))


@bp.route('/api/holds/from-run', methods=['POST'])
def from_run():
    """按事件批次生成待裁决封床：只新增版本，不改写既有证据与临床时长。"""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise DomainError('BODY', '请求必须是对象')
    result = board(payload.get('text'), payload.get('params'), persist=bool(payload.get('persist')))
    snapshot = {
        'run_id': result['manifest']['run_id'],
        'rule_version': result['manifest']['rules'],
        'intervals': result['intervals'],
        'conflicts': result['conflicts'],
        'holds': result['holds'],
    }
    parent = payload.get('parent')
    if parent is None:
        parent = holds.active_view().get('id')
    return jsonify(holds.publish(
        snapshot,
        releases=payload.get('releases'),
        attributions=payload.get('attributions'),
        parent=parent,
        source=result['manifest']['run_id'],
        fail_switch=bool(payload.get('fail_switch')),
    ))
