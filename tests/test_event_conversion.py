"""Converting events in the cameras' record format, with the fake ffmpeg: which
files and folders get converted, failures, retries and warnings. The fake writes
b'OK' + the video it was given, so the output shows which frames went in.
(tests/test_media_conversion.py checks the real thing.)"""
import json
import struct

import pytest

from conftest import GROUP_DIR

HEADER = struct.Struct('<IIIIQ')


def record(rtype, payload, t_us):
    return HEADER.pack(rtype, len(payload), t_us // 1000, 0, t_us) + payload


def segment(label, start_us):
    """A small segment in the record format: a keyframe, a frame and some audio.
    The label ends up in the fake's output, since it only copies what it gets."""
    # A keyframe holds its parameter sets and an IDR slice, as the cameras store it
    key = b'\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65' + label
    frame = b'\x00\x00\x00\x01\x41' + label
    return (record(1, key, start_us) + record(3, bytes(640), start_us + 38_000)
            + record(0, frame, start_us + 50_000))


def event_output(ws):
    return ws.root / 'output' / GROUP_DIR.relative_to('input/DCIM').parent / f'{GROUP_DIR.name}.mp4'


def convert(ws, *args):
    return ws.run('convert_media.py', '--accel', 'cpu', *args)


def test_failed_event_is_listed_and_converted_by_retry_failed(ws):
    ws.add_media('0000.media', segment(b'first', 1_000_000))
    ws.add_media('0013.media', segment(b'BAD', 14_000_000))

    result = convert(ws)

    assert result.returncode == 1
    failed = (ws.root / 'output' / 'conversion_failed.txt').read_text().split()
    assert failed == [str((ws.root / GROUP_DIR).relative_to(ws.root))]   # the event, not a segment
    assert not event_output(ws).exists()

    ws.add_media('0013.media', segment(b'fixed', 14_000_000))
    result = convert(ws, '--retry-failed')

    assert result.returncode == 0, result.stdout
    data = event_output(ws).read_bytes()
    assert data.startswith(b'OK') and b'first' in data and b'fixed' in data
    assert not (ws.root / 'output' / 'conversion_failed.txt').exists()


def test_retry_failed_converts_the_whole_event_of_a_listed_segment(ws):
    first = ws.add_media('0000.media', segment(b'first', 1_000_000))
    ws.add_media('0013.media', segment(b'second', 14_000_000))
    failed = ws.root / 'output' / 'conversion_failed.txt'
    failed.parent.mkdir()
    failed.write_text(str(first.relative_to(ws.root)) + '\n')

    result = convert(ws, '--retry-failed')

    assert result.returncode == 0, result.stdout
    data = event_output(ws).read_bytes()
    assert b'first' in data and b'second' in data


def test_retry_failed_with_several_segments_of_one_event_converts_it_once(ws):
    listed = [ws.add_media('0000.media', segment(b'first', 1_000_000)),
              ws.add_media('0013.media', segment(b'second', 14_000_000))]
    failed = ws.root / 'output' / 'conversion_failed.txt'
    failed.parent.mkdir()
    failed.write_text(''.join(str(p.relative_to(ws.root)) + '\n' for p in listed))
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    assert convert(ws, '--retry-failed').returncode == 0

    assert len(ws.ffmpeg_calls()) == 1


def test_folder_not_named_like_an_event_is_an_error(ws):
    # e.g. a folder copied twice by a file manager
    bad = ws.root / 'input/DCIM/2025/02/29/1740830400_0020 (1)'
    bad.mkdir(parents=True)
    (bad / '0000.media').write_bytes(segment(b'first', 1_000_000))

    result = convert(ws)

    assert result.returncode == 1
    assert any(line.startswith('ERROR:') and '1740830400_0020 (1)' in line for line in result.stdout.splitlines())
    assert ws.files('*.mp4') == []


def test_empty_segment_is_skipped_with_a_warning(ws):
    ws.add_media('0000.media', segment(b'first', 1_000_000))
    ws.add_media('0013.media', b'')

    result = convert(ws)

    assert result.returncode == 0, result.stdout
    assert any(line.startswith('WARNING') and GROUP_DIR.name in line and 'empty' in line
               for line in result.stdout.splitlines())
    assert b'first' in event_output(ws).read_bytes()


def test_segment_in_another_format_is_skipped_when_others_are_in_the_record_format(ws):
    ws.add_media('0000.media', segment(b'first', 1_000_000))
    ws.add_media('0013.media', bytes(range(256)) * 4)

    result = convert(ws)

    assert result.returncode == 0, result.stdout
    assert any(line.startswith('WARNING') and '0013.media' in line for line in result.stdout.splitlines())
    assert b'first' in event_output(ws).read_bytes()


def test_event_with_no_record_format_segment_is_converted_segment_by_segment(ws):
    ws.add_media('0000.media', b'good')
    ws.add_media('0013.media', b'')

    result = convert(ws)

    assert ws.converted('0000.media').read_bytes() == b'OKgood'
    assert not event_output(ws).exists()


@pytest.mark.parametrize('option', [[], ['--copy']])
def test_per_segment_files_get_the_same_video_time_base_as_event_files(ws, option):
    # Merging a mix of both on one day squashes the video unless the time bases match
    ws.add_media('0000.media', b'good')
    ws.env['FAKE_FFMPEG_LOG'] = str(ws.root / 'ffmpeg_calls.jsonl')

    assert convert(ws, *option).returncode == 0

    [args] = ws.ffmpeg_calls()
    assert args[args.index('-video_track_timescale') + 1] == '90000'


def test_event_with_no_frame_that_can_be_decoded_is_converted_with_a_warning(ws):
    # A lone frame marked as a keyframe, but holding only a slice that refers to
    # an earlier picture (#77)
    ws.add_media('0000.media', record(1, b'\x00\x00\x00\x01\x41lone', 1_000_000))

    result = convert(ws)

    assert result.returncode == 0, result.stdout
    assert any('WARNING' in line and 'may not play' in line for line in result.stdout.splitlines())
    assert b'lone' in event_output(ws).read_bytes()   # as it was, not dropped or split into segments


def test_frames_before_the_first_keyframe_are_dropped_with_a_warning(ws):
    ws.add_media('0000.media', record(1, b'\x00\x00\x00\x01\x41broken', 1_000_000) + segment(b'real', 28_000_000))

    result = convert(ws)

    assert result.returncode == 0, result.stdout
    assert any('WARNING' in line and '1 video frames at the start' in line and 'dropped' in line
               for line in result.stdout.splitlines())
    data = event_output(ws).read_bytes()
    assert b'real' in data and b'broken' not in data


def test_leftover_pictures_in_front_of_the_first_keyframe_are_removed(ws):
    key = (b'\x00\x00\x00\x01\x41leftover'
           b'\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65first')
    ws.add_media('0000.media', record(1, key, 1_000_000) + segment(b'later', 28_000_000))

    result = convert(ws)

    assert result.returncode == 0, result.stdout
    assert any('WARNING' in line and 'earlier pictures' in line for line in result.stdout.splitlines())
    data = event_output(ws).read_bytes()
    assert b'first' in data and b'later' in data and b'leftover' not in data
