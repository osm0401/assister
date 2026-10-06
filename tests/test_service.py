from datetime import datetime, timedelta

import pytest

from assister.tasks.repository import TaskRepository
from assister.tasks.service import TaskService, format_time

NOW = datetime(2026, 10, 6, 15, 0, 0)


@pytest.fixture
def service(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.db")
    yield TaskService(repository)
    repository.close()


def test_alarm_request_is_stored_and_confirmed(service):
    reply = service.handle("10분 뒤에 빨래 꺼내기", NOW)

    assert reply is not None
    assert "오후 3:10" in reply.text and "빨래 꺼내기" in reply.text
    assert [alarm.message for alarm in service.repository.list_pending_alarms()] == ["빨래 꺼내기"]


def test_todo_can_be_added_listed_and_completed_by_number(service):
    service.handle("할 일 추가 우유 사기", NOW)
    service.handle("할 일 추가 메일 답장", NOW)

    listing = service.handle("할 일 목록", NOW)
    assert "1. 우유 사기" in listing.text and "2. 메일 답장" in listing.text

    done = service.handle("1번 완료", NOW)
    assert "우유 사기" in done.text

    # 번호는 남은 할 일 기준으로 다시 매겨진다.
    assert "1. 메일 답장" in service.handle("할 일 목록", NOW).text


def test_empty_todo_list_says_so(service):
    assert "없" in service.handle("할 일 목록", NOW).text


def test_completing_missing_number_explains(service):
    reply = service.handle("3번 완료", NOW)

    assert "3번" in reply.text
    assert reply.expression == "thinking"


def test_ordinary_talk_is_not_handled(service):
    assert service.handle("오늘 날씨 어때?", NOW) is None


def test_due_alarms_ring_once(service):
    service.handle("5분 뒤 스트레칭", NOW)

    assert service.ring_due_alarms(NOW + timedelta(minutes=4)) == []
    rung = service.ring_due_alarms(NOW + timedelta(minutes=5))
    assert [reply.text for reply in rung] == ["스트레칭 시간이에요!"]
    assert service.ring_due_alarms(NOW + timedelta(minutes=6)) == []


def test_daily_alarm_rings_every_day(service):
    service.repository.add_alarm("약 먹기", NOW, repeat="daily")

    assert [reply.text for reply in service.ring_due_alarms(NOW)] == ["약 먹기 시간이에요!"]
    assert service.ring_due_alarms(NOW + timedelta(hours=1)) == []
    assert [reply.text for reply in service.ring_due_alarms(NOW + timedelta(days=1))] == ["약 먹기 시간이에요!"]


def test_daily_alarm_missed_for_days_rings_once_and_moves_to_the_future(service):
    """PC를 며칠 꺼 뒀다 켜도 밀린 만큼 연달아 울리지 않는다."""
    service.repository.add_alarm("약 먹기", NOW, repeat="daily")
    later = NOW + timedelta(days=3, hours=2)

    assert len(service.ring_due_alarms(later)) == 1
    assert service.repository.list_pending_alarms()[0].due_at == NOW + timedelta(days=4)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 6, 15, 10), "오후 3:10"),
        (datetime(2026, 10, 6, 0, 5), "오전 12:05"),
        (datetime(2026, 10, 6, 12, 0), "오후 12:00"),
        (datetime(2026, 10, 7, 9, 30), "내일 오전 9:30"),
    ],
)
def test_format_time(moment, expected):
    assert format_time(moment, NOW) == expected
