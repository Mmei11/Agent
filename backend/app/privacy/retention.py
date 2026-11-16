"""Phase F：数据保留期清理。

默认只打印将删除的会话；加 --execute 才真正删除 checkpoint、历史行和反馈。
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RETENTION_DAYS = 90


def retention_days() -> int:
    return int(os.getenv("DATA_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))


def cutoff_iso(days: int | None = None) -> str:
    days = retention_days() if days is None else days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat(timespec="milliseconds")


def purge(days: int | None = None, *, dry_run: bool = True) -> list[str]:
    from backend.app.accounts import store as account_store

    cutoff = cutoff_iso(days)
    candidates = account_store.sessions_before(cutoff)
    if dry_run or not candidates:
        return candidates

    deleted = account_store.purge_sessions_before(cutoff)

    from backend.app.feedback import store as feedback_store
    from backend.app.storage.saver import get_saver

    with get_saver() as saver:
        for session_id in deleted:
            saver.delete_thread(session_id)
    feedback_store.delete_for_sessions(deleted)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--execute", action="store_true", help="真正删除；默认 dry-run")
    args = parser.parse_args()

    ids = purge(args.days, dry_run=not args.execute)
    action = "将删除" if not args.execute else "已删除"
    print(f"{action} {len(ids)} 个超过保留期的会话")
    for session_id in ids[:20]:
        print(" -", session_id)
    if len(ids) > 20:
        print(f" ... 其余 {len(ids) - 20} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
