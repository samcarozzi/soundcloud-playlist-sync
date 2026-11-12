#!/usr/bin/env python3
"""Download a SoundCloud playlist with scdl, find what's missing, fill gaps from YouTube.

Usage:
  sc_sync.py <playlist_url> [--out DIR] [--skip-scdl] [--no-youtube] [--yt-dry-run]
             [--auth-token TOKEN] [--root ~/Documents/music]

Steps:
  1. Resolve the playlist through the SoundCloud API (name + full track list).
  2. Run scdl (high quality / originals via your auth token) into <root>/<playlist name>.
  3. Match local files to playlist tracks by their "NN." index prefix.
  4. For each missing track, search YouTube, pick the candidate whose duration
     best matches SoundCloud's, download it as m4a, and flag weak matches.
  5. Write <out>/.sync-report.md and print a JSON summary on stdout's last line.
"""
import argparse, configparser, glob, time, difflib, json, os, re, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".flac", ".aiff", ".aif", ".opus", ".ogg"}
# Words that, when present in a YT title but absent from the SC title, suggest the wrong version.
BAD_WORDS = ["live", "set", "lyrics", "reaction", "cover", "slowed", "sped up", "8d", "nightcore",
             "instrumental", "karaoke", "full album", "mashup", "bootleg", "vip"]
