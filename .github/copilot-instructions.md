# Copilot Instructions for crittervid

This project processes segmented `.media` files from Green Feathers wildlife cameras, converting them to `.mp4` and merging them into longer video clips. Follow these guidelines to maximize productivity:

## Project Architecture
- **Input Directory**: All raw camera files are under `input/DCIM/YYYY/MM/DD/groupid/`.
- **.media Files**: Each subfolder contains many `.media` files (short video segments) and a `.info` file (metadata).
- **Output Structure**: Place converted `.mp4` files and merged clips in a clearly organized output directory (suggest: `output/` with similar date-based structure).

## Essential Workflows
- **Conversion**: Use FFmpeg or similar tools to convert `.media` files to `.mp4`. Automate batch conversion for all files in a folder.
- **Merging**: After conversion, merge `.mp4` segments into longer clips (e.g., per hour or per day) using FFmpeg's concat feature.
- **Automation**: Scripts should recursively process all folders, handling new files as they arrive.

## Patterns & Conventions
- **File Naming**: Preserve original timestamps and camera IDs in output filenames for traceability.
- **Error Handling**: Log failed conversions and merges; skip corrupted files but report them.
- **Extensibility**: Design scripts to easily add support for new camera models or file formats.
- **Metadata Usage**: Parse `.info` files for additional context (e.g., recording time, camera settings).

## Integration Points
- **FFmpeg**: Ensure FFmpeg is installed and accessible in the environment. Document install steps if not present.
- **Batch Processing**: Prefer Python scripts for automation, but shell scripts are acceptable for simple tasks.
- **Testing**: Validate output videos for playback and integrity. Optionally, generate logs or summary reports.

## Example Workflow
1. Scan `input/DCIM` for all `.media` files.
2. Convert each `.media` file to `.mp4` using FFmpeg:
   ```sh
   ffmpeg -i inputfile.media outputfile.mp4
   ```
3. Merge `.mp4` files for a time period:
   ```sh
   ffmpeg -f concat -safe 0 -i filelist.txt -c copy merged.mp4
   ```
4. Place results in `output/` with clear naming.

## Key Files & Directories
- `input/DCIM/` — Source files from cameras
- `output/` — Destination for processed videos
- Scripts (to be created): `convert_media.py`, `merge_videos.py`, etc.

---
For questions or improvements, update this file to help future AI agents and developers.
