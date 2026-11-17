from decimal import Decimal as D

from app.modules.m8_holds.rules import board_rows, capacity_digest, hold_summary


def execute(ctx):
    """汇总封床看板；在七步管线之后执行，不改变事件阶段数量。"""
    holds = ctx.get('holds', [])
    if 'holds' in ctx and 'hold_board' not in ctx:
        summary = hold_summary(holds)
        ctx['hold_board'] = board_rows(holds)
        ctx['hold_summary'] = summary
        ctx['hold_digest'] = capacity_digest(ctx.get('board', []))
    return ctx


def metrics(ctx):
    summary = ctx.get('hold_summary') or hold_summary(ctx.get('holds', []))
    return {
        '待裁决封床': summary['pending'],
        '已释放封床': summary['released'],
        '封床 min': str(sum((h['minutes'] for h in ctx.get('holds', []) if h['status'] == '待裁决'), D(0))),
        '争用患者': len(summary['contending_patients']),
    }
