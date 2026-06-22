#!/usr/bin/env python3
"""
post_next.py — Post the NEXT un-posted reel for one account to Instagram.
Designed to run in GitHub Actions (cloud, no PC needed). Reads queue.json,
skips anything already in posted.json, posts the next item via Meta's official
API using the video's public GitHub raw URL, then records it in posted.json.

Tokens come from environment (GitHub Actions secrets):
  IG_<ACCOUNT>_USER_ID , IG_<ACCOUNT>_TOKEN
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse, urllib.error

API = "https://graph.instagram.com/v25.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_BASE = "https://raw.githubusercontent.com/alamventisson-cmyk/tunnel-reels/main/"


def req(url, data=None, headers=None, method="GET"):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            b = resp.read().decode()
            return resp.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, {"error": {"message": b}}


def load(name, default):
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def host_video(path):
    """Upload the local MP4 to a free public host (catbox.moe) so Meta can fetch
    it. Lets the repo stay PRIVATE — the video bytes go public only transiently
    (and they're about to be public on Instagram anyway)."""
    fname = os.path.basename(path).replace('"', "")
    with open(path, "rb") as f:
        blob = f.read()
    boundary = "----igp" + str(os.getpid())
    CRLF = b"\r\n"
    body = b""
    body += ("--" + boundary).encode() + CRLF
    body += b'Content-Disposition: form-data; name="reqtype"' + CRLF + CRLF + b"fileupload" + CRLF
    body += ("--" + boundary).encode() + CRLF
    body += ('Content-Disposition: form-data; name="fileToUpload"; filename="%s"' % fname).encode() + CRLF
    body += b"Content-Type: video/mp4" + CRLF + CRLF + blob + CRLF
    body += ("--" + boundary + "--").encode() + CRLF
    r = urllib.request.Request("https://catbox.moe/user/api.php", data=body,
                               headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    with urllib.request.urlopen(r, timeout=300) as resp:
        url = resp.read().decode().strip()
    if not url.startswith("http"):
        raise RuntimeError("video hosting failed: " + url)
    return url


def do_post(acc, ig_id, token, nxt, posted):
    video_url = RAW_BASE + nxt["file"]
    print(f"[{acc}] posting {nxt['id']} -> {video_url}")
    data = urllib.parse.urlencode({
        "media_type": "REELS", "video_url": video_url,
        "caption": nxt.get("caption", ""), "access_token": token,
        # Cover frame: 3s in (avoids the black fade-in frame at 0s).
        "thumb_offset": 3000,
    }).encode()
    st, body = req(f"{API}/{ig_id}/media", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    if "id" not in body:
        print(f"[{acc}] container failed: {json.dumps(body)}")
        return 1
    cid = body["id"]
    for i in range(12):
        st, body = req(f"{API}/{cid}?fields=status_code&access_token={urllib.parse.quote(token)}")
        code = body.get("status_code")
        print(f"[{acc}]   status {code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            print(f"[{acc}] processing {code}: {json.dumps(body)}")
            return 1
        time.sleep(20)
    else:
        print(f"[{acc}] never FINISHED")
        return 1
    data = urllib.parse.urlencode({"creation_id": cid, "access_token": token}).encode()
    st, body = req(f"{API}/{ig_id}/media_publish", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    if "id" not in body:
        print(f"[{acc}] publish failed: {json.dumps(body)}")
        return 1
    media_id = body["id"]
    print(f"[{acc}] PUBLISHED ✓ {nxt['id']} media_id={media_id}")
    posted.append({"id": nxt["id"], "account": acc, "media_id": media_id, "ts": int(time.time())})
    with open(os.path.join(ROOT, "posted.json"), "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2, ensure_ascii=False)
    return 0


def creds(acc):
    return os.environ.get(f"IG_{acc.upper()}_USER_ID"), os.environ.get(f"IG_{acc.upper()}_TOKEN")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--next-any", action="store_true",
                    help="Post the single next un-posted reel across ALL accounts, interleaved.")
    args = ap.parse_args()

    q = load("queue.json", {"queues": {}})
    posted = load("posted.json", [])
    posted_ids = {p["id"] for p in posted}
    queues = q.get("queues", {})

    if args.next_any:
        # interleave accounts so both grow evenly: mealzen, nourishly, mealzen, ...
        accs = list(queues.keys())
        order = []
        maxlen = max((len(v) for v in queues.values()), default=0)
        for i in range(maxlen):
            for a in accs:
                if i < len(queues[a]):
                    order.append((a, queues[a][i]))
        pick = next(((a, it) for a, it in order if it["id"] not in posted_ids), None)
        if not pick:
            print("queue empty / all posted — nothing to do")
            return 0
        acc, nxt = pick
        ig_id, token = creds(acc)
        if not ig_id or not token:
            print(f"[{acc}] missing creds — skipping")
            return 0
        return do_post(acc, ig_id, token, nxt, posted)

    if not args.account:
        print("provide --account or --next-any")
        return 1
    acc = args.account
    ig_id, token = creds(acc)
    if not ig_id or not token:
        print(f"[{acc}] missing IG_{acc.upper()}_USER_ID / IG_{acc.upper()}_TOKEN env — skipping")
        return 0
    nxt = next((it for it in queues.get(acc, []) if it["id"] not in posted_ids), None)
    if not nxt:
        print(f"[{acc}] queue empty / all posted — nothing to do")
        return 0
    return do_post(acc, ig_id, token, nxt, posted)


if __name__ == "__main__":
    sys.exit(main())
