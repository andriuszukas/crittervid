"""media_format.py: reading records, aligning audio, and the MPEG-TS writer.
No FFmpeg needed; test_media_conversion.py checks the result with the real one."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import media_format as mf  # noqa: E402

HEADER = struct.Struct('<IIIIQ')
KEY = b'\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps\x00\x00\x00\x01\x65idr'
P = b'\x00\x00\x00\x01\x41p'


def record(rtype, payload, t_us):
    return HEADER.pack(rtype, len(payload), t_us // 1000, 0, t_us) + payload


# --- records -------------------------------------------------------------------

def test_split_streams_separates_video_and_audio():
    data = record(1, KEY, 1_000_000) + record(3, b'\x01\x00' * 320, 1_038_000) + record(0, P, 1_050_000)

    streams = mf.split_streams(data)

    assert [(t, key) for t, _, key in streams.video] == [(1_000_000, True), (1_050_000, False)]
    assert [t for t, _ in streams.audio] == [1_038_000]
    assert streams.trailing_bytes == 0


@pytest.mark.parametrize('cut', [1, 10, 23, 24, 30])
def test_split_streams_ignores_an_incomplete_last_record(cut):
    # A damaged segment: the last record is cut short (in its header or its payload)
    last = record(3, b'\x01\x00' * 320, 1_038_000)
    data = record(1, KEY, 1_000_000) + record(0, P, 1_050_000) + last[:cut]

    streams = mf.split_streams(data)

    assert len(streams.video) == 2 and streams.audio == []
    assert streams.trailing_bytes == cut


@pytest.mark.parametrize('data', [b'', b'not a media file', record(3, b'\x00' * 640, 0),
                                  record(1, KEY, 0)[:-3], record(1, KEY, 0)[:10]])
def test_split_streams_rejects_non_record_data_or_no_video(data):
    # Also when the only video record is incomplete: there is nothing complete to use
    with pytest.raises(mf.MediaFormatError):
        mf.split_streams(data)


# --- audio ---------------------------------------------------------------------

def chunk(value=0x11):
    return bytes([value]) * 640   # 40 ms


def silence(n_ms):
    return b'\x00' * (n_ms * 16)   # 16 bytes per ms


def test_audio_starting_after_the_video_gets_leading_silence():
    pcm = mf.audio_pcm([(1_038_000, chunk())], start_us=1_000_000, end_us=1_078_000)

    assert pcm == silence(38) + chunk()


def test_audio_starting_before_the_video_is_trimmed():
    pcm = mf.audio_pcm([(1_000_000, chunk())], start_us=1_010_000, end_us=1_040_000)

    assert pcm == chunk()[10 * 16:]


def test_long_audio_gaps_are_filled_short_jitter_is_not():
    audio = [(0, chunk(1)), (40_000, chunk(2)), (81_000, chunk(3)),   # 1 ms late: jitter
             (191_000, chunk(4))]                                     # 71 ms after the data ended

    pcm = mf.audio_pcm(audio, start_us=0, end_us=231_000)

    assert pcm == chunk(1) + chunk(2) + chunk(3) + silence(71) + chunk(4)


@pytest.mark.parametrize(('start', 'end'), [(0, 1_000_000), (5_000, 1_234_567), (0, 1_001), (0, 1_100), (7, 7)])
def test_audio_has_exactly_the_requested_length(start, end):
    audio = [(start + 40_000 * i, chunk()) for i in range(40)]   # more than needed for some

    assert len(mf.audio_pcm(audio, start, end)) == (end - start) * 16_000 // 1_000_000 // 2 * 2


def test_missing_audio_at_the_end_is_silence():
    assert mf.audio_pcm([(0, chunk())], 0, 100_000) == chunk() + silence(60)


def test_audio_outside_the_range_is_dropped():
    audio = [(0, chunk(1)), (40_000, chunk(2)), (80_000, chunk(3))]

    assert mf.audio_pcm(audio, start_us=40_000, end_us=80_000) == chunk(2)


def test_no_audio_is_silence_of_the_right_length():
    assert mf.audio_pcm([], 1_000_000, 1_500_000) == silence(500)


def test_timestamp_jitter_is_absorbed_without_dropping_data():
    # 16 ms late, then back on time: the data stays contiguous
    audio = [(0, chunk(1)), (56_000, chunk(2)), (80_000, chunk(3)), (120_000, chunk(4))]

    pcm = mf.audio_pcm(audio, 0, 160_000)

    assert pcm == chunk(1) + chunk(2) + chunk(3) + chunk(4)


def test_early_records_within_jitter_are_absorbed_too():
    audio = [(0, chunk(1)), (24_000, chunk(2)), (80_000, chunk(3))]   # 16 ms early

    assert mf.audio_pcm(audio, 0, 120_000) == chunk(1) + chunk(2) + chunk(3)


def test_zero_filled_bursts_20_ms_apart_do_not_lengthen_the_audio():
    burst = [(40_000 + 20_000 * i, bytes(640)) for i in range(20)]   # 20 records in 400 ms
    after = [(440_000, chunk(5)), (480_000, chunk(6))]
    audio = [(0, chunk(1))] + burst + after

    pcm = mf.audio_pcm(audio, 0, 520_000)

    assert len(pcm) == 520 * 16
    # The audio after the burst is where the camera's clock puts it (to within the jitter)
    assert pcm[-2 * 640:] == chunk(5) + chunk(6)


def test_overlap_keeps_the_existing_real_audio():
    # The second record starts 30 ms before the first one ends: only its last 10 ms are new
    audio = [(0, chunk(1)), (10_000, chunk(2))]

    pcm = mf.audio_pcm(audio, 0, 50_000)

    assert pcm == chunk(1) + chunk(2)[30 * 16:]


def test_real_audio_replaces_overlapping_silence():
    audio = [(0, bytes(640)), (10_000, chunk(2))]

    pcm = mf.audio_pcm(audio, 0, 50_000)

    assert pcm == silence(10) + chunk(2)


def test_zero_audio_does_not_replace_real_audio():
    audio = [(0, chunk(1)), (10_000, bytes(640))]

    assert mf.audio_pcm(audio, 0, 50_000) == chunk(1) + silence(10)


def test_out_of_order_and_duplicate_records_add_no_length():
    audio = [(0, chunk(1)), (40_000, chunk(2)), (80_000, chunk(3)),
             (40_000, chunk(9)),    # arrives late
             (0, chunk(1)), (80_000, chunk(3))]   # duplicates

    pcm = mf.audio_pcm(audio, 0, 120_000)

    assert pcm == chunk(1) + chunk(2) + chunk(3)


def test_odd_length_payloads_are_trimmed_to_whole_samples():
    audio = [(0, chunk(1) + b''), (40_000, chunk(2))]

    pcm = mf.audio_pcm(audio, 0, 80_000)

    assert pcm == chunk(1) + chunk(2)


def test_absurd_timestamps_are_dropped_not_allocated():
    # 1e15 us would be 16 GB of silence
    audio = [(0, chunk(1)), (10**15, chunk(2)), (40_000, chunk(3))]

    pcm = mf.audio_pcm(audio, 0, 80_000)

    assert pcm == chunk(1) + chunk(3)


# --- MPEG-TS -------------------------------------------------------------------

def test_crc_matches_mpeg2_reference_value():
    assert mf._crc32_mpeg2(b'123456789') == 0x0376E6E7


def decode_pts(b):
    return (((b[0] >> 1) & 0x07) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)


@pytest.mark.parametrize('pts', [0, 90_000, 2**32 + 12345, 2**33 - 1])
def test_pts_encoding_round_trips(pts):
    assert decode_pts(mf._pts_bytes(pts)) == pts


def test_ts_rejects_frames_that_do_not_move_forward():
    for second in (1_000_000, 900_000):   # same time as the first frame, and earlier
        with pytest.raises(mf.MediaFormatError):
            mf.video_ts([(1_000_000, KEY, True), (second, P, False)])


def test_ts_handles_footage_longer_than_the_33_bit_clock():
    # 30 h after the first frame the 90 kHz timestamp passes 2**33 and wraps
    later = 30 * 3600 * 1_000_000
    frames = [(1_000_000, KEY, True), (1_000_000 + later, P, False)]

    found = []
    for pkt in packets(mf.video_ts(frames)):
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == mf.VIDEO_PID and pkt[1] & 0x40:
            payload = pkt[5 + pkt[4]:] if pkt[3] & 0x20 else pkt[4:]
            found.append(decode_pts(payload[9:14]))

    assert found == [mf.PTS_START, (mf.PTS_START + later * 9 // 100) % 2**33]


def video_frames():
    frames, t = [], 5_000_000
    for i in range(30):
        frames.append((t, (KEY if i % 15 == 0 else P) + bytes(i * 37), i % 15 == 0))   # varied sizes
        t += (50_000, 100_000)[i % 2]
    return frames


def packets(ts):
    assert len(ts) % mf.TS_PACKET == 0
    return [ts[i:i + mf.TS_PACKET] for i in range(0, len(ts), mf.TS_PACKET)]


def test_ts_is_whole_packets_with_continuous_counters():
    last = {}
    for pkt in packets(mf.video_ts(video_frames())):
        assert pkt[0] == 0x47
        pid, cc = ((pkt[1] & 0x1F) << 8) | pkt[2], pkt[3] & 0x0F
        if pid in last:
            assert cc == (last[pid] + 1) % 16, f'continuity counter jumps on PID {pid:#x}'
        last[pid] = cc
    assert set(last) == {0, mf.PMT_PID, mf.VIDEO_PID}


def test_ts_frames_carry_their_timestamps():
    frames = video_frames()
    found = []
    for pkt in packets(mf.video_ts(frames)):
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != mf.VIDEO_PID or not pkt[1] & 0x40:   # first packet of each frame
            continue
        payload = pkt[5 + pkt[4]:] if pkt[3] & 0x20 else pkt[4:]
        assert payload[:4] == b'\x00\x00\x01\xE0'
        found.append(decode_pts(payload[9:14]))
    t0 = frames[0][0]
    assert found == [mf.PTS_START + (t - t0) * 9 // 100 for t, _, _ in frames]
