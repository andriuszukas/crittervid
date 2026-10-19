"""The Streamlit GUI, driven headlessly with Streamlit's AppTest: fill in the form,
click Run, and check what the user sees. Uses the fake ffmpeg, so it's fast.
Skipped when Streamlit isn't installed (required on CI).

The Streamlit GUI is due to be replaced by a PySide6 window (#41); these tests
keep the current one honest until then.
"""
from pathlib import Path

import pytest

from conftest import require

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

RUN_TIMEOUT = 60


@pytest.fixture
def gui(ws, monkeypatch):
    """The app from the workspace, with the fake ffmpeg first on PATH."""
    require(AppTest is not None, 'Streamlit')
    # The app starts the pipeline with this process's environment
    monkeypatch.setenv('PATH', ws.env['PATH'])
    at = AppTest.from_file(str(ws.root / 'app.py'), default_timeout=RUN_TIMEOUT)
    return at.run()


def fill_in_and_run(gui, ws):
    gui.text_input[0].input(str(ws.root / 'input' / 'DCIM'))
    gui.text_input[1].input(str(ws.root / 'output'))
    gui.selectbox[0].select('CPU')
    [run_button] = [b for b in gui.button if b.label == 'Run']
    return run_button.click().run(timeout=RUN_TIMEOUT)


def test_gui_shows_the_form(gui):
    assert not gui.exception
    assert [t.label for t in gui.text_input] == ['Input directory', 'Output directory']
    assert 'Run' in [b.label for b in gui.button]


def test_gui_reports_success(gui, ws):
    ws.add_media('0001.media', b'good')

    at = fill_in_and_run(gui, ws)

    assert not at.exception
    assert [s.value for s in at.success] == ['✅ Pipeline completed successfully!']
    assert ws.converted('0001.media').read_bytes() == b'OKgood'


def test_gui_reports_failure(gui, ws):
    ws.add_media('0001.media', b'BAD')

    at = fill_in_and_run(gui, ws)

    assert not at.exception
    assert [e.value for e in at.error] == ['❌ Pipeline failed with exit code 1']


def test_app_help_text_matches_deployment_docs():
    app = Path(__file__).resolve().parent.parent / 'app.py'
    app_text = app.read_text(encoding="utf-8")
    assert "Auto recommended" not in app_text
    assert "Auto** - Automatically detects your GPU (recommended)" not in app_text
    assert "included with standalone executable" not in app_text
    assert "unless the build bundled it" in app_text
    assert "can't detect NVIDIA cards" in app_text
    assert "into the `_internal` folder" in app_text
