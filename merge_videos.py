import os

from pathlib import Path
import subprocess
from datetime import datetime, timedelta
import re

OUTPUT_ROOT = Path('output')
MERGED_ROOT = Path('output/merged')
FFMPEG_PATH = 'ffmpeg'  # Assumes ffmpeg is in PATH
SEGMENT_DURATION_HOURS = 12
SUMMARY_REPORT = MERGED_ROOT / 'merge_summary.txt'

# Regex to extract timestamp and camera ID from filename
FILENAME_RE = re.compile(r'(\d{10,})_(\d{4})_\d+\.mp4$')

def find_mp4_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.mp4'):
                yield Path(dirpath) / filename

# Group files by camera and 12-hour period
def group_files(files):
    groups = {}
    for f in files:
        m = FILENAME_RE.search(str(f))
        if not m:
            continue
        timestamp, camera_id = m.group(1), m.group(2)
        dt = datetime.fromtimestamp(int(timestamp))
        # Calculate 12-hour period start
        period_start = dt.replace(hour=(dt.hour // SEGMENT_DURATION_HOURS) * SEGMENT_DURATION_HOURS, minute=0, second=0, microsecond=0)
        key = (camera_id, period_start)
        groups.setdefault(key, []).append((dt, f))
    # Sort files in each group by time
    for k in groups:
        groups[k].sort()
    return groups

def merge_group(camera_id, period_start, files, summary):
    out_dir = MERGED_ROOT / period_start.strftime('%Y/%m/%d')
    out_dir.mkdir(parents=True, exist_ok=True)
    start_str = period_start.strftime('%Y%m%d_%H%M')
    end_dt = period_start + timedelta(hours=SEGMENT_DURATION_HOURS)
    end_str = end_dt.strftime('%Y%m%d_%H%M')
    out_name = f"{camera_id}_{start_str}_{end_str}.mp4"
    out_path = out_dir / out_name
    # Create filelist.txt for ffmpeg concat
    filelist_path = out_dir / f"filelist_{camera_id}_{start_str}.txt"
    with open(filelist_path, 'w') as f:
        for _, file in files:
            f.write(f"file '{file.resolve()}'\n")
    cmd = [FFMPEG_PATH, '-y', '-f', 'concat', '-safe', '0', '-i', str(filelist_path), '-c', 'copy', str(out_path)]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        error_msg = f"Failed to merge {out_name}: {result.stderr.decode()}"
        print(error_msg)
        summary.append(f"ERROR: {error_msg}")
    else:
        success_msg = f"Merged {len(files)} files -> {out_path}"
        print(success_msg)
        summary.append(f"SUCCESS: {success_msg}")
    filelist_path.unlink()

def main():
    mp4_files = list(find_mp4_files(OUTPUT_ROOT))
    groups = group_files(mp4_files)
    summary = []
    for (camera_id, period_start), files in groups.items():
        merge_group(camera_id, period_start, files, summary)
    # Write summary report
    MERGED_ROOT.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_REPORT, 'w') as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {SUMMARY_REPORT}")

if __name__ == '__main__':
    main()
