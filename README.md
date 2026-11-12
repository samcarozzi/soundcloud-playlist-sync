# soundcloud-playlist-sync

A [Claude Code](https://claude.com/claude-code) skill (and standalone script) that downloads a SoundCloud playlist in the best quality your account can get, checks what's missing, and fills the gaps from YouTube, flagging any matches it isn't sure about.

Built for DJs who keep crates as SoundCloud playlists: some tracks are DRM/Go+-only and can't be downloaded, so you end up with a folder that's quietly missing songs.

## What it does

1. **Resolves the playlist** via the SoundCloud API (name + full track list with durations).
2. **Downloads with [scdl](https://github.com/scdl-org/scdl)** into `~/Documents/music/<playlist name>/` as `NN. uploader - title.ext`. With a valid auth token you get original uploads (often WAV) and high-quality streams.
3. **Diffs the folder against the playlist** by track number and reports what's missing, plus any files whose names don't match their index (e.g. the playlist was reordered).
4. **Fills gaps from YouTube with [yt-dlp](https://github.com/yt-dlp/yt-dlp)**:
   - searches by artist + title (it uses SoundCloud's publisher artist when the uploader is a label)
   - scores candidates by how closely their duration matches SoundCloud's
   - penalises live sets, lyric videos, instrumentals and wrong versions, and prefers official "- Topic" and artist channels
   - rates each pick high, medium or low confidence, and only auto-downloads medium or better
   - tags files with the SoundCloud artist, title, album and track number, plus a comment marking the YouTube source
5. **Writes a report** (`.sync-report.md` / `.sync-report.json`) in the folder, with alternatives for each flagged match.

## Install

Requirements: Python 3.9+, `ffmpeg`, and:

```bash
pip install scdl yt-dlp mutagen
```

As a Claude Code skill:

```bash
git clone https://github.com/samcarozzi/soundcloud-playlist-sync ~/.claude/skills/soundcloud-playlist-sync
```

Then just ask Claude something like *"download https://soundcloud.com/someone/sets/my-crate"* or *"what's missing from my techno playlist folder?"*

## SoundCloud auth token

High quality needs your own SoundCloud OAuth token (it is **never** stored in this repo):

1. Log into soundcloud.com in your browser.
2. DevTools → Application/Storage → Cookies → `https://soundcloud.com` → copy `oauth_token`.
3. Put it in `~/.config/scdl/scdl.cfg`:
   ```ini
   [scdl]
   auth_token = <your token>
   ```
   or pass `--auth-token`, or set `SCDL_AUTH_TOKEN`.

Tokens expire. The script checks yours first and stops with a clear message if it's invalid. Pass `--allow-no-auth` to continue anyway with low-quality public streams.

## Standalone usage

```bash
python3 scripts/sc_sync.py "https://soundcloud.com/<user>/sets/<playlist>" [options]
```

| Option | Meaning |
|---|---|
| `--root DIR` | parent folder (default `~/Documents/music`) |
| `--out DIR` | exact output folder (overrides `--root/<playlist name>`) |
| `--skip-scdl` | only compare + YouTube fill |
| `--no-youtube` | only scdl + compare |
| `--yt-dry-run` | pick YouTube matches without downloading |
| `--min-confidence high\|medium\|low` | lowest confidence auto-downloaded (default `medium`) |
| `--offset N` | start at playlist index N |
| `--auth-token TOKEN` | override the token from scdl.cfg |
| `--allow-no-auth` | proceed without a valid token |

Re-running is safe: existing files are skipped.

## Notes & disclaimer

- YouTube fallbacks are usually ~128 kbps AAC, fine for previewing but not ideal on a club system. Buy the tracks you play out.
- Only download what you have the right to. This tool is meant for personal use, for tracks that are free downloads, or for tracks you otherwise have rights to. Respect artists, labels and each platform's terms of service.

## License

MIT
