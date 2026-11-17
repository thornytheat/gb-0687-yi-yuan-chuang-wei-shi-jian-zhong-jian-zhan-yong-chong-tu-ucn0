from importlib import import_module
from time import perf_counter

from app import store
from app.config import DEFAULTS, SAMPLES
from app.errors import DomainError
from app.manifest import make
from app.normalize import serial
from app.config import STAGES


def _stages(raw, params, stop):
    if raw is None:
        raw = (SAMPLES / 'dirty.csv').read_bytes()
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if not isinstance(params, (dict, type(None))):
        raise DomainError('PARAMS', '参数必须是对象')
    actual = {**DEFAULTS, **(params or {})}
    ctx = {'source': raw, 'params': actual, 'stages': [], 'quarantine': []}
    for stage in range(1, stop + 1):
        mod = import_module(f'app.modules.m{stage}_{STAGES[stage - 1]}.service')
        ctx = mod.execute(ctx)
    return ctx, actual


def _finish(ctx, raw, actual, started):
    ctx.pop('source', None)
    ctx.pop('raw', None)
    result = serial(ctx)
    result['manifest'] = make(raw, actual)
    result['elapsed_ms'] = round((perf_counter() - started) * 1000, 3)
    result['version'] = 1
    result['review'] = '待复核'
    return result


def run(raw=None, params=None, stop=7, persist=True):
    started = perf_counter()
    ctx, actual = _stages(raw, params, stop)
    source = raw if raw is not None else (SAMPLES / 'dirty.csv').read_bytes()
    if isinstance(source, str):
        source = source.encode('utf-8')
    result = _finish(ctx, source, actual, started)
    if persist and stop == 7:
        return store.save(result)
    return result


def board(raw=None, params=None, persist=False):
    """完整七步事件重建后再聚合封床看板，保持事件阶段数为七。"""
    from app.modules.m8_holds.service import execute, metrics

    started = perf_counter()
    ctx, actual = _stages(raw, params, 7)
    execute(ctx)
    ctx['hold_metrics'] = metrics(ctx)
    source = raw if raw is not None else (SAMPLES / 'dirty.csv').read_bytes()
    if isinstance(source, str):
        source = source.encode('utf-8')
    result = _finish(ctx, source, actual, started)
    if persist:
        return store.save(result)
    return result
