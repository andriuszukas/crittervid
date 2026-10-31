"""The Streamlit GUI, driven headlessly with Streamlit's AppTest: fill in the form,
click Run, and check what the user sees. Uses the fake ffmpeg, so it's fast.
Skipped when Streamlit isn't installed (required on CI).

The Streamlit GUI is due to be replaced by a PySide6 window (#41); these tests
keep the current one honest until then.
"""
import json
from pathlib import Path

import pytest

from conftest import require

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

RUN_TIMEOUT = 60
NO_TIME_ZONE = "Not set (this computer's)"


@pytest.fixture
def gui(ws, monkeypatch):
    """The app from the workspace, with the fake ffmpeg first on PATH."""
    require(AppTest is not None, 'Streamlit')
    # The app starts the pipeline with this process's environment
    monkeypatch.setenv('PATH', ws.env['PATH'])
    at = AppTest.from_file(str(ws.root / 'src' / 'crittervid' / 'gui' / 'streamlit_app.py'), default_timeout=RUN_TIMEOUT)
    return at.run()


def fill_in_and_run(gui, ws):
    gui.text_input[0].input(str(ws.root / 'input' / 'DCIM'))
    gui.text_input[1].input(str(ws.root / 'output'))
    gui.selectbox[1].select('CPU')   # after the time zone
    [run_button] = [b for b in gui.button if b.label == 'Run']
    return run_button.click().run(timeout=RUN_TIMEOUT)


def test_gui_shows_the_form(gui):
    assert not gui.exception
    assert [t.label for t in gui.text_input] == ['Input directory', 'Output directory', 'Site', 'Camera']
    assert 'Run' in [b.label for b in gui.button]
    assert 'Merge everything again' in [c.label for c in gui.checkbox]


def test_gui_reports_success(gui, ws):
    ws.add_media('0001.media', b'good')

    at = fill_in_and_run(gui, ws)

    assert not at.exception
    assert [s.value for s in at.success] == ['✅ Pipeline completed successfully!']
    [caption] = [c.value for c in at.caption if c.value.startswith('Run ')]
    assert ' · log: ' in caption and caption.split(' · log: ')[1].split(' (')[0].endswith('.jsonl')
    assert ws.converted('0001.media').read_bytes() == b'OKgood'


def test_gui_reports_failure(gui, ws):
    ws.add_media('0001.media', b'BAD')

    at = fill_in_and_run(gui, ws)

    assert not at.exception
    assert [e.value for e in at.error] == ['❌ Pipeline failed with exit code 1']


def test_app_help_text_matches_deployment_docs():
    app = Path(__file__).resolve().parent.parent / 'src' / 'crittervid' / 'gui' / 'streamlit_app.py'
    app_text = app.read_text(encoding="utf-8")
    assert "Auto recommended" not in app_text
    assert "Auto** - Automatically detects your GPU (recommended)" not in app_text
    assert "included with standalone executable" not in app_text
    assert "unless the build bundled it" in app_text
    assert "can't detect NVIDIA cards" not in app_text   # Auto tries each encoder now (#2, #10)
    assert "uses the first that works" in app_text
    assert "into the `_internal` folder" in app_text


def test_old_app_name_still_shows_the_form(ws):
    # `streamlit run app.py` from the project folder, as run_app.bat once did
    require(AppTest is not None, 'Streamlit')
    at = AppTest.from_file(str(ws.root / 'app.py'), default_timeout=RUN_TIMEOUT).run()

    assert not at.exception
    assert 'Run' in [b.label for b in at.button]


def test_gui_saves_the_camera_details_and_fills_them_in_again(gui, ws):
    ws.add_media('0001.media', b'good')
    gui.text_input[1].input(str(ws.root / 'output')).run()   # a new folder fills them in afresh
    gui.selectbox(key='timezone').select('Europe/Vilnius')
    gui.number_input(key='clock_offset').set_value(75)
    gui.text_input(key='site').input('Pond')
    gui.selectbox[1].select('CPU')
    [run_button] = [b for b in gui.button if b.label == 'Run']

    at = run_button.click().run(timeout=RUN_TIMEOUT)

    assert not at.exception
    assert [s.value for s in at.success] == ['✅ Pipeline completed successfully!']
    saved = json.loads((ws.root / 'output' / 'deployment.json').read_text(encoding='utf-8'))
    assert (saved['timezone'], saved['clock_offset'], saved['site']) == ('Europe/Vilnius', 75, 'Pond')
    # A new session on that output folder starts from what's saved
    fresh = AppTest.from_file(str(ws.root / 'src' / 'crittervid' / 'gui' / 'streamlit_app.py'),
                              default_timeout=RUN_TIMEOUT).run()
    fresh.text_input[1].input(str(ws.root / 'output')).run()
    assert fresh.selectbox(key='timezone').value == 'Europe/Vilnius'
    assert fresh.number_input(key='clock_offset').value == 75
    assert fresh.text_input(key='site').value == 'Pond'


def test_gui_warns_without_a_time_zone_and_shows_a_broken_file(gui, ws):
    assert any("No time zone set" in w.value for w in gui.warning)
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text('{not json', encoding='utf-8')

    at = gui.text_input[1].input(str(ws.root / 'output')).run()

    assert any("can't be read" in e.value for e in at.error)