# Version words: a different version usually also has a different length, so penalise lightly.
VERSION_WORDS = ["remix", "edit", "extended", "radio", "mix", "dub", "rework"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_json(url, token=None):
    h = dict(UA)
    if token:
        h["Authorization"] = f"OAuth {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.load(r)


def http_text(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def scdl_config():
    p = Path.home() / ".config/scdl/scdl.cfg"
    c = configparser.ConfigParser()
    if p.exists():
        c.read(p)
    return c


def get_client_id(cfg):
    cid = cfg.get("scdl", "client_id", fallback="").strip()
    if cid:
        return cid
    # Scrape the current web client id from soundcloud.com's JS bundles.
    try:
        html = http_text("https://soundcloud.com/")
        for src in reversed(re.findall(r'<script[^>]+src="([^"]+)"', html)):
            m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', http_text(src))
            if m:
                return m.group(1)
    except Exception as e:
        log("client_id scrape failed:", e)
    try:  # yt-dlp's cache as a last resort
        return json.load(open(Path.home() / ".cache/yt-dlp/soundcloud/client_id.json"))["data"]
    except Exception:
        sys.exit("Could not obtain a SoundCloud client_id.")


def check_token(cid, token):
    """Return the SoundCloud username the token belongs to, or None if it's invalid/expired."""
    if not token:
        return None
    try:
        return http_json(f"https://api-v2.soundcloud.com/me?client_id={cid}", token).get("username")
    except Exception:
        return None


def resolve_playlist(url, cid, token):
    q = urllib.parse.urlencode({"url": url, "client_id": cid})
    pl = http_json(f"https://api-v2.soundcloud.com/resolve?{q}", token)
    if pl.get("kind") != "playlist":
        sys.exit(f"URL is not a playlist (kind={pl.get('kind')})")
    ids = [t["id"] for t in pl["tracks"]]
    full = {t["id"]: t for t in pl["tracks"] if "title" in t}
    need = [i for i in ids if i not in full]
    for i in range(0, len(need), 50):
        chunk = ",".join(map(str, need[i:i + 50]))
        for t in http_json(f"https://api-v2.soundcloud.com/tracks?ids={chunk}&client_id={cid}", token):
            full[t["id"]] = t
    tracks = []
    for n, tid in enumerate(ids, 1):
        t = full.get(tid, {})
        pm = t.get("publisher_metadata") or {}
        tracks.append({
            "index": n, "id": tid,
            "uploader": (t.get("user") or {}).get("username", "?"),
            "artist": pm.get("artist") or (t.get("user") or {}).get("username", ""),
            "title": t.get("title", f"<unavailable track {tid}>"),
            "duration": round((t.get("full_duration") or t.get("duration") or 0) / 1000),
            "url": t.get("permalink_url", ""),
            "policy": t.get("policy", ""),
        })
    return pl["title"], tracks


def safe_name(s):
    return s.replace("/", "⧸").replace("\0", "").strip() or "playlist"


def local_index(folder):
    found = {}
    for f in folder.iterdir():
        if f.suffix.lower() in AUDIO_EXT and not f.name.startswith("."):
            m = re.match(r"^(\d+)\.\s", f.name)
            if m:
                found.setdefault(int(m.group(1)), []).append(f.name)
    return found


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def yt_candidates(query, n=8):
    r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", f"ytsearch{n}:{query}"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("entries") or []
    except Exception:
        return []


def score(c, tr):
    """Lower is better. Duration diff in seconds plus penalties."""
    dur = c.get("duration") or 0
    if not dur:
        return 999, ["no duration"]
    yt_title = norm(c.get("title") or "")
    chan = norm(c.get("channel") or c.get("uploader") or "")
    sc_title = norm(tr["title"])
    notes = []
    s = abs(dur - tr["duration"])
    for words, pen in ((BAD_WORDS, 15), (VERSION_WORDS, 4)):
        for w in words:
            if re.search(rf"\b{w}\b", yt_title) and not re.search(rf"\b{w}\b", sc_title):
                s += pen
                notes.append(f"'{w}' not in SC title")
    sim = difflib.SequenceMatcher(None, sc_title, yt_title).ratio()
    if not any(tok in yt_title for tok in sc_title.split() if len(tok) > 3) and sim < 0.4:
        s += 30
        notes.append("title mismatch")
    if chan.endswith(" topic") or norm(tr["artist"]) in chan:
        s -= 3  # official artist channel / YouTube Music auto-upload
    return s, notes


def pick(tr):
    seen, cands = set(), []
    for q in {f"{tr['artist']} {tr['title']}", f"{tr['uploader']} {tr['title']}"}:
        for c in yt_candidates(q):
            if c.get("id") and c["id"] not in seen:
                seen.add(c["id"])
                cands.append(c)
    if not cands:
        return None, "none", [], []
    ranked = sorted(((*score(c, tr), c) for c in cands), key=lambda x: x[0])
    s, notes, best = ranked[0]
    diff = abs((best.get("duration") or 0) - tr["duration"])
    tol = max(5, tr["duration"] * 0.03)
    if diff > tol:
        notes = notes + [f"duration off by {diff}s"]
    confidence = "high" if s <= 2 and diff <= tol else ("low" if diff > tol or s > 15 else "medium")
    alts = [{"title": c.get("title"), "channel": c.get("channel"), "duration": c.get("duration"),
             "url": f"https://www.youtube.com/watch?v={c['id']}"} for _, _, c in ranked[1:4]]
    return best, confidence, notes, alts


def tag_file(path, t, album, yurl):
    """Overwrite tags with the SoundCloud metadata so DJ software shows the right artist/title."""
    try:
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        for k in ("desc", "ldes", "\xa9cmt"):
            f.pop(k, None)
        f["\xa9ART"], f["\xa9nam"], f["\xa9alb"] = t["artist"], t["title"], album
        f["\xa9cmt"] = f"YouTube fallback (lower quality): {yurl} | SoundCloud: {t['url']}"
        f["trkn"] = [(t["index"], 0)]
        f.save()
    except Exception as e:
        log(f"  tagging failed for {path.name}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--root", default="~/Documents/music")
    ap.add_argument("--out", help="override output folder (default <root>/<playlist name>)")
    ap.add_argument("--auth-token", default=os.environ.get("SCDL_AUTH_TOKEN"))
    ap.add_argument("--skip-scdl", action="store_true", help="only compare + YouTube fill")
    ap.add_argument("--no-youtube", action="store_true", help="only scdl + compare")
    ap.add_argument("--yt-dry-run", action="store_true", help="pick YouTube matches but don't download")
    ap.add_argument("--offset", type=int, help="start scdl at this playlist index (only tracks >= it are checked)")
    ap.add_argument("--allow-no-auth", action="store_true",
                    help="continue (low-quality SoundCloud streams) when the auth token is missing/expired")
    ap.add_argument("--min-confidence", default="medium", choices=["high", "medium", "low"],
                    help="lowest YouTube match confidence that gets auto-downloaded")
    a = ap.parse_args()

    cfg = scdl_config()
    token = a.auth_token or cfg.get("scdl", "auth_token", fallback="").strip() or None
    cid = get_client_id(cfg)
    user = check_token(cid, token)
    if token and not user:
        log("WARNING: SoundCloud auth token is INVALID or EXPIRED. scdl will fall back to "
            "low-quality public streams and Go+ tracks will fail. Refresh it (see SKILL.md).")
        token_ok = False
    else:
        token_ok = bool(user)
        if user:
            log(f"Auth token OK (logged in as {user})")
    name, tracks = resolve_playlist(a.url, cid, token if token_ok else None)
    out = Path(a.out).expanduser() if a.out else Path(a.root).expanduser() / safe_name(name)
    out.mkdir(parents=True, exist_ok=True)
    log(f"Playlist: {name} ({len(tracks)} tracks) -> {out}")
    if not token_ok and not a.skip_scdl and not a.allow_no_auth:
        log("ABORTING: no valid SoundCloud auth token. Refresh it, or rerun with --allow-no-auth.")
        print(json.dumps({"error": "auth_token_invalid", "playlist": name, "folder": str(out)}))
        sys.exit(2)
    if not token_ok:
        token = None

    if not a.skip_scdl:
        cmd = ["scdl", "-l", a.url, "--path", str(out), "--no-playlist-folder", "-c",
               "--playlist-name-format", "%(playlist_index)s. %(uploader)s - %(title)s.%(ext)s"]
        if a.offset:
            cmd += ["-o", str(a.offset)]
        env = dict(os.environ)
        if token:
            cmd += ["--auth-token", token]
        else:
            # scdl aborts if its config holds an invalid token; give it a token-less copy.
            import tempfile
            tmp = Path(tempfile.mkdtemp(prefix="scdl-noauth-"))
            (tmp / "scdl").mkdir()
            c2 = configparser.RawConfigParser()
            c2.read_dict({s_: dict(cfg.items(s_, raw=True)) for s_ in cfg.sections()})
            for sec in ["DEFAULT", *c2.sections()]:
                for k in ("auth_token", "oauth_token"):
                    if c2.has_option(sec, k):
                        c2.set(sec, k, "")
            with open(tmp / "scdl" / "scdl.cfg", "w") as fh:
                c2.write(fh)
            env["XDG_CONFIG_HOME"] = str(tmp)
        log("Running scdl ...")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="scdl-work-") as work:  # scdl drops temp cover art in cwd
            subprocess.run(cmd, env=env, cwd=work)  # failures (DRM etc.) are expected; we diff afterwards

    have = local_index(out)
    missing = [t for t in tracks if t["index"] not in have and t["index"] >= (a.offset or 1)]
    extra = sorted(i for i in have if i > len(tracks))
    mismatched = []
    for t in tracks:
        for fn in have.get(t["index"], []):
            if difflib.SequenceMatcher(None, norm(t["title"]), norm(fn)).ratio() < 0.35 \
                    and norm(t["title"])[:12] not in norm(fn):
                mismatched.append({"index": t["index"], "file": fn, "expected": t["title"]})
    log(f"Local: {len(tracks) - len(missing)}/{len(tracks)} present, {len(missing)} missing")

    rank = {"high": 0, "medium": 1, "low": 2}
    results = []
    for t in missing:
        r = {**t, "status": "missing"}
        if not a.no_youtube:
            best, conf, notes, alts = pick(t)
            if best is None:
                r.update(status="not found on YouTube")
            else:
                yurl = f"https://www.youtube.com/watch?v={best['id']}"
                r.update(yt_url=yurl, yt_title=best.get("title"), yt_channel=best.get("channel"),
                         yt_duration=best.get("duration"), confidence=conf, notes=notes, alternatives=alts)
                if a.yt_dry_run:
                    r["status"] = "youtube match (dry run)"
                elif rank[conf] > rank[a.min_confidence]:
                    r["status"] = "skipped: low-confidence match"
                else:
                    fname = f"{t['index']:02d}. {t['uploader']} - {t['title']}".replace("/", "⧸")
                    for attempt in range(3):  # YouTube throws transient 403s
                        dl = subprocess.run([
                            "yt-dlp", "-q", "--no-warnings", "--no-playlist", "--retries", "5",
                            "-f", "bestaudio[ext=m4a]/bestaudio", "-x", "--audio-format", "m4a",
                            "--embed-metadata", "--embed-thumbnail",
                            "-o", str(out / (fname + ".%(ext)s")), yurl], capture_output=True, text=True)
                        if dl.returncode == 0:
                            break
                        time.sleep(3 * (attempt + 1))
                    for leftover in out.glob(glob.escape(fname) + ".*"):
                        if leftover.suffix != ".m4a" or dl.returncode != 0:
                            leftover.unlink(missing_ok=True)
                    if dl.returncode == 0:
                        tag_file(out / (fname + ".m4a"), t, name, yurl)
                    r["status"] = "downloaded from YouTube" if dl.returncode == 0 else \
                        f"youtube download failed: {dl.stderr.strip()[-200:]}"
            log(f"  {t['index']:02d} {t['title']}: {r['status']} {r.get('confidence', '')}")
        results.append(r)

    # Report
    lines = [f"# Sync report: {name}", "", f"- Playlist: {a.url}", f"- Folder: {out}", f"- SoundCloud auth token: {'OK' if token_ok else 'MISSING/EXPIRED (low quality downloads)'}",
             f"- Tracks: {len(tracks)}, from SoundCloud: {len(tracks) - len(missing)}, missing after scdl: {len(missing)}", ""]
    if results:
        lines += ["| # | Artist | Title | SC len | Status | Conf | YT match | Notes |", "|---|---|---|---|---|---|---|---|"]
        for r in results:
            yt = f"[{r.get('yt_title', '')}]({r['yt_url']}) ({r.get('yt_channel', '')}, {r.get('yt_duration')}s)" if r.get("yt_url") else ""
            lines.append(f"| {r['index']} | {r['artist']} | {r['title']} | {r['duration']}s | {r['status']} | "
                         f"{r.get('confidence', '')} | {yt} | {'; '.join(r.get('notes', []))} |")
    if mismatched:
        lines += ["", "## Files whose name doesn't match the playlist entry at that index",
                  *[f"- {m['index']}: `{m['file']}` vs expected '{m['expected']}'" for m in mismatched]]
    if extra:
        lines += ["", f"## Local files numbered beyond the playlist length: {extra}"]
    (out / ".sync-report.md").write_text("\n".join(lines) + "\n")

    summary = {"playlist": name, "auth_ok": token_ok, "folder": str(out), "total": len(tracks),
                      "missing_after_scdl": len(missing), "results": results,
                      "mismatched": mismatched, "extra_indexes": extra,
                      "report": str(out / ".sync-report.md")}
    (out / ".sync-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(json.dumps({"playlist": name, "folder": str(out), "auth_ok": token_ok, "total": len(tracks),
                      "missing_after_scdl": len(missing), "status_counts": counts,
                      "flagged": [f"{r['index']}: {r['title']} ({r.get('confidence')}; {'; '.join(r.get('notes', []))})"
                                  for r in results if r.get("confidence") not in (None, "high")],
                      "report_md": str(out / ".sync-report.md"),
                      "report_json": str(out / ".sync-report.json")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
