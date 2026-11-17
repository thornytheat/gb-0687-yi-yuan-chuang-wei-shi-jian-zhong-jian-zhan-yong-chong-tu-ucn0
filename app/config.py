from pathlib import Path
TITLE = '医院床位事件重建与占用冲突'
SHORT = 'bed-chronicle'
STAGES = ['receive', 'mapping', 'episodes', 'conflicts', 'calendar', 'reconcile', 'replay']
MODULES = ['事件接收', '病区映射', '住院分段', '转床裁决', '占床日历', '病区对账', '补传重算']
BOARD = '封床裁决'
COLUMNS = {'id': '事件号', 'patient': '患者号', 'bed': '床号', 'ward': '病区', 'time': '事件时刻 UTC', 'type': '事件', 'start': '开始 UTC', 'end': '结束 UTC', 'minutes': '占床 min', 'date': '日期 +08', 'state': '状态', 'line': '行号', 'code': '原因码', 'reason': '原因', 'count': '数量', 'open': '开放段', 'version': '版本', 'hold_id': '封床号', 'dispute_id': '争议号', 'contending': '争用患者', 'los': '住院 min（患者:分钟）', 'attribution': '归属复核', 'responsible': '责任患者', 'release_basis': '释放依据', 'capacity': '容量 min', 'occupied': '占床 min', 'held': '封床 min', 'released': '已释放 min', 'available': '可用 min', 'bookable': '可收治 min', 'pending_holds': '待裁决数', 'released_holds': '已释放数'}
DEFAULTS = {'observation_end': '2026-11-18T00:00:00+08:00', 'day_offset': '8', 'late_minutes': '30'}
RULE_VERSION = "2026.11.18-v1"
SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
MAX_BYTES = 4 * 1024 * 1024