def run_command(at):
    """The command line the Running: line shows."""
    [line] = [m.value for m in at.markdown if str(m.value).startswith('Running: ')]
    return line


def test_gui_passes_the_details_and_can_reset_a_saved_offset(gui, ws):
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text(
        '{"schema": 1, "timezone": "UTC", "clock_offset": 75}', encoding='utf-8')
    gui.text_input[1].input(str(ws.root / 'output')).run()
    assert NO_TIME_ZONE not in gui.selectbox(key='timezone').options   # saved: change it, not unset it
    gui.number_input(key='clock_offset').set_value(0)
    gui.text_input(key='camera').input('-Cam 2')   # starts like an option

    at = [b for b in gui.button if b.label == 'Run'][0].click().run(timeout=RUN_TIMEOUT)

    assert '--clock-offset=0' in run_command(at) and '--timezone=UTC' in run_command(at)
    saved = json.loads((ws.root / 'output' / 'deployment.json').read_text(encoding='utf-8'))
    assert saved['clock_offset'] == 0 and saved['camera'] == '-Cam 2'


def test_gui_shows_an_out_of_range_saved_offset_as_an_error(gui, ws):
    (ws.root / 'output').mkdir()
    (ws.root / 'output' / 'deployment.json').write_text(
        '{"schema": 1, "clock_offset": 999999999}', encoding='utf-8')

    at = gui.text_input[1].input(str(ws.root / 'output')).run()

    assert not at.exception
    assert any('more than 10 years' in e.value for e in at.error)



def add_unset_event(ws):
    path = ws.root / 'input' / 'DCIM' / '2106' / '02' / '07' / '0000000001_0602' / '0001.media'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'good')


def test_gui_lists_a_series_and_passes_its_start(gui, ws):
    add_unset_event(ws)
    gui.text_input[0].input(str(ws.root / 'input' / 'DCIM'))
    at = gui.text_input[1].input(str(ws.root / 'output')).run()
    at = at.selectbox(key='timezone').select('UTC').run()

    [field] = [t for t in at.text_input if t.key == 'series_0000000001_0602']
    assert field.label == 'Series 0000000001_0602: 1 events, 0 h 10 min'
    field.input('2025-10-01 08:00')
    at.selectbox[1].select('CPU')
    at = [b for b in at.button if b.label == 'Run'][0].click().run(timeout=RUN_TIMEOUT)

    assert not at.exception
    assert [s.value for s in at.success] == ['✅ Pipeline completed successfully!']
    saved = json.loads((ws.root / 'output' / 'deployment.json').read_text(encoding='utf-8'))
    assert saved['series'] == {'0000000001_0602': '2025-10-01T08:00:00+00:00'}
    assert list((ws.root / 'output' / 'merged' / '2025' / '10' / '01').glob('merged_*.mp4'))


def test_gui_shows_a_series_start_it_cant_use(gui, ws):
    add_unset_event(ws)
    at = gui.text_input[0].input(str(ws.root / 'input' / 'DCIM')).run()

    [field] = [t for t in at.text_input if t.key == 'series_0000000001_0602']
    at = field.input('2025-10-01 08:00').run()   # no time zone chosen

    assert any('no time zone is set' in e.value for e in at.error)


def test_gui_shows_no_series_section_for_a_card_without_any(gui):
    assert not any(t.key and str(t.key).startswith('series_') for t in gui.text_input)


def test_gui_fills_in_series_starts_again_for_another_output_folder(gui, ws):
    add_unset_event(ws)
    for folder, start in (('out-a', '2025-10-01T08:00:00+00:00'), ('out-b', '2025-10-02T09:00:00+00:00')):
        (ws.root / folder).mkdir()
        (ws.root / folder / 'deployment.json').write_text(
            json.dumps({'schema': 2, 'timezone': 'UTC', 'series': {'0000000001_0602': start}}), encoding='utf-8')
    gui.text_input[0].input(str(ws.root / 'input' / 'DCIM'))

    at = gui.text_input[1].input(str(ws.root / 'out-a')).run()
    assert at.text_input(key='series_0000000001_0602').value == '2025-10-01T08:00:00+00:00'
    at.session_state['output_dir_value'] = str(ws.root / 'out-b')   # as Browse sets it
    at = at.run()
    assert at.text_input[1].value == str(ws.root / 'out-b')
    assert at.text_input(key='series_0000000001_0602').value == '2025-10-02T09:00:00+00:00'


def test_gui_doesnt_run_with_a_series_start_it_cant_use(gui, ws):
    add_unset_event(ws)
    at = gui.text_input[0].input(str(ws.root / 'input' / 'DCIM')).run()
    at = at.text_input(key='series_0000000001_0602').input('2099-01-01 08:00').run()

    at = [b for b in at.button if b.label == 'Run'][0].click().run(timeout=RUN_TIMEOUT)

    assert any('Fix the series starts' in e.value for e in at.error)
    assert not at.success and not (ws.root / 'output' / 'conversion_summary.txt').exists()
