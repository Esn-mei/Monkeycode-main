import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monkeycode.messages import ToolCall
from monkeycode.plan import DefaultPlanManager, PlanStatus, parse_plan
from monkeycode.session_archive import SessionArchive, cleanup_expired_sessions, generate_session_id


def test_generates_session_id_shape() -> None:
    ident = generate_session_id(now=datetime(2026, 6, 26, 12, 1, 2, tzinfo=timezone.utc))

    assert re.fullmatch(r"20260626-120102-[0-9a-f]{4}", ident)


def test_appends_events_and_scans_summary(tmp_path: Path) -> None:
    archive = SessionArchive.create(tmp_path)
    archive.append_user_message("hello world")
    archive.append_assistant_message("hi")
    archive.end()

    lines = archive.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "session_started"
    assert any(json.loads(line)["type"] == "user_message" for line in lines)
    assert not list(archive.path.parent.glob("*.meta.*"))
    summary = archive.summarize()
    assert summary.title == "hello world"
    assert summary.message_count == 2
    assert summary.updated_at is not None


def test_lists_summaries_newest_first(tmp_path: Path) -> None:
    older = SessionArchive.create(tmp_path, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    older.append_user_message("older", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = SessionArchive.create(tmp_path, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    newer.append_user_message("newer", timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc))

    summaries = SessionArchive.list_summaries(tmp_path)

    assert [summary.session_id for summary in summaries] == [newer.session_id, older.session_id]


def test_restore_skips_bad_lines(tmp_path: Path) -> None:
    archive = SessionArchive.create(tmp_path)
    archive.append_user_message("hello")
    with archive.path.open("a", encoding="utf-8") as handle:
        handle.write("{bad json\n")
    archive.append_assistant_message("hi")

    result = archive.restore()

    assert result.skipped_bad_lines == 1
    assert [message.content for message in result.messages] == ["hello", "hi"]


def test_restore_truncates_incomplete_tool_tail(tmp_path: Path) -> None:
    archive = SessionArchive.create(tmp_path)
    archive.append_user_message("read")
    archive.append_assistant_tool_calls(
        [ToolCall(id="call_1", name="read_file", arguments_json="{}", arguments={})]
    )

    result = archive.restore()

    assert result.truncated_incomplete_tail is True
    assert [message.role for message in result.messages] == ["user"]


def test_plan_events_round_trip_and_legacy_session_compatibility(tmp_path: Path) -> None:
    archive = SessionArchive.create(tmp_path)
    plan = parse_plan("1. 读取文件\n2. 运行测试")
    manager = DefaultPlanManager()
    running = manager.mark_tool_started(plan, "call_1")
    archive.append_plan_created(plan)
    archive.append_plan_checkpoint(running)

    restored = archive.restore()

    assert restored.plan == running

    legacy = SessionArchive.create(tmp_path)
    legacy.append_user_message("legacy")
    assert legacy.restore().plan is None


def test_restore_adds_stale_notice(tmp_path: Path) -> None:
    old_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    archive = SessionArchive.create(tmp_path, now=old_time)
    archive.append_user_message("old", timestamp=old_time)
    result = archive.restore(now=datetime(2026, 1, 3, tzinfo=timezone.utc), stale_after_seconds=86400)

    assert result.restore_notice_added is True
    assert "中断" in result.messages[-1].content


def test_cleanup_expired_sessions(tmp_path: Path) -> None:
    old_archive = SessionArchive.create(tmp_path)
    new_archive = SessionArchive.create(tmp_path)
    old_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    old_archive.path.touch()
    new_archive.path.touch()
    import os

    os.utime(old_archive.path, (old_time, old_time))

    removed = cleanup_expired_sessions(tmp_path, older_than_days=30)

    assert removed == 1
    assert not old_archive.path.exists()
    assert new_archive.path.exists()
