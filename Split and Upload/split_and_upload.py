"""
Robot Match Video Splitter & Uploader

Splits a robot match video into highlight segments based on start/end/score times,
concatenates them, and optionally uploads to YouTube with metadata.

Usage:
  python split_and_upload.py --input ... --config ... --match_start_time ... --match_end_time ... --score_post_time ... --event_name ... --match_name ...

Config YAML keys for segment padding:
  before_match_seconds: Seconds before match start (default: 3)
  after_match_seconds: Seconds after match end (default: 3)
  before_score_post_seconds: Seconds before score post (default: 3)
  after_score_post_seconds: Seconds after score post (default: 3)
  before_seconds: Generic seconds before segment (overrides before_match_seconds if set)
  after_seconds: Generic seconds after segment (overrides after_match_seconds if set)

If both specific and generic keys are set, specific keys take precedence for their segment.

Example config:
---
before_match_seconds: 2
after_match_seconds: 4
before_score_post_seconds: 1.5
after_score_post_seconds: 2.5
before_seconds: 3
after_seconds: 3
youtube:
  upload: true
  client_secrets_path: client_secret.json
  credentials_file: youtube_credentials.json
  privacyStatus: public

"""
import argparse
import os
import sys
import tempfile
from pathlib import Path
import yaml
import ffmpeg
import csv
import math
from datetime import datetime

os.chdir("C:\\Users\\4201 STREAM PC\\Documents\\Split and Upload")

# --- YouTube Upload Logic ---
def authenticate_youtube(yt_config: dict, reauthorize=False):
    """Refresh or obtain credentials and verify the destination before any upload."""
    try:
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except Exception as exc:
        raise RuntimeError("Missing google libs. Install dependencies from requirements.txt")

    # --- YouTube API scopes ---
    # youtube.upload: upload videos
    # youtube: manage playlists and more
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube"
    ]
    client_secrets_path = yt_config.get("client_secrets_path")
    credentials_file = yt_config.get("credentials_file", "youtube_credentials.json")
    expected_channel = yt_config.get("expected_channel_id")
    if not expected_channel:
        raise RuntimeError("Set youtube.expected_channel_id in config before authenticating or uploading")

    creds = None
    if client_secrets_path is None:
        raise RuntimeError("YouTube upload requested but 'client_secrets_path' not specified in config")

    # Load or create OAuth credentials
    if not reauthorize and os.path.exists(credentials_file):
        creds = Credentials.from_authorized_user_file(credentials_file, scopes)
    credentials_changed = False
    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
            credentials_changed = True
        except RefreshError as exc:
            if getattr(exc, "retryable", False):
                raise RuntimeError("Google temporarily rejected token refresh; retry later") from exc
            print("Saved authorization could not be refreshed. Opening Google authorization.")
            creds = None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes)
        creds = flow.run_local_server(port=0, prompt="select_account consent", access_type="offline")
        credentials_changed = True

    youtube = build("youtube", "v3", credentials=creds)
    channels = youtube.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    for channel in channels:
        print(f"Authorized YouTube channel: {channel['snippet']['title']} ({channel['id']})")
    if len(channels) != 1 or channels[0]["id"] != expected_channel:
        raise RuntimeError(
            f"YouTube channel verification failed. Expected {expected_channel}; "
            "no upload was started. Run --auth_only --reauthorize and select the intended channel. "
            "Existing saved credentials were not replaced."
        )
    if credentials_changed:
        with open(credentials_file, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
    return youtube


def upload_to_youtube(video_file: str, yt_config: dict, event_name: str = None):
    """Upload only after authentication and destination channel verification."""
    from googleapiclient.http import MediaFileUpload

    youtube = authenticate_youtube(yt_config)

    # Prepare video metadata
    body = {
        "snippet": {
            "title": yt_config.get("title", Path(video_file).stem),
            "description": yt_config.get("description", ""),
            "tags": yt_config.get("tags", []),
        },
        "status": {
            "privacyStatus": yt_config.get("privacyStatus", "unlisted"),
            "madeForKids": yt_config.get("madeForKids", False),
        },
    }

    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)

    # Upload with progress
    response = None
    print(f"YouTube upload title: {body['snippet']['title']}", flush=True)
    print("Starting upload...")
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    video_id = response.get("id")
    print("Upload finished. Video id:", video_id)
    print(f"YouTube link: https://youtu.be/{video_id}")

    # --- Playlist logic ---
    if event_name:
        playlist_id = None
        # Search for existing playlist with event name
        playlists = youtube.playlists().list(mine=True, part="id,snippet", maxResults=50).execute()
        for pl in playlists.get("items", []):
            if pl["snippet"]["title"] == event_name:
                playlist_id = pl["id"]
                break
        # If not found, create playlist
        if not playlist_id:
            print(f"Creating new playlist: {event_name}")
            pl_body = {
                "snippet": {
                    "title": event_name,
                    "description": f"Playlist for {event_name}",
                },
                "status": {
                    "privacyStatus": yt_config.get("privacyStatus", "unlisted"),
                },
            }
            pl_resp = youtube.playlists().insert(part="snippet,status", body=pl_body).execute()
            playlist_id = pl_resp["id"]
        # Add video to playlist
        print(f"Adding video to playlist: {event_name}")
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            }
        ).execute()
        print(f"Video added to playlist: {event_name}")
    return response

