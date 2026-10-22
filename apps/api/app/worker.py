"""排程工作進入點（Railway Cron 執行後結束程序）。

  python -m app.worker sync-invoices       每 15 分鐘（UTC 5-59/15 * * * *）
  python -m app.worker run-recurring       每月 1 日（UTC 5 0 1 * *，台北 08:05）；可帶月份參數
  python -m app.worker refresh-directory   每小時（UTC 40 * * * *）

每個工作在 job_runs 留紀錄；失敗時以非 0 結束，Railway 會顯示失敗。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from app.db import engine, session_scope
from app.feature.accruals import AccrualService
from app.feature.common import period_of
from app.feature.invoice_sync import InvoiceSyncService
from app.feature.repositories import OpsRepository
from app.seams.company import get_directory

TAIPEI = timezone(timedelta(hours=8))


def _run(job: str, fn) -> int:
    with session_scope() as s:
        ops = OpsRepository(s)
        run = ops.job_start(job)
        try:
            summary = fn(s)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            with session_scope() as s2:
                OpsRepository(s2).job_finish(OpsRepository(s2).job_start(job), {"error": repr(e)})
            print(json.dumps({"job": job, "error": repr(e)}, ensure_ascii=False), file=sys.stderr)
            return 1
        ops.job_finish(run, summary)
    print(json.dumps({"job": job, **summary}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    try:
        if cmd == "sync-invoices":
            return _run(cmd, lambda s: InvoiceSyncService(s).run())
        if cmd == "run-recurring":
            period = argv[1] if len(argv) > 1 else period_of(datetime.now(TAIPEI).date())
            return _run(cmd, lambda s: AccrualService(s).run_recurring(period) | {"period": period})
        if cmd == "refresh-directory":
            d = get_directory()
            return _run(cmd, lambda s: {"companies": len(d.list_companies()), "stores": len(d.list_stores())})
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    finally:
        engine().dispose()  # Railway Cron：結束前關閉連線


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
