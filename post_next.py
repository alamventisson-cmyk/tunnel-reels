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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    args = ap.parse_args()
    acc = args.account
    ACC = acc.upper()

    ig_id = os.environ.get(f"IG_{ACC}_USER_ID")
    token = os.environ.get(f"IG_{ACC}_TOKEN")
    if not ig_id or not token:
        print(f"[{acc}] missing IG_{ACC}_USER_ID / IG_{ACC}_TOKEN env — skipping")
        return 0

    q = load("queue.json", {"base_raw": "", "queues": {}})
    posted = load("posted.json", [])
    posted_ids = {p["id"] for p in posted}

    queue = q["queues"].get(acc, [])
    nxt = next((it for it in queue if it["id"] not in posted_ids), None)
    if not nxt:
        print(f"[{acc}] queue empty / all posted — nothing to do")
        return 0

    local_path = os.path.join(ROOT, nxt["file"])
    video_url = host_video(local_path)
    print(f"[{acc}] posting {nxt['id']} (hosted -> {video_url})")

    # 1) create container
    data = urllib.parse.urlencode({
        "media_type": "REELS", "video_url": video_url,
        "caption": nxt.get("caption", ""), "access_token": token,
    }).encode()
    st, body = req(f"{API}/{ig_id}/media", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    if "id" not in body:
        print(f"[{acc}] container failed: {json.dumps(body)}")
        return 1
    cid = body["id"]

    # 2) wait for processing
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

    # 3) publish
    data = urllib.parse.urlencode({"creation_id": cid, "access_token": token}).encode()
    st, body = req(f"{API}/{ig_id}/media_publish", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    if "id" not in body:
        print(f"[{acc}] publish failed: {json.dumps(body)}")
        return 1
    media_id = body["id"]
    print(f"[{acc}] PUBLISHED ✓ {nxt['id']} media_id={media_id}")

    # 4) record
    posted.append({"id": nxt["id"], "account": acc, "media_id": media_id, "ts": int(time.time())})
    with open(os.path.join(ROOT, "posted.json"), "w", encoding="utf-8") as f:
        json.dump(posted, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
