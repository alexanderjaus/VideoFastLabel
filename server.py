import json
import os
import threading
import time
import hmac
import hashlib
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote


ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
VIDEOS_DIR = os.path.join(ROOT, "videos")
DATA_DIR = os.path.join(ROOT, "data")

# Config
PORT = int(os.environ.get("PORT", "8000"))
ASSIGNMENT_TTL_SEC = int(os.environ.get("ASSIGNMENT_TTL", "180"))
SINGLE_LABEL_PER_VIDEO = os.environ.get("SINGLE_LABEL_PER_VIDEO", "1") not in ("0", "false", "False")
REVIEWER_PASSWORD = os.environ.get("REVIEWER_PASSWORD", "review")
SECRET_PATH = os.path.join(DATA_DIR, "secret.txt")

VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov"}


def ensure_dirs():
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.videos = self._scan_videos()
        self.assigned = {}  # video_id -> {user, ts}
        self.labeled_ids = set()  # unique labeled video ids
        self.per_user_counts = {}  # user -> count
        self.labels_path = os.path.join(DATA_DIR, "labels.jsonl")
        self.secret = self._load_or_create_secret()
        self.active_users = set()
        self.owner_map = {}  # video_id -> owner user (for unlabeled only)
        self._balance_sig = None  # signature for when to recompute balancing
        self.last_undo = {}  # user -> list of undone records (most recent batch)
        self._load_existing_labels()

    def _scan_videos(self):
        items = []
        if not os.path.isdir(VIDEOS_DIR):
            return []
        # Walk subdirectories and collect files with supported extensions.
        for root, _dirs, files in os.walk(VIDEOS_DIR):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXTS:
                    continue
                p = os.path.join(root, name)
                # Store paths relative to VIDEOS_DIR using forward slashes for consistency.
                rel = os.path.relpath(p, VIDEOS_DIR)
                if os.sep != "/":
                    rel = rel.replace(os.sep, "/")
                items.append(rel)
        items.sort()
        return items

    def _load_existing_labels(self):
        if not os.path.exists(self.labels_path):
            return
        try:
            with open(self.labels_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    vid = rec.get("id")
                    user = rec.get("user")
                    if vid:
                        self.labeled_ids.add(vid)
                    if user:
                        self.per_user_counts[user] = self.per_user_counts.get(user, 0) + 1
        except Exception:
            # If labels file corrupted, continue with empty state
            pass

    def _load_or_create_secret(self):
        try:
            if os.path.exists(SECRET_PATH):
                with open(SECRET_PATH, "rb") as f:
                    data = f.read().strip()
                    if data:
                        return data
            # create
            val = hashlib.sha256(os.urandom(32)).hexdigest().encode("ascii")
            with open(SECRET_PATH, "wb") as f:
                f.write(val)
            return val
        except Exception:
            # fallback ephemeral
            return hashlib.sha256(os.urandom(32)).hexdigest().encode("ascii")

    def _prune_assignments(self):
        now = time.time()
        expired = [vid for vid, info in self.assigned.items() if now - info["ts"] > ASSIGNMENT_TTL_SEC]
        for vid in expired:
            del self.assigned[vid]

    def _eligible_videos(self):
        # Returns list of video ids that can be assigned
        # Rescan videos to pick up new files without restarting the server
        self.videos = self._scan_videos()
        self._prune_assignments()
        eligible = []
        for vid in self.videos:
            if vid in self.assigned:
                continue
            if SINGLE_LABEL_PER_VIDEO and vid in self.labeled_ids:
                continue
            eligible.append(vid)
        return eligible

    def _rebalance_if_needed(self):
        # Recompute ownership of unlabeled videos when participants/dataset changes
        participants = sorted(self.active_users | set(self.per_user_counts.keys()))
        sig = (tuple(participants), len(self.videos), len(self.labeled_ids))
        if self._balance_sig == sig:
            # Still ensure owner_map covers unlabeled set
            unlabeled = {v for v in self.videos if v not in self.labeled_ids}
            missing = [v for v in unlabeled if v not in self.owner_map]
            if not missing:
                return
        # Recompute
        self._balance_sig = sig
        self.owner_map = {}
        if not participants:
            return
        total = len(self.videos)
        n = len(participants)
        base = total // n
        rem = total % n
        # Target total per participant: base or base+1 for first rem users
        targets = {}
        for i, u in enumerate(participants):
            targets[u] = base + (1 if i < rem else 0)
        # Current labeled counts for participants
        labeled_counts = {u: self.per_user_counts.get(u, 0) for u in participants}
        # Need is how many more until target (clamped >=0)
        need = {u: max(0, targets[u] - labeled_counts.get(u, 0)) for u in participants}
        # Assign all unlabeled videos to users in a round-robin by remaining need
        unlabeled = [v for v in self.videos if v not in self.labeled_ids]
        if not unlabeled:
            return
        # Build a queue of users repeated by remaining need
        queue = []
        for u in participants:
            count = need.get(u, 0)
            queue.extend([u] * count)
        if not queue:
            # No one needs more (oversubscribed case); leave unowned
            return
        qi = 0
        qlen = len(queue)
        for vid in unlabeled:
            owner = queue[qi]
            self.owner_map[vid] = owner
            qi = (qi + 1) % qlen

    def peek_next(self):
        with self.lock:
            eligible = self._eligible_videos()
            # Return first eligible globally
            return eligible[0] if eligible else None

    def peek_next_for_user(self, user):
        with self.lock:
            # Ensure latest videos before balancing
            self.videos = self._scan_videos()
            self.active_users.add(user)
            self._rebalance_if_needed()
            eligible = self._eligible_videos()
            for vid in eligible:
                if self.owner_map.get(vid) == user:
                    return vid
            return None

    def assign_next(self, user):
        with self.lock:
            # Ensure latest videos before balancing
            self.videos = self._scan_videos()
            self.active_users.add(user)
            self._rebalance_if_needed()
            eligible = self._eligible_videos()
            # Prefer videos owned by this user
            vid = None
            for v in eligible:
                if self.owner_map.get(v) == user:
                    vid = v
                    break
            if not vid:
                # If none owned, consider done for this user (balanced)
                return None
            self.assigned[vid] = {"user": user, "ts": time.time()}
            return vid

    def release(self, vid, user=None):
        with self.lock:
            info = self.assigned.get(vid)
            if info and (user is None or info["user"] == user):
                del self.assigned[vid]

    def record_label(self, payload):
        # payload must include: id, user, label
        vid = payload.get("id")
        user = payload.get("user")
        label = payload.get("label")
        if not vid or not user or label not in ("ok", "not_ok"):
            return False, "Invalid payload"
        now_ms = int(time.time() * 1000)
        payload["ts"] = now_ms
        line = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            # Only accept if assigned to this user or no assignment present.
            info = self.assigned.get(vid)
            if info and info["user"] != user:
                return False, "Assigned to another user"
            try:
                with open(self.labels_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception as e:
                return False, f"Failed to write label: {e}"
            self.per_user_counts[user] = self.per_user_counts.get(user, 0) + 1
            self.labeled_ids.add(vid)
            # Release assignment if exists
            if vid in self.assigned:
                del self.assigned[vid]
        return True, "ok"

    def remove_label(self, user: str, vid: str):
        if not user or not vid:
            return False, "invalid params"
        with self.lock:
            rows = []
            removed = 0
            try:
                if os.path.exists(self.labels_path):
                    with open(self.labels_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                rec = json.loads(line)
                            except Exception:
                                continue
                            if rec.get("user") == user and rec.get("id") == vid and removed == 0:
                                removed = 1
                                continue
                            rows.append(rec)
            except Exception as e:
                return False, f"failed to read: {e}"
            if removed == 0:
                return True, "not found"
            tmp_path = self.labels_path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for rec in rows:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                os.replace(tmp_path, self.labels_path)
            except Exception as e:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return False, f"failed to write: {e}"
            # Update memory
            self.per_user_counts[user] = max(0, self.per_user_counts.get(user, 0) - 1)
            if vid in self.labeled_ids:
                self.labeled_ids.discard(vid)
            self._balance_sig = None
            return True, "ok"

    def undo_last(self, user: str, count: int):
        if not user or count <= 0:
            return False, "invalid params", []
        with self.lock:
            # Read all rows, find last N for user
            rows = []
            idx_user = []
            try:
                if os.path.exists(self.labels_path):
                    with open(self.labels_path, "r", encoding="utf-8") as f:
                        for i, line in enumerate(f):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except Exception:
                                continue
                            rows.append(rec)
                            if rec.get("user") == user:
                                idx_user.append(len(rows) - 1)
            except Exception as e:
                return False, f"failed to read: {e}", []

            if not idx_user:
                return True, "nothing to undo", []
            k = min(count, len(idx_user))
            pos_batch = idx_user[-k:]
            to_remove_positions = set(pos_batch)
            undone_ids = []
            undone_records = []
            new_rows = []
            for i, rec in enumerate(rows):
                if i in to_remove_positions:
                    vid = rec.get("id")
                    if vid:
                        undone_ids.append(vid)
                    undone_records.append(rec)
                    continue
                new_rows.append(rec)

            # Write back safely
            tmp_path = self.labels_path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for rec in new_rows:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                os.replace(tmp_path, self.labels_path)
            except Exception as e:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return False, f"failed to write: {e}", []

            # Update in-memory stats
            removed = len(undone_ids)
            if removed > 0:
                self.per_user_counts[user] = max(0, self.per_user_counts.get(user, 0) - removed)
                for vid in undone_ids:
                    if vid in self.labeled_ids:
                        self.labeled_ids.discard(vid)
                # Force rebalance on next request
                self._balance_sig = None
                # Save last undo batch for redo
                self.last_undo[user] = undone_records
            return True, "ok", undone_ids

    def redo_last(self, user: str):
        if not user:
            return False, "invalid params", 0
        with self.lock:
            batch = self.last_undo.get(user)
            if not batch:
                return True, "nothing to redo", 0
            appended = 0
            try:
                with open(self.labels_path, "a", encoding="utf-8") as f:
                    for rec in batch:
                        vid = rec.get("id")
                        if SINGLE_LABEL_PER_VIDEO and vid in self.labeled_ids:
                            continue
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        self.per_user_counts[user] = self.per_user_counts.get(user, 0) + 1
                        if vid:
                            self.labeled_ids.add(vid)
                        appended += 1
            except Exception as e:
                return False, f"failed to write: {e}", 0
            # Clear stored batch
            self.last_undo[user] = []
            self._balance_sig = None
            return True, "ok", appended

    def get_user_stats(self, user):
        with self.lock:
            self.videos = self._scan_videos()
            total = len(self.videos)
            labeled_global = len(self.labeled_ids) if SINGLE_LABEL_PER_VIDEO else sum(self.per_user_counts.values())
            remaining_global = max(0, total - len(self.labeled_ids))
            if not user:
                return {
                    "total": total,
                    "labeled": labeled_global,
                    "remaining": remaining_global,
                    "user": None,
                }
            # Include users who have labeled before and the requester
            self.active_users.add(user)
            self._rebalance_if_needed()
            my_labeled = self.per_user_counts.get(user, 0)
            # Compute target via same distribution used by balancer
            participants = sorted(self.active_users | set(self.per_user_counts.keys()))
            n = max(1, len(participants))
            base = total // n
            rem = total % n
            # Find index of user among participants for remainder distribution
            try:
                idx = participants.index(user)
            except ValueError:
                idx = 0
            my_target = base + (1 if idx < rem else 0)
            # Remaining owned items for this user
            my_remaining = sum(1 for v, owner in self.owner_map.items() if owner == user and v not in self.labeled_ids)
            return {
                "total": total,
                "labeled": labeled_global,
                "remaining": remaining_global,
                "user": {
                    "id": user,
                    "labeled": my_labeled,
                    "target": my_target,
                    "remaining": my_remaining,
                },
            }


STATE = None


def guess_mime(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp4":
        return "video/mp4"
    if ext == ".webm":
        return "video/webm"
    if ext == ".m4v":
        return "video/x-m4v"
    if ext == ".mov":
        return "video/quicktime"
    if ext == ".js":
        return "application/javascript"
    if ext == ".css":
        return "text/css"
    if ext in (".html", ".htm"):
        return "text/html; charset=utf-8"
    if ext == ".json":
        return "application/json"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = "FastVideoLabel/0.1"

    def _video_url(self, vid: str) -> str:
        # Return URL-encoded path for client usage
        return "/videos/" + quote(vid)

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        global STATE
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path == "/review":
            return self._serve_static("review.html")
        if path == "/my":
            return self._serve_static("my.html")
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            return self._serve_file(os.path.join(STATIC_DIR, rel), cache=True)
        if path.startswith("/videos/"):
            rel = unquote(path[len("/videos/") :])
            safe = os.path.normpath(rel)
            if safe.startswith(".."):
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            return self._serve_video(os.path.join(VIDEOS_DIR, safe))
        if path == "/api/next":
            qs = parse_qs(parsed.query)
            user = (qs.get("user") or [""])[0].strip()
            if not user:
                return self._send_json({"error": "missing user"}, 400)
            vid = STATE.assign_next(user)
            if not vid:
                return self._send_json({"done": True})
            return self._send_json({
                "id": vid,
                "url": self._video_url(vid),
            })
        if path == "/api/peek":
            qs = parse_qs(parsed.query)
            user = (qs.get("user") or [None])[0]
            vid = STATE.peek_next_for_user(user) if user else STATE.peek_next()
            if not vid:
                return self._send_json({"done": True})
            return self._send_json({"id": vid, "url": self._video_url(vid)})
        if path == "/api/user_labels":
            qs = parse_qs(parsed.query)
            user = (qs.get("user") or [None])[0]
            flt = (qs.get("label") or ["all"])[0]
            limit = int((qs.get("limit") or ["1000"])[0])
            limit = max(1, min(20000, limit))
            if not user:
                return self._send_json({"error": "missing user"}, 400)
            rows = []
            try:
                if os.path.exists(STATE.labels_path):
                    with open(STATE.labels_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except Exception:
                                continue
                            if rec.get("user") != user:
                                continue
                            if flt in ("ok", "not_ok") and rec.get("label") != flt:
                                continue
                            rows.append(rec)
                rows = rows[-limit:][::-1]
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"items": rows, "count": len(rows)})
        if path == "/api/stats":
            total = len(STATE.videos)
            labeled = len(STATE.labeled_ids) if SINGLE_LABEL_PER_VIDEO else sum(STATE.per_user_counts.values())
            remaining = max(0, total - len(STATE.labeled_ids))
            return self._send_json({
                "total": total,
                "labeled": labeled,
                "remaining": remaining,
                "perUser": STATE.per_user_counts,
                "singleLabelPerVideo": SINGLE_LABEL_PER_VIDEO,
            })
        if path == "/api/mystats":
            qs = parse_qs(parsed.query)
            user = (qs.get("user") or [None])[0]
            data = STATE.get_user_stats(user)
            return self._send_json(data)
        if path == "/api/users":
            if not self._require_reviewer():
                return
            return self._send_json({
                "perUser": STATE.per_user_counts,
                "totalVideos": len(STATE.videos),
            })
        if path == "/api/labels":
            if not self._require_reviewer():
                return
            qs = parse_qs(parsed.query)
            user = (qs.get("user") or [None])[0]
            limit = int((qs.get("limit") or ["1000"])[0])
            limit = max(1, min(20000, limit))
            rows = []
            try:
                if os.path.exists(STATE.labels_path):
                    with open(STATE.labels_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                                if user and rec.get("user") != user:
                                    continue
                                rows.append(rec)
                            except Exception:
                                continue
                # Return latest first
                rows = rows[-limit:][::-1]
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"items": rows, "count": len(rows)})

        # Fallback to static
        p = path.lstrip("/")
        if p:
            maybe = os.path.join(STATIC_DIR, p)
            if os.path.isfile(maybe):
                return self._serve_file(maybe, cache=False)
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self):
        global STATE
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}

        if path == "/api/label":
            ok, msg = STATE.record_label(payload)
            if ok:
                return self._send_json({"ok": True})
            return self._send_json({"ok": False, "error": msg}, 400)
        if path == "/api/skip":
            vid = payload.get("id")
            user = payload.get("user")
            if not vid:
                return self._send_json({"ok": False, "error": "missing id"}, 400)
            STATE.release(vid, user)
            return self._send_json({"ok": True})
        if path == "/api/unlabel":
            user = (payload.get("user") or "").strip()
            vid = (payload.get("id") or "").strip()
            ok, msg = STATE.remove_label(user, vid)
            if ok:
                return self._send_json({"ok": True})
            return self._send_json({"ok": False, "error": msg}, 400)
        if path == "/api/reviewer/login":
            pwd = (payload.get("password") or "").strip()
            if not pwd:
                return self._send_json({"ok": False, "error": "missing password"}, 400)
            if pwd != REVIEWER_PASSWORD:
                return self._send_json({"ok": False, "error": "invalid password"}, 401)
            token = self._reviewer_token()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self._set_reviewer_cookie(token)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        if path == "/api/reviewer/logout":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            # expire cookie
            self.send_header("Set-Cookie", "rev=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        if path == "/api/undo":
            user = (payload.get("user") or "").strip()
            count = int(payload.get("count") or 0)
            ok, msg, ids = STATE.undo_last(user, count)
            if ok:
                return self._send_json({"ok": True, "undone": len(ids), "ids": ids})
            return self._send_json({"ok": False, "error": msg}, 400)
        if path == "/api/redo":
            user = (payload.get("user") or "").strip()
            ok, msg, n = STATE.redo_last(user)
            if ok:
                return self._send_json({"ok": True, "redone": n})
            return self._send_json({"ok": False, "error": msg}, 400)

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    # --- Static helpers
    def _serve_static(self, name):
        path = os.path.join(STATIC_DIR, name)
        return self._serve_file(path, cache=False)

    def _serve_file(self, path, cache=False):
        if not os.path.isfile(path):
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
            return
        mime = guess_mime(path)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_video(self, path):
        if not os.path.isfile(path):
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        try:
            file_size = os.path.getsize(path)
            range_header = self.headers.get("Range")
            if range_header:
                # Simple Range: bytes=start-end
                try:
                    units, rng = range_header.split("=", 1)
                    if units.strip() != "bytes":
                        raise ValueError
                    start_s, end_s = (rng.split("-", 1) + [""])[:2]
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else file_size - 1
                    if start < 0 or end < start:
                        raise ValueError
                    end = min(end, file_size - 1)
                except Exception:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return
                length = end - start + 1
                with open(path, "rb") as f:
                    f.seek(start)
                    data = f.read(length)
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", guess_mime(path))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                self.wfile.write(data)
                return
            # No Range: send whole file
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", guess_mime(path))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    # --- Reviewer auth helpers
    def _reviewer_token(self):
        msg = b"reviewer"
        return hmac.new(STATE.secret, msg, hashlib.sha256).hexdigest()

    def _get_cookie(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(";")]
        for p in parts:
            if not p:
                continue
            if "=" in p:
                k, v = p.split("=", 1)
                if k.strip() == name:
                    return v
        return None

    def _set_reviewer_cookie(self, token):
        self.send_header("Set-Cookie", f"rev={token}; Path=/; HttpOnly; SameSite=Lax")

    def _require_reviewer(self):
        cookie = self._get_cookie("rev")
        if cookie and cookie == self._reviewer_token():
            return True
        # Not authorized
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"unauthorized"}')
        return False


def run():
    global STATE
    ensure_dirs()
    STATE = State()
    addr = ("", PORT)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"FastVideoLabel server running on http://localhost:{PORT}")
    print(f"Place videos in: {VIDEOS_DIR}")
    print(f"Open: http://localhost:{PORT}/?user=alice")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
