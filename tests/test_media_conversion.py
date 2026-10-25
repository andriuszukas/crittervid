"""Converting .media files in the cameras' record format with the real FFmpeg:
camera timestamps kept exactly, audio included and lined up, one file per event.
Skipped when FFmpeg isn't installed (required on CI)."""
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from media_fixture import write_event, utc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_format import read_records  # noqa: E402

START = utc(2026, 10, 1, 6, 0)


def probe(path, entries):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', entries, '-of', 'json', str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def streams(path):
    return {s['codec_type']: s for s in probe(path, 'stream=codec_type,codec_name,sample_rate,start_time,duration')['streams']}


def event_output(ws, folder):
    return ws.root / 'output' / folder.relative_to(ws.root / 'input' / 'DCIM').parent / f'{folder.name}.mp4'


def frame_times(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'packet=pts_time', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True).stdout
    return [float(p['pts_time']) for p in json.loads(out)['packets']]


def assert_camera_times(path, stamps):
    """Every frame is where the camera put it, to within a millisecond."""
    times = frame_times(path)
    assert len(times) == len(stamps)
    off = max(abs((t - times[0]) - (s - stamps[0]) / 1e6) for t, s in zip(times, stamps))
    assert off < 0.001, f'a frame is {off * 1000:.1f} ms off'


def convert(ws):
    result = ws.run('convert_media.py', '--accel', 'cpu')
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_event_becomes_one_file_with_camera_timestamps(real_ws):
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 30)

    result = convert(real_ws)

    assert '(3 segments)' in result.stdout
    assert_camera_times(event_output(real_ws, folder), stamps)
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


def first_loud(audio, threshold=4000):
    """Seconds into the (8 kHz) audio of the first sample louder than threshold."""
    return next(i for i, sample in enumerate(audio) if abs(sample) > threshold) / 8000


def test_audio_lines_up_with_the_video(real_ws):
    # The camera's audio starts 300 ms after its first video frame
    write_event(real_ws.root / 'input' / 'DCIM', START, 5, audio_offset_us=300_000)

    convert(real_ws)

    [out] = real_ws.root.joinpath('output').rglob('*.mp4')
    assert first_loud(decoded_audio(out)) == pytest.approx(0.3, abs=0.01)
    s = streams(out)
    assert float(s['video']['start_time']) == float(s['audio']['start_time'])


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


# --- oddities seen on real cards -------------------------------------------------

def only_output(ws):
    """The one .mp4 the conversion wrote (an event file, not per-segment files)."""
    outputs = list(ws.root.joinpath('output').rglob('*.mp4'))
    assert len(outputs) == 1, outputs
    return outputs[0]


def assert_audio_matches_video(out, video_seconds=None, tolerance=0.1):
    s = streams(out)
    assert float(s['audio']['duration']) == pytest.approx(float(s['video']['duration']), abs=tolerance)
    if video_seconds is not None:
        assert float(s['video']['duration']) == pytest.approx(video_seconds, abs=0.2)


def warnings(result):
    return [line for line in result.stdout.splitlines() if line.startswith('WARNING')]


def test_zero_filled_audio_bursts_do_not_drift_the_audio(real_ws):
    # Bursts of 20 and 24 zero records 20 ms apart would add 0.8 s of audio if appended
    write_event(real_ws.root / 'input' / 'DCIM', START, 60, audio_bursts=[(15, 20), (35, 24)], marker_at_s=55)

    convert(real_ws)

    out = only_output(real_ws)
    assert_audio_matches_video(out, 60)
    # A loud 200 ms marker at 55 s is still at 55 s in the audio
    assert first_loud(decoded_audio(out)) == pytest.approx(55, abs=0.025)


def test_recording_gap_is_kept_in_video_and_audio(real_ws):
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 40, gap=(10, 15))
    assert len(stamps) < 400   # 25 s of video, not 40

    convert(real_ws)

    out = only_output(real_ws)
    assert_camera_times(out, stamps)   # including the frames after the gap
    assert_audio_matches_video(out, (stamps[-1] - stamps[0]) / 1e6)


def test_first_keyframe_with_leftover_pictures_before_a_long_pause_keeps_its_time(real_ws):
    # The first keyframe, 27 s before the rest, carries slices of an earlier
    # picture; FFmpeg took them for extra frames and shifted the keyframe and the
    # video's start, which cut the start of the event from merged days (#77)
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 20, leftovers_first_s=27)

    result = convert(real_ws)

    out = only_output(real_ws)
    assert float(streams(out)['video']['start_time']) == pytest.approx(0, abs=0.001)
    assert_camera_times(out, stamps)
    assert_audio_matches_video(out, (stamps[-1] - stamps[0]) / 1e6)
    assert any('earlier pictures' in line and 'removed' in line for line in warnings(result))


def test_truncated_last_segment_is_used_up_to_its_last_record(real_ws):
    folder, _ = write_event(real_ws.root / 'input' / 'DCIM', START, 30, truncate_last_bytes=23)

    result = convert(real_ws)

    out = only_output(real_ws)   # one event file, not per-segment fallbacks
    assert out == event_output(real_ws, folder)
    assert any('bytes' in line for line in warnings(result))
    assert_audio_matches_video(out, 30, tolerance=0.2)


