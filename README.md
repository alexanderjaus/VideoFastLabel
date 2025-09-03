Fast Video Labeling (Binary)

Minimal web app for fast binary labeling of short videos (OK vs Not OK). Runs locally in the browser, serves videos from a local folder, and supports up to ~5 concurrent annotators.

Quick start

- Requirements: Python 3.8+ (no external dependencies)
- Steps:
  1) Put your 5-second clips into the `videos/` folder (supported: .mp4, .webm, .m4v, .mov)
  2) Run the server: `python3 server.py`
  3) Open: `http://localhost:8000/?user=alice` (use a unique `user` per annotator)
  4) Reviewer dashboard: `http://localhost:8000/review` (see login below)

Features

- Fast playback: static serving with HTTP Range support and caching for videos.
- Minimal UI: keyboard shortcuts and preloading of the next item.
- Simple coordination: the server assigns one unlabeled video at a time. Soft locks expire after 3 minutes.
- Persistence: labels are appended to `data/labels.jsonl` as JSON lines.
- Fair assignment: when a new annotator joins, the server redistributes remaining unlabeled videos so that each active annotator is on track to label the same total number of videos (e.g., with 200 total, two users each target ≈100; if Alex already labeled 50 when Bob joins, Alex gets ~50 more and Bob ~100).

Keyboard shortcuts

- K: OK
- J: Not OK
- S: Skip (releases current assignment)
- Space: Play/Pause
- R: Replay
- ←/→: Seek -/+ 0.5s
- U: Undo last label (press repeatedly to undo multiple)
- Ctrl+Z: Undo last label

Data output

- Labels are appended to `data/labels.jsonl` with fields:
  - `id`: filename of the video
  - `user`: annotator id (from URL or modal)
  - `label`: `ok` or `not_ok`
  - `time_ms`: current playback position when labeled
  - `duration_ms`: video duration
  - `ts`: server timestamp (ms)

Config

- Port: `PORT=8080 python3 server.py`
- Assignment TTL (seconds): `ASSIGNMENT_TTL=120 python3 server.py`
- Single label per video (default on): `SINGLE_LABEL_PER_VIDEO=0 python3 server.py` to allow multiple labels per clip.
 - Reviewer password (default `review`): `REVIEWER_PASSWORD=yourpass python3 server.py`

Notes

- This is designed for small teams (<=5) and local datasets. It does not include authentication or advanced consensus features.
- For best performance, keep videos short (~5 seconds) and encoded for web playback (H.264 for `.mp4`, VP9/AV1 for `.webm`).

Reviewer dashboard

- Visit `/review`. Enter the reviewer password (default `review`, set via `REVIEWER_PASSWORD`).
- Lists labels in reverse chronological order and supports filtering by user and searching by video id.
- Buttons: Refresh, Logout. Links open the original video in a new tab.
 
Assignment logic details

- The app balances remaining clips across currently active users (those who have requested `/api/next`).
- Balancing updates dynamically when new users join or when new videos are added; no restart required.
- If a user reaches their balanced quota, they will receive no further clips (their `/api/next` will return done).

APIs

- `GET /api/next?user=<name>`: Assigns next eligible video to that user.
- `GET /api/peek?user=<name>`: Preview next eligible for that user (no lock).
- `POST /api/label`: { id, user, label: "ok"|"not_ok", time_ms, duration_ms }
- `POST /api/skip`: { id, user } releases assignment.
- `GET /api/stats`: Global progress and per-user counts.
- `GET /api/mystats?user=<name>`: Per-user progress with balanced target and remaining owned clips.
- `POST /api/undo`: { user, count } removes the last N labels by the user and returns them to the unlabeled pool.
- `POST /api/redo`: { user } re-applies the last undo batch for that user (if available).
- `GET /api/user_labels?user=<name>&label=all|ok|not_ok&limit=...`: Returns labels for a user, newest first.
- `POST /api/unlabel`: { user, id } removes that specific label for the user.

My Labels page

- Access via the “My Labels” button on the top bar, or `/my?user=<name>`.
- Filter by All / Accepted / Rejected.
- Navigate with Prev(A)/Next(D). Space toggles play/pause.
- “Remove Label” button (Del/Backspace) deletes that label and returns the video to the unlabeled pool.
