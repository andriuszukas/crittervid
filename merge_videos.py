import os

from pathlib import Path
import subprocess
from datetime import datetime, timedelta
import re
import argparse
from ffmpeg_helper import get_ffmpeg_path

OUTPUT_ROOT = Path('output')
MERGED_ROOT = Path('output/merged')
FFMPEG_PATH = get_ffmpeg_path()  # Auto-detect ffmpeg location
SEGMENT_DURATION_HOURS = 12
SUMMARY_REPORT = MERGED_ROOT / 'merge_summary.txt'

# Regex to extract timestamp and group ID from filename
FILENAME_RE = re.compile(r'(\d{10,})_(\d{4})_\d+\.mp4$')

def find_mp4_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.mp4'):
                yield Path(dirpath) / filename

# Group files by day
def group_files_by_day(files):
    groups = {}
    for f in files:
        m = FILENAME_RE.search(str(f))
        if not m:
            continue
        timestamp = m.group(1)
        dt = datetime.fromtimestamp(int(timestamp))
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        key = day_start
        groups.setdefault(key, []).append((dt, f))
    # Sort files in each group by time
    for k in groups:
        groups[k].sort()
    return groups

def merge_group(period_start, files, summary, split_idx=None, total_splits=None):
    out_dir = MERGED_ROOT / period_start.strftime('%Y/%m/%d')
    out_dir.mkdir(parents=True, exist_ok=True)
    start_str = period_start.strftime('%Y%m%d_%H%M')
    end_dt = period_start + timedelta(hours=24 // (total_splits or 1))
    end_str = end_dt.strftime('%Y%m%d_%H%M')
    if split_idx is not None and total_splits is not None:
        out_name = f"merged_{start_str}_{end_str}_part{split_idx+1}of{total_splits}.mp4"
    else:
        out_name = f"merged_{start_str}_{end_str}.mp4"
    out_path = out_dir / out_name
    # Create filelist.txt for ffmpeg concat
    filelist_path = out_dir / f"filelist_{start_str}.txt"
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
    parser = argparse.ArgumentParser(description="Merge .mp4 segments into longer clips per group and time period.")
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory containing .mp4 files to merge (default: output)')
    parser.add_argument('--splits-per-day', type=int, default=1, help='Number of merged .mp4 files to create per day per group (default: 1, i.e. all segments merged into one file per day).')
    args = parser.parse_args()

    # Update global paths based on arguments
    global OUTPUT_ROOT, MERGED_ROOT, SUMMARY_REPORT
    OUTPUT_ROOT = Path(args.output_dir)
    MERGED_ROOT = OUTPUT_ROOT / 'merged'
    SUMMARY_REPORT = MERGED_ROOT / 'merge_summary.txt'

    mp4_files = list(find_mp4_files(OUTPUT_ROOT))
    groups = group_files_by_day(mp4_files)
    summary = []
    for day_start, files in groups.items():
        n = max(1, args.splits_per_day)
        total = len(files)
        split_size = (total + n - 1) // n  # ceil division
        for i in range(n):
            split_files = files[i*split_size:(i+1)*split_size]
            if not split_files:
                continue
            split_period_start = day_start + timedelta(hours=(i * 24 // n))
            merge_group(split_period_start, split_files, summary, split_idx=i, total_splits=n)
    # Write summary report
    MERGED_ROOT.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_REPORT, 'w') as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {SUMMARY_REPORT}")

if __name__ == '__main__':
    main()
