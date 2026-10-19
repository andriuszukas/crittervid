# 0001: The .media format, and how to convert it

- **Status:** accepted
- **Issues:** investigation #22 and #23; follow-up #71, #72, #73, #74

## Context

CritterVid converted `.media` files by handing them to FFmpeg as if they were plain video. Nobody had checked what the files contain. Phase 1 of the roadmap examined real SD cards to find out, using copies; the originals were never modified. This record describes what was found and what it means for the design. Format facts only: nothing here identifies a camera, a site or a recording date.

`tools/inspect_media.py` reproduces the format analysis on any `.media` file or event folder.

## Findings: the `.media` card

The card holds about 2,200 events and 95,000 `.media` files (88.5 GB).

### Layout
`DCIM/YYYY/MM/DD/<event start>_<duration>/<offset>.media`, plus one `.info` per event. This matches Tuya's documented IPC storage layout.

- `<event start>` is a Unix time.
- `<duration>` is the event's length in seconds, so it is **not a group ID** (#30). It agrees with the segment offsets.
- `<offset>` is the segment's start in seconds from the event start. Segments are about 13.5 s long, and events are mostly 10 minutes long.
- The date folders are in the **camera's local time**, which isn't necessarily UTC. Events near midnight therefore sit in a different date folder than their UTC date would suggest.

### Record format
Each `.media` file is a sequence of records. Every byte of every sampled file is accounted for (149 of 149 sampled segments).

| Bytes | Field |
|---|---|
| 0–3 | record type: `0` video frame, `1` video keyframe (begins with SPS/PPS), `3` audio |
| 4–7 | payload length |
| 8–11 | a millisecond value (meaning not confirmed) |
| 12–15 | always 0 |
| 16–23 | **timestamp, microseconds** |

- **Video:** H.264 High profile, 2304×1296, in Annex B form. It runs at **about 14.7 fps** on average, with frames 50 or 100 ms apart (variable), and a keyframe every ~4.5 s.
- **Audio:** **PCM signed 16-bit, 8 kHz, mono**, in 640-byte records every 40 ms.

### `.info`
Every `.info` file on the card is identical: `{"version": 1, "eventType": 0, "codec": 2}`. They carry no event-specific information.

### Clock problems
A few events were recorded before the camera's clock was set:
- They're in `1970/…` folders, or in `2106/…` folders where the local date wrapped around below 1970.
- They have no `.info` file.

