"""crittervid.log on its own (decision record 0003): the run ID, the console,
the run log and the app log."""
import json
import logging
import sys

import pytest

import conftest  # noqa: F401  (puts src/ on the import path)
from crittervid import log
from crittervid.config import PipelineConfig


@pytest.fixture
def logs(tmp_path, monkeypatch, capsys):
    """App log in a temporary folder; the console is pytest's captured stdout."""
    monkeypatch.setenv('CRITTERVID_LOG_DIR', str(tmp_path / 'app-logs'))
    yield tmp_path, capsys
    log.stop_run()


def console_text(capsys):
    return capsys.readouterr().out


def records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_without_a_run_nothing_is_printed_or_written(logs):
    tmp_path, console = logs

    log.get('convert').warning('WARNING: not set up')

    assert console_text(console) == ''
    assert not (tmp_path / 'app-logs').exists()


def test_a_run_logs_to_the_console_the_run_log_and_the_app_log(logs):
    tmp_path, console = logs
    config = PipelineConfig(output_dir=tmp_path / 'out', splits_per_day=2)

    run = log.start_run(config, 'run')
    log.get('convert').info('SUCCESS: Converted a -> b', extra={'file': 'a', 'duration_s': 1.5})
    log.get('merge').warning('WARNING: kept 1 merged files')
    log.stop_run()

    # The console shows the messages as they are
    assert console_text(console).splitlines() == [f'Run {run.id} started; log: {run.run_log}',
                                                  'SUCCESS: Converted a -> b', 'WARNING: kept 1 merged files']
    assert run.run_log.parent == tmp_path / 'out' / 'logs' and run.run_log.name.endswith(f'_{run.id}.jsonl')
    first, converted, kept = records(run.run_log)
    assert first['command'] == 'run' and first['settings']['splits_per_day'] == 2
    assert 'cancel' not in first['settings']
    assert {r['run'] for r in (first, converted, kept)} == {run.id}
    assert converted['step'] == 'convert' and kept['step'] == 'merge'
    assert converted['file'] == 'a' and converted['duration_s'] == 1.5
    assert kept['level'] == 'WARNING'
    assert records(run.app_log)[-1]['message'] == 'WARNING: kept 1 merged files'


def test_record_fields_and_types(logs):
    tmp_path, _ = logs
    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    log.get('validate').error('ERROR: x')
    log.stop_run()

    record = records(run.run_log)[-1]
    assert set(record) == {'schema', 'time', 'run', 'level', 'step', 'message', 'elapsed_s'}
    assert record['schema'] == log.SCHEMA and isinstance(record['elapsed_s'], float)
    assert record['time'].endswith('Z') and 'T' in record['time']
    assert (record['level'], record['step'], record['message']) == ('ERROR', 'validate', 'ERROR: x')


def test_a_traceback_and_text_that_isnt_utf8_are_kept(logs):
    tmp_path, _ = logs
    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    name = b'in\xff'.decode('utf-8', 'surrogateescape')   # a Linux file name that isn't UTF-8
    try:
        raise ValueError('boom')
    except ValueError:
        log.get('convert').exception(f'ERROR: {name}')
    log.stop_run()

    record = records(run.run_log)[-1]
    assert record['message'] == f'ERROR: {name}'
    assert 'ValueError: boom' in record['traceback']


def test_starting_again_replaces_the_outputs(logs):
    tmp_path, console = logs
    log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))

    log.get('merge').info('once')

    assert console_text(console).count('once') == 1


def test_no_run_log_for_commands_that_write_no_output(logs):
    tmp_path, console = logs

    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'), 'validate', run_log=False)
    log.get('validate').info('All output videos passed the checks.')
    log.stop_run()

    assert run.run_log is None and not (tmp_path / 'out').exists()
    assert f'log: {run.app_log}' in console_text(console)


def test_without_a_console_the_logs_are_still_written(logs, monkeypatch):
    tmp_path, _ = logs
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)

    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    log.get('convert').info('SUCCESS: done')
    log.stop_run()

    assert records(run.run_log)[-1]['message'] == 'SUCCESS: done'


def test_a_log_that_cant_be_written_is_skipped_with_a_warning(logs):
    tmp_path, console = logs
    (tmp_path / 'out').write_text('a file where the output folder should be')

    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    log.get('convert').info('SUCCESS: still running')
    log.get('convert').info('SUCCESS: and again')
    log.stop_run()

    text = console_text(console)
    assert text.count("can't write the log") == 1 and 'SUCCESS: and again' in text
    assert records(run.app_log)[-1]['message'] == 'SUCCESS: and again'


def test_an_app_log_folder_that_cant_be_used_is_skipped_with_a_warning(logs, monkeypatch):
    tmp_path, console = logs
    blocked = tmp_path / 'blocked'
    blocked.write_text('a file where the log folder should be')
    monkeypatch.setenv('CRITTERVID_LOG_DIR', str(blocked))

    run = log.start_run(PipelineConfig(output_dir=tmp_path / 'out'))
    log.get('convert').info('SUCCESS: done')
    log.stop_run()

    assert run.app_log is None
    assert "can't use the app log folder" in console_text(console)
    assert records(run.run_log)[-1]['message'] == 'SUCCESS: done'


def test_a_large_app_log_is_moved_aside_when_a_run_starts(logs, monkeypatch):
    tmp_path, _ = logs
    folder = tmp_path / 'app-logs'
    folder.mkdir()
    (folder / log.APP_LOG_NAME).write_text('x' * 200)
    (folder / f'{log.APP_LOG_NAME}.1').write_text('older')
    monkeypatch.setattr(log, 'APP_LOG_MAX_BYTES', 100)

    log.start_run(None, 'convert')
    log.stop_run()

    assert (folder / f'{log.APP_LOG_NAME}.1').read_text() == 'x' * 200
    assert (folder / f'{log.APP_LOG_NAME}.2').read_text() == 'older'
    assert records(folder / log.APP_LOG_NAME)[0]['message'].startswith('Run ')


def test_verbose_shows_debug_records_on_the_console(logs):
    tmp_path, console = logs
    log.start_run(None, 'convert', verbose=False)
    log.get('convert').debug('ffmpeg -i x')
    log.start_run(None, 'convert', verbose=True)
    log.get('convert').debug('ffmpeg -i y')

    text = console_text(console)
    assert 'ffmpeg -i x' not in text and 'ffmpeg -i y' in text


def test_the_formatter_never_raises():
    class Unprintable:
        def __str__(self):
            raise RuntimeError('no')

        __repr__ = object.__repr__

    record = logging.LogRecord('crittervid.convert', logging.INFO, __file__, 1, 'x %s %s', ('only one',), None)
    record.file = Unprintable()

    json.loads(log.JsonFormatter().format(record))   # still valid JSON
