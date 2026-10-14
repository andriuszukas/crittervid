"""Converting .media files in the cameras' record format with the real FFmpeg:
camera timestamps kept exactly, audio included and lined up, one file per event.
Skipped when FFmpeg isn't installed (required on CI)."""
import json
import struct
import subprocess

import pytest

from media_fixture import write_event, utc

START = utc(2026, 10, 1, 6, 0)


def probe(path, entries):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', entries, '-of', 'json', str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def streams(path):
    return {s['codec_type']: s for s in probe(path, 'stream=codec_type,codec_name,sample_rate,start_time,duration')['streams']}


def event_output(ws, folder):
    return ws.root / 'output' / folder.relative_to(ws.root / 'input' / 'DCIM').parent / f'{folder.name}.mp4'


def convert(ws):
    result = ws.run('convert_media.py', '--accel', 'cpu')
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_event_becomes_one_file_with_camera_timestamps(real_ws):
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 30)

    result = convert(real_ws)

    assert '(3 segments)' in result.stdout
    out = event_output(real_ws, folder)
    packets = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'packet=pts_time', '-of', 'json', str(out)],
        capture_output=True, text=True, check=True).stdout
    video = [float(p['pts_time']) for p in json.loads(packets)['packets']]
    # Every frame where the camera put it (to within FFmpeg's 90 kHz clock)
    assert len(video) == len(stamps)
    expected = [(t - stamps[0]) / 1e6 for t in stamps]
    assert [round(v - video[0], 3) for v in video] == [round(e, 3) for e in expected]
    assert not list(real_ws.root.joinpath('output').rglob('*.part'))


def test_event_has_aac_audio_as_long_as_the_video(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', START, 30)

    convert(real_ws)

    [out] = real_ws.root.joinpath('output').rglob('*.mp4')
    s = streams(out)
    assert s['audio']['codec_name'] == 'aac' and s['audio']['sample_rate'] == '8000'
    assert float(s['audio']['duration']) == pytest.approx(float(s['video']['duration']), abs=0.1)
    assert float(s['video']['duration']) == pytest.approx(30, abs=0.15)


def decoded_audio(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a', '-f', 's16le', '-ac', '1', '-'],
                         capture_output=True, check=True).stdout
    return struct.unpack(f'<{len(raw) // 2}h', raw)


def loudness(samples):
    return max((abs(s) for s in samples), default=0)


def test_audio_lines_up_with_the_video(real_ws):
    # The camera's audio starts 300 ms after its first video frame
    write_event(real_ws.root / 'input' / 'DCIM', START, 5, audio_offset_us=300_000)

    convert(real_ws)

    [out] = real_ws.root.joinpath('output').rglob('*.mp4')
    audio = decoded_audio(out)   # 8 kHz: 8 samples per ms
    assert loudness(audio[:250 * 8]) < 500          # silence until ~300 ms
    assert loudness(audio[350 * 8:600 * 8]) > 4000   # the tone after it


def test_duration_mismatch_is_reported(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', START, 10, folder_seconds=60)

    result = convert(real_ws)

    assert 'WARNING' in result.stdout and 'the folder name says 60 s' in result.stdout


def test_unknown_duration_is_not_reported(real_ws):
    # Real cards have events named ..._0000 when the camera didn't record a length
    write_event(real_ws.root / 'input' / 'DCIM', START, 10, folder_seconds=0)

    result = convert(real_ws)

    assert 'WARNING' not in result.stdout


def test_rerun_skips_converted_events(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', START, 10)
    convert(real_ws)

    result = convert(real_ws)

    assert result.stdout.count('SKIPPED:') == 1