# --- FFmpeg Helpers ---
def extract_segment(input_path, start, duration, out_path):
    """
    Extracts a segment from a video using ffmpeg-python.
    Prints progress and handles errors.
    """
    print(f"Extracting segment: {out_path} ({duration:.2f}s)")
    try:
        (
            ffmpeg
            .input(input_path, ss=start, t=duration)
            .output(out_path, vcodec='libx264', acodec='aac', y=None, threads=12)
            .run(overwrite_output=True, quiet=False)
        )
    except ffmpeg.Error as e:
        print('FFmpeg error:')
        if hasattr(e, 'stderr') and e.stderr:
            print(e.stderr.decode(errors='ignore') if isinstance(e.stderr, bytes) else e.stderr)
        raise


def concat_segments(segment_paths, out_path):
    """
    Concatenates video segments using ffmpeg-python.
    """
    print(f"Concatenating segments into: {out_path}")
    list_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt")
    try:
        for p in segment_paths:
            list_file.write(f"file '{p}'\n")
        list_file.flush()
        try:
            (
                ffmpeg
                .input(list_file.name, format='concat', safe=0)
                .output(out_path, c='copy', y=None, threads=12)
                .run(overwrite_output=True, quiet=False)
            )
        except ffmpeg.Error as e:
            print('FFmpeg error:')
            if hasattr(e, 'stderr') and e.stderr:
                print(e.stderr.decode(errors='ignore') if isinstance(e.stderr, bytes) else e.stderr)
            raise
    finally:
        list_file.close()
        os.unlink(list_file.name)


def get_video_duration(input_path):
    """
    Returns the duration of a video file in seconds, or None if unavailable.
    """
    try:
        probe = ffmpeg.probe(input_path)
        return float(probe['format']['duration'])
    except Exception:
        return None


def parse_timecode(val):
    """
    Parses seconds, MM:SS, or HH:MM:SS, optionally wrapped in square brackets.
    """
    def invalid_timecode():
        return argparse.ArgumentTypeError(
            f"Invalid timecode: {val}. Expected non-negative seconds, MM:SS, "
            "or HH:MM:SS (optionally enclosed in square brackets)."
        )

    if isinstance(val, (int, float)):
        seconds = float(val)
        if not math.isfinite(seconds) or seconds < 0:
            raise invalid_timecode()
        return seconds
    if isinstance(val, str):
        timecode = val.strip()
        if timecode.startswith('[') and timecode.endswith(']'):
            timecode = timecode[1:-1].strip()
        parts = timecode.split(':')
        if not 1 <= len(parts) <= 3:
            raise invalid_timecode()
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            raise invalid_timecode() from None
        if any(not math.isfinite(p) or p < 0 for p in parts):
            raise invalid_timecode()
        # Only the final component may be fractional; minutes and seconds
        # following a colon must be below 60.
        if any(not p.is_integer() for p in parts[:-1]) or any(p >= 60 for p in parts[1:]):
            raise invalid_timecode()
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        if not math.isfinite(seconds):
            raise invalid_timecode()
        return seconds
    raise argparse.ArgumentTypeError(f"Invalid time value: {val}")

