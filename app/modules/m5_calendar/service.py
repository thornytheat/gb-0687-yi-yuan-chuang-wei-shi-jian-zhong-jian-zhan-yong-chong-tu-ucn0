from decimal import Decimal as D

from .capacity import capacity_calendar
from .rules import calendar


def execute(ctx):
    rows = calendar(ctx['intervals'])
    board = capacity_calendar(ctx['intervals'], ctx.get('holds', []))
    ctx['calendar'] = rows
    ctx['board'] = board
    ctx['stages'].append({
        'title': '日占床',
        'rows': board,
        'metrics': {
            '床日桶': len(rows),
            '总占床 min': str(sum((x['minutes'] for x in rows), D(0))),
            '超占桶': sum(x['state'] == '超占' for x in rows),
            '暂停收治床日': sum(r['state'] == '暂停收治' for r in board),
            '封床 min': str(sum((D(str(r['held'])) for r in board), D(0))),
        },
    })
    return ctx