def test_empty_segment_is_skipped_with_a_warning(real_ws):
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 40, empty_segment=1)

    result = convert(real_ws)

    out = only_output(real_ws)
    assert out == event_output(real_ws, folder)
    assert any(folder.name in line and 'empty' in line for line in warnings(result))
    assert len(frame_times(out)) < len(stamps)
    assert 'audio' in streams(out)


def test_repeated_frames_and_garbage_records_do_not_stretch_the_audio(real_ws):
    # Segment 1 starts with segment 0's first 10 frames and their audio again; the
    # last segment ends with 5 records with timestamp 0
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 40, repeat_first_frames=10, garbage_tail=5)

    result = convert(real_ws)

    out = only_output(real_ws)
    assert warnings(result)   # frames dropped
    assert_camera_times(out, stamps)
    assert_audio_matches_video(out, (stamps[-1] - stamps[0]) / 1e6)


def test_event_without_audio_gets_a_silent_audio_track(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', START, 10, no_audio=True)

    result = convert(real_ws)

    out = only_output(real_ws)
    assert any(line.startswith('SUCCESS') and 'silent' in line for line in result.stdout.splitlines())
    assert_audio_matches_video(out, 10)
    assert loudness(decoded_audio(out)) < 50


def test_day_starting_with_a_silent_event_merges_with_audio(real_ws):
    dcim = real_ws.root / 'input' / 'DCIM'
    write_event(dcim, utc(2026, 10, 1, 6, 0), 10, no_audio=True)
    write_event(dcim, utc(2026, 10, 1, 6, 30), 10)
    convert(real_ws)

    result = real_ws.run('merge_videos.py')

    assert result.returncode == 0, result.stdout + result.stderr
    [merged] = real_ws.files('merged_*.mp4')
    assert_audio_matches_video(merged, 20, tolerance=0.15)


def test_large_frames_convert_with_exact_times(real_ws):
    # Real 2304x1296 keyframes exceed 64 KB (up to ~180 KB); noisy test frames make
    # sure these do too, whatever the FFmpeg build
    folder, stamps = write_event(real_ws.root / 'input' / 'DCIM', START, 10, frame_size='2304x1296',
                                 large_frames=True)
    first = sorted(folder.glob('*.media'))[0].read_bytes()
    assert max(length for rtype, length, _, _ in read_records(first) if rtype == 1) > 65536

    convert(real_ws)

    out = only_output(real_ws)
    assert_camera_times(out, stamps)
    assert_audio_matches_video(out, 10)


def test_merging_many_events_does_not_drift(real_ws):
    # A day can hold dozens of events. Each event file's audio and video end a few
    # milliseconds apart, and if every join added that difference the merged day
    # would slowly drift; joins must follow the video exactly
    # 20 s: an event whose AAC audio ends a little after its video
    write_event(real_ws.root / 'input' / 'DCIM', START, 20)
    convert(real_ws)
    [event] = real_ws.root.joinpath('output').rglob('*.mp4')
    one = float(streams(event)['video']['duration'])
    copies = 20
    for i in range(1, copies):   # the same footage as 19 more events that day
        event.with_name(f'{int(START.timestamp()) + 60 * i}_0020.mp4').write_bytes(event.read_bytes())

    assert real_ws.run('merge_videos.py').returncode == 0

    [merged] = real_ws.root.joinpath('output', 'merged').rglob('merged_*.mp4')
    s = streams(merged)
    assert float(s['video']['duration']) == pytest.approx(copies * one, abs=0.02)
    assert float(s['audio']['duration']) == pytest.approx(float(s['video']['duration']), abs=0.13)


def write_day_of_events(ws, count=5):
    for i in range(count):
        write_event(ws.root / 'input' / 'DCIM', utc(2026, 10, 1, 6 + i, 0), 10)


def test_validation_passes_a_merged_day_of_events(real_ws):
    write_day_of_events(real_ws)
    convert(real_ws)
    assert real_ws.run('merge_videos.py').returncode == 0

    result = real_ws.run('validate_output.py')

    assert result.returncode == 0, result.stdout
    assert 'WARNING' not in result.stdout


def test_validation_fails_a_merged_day_missing_an_event(real_ws):
    write_day_of_events(real_ws)
    convert(real_ws)
    left_out = sorted(real_ws.root.joinpath('output').rglob('*.mp4'))[2]
    aside = left_out.with_suffix('.aside')
    left_out.rename(aside)
    assert real_ws.run('merge_videos.py').returncode == 0
    aside.rename(left_out)

    result = real_ws.run('validate_output.py')

    assert result.returncode == 1
    assert 'converted files add up to' in result.stdout


def creation_time(path):
    return probe(path, 'format_tags=creation_time')['format'].get('tags', {}).get('creation_time')


def test_event_file_records_when_its_footage_starts(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', START, 10)

    convert(real_ws)

    assert creation_time(only_output(real_ws)) == '2026-10-01T06:00:00.000000Z'


def test_event_recorded_before_the_clock_was_set_gets_no_creation_time(real_ws):
    write_event(real_ws.root / 'input' / 'DCIM', utc(1970, 1, 1, 3, 0), 10)

    convert(real_ws)

    assert creation_time(only_output(real_ws)) is None   # not a made-up date in 1970
