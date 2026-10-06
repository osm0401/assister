import os
import time

import pytest

# 테스트 중에는 실제 화면에 창을 띄우지 않는다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def wait_until(qapp):
    """다른 스레드가 보낸 신호가 화면 스레드에 닿을 때까지 기다린다."""

    def wait(condition, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qapp.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    return wait