def log_run_to_csv(
    csv_path,
    input_path,
    config_path,
    output_path,
    event_name,
    match_name,
    match_start,
    match_end,
    score_post,
    before_match,
    after_match,
    before_score,
    after_score,
    youtube_url=None,
    youtube_title=None,
    youtube_id=None
):
    """
    Logs the run details to a CSV file. Creates the file with headers if it doesn't exist.
    """
    fieldnames = [
        "timestamp", "input_path", "config_path", "output_path", "event_name", "match_name",
        "match_start", "match_end", "score_post",
        "before_match", "after_match", "before_score", "after_score",
        "youtube_url", "youtube_title", "youtube_id"
    ]
    write_header = not os.path.exists(csv_path)
    with open(csv_path, "a", newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "input_path": input_path,
            "config_path": config_path,
            "output_path": output_path,
            "event_name": event_name,
            "match_name": match_name,
            "match_start": match_start,
            "match_end": match_end,
            "score_post": score_post,
            "before_match": before_match,
            "after_match": after_match,
            "before_score": before_score,
            "after_score": after_score,
            "youtube_url": youtube_url,
            "youtube_title": youtube_title,
            "youtube_id": youtube_id
        })

# --- Main Workflow ---
def main():
    """
    Main entry point: parses arguments, loads config, extracts segments, concatenates, uploads.
    """
    p = argparse.ArgumentParser(description="Split robot match video and optionally upload to YouTube.")
    p.add_argument("--auth_only", action="store_true", help="Authenticate and verify the channel without splitting or uploading")
    p.add_argument("--reauthorize", action="store_true", help="With --auth_only, select an account again instead of using saved credentials")
    p.add_argument("--input", help="Path to input video file")
    p.add_argument("--config", required=True, help="Path to YAML config file")
    p.add_argument("--output_path", required=False, help="Path to output video (mp4). If omitted, auto-generates from event/match name in current working directory.")
    p.add_argument("--match_start_time", type=parse_timecode, help="Match start time (seconds or HH:MM:SS)")
    p.add_argument("--match_end_time", type=parse_timecode, help="Match end time (seconds or HH:MM:SS)")
    p.add_argument("--score_post_time", type=parse_timecode, required=False, help="Score post time (seconds or HH:MM:SS). If omitted, only the match segment will be included.")
    p.add_argument("--event_name", help="Event name for output and YouTube title")
    p.add_argument("--match_name", help="Match name for output and YouTube title")
    p.add_argument("--threads", required=False, help="Number of threads to for FFMPEG to use.")
    args = p.parse_args()
    if args.reauthorize and not args.auth_only:
        p.error("--reauthorize requires --auth_only")
    if not args.auth_only:
        missing = [f"--{name}" for name in ("input", "match_start_time", "match_end_time", "event_name", "match_name") if getattr(args, name) is None]
        if missing:
            p.error("the following arguments are required: " + ", ".join(missing))
        # Snapshot the match title before config loading or slow video processing.
        event_name = args.event_name
        match_name = args.match_name
        title = f"{event_name} - {match_name}"
        out_path = args.output_path or os.path.join(os.getcwd(), f"{title}.mp4")
        print(f"Cached match title: {title}", flush=True)

    # Load config
    with open(args.config, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if args.auth_only:
        authenticate_youtube(cfg.get("youtube", {}), reauthorize=args.reauthorize)
        print("YouTube authentication and channel verification succeeded. No video was uploaded.")
        return

    # Padding settings for segments
    # These allow fine-tuning of how much video is included before/after each segment.
    # If not specified in config, defaults are used.
    before_match = float(cfg.get("before_match_seconds", 3))   # Seconds before match start
    after_match = float(cfg.get("after_match_seconds", 3))     # Seconds after match end
    before_score = float(cfg.get("before_score_post_seconds", 3))  # Seconds before score post
    after_score = float(cfg.get("after_score_post_seconds", 3))    # Seconds after score post

    match_start = args.match_start_time
    match_end = args.match_end_time
    score_time = getattr(args, "score_post_time", None)

    input_path = args.input
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    duration_total = get_video_duration(input_path)
    # If duration is None, just use the user-provided times and padding

    # Compute segment times using specific paddings for each segment
    match_segment_start = max(0.0, match_start - before_match)
    match_segment_end = match_end + after_match
    match_segment_duration = max(0.0, match_segment_end - match_segment_start)

    score_post_segment_start = None
    score_post_segment_end = None
    score_post_segment_duration = None
    include_score_segment = score_time is not None
    if include_score_segment:
        score_post_segment_start = max(0.0, score_time - before_score)
        score_post_segment_end = score_time + after_score
        score_post_segment_duration = max(0.0, score_post_segment_end - score_post_segment_start)

    if match_segment_duration <= 0 or (include_score_segment and score_post_segment_duration <= 0):
        raise RuntimeError("Computed zero-length segment(s). Check times and padding in config/args")

    # Create temp files for segments
    tmpdir = tempfile.mkdtemp(prefix="video_split_")
    match_segment_path = os.path.join(tmpdir, "match_segment.mp4")
    score_post_segment_path = os.path.join(tmpdir, "score_post_segment.mp4") if include_score_segment else None

    csv_log_path = os.path.join(os.getcwd(), "split_and_upload_log.csv")
    youtube_url = None
    youtube_title = None
    youtube_id = None

    try:
        # Extract segments
        extract_segment(input_path, match_segment_start, match_segment_duration, match_segment_path)
        segment_paths = [match_segment_path]
        if include_score_segment:
            extract_segment(input_path, score_post_segment_start, score_post_segment_duration, score_post_segment_path)
            segment_paths.append(score_post_segment_path)
        # Concatenate segments
        concat_segments(segment_paths, out_path)
        print("Final video written to:", out_path)
        # Optionally upload to YouTube
        yt_cfg = cfg.get("youtube")
        if yt_cfg and yt_cfg.get("upload", False):
            yt_cfg["title"] = title
            yt_response = upload_to_youtube(out_path, yt_cfg, event_name)
            youtube_id = yt_response.get("id")
            youtube_url = f"https://youtu.be/{youtube_id}" if youtube_id else None
            youtube_title = yt_cfg["title"]
    except Exception as exc:
        # Log run to CSV even if there is an error
        log_run_to_csv(
            csv_log_path,
            input_path,
            args.config,
            out_path if 'out_path' in locals() else None,
            event_name,
            match_name,
            match_start,
            match_end,
            score_time,
            before_match,
            after_match,
            before_score,
            after_score,
            youtube_url,
            youtube_title,
            youtube_id
        )
        print(f"Error occurred: {exc}")
        raise
    else:
        # Log run to CSV if no error
        log_run_to_csv(
            csv_log_path,
            input_path,
            args.config,
            out_path,
            event_name,
            match_name,
            match_start,
            match_end,
            score_time,
            before_match,
            after_match,
            before_score,
            after_score,
            youtube_url,
            youtube_title,
            youtube_id
        )
    finally:
        # Clean up temp files
        try:
            if os.path.exists(match_segment_path):
                os.remove(match_segment_path)
            if include_score_segment and score_post_segment_path and os.path.exists(score_post_segment_path):
                os.remove(score_post_segment_path)
        except Exception:
            pass

if __name__ == "__main__":
    main()
