"""
Statistics of an HLS stream, read from its playlist and segment files.
"""

import os


def _is_segment_name(name):
    # A bare file name ending in .ts; anything with a path part is untrusted (VFX-30).
    return (
        name.endswith(".ts")
        and name not in (".ts", "")
        and name == os.path.basename(name)
        and "\\" not in name
        and ":" not in name
    )


def _segment_size(base_dir, name):
    """
    Size of a segment file inside base_dir, or None if it is not a regular file there.
    """
    path = os.path.realpath(os.path.join(base_dir, name))
    if not path.startswith(base_dir + os.sep) or not os.path.isfile(path):
        return None
    return os.path.getsize(path)


def read_stream_stats(playlist_path):
    """
    Compute segment length, count, duration, size and bitrate from a media playlist.

    Returns None if the playlist or one of its segments is missing or unreadable,
    or if the playlist has no duration.
    """
    try:
        base_dir = os.path.realpath(os.path.dirname(playlist_path))
        with open(playlist_path, "r", encoding="utf-8") as playlist:
            lines = [line.strip() for line in playlist]
        target = None
        segment_count = 0
        duration = 0.0
        size_bytes = 0
        pending = None
        for line in lines:
            if line.startswith("#EXT-X-TARGETDURATION:"):
                target = int(float(line.split(":", 1)[1]))
            elif line.startswith("#EXTINF:"):
                pending = float(line.split(":", 1)[1].split(",", 1)[0])
            elif line and not line.startswith("#"):
                # Segments with a path part count neither for size nor for duration.
                if pending is not None and _is_segment_name(line):
                    size = _segment_size(base_dir, line)
                    if size is None:
                        return None
                    segment_count += 1
                    duration += pending
                    size_bytes += size
                pending = None
    except (OSError, ValueError):
        return None
    if target is None or duration <= 0:
        return None
    return {
        "bitrate_kbps": round(size_bytes * 8 / duration / 1000),
        "segment_count": segment_count,
        "segment_seconds": target,
        "duration_seconds": round(duration, 6),
        "size_bytes": size_bytes,
    }
