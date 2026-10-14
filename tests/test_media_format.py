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

    video, audio = mf.split_streams(data)

    assert [(t, key) for t, _, key in video] == [(1_000_000, True), (1_050_000, False)]
    assert [t for t, _ in audio] == [1_038_000]


@pytest.mark.parametrize('data', [b'', b'not a media file', record(3, b'\x00' * 640, 0)])
def test_split_streams_rejects_non_record_data_or_no_video(data):
    with pytest.raises(mf.MediaFormatError):
        mf.split_streams(data)


# --- audio ---------------------------------------------------------------------

def chunk(value=0x11):
    return bytes([value]) * 640   # 40 ms


def test_audio_starting_after_the_video_gets_leading_silence():
    pcm = mf.audio_pcm([(1_038_000, chunk())], start_us=1_000_000)

    assert pcm[:38 * 16] == b'\x00' * (38 * 16)   # 38 ms at 16 bytes per ms
    assert pcm[38 * 16:] == chunk()


def test_audio_starting_before_the_video_is_trimmed():
    pcm = mf.audio_pcm([(1_000_000, chunk())], start_us=1_010_000)

    assert pcm == chunk()[10 * 16:]


def test_long_audio_gaps_are_filled_short_jitter_is_not():
    audio = [(0, chunk(1)), (40_000, chunk(2)), (81_000, chunk(3)),   # 1 ms late: jitter
             (191_000, chunk(4))]                                     # 70 ms gap

    pcm = mf.audio_pcm(audio, start_us=0)

    assert pcm == chunk(1) + chunk(2) + chunk(3) + b'\x00' * (70 * 16) + chunk(4)


# --- MPEG-TS -------------------------------------------------------------------

def test_crc_matches_mpeg2_reference_value():
    assert mf._crc32_mpeg2(b'123456789') == 0x0376E6E7


def decode_pts(b):
    return (((b[0] >> 1) & 0x07) << 30) | (b[1] << 22) | ((b[2] >> 1) << 15) | (b[3] << 7) | (b[4] >> 1)


@pytest.mark.parametrize('pts', [0, 90_000, 2**32 + 12345, 2**33 - 1])
def test_pts_encoding_round_trips(pts):
    assert decode_pts(mf._pts_bytes(pts)) == pts


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
