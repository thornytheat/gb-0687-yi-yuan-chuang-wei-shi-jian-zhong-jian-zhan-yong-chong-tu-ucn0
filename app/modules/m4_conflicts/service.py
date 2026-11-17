from app.modules.m4_conflicts.rules import conflicts, generate_holds, hold_summary
from decimal import Decimal as D


def execute(ctx):
    rows = conflicts(ctx['intervals'])
    holds = generate_holds(rows, ctx['intervals'])
    ctx['conflicts'] = rows
    ctx['holds'] = holds
    ctx['stages'].append({
        'title': '双占裁决',
        'rows': rows,
        'metrics': {
            '冲突段': len(rows),
            '涉床': len({x['bed'] for x in rows}),
            '双占 min': str(sum((x['minutes'] for x in rows), D(0))),
            '待裁决封床': sum(h['status'] == '待裁决' for h in holds),
            '封床 min': str(sum((h['minutes'] for h in holds), D(0))),
        },
    })
    return ctx
