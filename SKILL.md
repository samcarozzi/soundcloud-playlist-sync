---
name: soundcloud-playlist-sync
description: Download a SoundCloud playlist/set in the best available quality with scdl (using the user's SoundCloud auth token), save it to ~/Documents/music/<playlist name>, diff the folder against the playlist to find tracks that failed (DRM / Go+ / removed), and fill the gaps from YouTube with yt-dlp, flagging uncertain matches. Use this whenever the user shares a soundcloud.com/.../sets/... link, asks to download, rip, grab, sync or update a SoundCloud playlist, asks what's missing from a downloaded playlist folder, or wants missing SoundCloud tracks pulled from YouTube, even if they don't mention scdl or yt-dlp.
---

# SoundCloud playlist sync

The script `scripts/sc_sync.py` handles the whole pipeline deterministically. Your job is to run it,
deal with the auth-token decision point, and present the results (especially flagged matches) clearly.

## What the script does

1. Resolves the playlist via the SoundCloud API → playlist name + full track list (index, artist, title, duration).
2. Validates the auth token from `~/.config/scdl/scdl.cfg` (`[scdl] auth_token`), or `--auth-token` / `$SCDL_AUTH_TOKEN`.
3. Runs `scdl -c --no-playlist-folder` into `~/Documents/music/<playlist name>` with filenames
   `NN. uploader - title.ext`. A valid token gets original files (often WAV) and 256 kbps Go+ streams.
4. Matches local files to playlist entries by their `NN.` prefix → list of missing tracks.
5. For each missing track: searches YouTube (artist + title), scores candidates by how closely the duration
   matches SoundCloud's, penalises live/lyrics/instrumental/wrong-version uploads, prefers "- Topic" and artist channels,
   downloads the best as m4a, and rewrites tags (artist/title/album/track no.; comment notes it's a YouTube fallback).
6. Writes `.sync-report.md` and `.sync-report.json` in the folder; prints a short JSON summary to stdout.

## Running it

```bash
python3 ~/.claude/skills/soundcloud-playlist-sync/scripts/sc_sync.py "<playlist_url>" [options]
```

Useful options:
- `--out DIR`: use an existing folder instead of `~/Documents/music/<name>` (e.g. a folder the user downloaded earlier, possibly in a nested layout).
- `--skip-scdl`: only compare against the playlist and fill gaps from YouTube.
- `--no-youtube`: only scdl and compare.
- `--yt-dry-run`: pick YouTube matches without downloading. Good when the user wants to review first.
- `--min-confidence high|medium|low` (default medium): lowest confidence that is auto-downloaded.
- `--offset N`: start at playlist index N.
- `--allow-no-auth`: continue when the token is missing or expired (see below).

Downloads can take several minutes for large playlists, so give the Bash call a long timeout (up to 600000 ms)
or run it in the background. stderr shows progress; the final stdout JSON is the summary.

Before running, check whether the target folder already exists and roughly how many files it holds, so you
can tell the user whether this is a fresh download or an update. Re-running is safe, because `-c` skips files that already exist.

## Auth token (the decision point)

If the token is expired or invalid, the script exits with code 2 and prints `{"error": "auth_token_invalid", ...}`.
scdl itself refuses to run with a bad token, and without one you only get ~128 kbps streams and every Go+ track fails.
Tell the user, and give them the refresh steps:

1. Log into soundcloud.com in a browser.
2. Open DevTools → Application (Chrome) / Storage (Safari/Firefox) → Cookies → `https://soundcloud.com`.
3. Copy the value of the `oauth_token` cookie (looks like `2-123456-12345678-AbCdEf...`).
4. Either paste it to you so you can update `auth_token = ...` under `[scdl]` in `~/.config/scdl/scdl.cfg`, or have them run it with `--auth-token`.

Treat the token as a secret: don't echo it back in full and don't write it anywhere except scdl.cfg.
If the user would rather go ahead at low quality, rerun with `--allow-no-auth`.

## Presenting results

Read `.sync-report.md` (or the JSON) and give the user:
- The count: N of M tracks from SoundCloud, K filled from YouTube, and any still missing.
- A table of the YouTube-sourced tracks: number, artist, title, YouTube channel, and confidence.
- **Flagged items first**: anything rated medium or low, or "skipped: low-confidence match", "not found on YouTube" or "youtube download failed".
  For each one, say why (from `notes`) and list the `alternatives` from the JSON so the user can choose. To download a
  specific alternative manually, use the same yt-dlp flags as the script (`-f "bestaudio[ext=m4a]/bestaudio" -x
  --audio-format m4a --embed-metadata --embed-thumbnail -o "<folder>/NN. uploader - title.%(ext)s" <url>`).
- Any `mismatched` files (a file's name doesn't match the playlist entry at that index). This usually means the playlist
  was reordered after the last download, so numbering drifted. Point it out rather than silently renaming.
- A reminder that YouTube fallbacks are ~128 kbps AAC (fine for previewing, weak on a club system), and that buying
  them (Beatport/Bandcamp) is the way to get proper copies.

Also flag tracks where the uploader is a label (e.g. "Slow Cooked Records") and the real artist differs. The script
already uses SoundCloud's `publisher_metadata.artist` for tags and searches, but it's worth a sanity glance.

## Dependencies

`scdl`, `yt-dlp`, `ffmpeg`, and Python `mutagen`. If one is missing, install it with
`pip install scdl yt-dlp mutagen` / `brew install ffmpeg`.