Their footage is valid, but their times are not (#73).

## Findings: a second, MP4-based camera

A second card, from a different camera or firmware, has no `.media` files at all. It records MP4 directly:

- **Layout:** `record/YYYYMMDD/HH/<session start>/<segment start>.mp4`. Date and hour folders are in local time; the names are Unix times.
- **Segments:** 3 s long, HEVC 1920×1080 at a constant 20 fps, with G.711 A-law 8 kHz audio. About 6% of segments are followed by a 1–2 s gap.
- **`.record_msg` files:** binary records of (start time, duration in seconds, 256) that look like a motion-event index. Not yet verified.

Details in #72.

## Verification

- **Today's conversion is wrong.** One 13.4 s segment, converted with the command `convert_media.py` uses, becomes an **18.0 s** MP4 at 11 fps **with no audio**. FFmpeg reads `.media` as raw H.264 and doesn't see the record timestamps or the audio.
- **Reading the records works.** A proof of concept rebuilt one whole event (45 segments, folder duration `0602`) from its records:
  - video copied without re-encoding, at the average frame rate from the timestamps
  - audio encoded as AAC

  The result is **601.97 s** of video and **601.92 s** of audio, in sync with each other and with the folder's duration.

## Decisions

1. **Convert `.media` by parsing its records** (#71). Write the H.264 video into MP4 **without re-encoding**, using each frame's own timestamp (variable frame rate), and encode the audio as AAC. This replaces running FFmpeg directly on the file. It fixes both the timing and the missing audio, and makes hardware encoding unnecessary for these files.
2. **Check every converted event against its folder's duration suffix.** A mismatch means the conversion is wrong, so report it rather than producing silently mistimed footage.
3. **Read each camera format with its own reader.** The `.media` cards and the MP4 `record/` cards differ in layout, codecs and timing. Each gets a reader that produces the same internal description of events and segments (#24, #25, #72).
4. **The camera's time zone is an input, not an assumption.** Folder dates follow the camera's local time and event names follow UTC, so the deployment's time zone must be entered by the user (#36). Events from before the clock was set are detected and kept apart (#73).
5. **Test with the real record format** (#74). Generated `.media` test fixtures should use records with variable timestamps and audio, so the tests catch the problems found here.

## Implementation (#71)

Measured on real footage while building the converter:

- **Per-frame timestamps, not an average frame rate.**
  - A constant frame rate per segment would put frames within about 40 ms of their true time in typical segments, but up to 243 ms off in rare ones.
  - FFmpeg can't attach timestamps to raw H.264, so `media_format.py` writes the frames into a minimal MPEG-TS stream, where each frame carries its own timestamp. FFmpeg then copies them into MP4.
  - On a real segment, all 198 frames came out within 0.011 ms of the camera's timestamps.
- **One MP4 per event, not per segment.**
  - FFmpeg gives the last frame of every file an estimated duration, so joining a 10-minute event from per-segment files added about 50 ms per join: 604.1 s instead of 602.
  - Converting all of an event's segments into one file avoids this. The real 10-minute event comes out at 601.97 s of video and 602.04 s of audio, against a camera timeline of 601.88 s plus the last frame.
- **Audio aligned in the data.**
  - The camera's audio starts a little after its video: typically 39 ms, up to 329 ms.
  - Container offsets (`-itsoffset`) were lost when muxing, so the PCM is padded or trimmed to start exactly at the first video frame.
  - Dropouts longer than 60 ms (seen occasionally) are filled with silence, so later audio stays in sync.
- **Merging.**
  - Each file's AAC track has encoder padding before time zero, which the concat demuxer turned into a 128 ms video delay at every join.
  - Each file is now joined from its own time zero (`inpoint 0`), and the audio is re-encoded once while the video is copied.
  - A real day of 9 events merges to 130.81 s, against 130.79 s for the events alone.
- **Events named `<start>_0000`.** About 3% of real events have a duration of 0 in their name but contain footage (7–22 s in the examples checked). The camera evidently didn't record the length, for example because the event was cut short. The duration check treats 0 as unknown.
- **Files not in the record format** are still converted by FFmpeg directly, one segment at a time, but only when no segment of the event is in the record format.

### After review: what real cards add

Reviewing the first version against the whole card found three problems the generated test data didn't have:

- **Bursts of silent audio records.**
  - About 23% of events contain runs of zero-filled audio records 20 ms apart instead of 40 ms. The camera writes silence at twice the data rate where it lost sound.
  - Appending records in file order made the audio run long: up to 26.5 s late by the end of a 10-minute event.
  - Audio is now **placed by its timestamps**. Records within 20 ms of where the previous audio ended are appended as is, which absorbs the cameras' timestamp jitter of up to ~16 ms while their samples run on. Later records leave silence before them. Overlapping records only add the part beyond what's already placed, though real sound replaces silence. The audio is cut or padded to end with the video.
  - The worst event now has 602.78 s of audio for 602.82 s of video.
- **Pauses longer than 10 s.**
  - About 11 events have recording pauses of 11–31 s.
  - FFmpeg treats MPEG-TS timestamp jumps over 10 s as errors and closes them up. That shortened the video but not the audio.
  - The threshold is raised (`-dts_delta_threshold`), so pauses are kept.
  - One event starts with a single damaged keyframe 31 s before the rest. FFmpeg places that one frame 0.27 s late; every later frame is exact.
- **Damaged segments.**
  - A few events end in a segment cut off by power loss, or an empty file. Some have tails of garbage records with timestamp 0, or segments that start by repeating earlier frames.
  - Empty segments are skipped, incomplete final records ignored, and frames that don't move forward in time dropped, all with a warning.
  - An event falls back to per-segment FFmpeg conversion only if none of its segments is in the record format.

Every event file now has an audio track, silent if none was recorded, so a merged day keeps its sound even when its first event has none. Per-segment fallback files use the same 90 kHz video time base as event files, so the two can be merged together.

## Consequences

- Existing outputs made with earlier versions play at the wrong speed and have no sound. They should be regenerated once #71 lands.
- Hardware-encoder selection (#2, #10) matters much less for `.media` cards, because their video is copied rather than re-encoded.
- Behaviour analysis gets correct durations, which it depends on.

## Open questions

- What does the millisecond field (bytes 8–11) hold? It's close to, but not equal to, the microsecond timestamp.
- Are there other record types or `eventType` values on other cameras or firmware versions?
- Do the `.record_msg` files really index motion events, and why do MP4 session folders start minutes before their first segment?
