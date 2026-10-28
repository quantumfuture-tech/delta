import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-ui-test")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from delta.ui import theme

    theme.apply(app)
    return app
