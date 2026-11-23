import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
import httpx

try:
    from curl_cffi import requests as cr
except ImportError:
    cr = None
    import httpx

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = FastAPI(
    title="MovieBox 1080p API",
    description="REST API for MovieBox with 1080p Stream & Subtitle Extraction",
    version="2.5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY_DEFAULT_B64 = "NzZpUmwwN3MweFNOOWpxbUVXQXQ3OUVCSlp1bElRSXNWNjRGWnIyTw=="
SECRET_KEY_ALT_B64 = "WHFuMm5uTzQxL0w5Mm8xaXVYaFNMSFRiWHZZNFo1Wlo2Mm04bVNMQQ=="
MODERN_USER_AGENT = "com.community.mbox.in/50020137 (Linux; U; Android 14; en_IN; Pixel 8; Build/UD1A.230803.041; Cronet/145.0.7582.0)"
WEB_USER_AGENT = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36"
API_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
NATIVE_API_BASE = "https://api3.aoneroom.com"
BOOTSTRAP_HOST = "https://apig.inmoviebox.com"
BOOTSTRAP_URL = f"{BOOTSTRAP_HOST}/wefeed-mobile-bff/tab/ranking-list?tabId=0&categoryType=4516404531735022304&page=1&perPage=1"

# Active, healthy MovieBox Web CDN domains verified live
WEB_DOMAINS = [
    "https://sflix.film",
    "https://moviebox.ph",
    "https://movieboxofficial.net",
    "https://movieboxhd.net",
    "https://netnaija.film",
    "https://www.moviesbox.com.co",
    "https://h5-api.aoneroom.com",
]

# High-Performance In-Memory Stream Cache (60s TTL to prevent signed CDN URL expiration)
_STREAMS_CACHE: Dict[str, Any] = {}
_STREAMS_CACHE_TTL = 60  # 60s TTL for fresh CDN signatures

def _b64decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)

def get_secret_bytes(use_alt_key: bool = False) -> bytes:
    outer = SECRET_KEY_ALT_B64 if use_alt_key else SECRET_KEY_DEFAULT_B64
    inner = _b64decode(outer).decode("utf-8")
    return _b64decode(inner)

def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def generate_device_id() -> str:
    return secrets.token_hex(16)

def generate_x_client_token(timestamp: int) -> str:
    ts_str = str(timestamp)
    reversed_ts = ts_str[::-1].encode("utf-8")
    return f"{ts_str},{md5_hex(reversed_ts)}"

def generate_client_info(device_id: str) -> str:
    return json.dumps(
        {
            "package_name": "com.community.mbox.in",
            "version_name": "4.0.03.0920.03",
            "version_code": 50020130,
            "os": "android",
            "os_version": "14",
            "device_id": device_id,
            "install_store": "official",
            "gaid": "1b2212c1-dadf-43c3-a0c8-bd6ce48ae22d",
            "brand": "Google",
            "model": "Pixel 8",
            "system_language": "en",
            "net": "NETWORK_WIFI",
            "region": "IN",
            "timezone": "Asia/Calcutta",
            "sp_code": "",
        },
        separators=(",", ":"),
    )

def build_canonical_string(method: str, accept: str | None, content_type: str | None, url: str, body: str | None, timestamp: int) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        pairs = []
        for segment in parsed.query.split("&"):
            if not segment:
                continue
            pieces = segment.split("=", 1)
            pairs.append((pieces[0], pieces[1] if len(pieces) > 1 else ""))
        pairs.sort(key=lambda kv: kv[0])
        canonical_query = "&".join(f"{k}={v}" for k, v in pairs)
        canonical_url = f"{path}?{canonical_query}"
    else:
        canonical_url = path

    body_bytes = body.encode("utf-8") if body is not None else None
    if body_bytes:
        body_length = str(len(body_bytes))
        body_hash = md5_hex(body_bytes[:102400])
    else:
        body_length = ""
        body_hash = ""

    return "\n".join(
        [
            method.upper(),
            accept or "",
            content_type or "",
            body_length,
            str(timestamp),
            body_hash,
            canonical_url,
        ]
    )

def generate_x_tr_signature(method: str, accept: str | None, content_type: str | None, url: str, body: str | None, timestamp: int, use_alt_key: bool = False) -> str:
    canonical = build_canonical_string(method, accept, content_type, url, body, timestamp)
    key = get_secret_bytes(use_alt_key)
    digest = hmac.new(key, canonical.encode("utf-8"), hashlib.md5).digest()
    sig_b64 = base64.b64encode(digest).decode("ascii")
    return f"{timestamp}|2|{sig_b64}"

def build_auth_headers(method: str, url: str, body: str | None = None, token: str | None = None, device_id: str | None = None, use_alt_key: bool = False, content_type: str = "application/json", accept: str = "application/json") -> Dict[str, str]:
    ts = int(time.time() * 1000)
    dev_id = device_id or generate_device_id()
    headers = {
        "user-agent": MODERN_USER_AGENT,
        "accept": accept,
        "content-type": content_type,
        "connection": "keep-alive",
        "x-client-token": generate_x_client_token(ts),
        "x-tr-signature": generate_x_tr_signature(method, accept, content_type, url, body, ts, use_alt_key),
        "x-client-info": generate_client_info(dev_id),
        "x-client-status": "0",
        "x-forwarded-for": "49.204.100.1",
        "x-real-ip": "49.204.100.1",
        "cf-connecting-ip": "49.204.100.1",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers

def extract_policy_resource(sign_cookie: str | None) -> str | None:
    if not sign_cookie:
        return None
    m_edge = re.search(r"Edge-Cache-Cookie=urlprefix=([^:;\s]+)", sign_cookie)
    if m_edge:
        try:
            decoded = _b64decode(m_edge.group(1)).decode("utf-8").rstrip("/")
            return decoded + "/index.mpd"
        except Exception:
            pass
    m_cf = re.search(r"CloudFront-Policy=([^;]+)", sign_cookie)
    if m_cf:
        raw = m_cf.group(1)
        for t in ("cf", "std"):
            try:
                b = raw.replace("-", "+").replace("_", "=") if t == "cf" else raw
                b += "=" * (-len(b) % 4)
                data = json.loads(base64.b64decode(b).decode("utf-8"))
                for s in data.get("Statement", []):
                    res = s.get("Resource")
                    if res:
                        return res.rstrip("*").rstrip("/") + "/index.mpd"
            except Exception:
                pass
    return None

class MovieBoxEngine:
    _instance = None

    def __init__(self):
        self.device_id = generate_device_id()
        self.bearer_token: Optional[str] = None
        if cr:
            self.session = cr.Session(impersonate="chrome120")
        else:
            self.session = httpx.Client(timeout=25, follow_redirects=True)

    @classmethod
    def get_instance(cls) -> "MovieBoxEngine":
        if cls._instance is None:
            cls._instance = MovieBoxEngine()
        return cls._instance

    def bootstrap_token(self, force: bool = False) -> str:
        if self.bearer_token and not force:
            return self.bearer_token
        headers = build_auth_headers("GET", BOOTSTRAP_URL, device_id=self.device_id)
        resp = self.session.get(BOOTSTRAP_URL, headers=headers, timeout=20)
        x_user = resp.headers.get("x-user")
        if not x_user:
            raise HTTPException(status_code=502, detail=f"Failed to bootstrap MovieBox token (HTTP {resp.status_code}): {resp.text[:150]}")
        token_data = json.loads(x_user)
        token = token_data.get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Empty token in upstream header")
        self.bearer_token = token
        return token

    def api_request(self, method: str, path_or_url: str, body_dict: Optional[Dict[str, Any]] = None, use_token: bool = True, retry_auth: bool = True) -> Dict[str, Any]:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            full_url = path_or_url
        else:
            full_url = f"{NATIVE_API_BASE}{path_or_url}"
        if use_token and not self.bearer_token:
            self.bootstrap_token()
        body_str = json.dumps(body_dict, separators=(",", ":")) if body_dict is not None else None
        headers = build_auth_headers(
            method=method,
            url=full_url,
            body=body_str,
            token=self.bearer_token if use_token else None,
            device_id=self.device_id,
        )
        data_bytes = body_str.encode("utf-8") if body_str is not None else None
        if method.upper() == "POST":
            resp = self.session.post(full_url, headers=headers, data=data_bytes, timeout=25)
        else:
            resp = self.session.get(full_url, headers=headers, timeout=25)
        if resp.status_code in (401, 441) and retry_auth:
            self.bootstrap_token(force=True)
            return self.api_request(method, path_or_url, body_dict, use_token, retry_auth=False)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"API returned HTTP {resp.status_code}")
        return resp.json()

    def search(self, query: str, page: int = 1, per_page: int = 20) -> List[Dict[str, Any]]:
        # Upstream MovieBox BFF requires perPage <= 20
        safe_per_page = min(max(int(per_page or 20), 1), 20)
        payload = {"keyword": query, "page": page, "perPage": safe_per_page}
        url = "/wefeed-mobile-bff/subject-api/search"
        data = {}
        try:
            data = self.api_request("POST", url, body_dict=payload).get("data", {})
        except Exception:
            try:
                # Force token refresh on error
                self.bootstrap_token(force=True)
                data = self.api_request("POST", url, body_dict=payload, retry_auth=False).get("data", {})
            except Exception:
                try:
                    # Fallback to H5 web search
                    h5_url = f"{API_BASE}/subject/search"
                    headers = {"User-Agent": WEB_USER_AGENT, "Referer": "https://moviebox.ph/"}
                    resp = self.session.post(h5_url, headers=headers, json={"keyword": query, "page": page, "perPage": per_page}, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                    else:
                        data = {}
                except Exception:
                    data = {}

        raw_items = data.get("items", []) or data.get("list", [])
        results = []
        for item in raw_items:
            sub = item.get("subject", item)
            results.append({
                "subject_id": str(sub.get("subjectId")),
                "title": sub.get("title"),
                "type": "Movie" if sub.get("subjectType") == 1 else "Series",
                "subject_type": sub.get("subjectType"),
                "release_date": sub.get("releaseDate"),
                "imdb_rating": sub.get("imdbRatingValue"),
                "genre": sub.get("genre"),
                "cover": sub.get("cover", {}).get("url") if isinstance(sub.get("cover"), dict) else None,
                "detail_path": sub.get("detailPath"),
                "language": sub.get("language")
            })
        return results

    def get_details(self, subject_id: str) -> Dict[str, Any]:
        url = f"/wefeed-mobile-bff/subject-api/get?subjectId={subject_id}"
        data = self.api_request("GET", url).get("data", {})
        detail_url_str = data.get("detailUrl", "")
        detail_path = detail_url_str.split("/")[-1] if "/" in detail_url_str else detail_url_str
        return {
            "subject_id": str(data.get("subjectId")),
            "title": data.get("title"),
            "subject_type": data.get("subjectType"),
            "type": "Movie" if data.get("subjectType") == 1 else "Series",
            "description": data.get("description"),
            "release_date": data.get("releaseDate"),
            "duration": data.get("duration"),
            "imdb_rating": data.get("imdbRatingValue"),
            "genre": data.get("genre"),
            "country": data.get("countryName"),
            "language": data.get("language"),
            "cover": data.get("cover", {}).get("url") if isinstance(data.get("cover"), dict) else None,
            "detail_path": detail_path,
            "dubs": [
                {
                    "subject_id": str(d.get("subjectId")),
                    "lang_name": d.get("lanName"),
                    "language": d.get("language"),
                }
                for d in data.get("dubs", [])
                if isinstance(d, dict)
            ]
        }

    def get_episodes(self, subject_id: str) -> List[Dict[str, Any]]:
        url = f"/wefeed-mobile-bff/subject-api/season-info?subjectId={subject_id}"
        res = self.api_request("GET", url)
        seasons = res.get("data", {}).get("seasons", [])
        season_list = []
        for s in seasons:
            season_list.append({
                "season": s.get("se", 1),
                "episode_count": s.get("maxEp", 1),
                "resolutions": s.get("resolutions", [])
            })
        return season_list

    def get_streams(self, subject_id: str, season: int = 0, episode: int = 0, detail_path: Optional[str] = None) -> List[Dict[str, Any]]:
        cache_key = f"{subject_id}_{season}_{episode}"
        now = time.time()
        if cache_key in _STREAMS_CACHE:
            cached_time, cached_res = _STREAMS_CACHE[cache_key]
            if now - cached_time < _STREAMS_CACHE_TTL and cached_res:
                return cached_res

        if not detail_path:
            try:
                details = self.get_details(subject_id)
                detail_path = details.get("detail_path", "")
            except Exception:
                detail_path = ""
        token = self.bootstrap_token()
        streams: List[Dict[str, Any]] = []
        seen_urls = set()

        for domain in WEB_DOMAINS:
            play_url = (
                f"{domain}/wefeed-h5api-bff/subject/play?"
                f"subjectId={subject_id}&se={season}&ep={episode}&detailPath={detail_path}&"
                f"streamSignType=1&supportCodecs%5Bhevc%5D=1&supportCodecs%5Bh264%5D=1"
            )
            referer = f"{domain}/movies/{detail_path}?id={subject_id}&type=/movie/detail&detailSe=&detailEp=&lang=en"
            headers = {
                "User-Agent": WEB_USER_AGENT,
                "Referer": referer,
                "Accept": "application/json",
                "x-client-info": '{"timezone":"Asia/Calcutta"}',
                "x-request-lang": "en",
                "x-vip-restrict": "0",
                "x-no-high-risk-restrict": "0",
                "x-source": "moviebox",
                "Authorization": f"Bearer {token}",
            }
            try:
                resp = self.session.get(play_url, headers=headers, timeout=12)
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", {})
                raw_streams = data.get("streams", [])
                dash_items = data.get("dash", [])

                if raw_streams or dash_items:
                    for st in raw_streams:
                        url = st.get("url")
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        res_str = str(st.get("resolutions", "unknown"))
                        size_bytes = int(st.get("size") or 0)
                        duration_sec = int(st.get("duration") or 0)
                        # Filter out short preview/trailer/sample clips (e.g. 18-23s teaser clips < 15MB)
                        if (0 < size_bytes < 15 * 1024 * 1024) or (0 < duration_sec < 180):
                            continue
                        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                        quality_val = 0
                        for q in (2160, 1440, 1080, 720, 480, 360, 240):
                            if str(q) in res_str:
                                quality_val = q
                                break
                        streams.append({
                            "source": f"Web CDN ({domain})",
                            "format": st.get("format", "MP4"),
                            "quality": f"{quality_val}p" if quality_val else res_str,
                            "quality_value": quality_val,
                            "resolution_tag": res_str,
                            "url": url,
                            "size_mb": round(size_mb, 1),
                            "codec": st.get("codecName"),
                            "duration_sec": st.get("duration"),
                            "headers": {
                                "User-Agent": "Mozilla/5.0",
                                "Referer": f"{domain}/",
                                "Origin": domain,
                            }
                        })

                    for d in dash_items:
                        url = d.get("url")
                        cookie = d.get("signCookie")
                        extracted_mpd = extract_policy_resource(cookie) if cookie else None
                        final_url = extracted_mpd or url
                        if final_url and final_url not in seen_urls:
                            seen_urls.add(final_url)
                            res_tag = str(d.get("resolutions", "adaptive"))
                            streams.append({
                                "source": f"Web DASH ({domain})",
                                "format": "DASH",
                                "quality": "1080p (Adaptive)" if "1080" in res_tag else "Adaptive",
                                "quality_value": 1080 if "1080" in res_tag else 999,
                                "resolution_tag": res_tag,
                                "url": final_url,
                                "size_mb": 0,
                                "codec": "hevc/h264",
                                "duration_sec": None,
                                "cookie": cookie,
                                "headers": {
                                    "User-Agent": "Mozilla/5.0",
                                    "Referer": f"{domain}/",
                                    "Origin": domain,
                                }
                            })
                    break
            except Exception:
                continue

        try:
            native_url = f"/wefeed-mobile-bff/subject-api/play-info?subjectId={subject_id}&se={season}&ep={episode}"
            native_data = self.api_request("GET", native_url).get("data", {})
            for st in native_data.get("streams", []):
                raw_url = st.get("url")
                if not raw_url or "b164fbfb4347792950bdfbfb563d39d9" in raw_url:
                    continue
                cookie = st.get("signCookie")
                policy_url = extract_policy_resource(cookie) if cookie else None
                final_url = policy_url or raw_url
                if final_url in seen_urls:
                    continue
                seen_urls.add(final_url)
                res_str = str(st.get("resolutions", ""))
                quality_val = 0
                for q in (2160, 1440, 1080, 720, 480, 360, 240):
                    if str(q) in res_str:
                        quality_val = q
                        break
                size_bytes = int(st.get("size") or 0)
                duration_sec = int(st.get("duration") or 0)
                # Filter out short preview/trailer/sample clips (e.g. 18-23s teaser clips < 15MB)
                if (0 < size_bytes < 15 * 1024 * 1024) or (0 < duration_sec < 180):
                    continue
                size_mb = size_bytes / (1024 * 1024) if size_bytes else 0
                streams.append({
                    "source": "Mobile Native CDN",
                    "format": "DASH" if (policy_url or ".mpd" in final_url) else st.get("format", "MP4"),
                    "quality": f"{quality_val}p" if quality_val else (res_str or "Default"),
                    "quality_value": quality_val,
                    "resolution_tag": res_str,
                    "url": final_url,
                    "size_mb": round(size_mb, 1),
                    "codec": st.get("codecName"),
                    "duration_sec": st.get("duration"),
                    "headers": {
                        "User-Agent": MODERN_USER_AGENT,
                        "Referer": "https://api3.aoneroom.com/",
                    }
                })
        except Exception:
            pass

        streams.sort(key=lambda s: s["quality_value"], reverse=True)
        if streams:
            _STREAMS_CACHE[cache_key] = (now, streams)
        return streams

    def get_subtitles(self, subject_id: str, stream_id: Optional[str] = None, season: int = 0, episode: int = 0) -> List[Dict[str, Any]]:
        subs: List[Dict[str, Any]] = []
        seen_urls = set()

        if not stream_id:
            try:
                native_url = f"/wefeed-mobile-bff/subject-api/play-info?subjectId={subject_id}&se={season}&ep={episode}"
                raw_streams = self.api_request("GET", native_url).get("data", {}).get("streams", [])
                if raw_streams and raw_streams[0].get("id"):
                    stream_id = str(raw_streams[0]["id"])
            except Exception:
                pass

        def _add_sub(item: Dict[str, Any]):
            u = item.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                subs.append({
                    "id": str(item.get("id", "")),
                    "language": item.get("lanName") or item.get("language") or "English",
                    "code": item.get("lan", ""),
                    "url": u,
                    "size": item.get("size")
                })

        if stream_id:
            try:
                url = f"/wefeed-mobile-bff/subject-api/get-stream-captions?subjectId={subject_id}&streamId={stream_id}"
                data = self.api_request("GET", url).get("data", {})
                captions = data if isinstance(data, list) else data.get("extCaptions") or data.get("captions") or []
                for s in captions:
                    if isinstance(s, dict):
                        _add_sub(s)
            except Exception:
                pass

        try:
            res_id = stream_id or "0"
            url = f"/wefeed-mobile-bff/subject-api/get-ext-captions?subjectId={subject_id}&resourceId={res_id}&episode={episode}"
            data = self.api_request("GET", url).get("data", {})
            ext = data if isinstance(data, list) else data.get("extCaptions") or data.get("captions") or []
            for s in ext:
                if isinstance(s, dict):
                    _add_sub(s)
        except Exception:
            pass

        return subs

engine = MovieBoxEngine.get_instance()

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MovieBox API</title>
    <style>
        :root {
            --bg: #09090b;
            --surface: #121215;
            --surface-hover: #18181b;
            --border: #27272a;
            --text: #fafafa;
            --text-dim: #71717a;
            --accent: #3b82f6;
            --accent-soft: rgba(59, 130, 246, 0.12);
            --green: #10b981;
            --green-soft: rgba(16, 185, 129, 0.12);
            --radius: 8px;
            --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: var(--sans);
            font-size: 14px;
            line-height: 1.5;
            padding: 32px 20px;
        }
        .wrap {
            max-width: 980px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 28px;
        }
        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .brand h1 {
            font-size: 18px;
            font-weight: 600;
            letter-spacing: -0.3px;
        }
        .pill {
            font-size: 11px;
            font-family: var(--mono);
            padding: 2px 7px;
            border-radius: 4px;
            background: var(--border);
            color: var(--text-dim);
        }
        .pill.active {
            background: var(--green-soft);
            color: var(--green);
        }
        .section-title {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-dim);
            margin-bottom: 12px;
            font-weight: 600;
        }
        .box {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 18px;
        }
        .search-bar {
            display: flex;
            gap: 8px;
            position: relative;
        }
        .input-text {
            flex: 1;
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 10px 14px;
            border-radius: var(--radius);
            font-size: 14px;
            font-family: inherit;
            outline: none;
        }
        .input-text:focus {
            border-color: var(--accent);
        }
        .input-num {
            width: 80px;
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
            padding: 10px 12px;
            border-radius: var(--radius);
            font-size: 14px;
            outline: none;
        }
        .btn-action {
            background: var(--text);
            color: var(--bg);
            border: none;
            padding: 10px 18px;
            border-radius: var(--radius);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
        }
        .btn-action:hover {
            opacity: 0.9;
        }
        .btn-ghost {
            background: transparent;
            color: var(--text);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
        }
        .btn-ghost:hover {
            background: var(--surface-hover);
        }
        .dropdown {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            margin-top: 4px;
            max-height: 240px;
            overflow-y: auto;
            z-index: 50;
            display: none;
        }
        .dropdown-item {
            padding: 10px 14px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #1c1c20;
        }
        .dropdown-item:hover {
            background: var(--surface-hover);
        }
        .media-row {
            display: flex;
            gap: 14px;
            align-items: center;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
        }
        .media-poster {
            width: 44px;
            height: 62px;
            border-radius: 4px;
            object-fit: cover;
            background: var(--border);
        }
        .stream-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            font-size: 13px;
        }
        .stream-row:last-child {
            border-bottom: none;
        }
        .stream-spec {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .stream-badge {
            font-family: var(--mono);
            font-weight: 600;
            font-size: 11px;
            padding: 2px 6px;
            border-radius: 4px;
            background: var(--accent-soft);
            color: var(--accent);
        }
        .stream-badge.hd {
            background: var(--green-soft);
            color: var(--green);
        }
        .video-box {
            display: none;
            flex-direction: column;
            gap: 10px;
            margin-top: 16px;
        }
        video {
            width: 100%;
            max-height: 480px;
            background: #000;
            border-radius: var(--radius);
            border: 1px solid var(--border);
        }
        .endpoint-table {
            width: 100%;
            border-collapse: collapse;
        }
        .endpoint-table th, .endpoint-table td {
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 13px;
        }
        .endpoint-table tr:last-child td {
            border-bottom: none;
        }
        .endpoint-method {
            font-family: var(--mono);
            font-size: 11px;
            font-weight: 700;
            color: var(--accent);
        }
        .endpoint-path {
            font-family: var(--mono);
            color: var(--text);
        }
    </style>
</head>
<body>
    <div class="wrap">
        <header>
            <div class="brand">
                <h1>MovieBox API</h1>
                <span class="pill">v2.5.0</span>
                <span class="pill active">1080p Online</span>
            </div>
            <div>
                <a class="btn-ghost" href="/docs" target="_blank">Swagger Docs</a>
            </div>
        </header>

        <div>
            <div class="section-title">Stream & Subtitle Resolver</div>
            <div class="box">
                <div class="search-bar">
                    <input type="text" id="searchInput" class="input-text" placeholder="Search title or paste Subject ID (e.g. Avatar, 8906247916759695608)..." autocomplete="off">
                    <input type="number" id="seasonInput" class="input-num" placeholder="S" title="Season" value="0">
                    <input type="number" id="episodeInput" class="input-num" placeholder="E" title="Episode" value="0">
                    <button class="btn-action" onclick="resolveStreams()">Resolve</button>
                    <div id="dropdownList" class="dropdown"></div>
                </div>

                <div id="mediaHeader" style="display: none;" class="media-row">
                    <img id="mediaPoster" class="media-poster">
                    <div>
                        <div id="mediaTitle" style="font-weight: 600; font-size: 15px;"></div>
                        <div id="mediaMeta" style="font-size: 12px; color: var(--text-dim); margin-top: 2px;"></div>
                    </div>
                </div>

                <div class="video-box" id="playerContainer">
                    <video id="player" controls playsinline></video>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span id="playerStatus" style="font-size: 12px; color: var(--text-dim); font-family: var(--mono);"></span>
                        <select id="subPicker" onchange="switchSubtitle(this.value)" class="input-text" style="max-width: 220px; padding: 6px 10px; font-size: 12px;">
                            <option value="">Subtitles Off</option>
                        </select>
                    </div>
                </div>

                <div id="streamList" style="margin-top: 14px;"></div>
            </div>
        </div>

        <div>
            <div class="section-title">Endpoints (Click Test to Open Raw JSON)</div>
            <div class="box" style="padding: 0;">
                <table class="endpoint-table">
                    <thead>
                        <tr style="color: var(--text-dim); font-size: 11px; text-transform: uppercase;">
                            <th style="width: 70px;">Method</th>
                            <th>Endpoint Path</th>
                            <th>Description</th>
                            <th style="width: 110px; text-align: right;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill" style="margin-right: 6px;">Movie</span>/api/stream/8906247916759695608</td>
                            <td style="color: var(--text-dim);">Movie Streams 1080p, 720p, 480p (Avatar)</td>
                            <td style="text-align: right;"><a href="/api/stream/8906247916759695608" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill" style="margin-right: 6px;">Movie</span>/api/stream/8906247916759695608/captions</td>
                            <td style="color: var(--text-dim);">Movie Subtitle Tracks .srt (Avatar)</td>
                            <td style="text-align: right;"><a href="/api/stream/8906247916759695608/captions" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill active" style="margin-right: 6px;">Series</span>/episodes/6207982430134357800</td>
                            <td style="color: var(--text-dim);">TV Seasons & Episode Counts (Breaking Bad)</td>
                            <td style="text-align: right;"><a href="/episodes/6207982430134357800" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill active" style="margin-right: 6px;">Series</span>/api/stream/6207982430134357800?se=1&ep=1</td>
                            <td style="color: var(--text-dim);">TV Episode Stream: Season 1, Episode 1</td>
                            <td style="text-align: right;"><a href="/api/stream/6207982430134357800?se=1&ep=1" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill active" style="margin-right: 6px;">Series</span>/api/stream/6207982430134357800?se=1&ep=2</td>
                            <td style="color: var(--text-dim);">TV Episode Stream: Season 1, Episode 2</td>
                            <td style="text-align: right;"><a href="/api/stream/6207982430134357800?se=1&ep=2" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path"><span class="pill active" style="margin-right: 6px;">Series</span>/api/stream/6207982430134357800/captions?se=1&ep=1</td>
                            <td style="color: var(--text-dim);">TV Episode Subtitles: Season 1, Episode 1</td>
                            <td style="text-align: right;"><a href="/api/stream/6207982430134357800/captions?se=1&ep=1" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/search?q=Avatar</td>
                            <td style="color: var(--text-dim);">Search catalog with metadata & IDs</td>
                            <td style="text-align: right;"><a href="/search?q=Avatar" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/search/suggest?q=Avatar</td>
                            <td style="color: var(--text-dim);">Real-time search autocomplete suggestions</td>
                            <td style="text-align: right;"><a href="/search/suggest?q=Avatar" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/detail/8906247916759695608</td>
                            <td style="color: var(--text-dim);">Deep metadata, audio dubs, and plot summary</td>
                            <td style="text-align: right;"><a href="/detail/8906247916759695608" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/movies?page=1</td>
                            <td style="color: var(--text-dim);">Paginated movie feed</td>
                            <td style="text-align: right;"><a href="/movies?page=1" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/tv-series?page=1</td>
                            <td style="color: var(--text-dim);">Paginated TV series feed</td>
                            <td style="text-align: right;"><a href="/tv-series?page=1" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                        <tr>
                            <td><span class="endpoint-method">GET</span></td>
                            <td class="endpoint-path">/home</td>
                            <td style="color: var(--text-dim);">Banners, hot sections, and operating feeds</td>
                            <td style="text-align: right;"><a href="/home" target="_blank" class="btn-ghost">Test</a></td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let searchTimer = null;
        let activeSubjectId = "";
        const searchInput = document.getElementById("searchInput");
        const dropdownList = document.getElementById("dropdownList");

        searchInput.addEventListener("input", (e) => {
            clearTimeout(searchTimer);
            const val = e.target.value.trim();
            if (val.length < 2) {
                dropdownList.style.display = "none";
                return;
            }
            if (/^\\d{15,}$/.test(val)) {
                activeSubjectId = val;
                dropdownList.style.display = "none";
                return;
            }
            searchTimer = setTimeout(async () => {
                try {
                    const res = await fetch(`/search/suggest?q=${encodeURIComponent(val)}`);
                    const data = await res.json();
                    const list = data.suggestions || [];
                    if (!list.length) {
                        dropdownList.style.display = "none";
                        return;
                    }
                    dropdownList.innerHTML = list.map(item => `
                        <div class="dropdown-item" onclick="selectItem('${item.subject_id}', '${item.title.replace(/'/g, "\\\\'")}')">
                            <span style="font-weight: 500;">${item.title}</span>
                            <span style="font-family: var(--mono); font-size: 11px; color: var(--text-dim);">${item.subject_id}</span>
                        </div>
                    `).join("");
                    dropdownList.style.display = "block";
                } catch (err) {}
            }, 200);
        });

        function selectItem(id, title) {
            activeSubjectId = id;
            searchInput.value = title;
            dropdownList.style.display = "none";
            resolveStreams();
        }

        async function resolveStreams() {
            const val = searchInput.value.trim();
            const subjectId = /^\\d{15,}$/.test(val) ? val : (activeSubjectId || val);
            const se = parseInt(document.getElementById("seasonInput").value) || 0;
            const ep = parseInt(document.getElementById("episodeInput").value) || 0;

            if (!subjectId) {
                alert("Please enter a title or Subject ID");
                return;
            }

            const streamList = document.getElementById("streamList");
            streamList.innerHTML = '<div style="padding: 12px; color: var(--text-dim); font-size: 13px;">Resolving streams...</div>';

            try {
                const [streamRes, capRes, detailRes] = await Promise.all([
                    fetch(`/api/stream/${subjectId}?se=${se}&ep=${ep}`).then(r => r.json()),
                    fetch(`/api/stream/${subjectId}/captions?se=${se}&ep=${ep}`).then(r => r.json()),
                    fetch(`/detail/${subjectId}`).then(r => r.json()).catch(() => null)
                ]);

                if (detailRes && detailRes.title) {
                    document.getElementById("mediaHeader").style.display = "flex";
                    document.getElementById("mediaTitle").textContent = `${detailRes.title} (${detailRes.type})`;
                    document.getElementById("mediaMeta").textContent = `${detailRes.release_date || ""} • IMDb: ${detailRes.imdb_rating || "N/A"} • ${detailRes.genre || ""}`;
                    if (detailRes.cover) {
                        const p = document.getElementById("mediaPoster");
                        p.src = detailRes.cover;
                        p.style.display = "block";
                    }
                }

                const streams = streamRes.streams || [];
                if (!streams.length) {
                    streamList.innerHTML = '<div style="padding: 12px; color: var(--text-dim); font-size: 13px;">No streams found for this request.</div>';
                    return;
                }

                streamList.innerHTML = streams.map((s, idx) => `
                    <div class="stream-row">
                        <div class="stream-spec">
                            <span class="stream-badge ${s.quality.includes('1080') ? 'hd' : ''}">${s.quality}</span>
                            <span style="font-weight: 500;">${s.format}</span>
                            <span style="color: var(--text-dim); font-size: 12px;">${s.size_mb ? s.size_mb + ' MB' : 'DASH Master'}</span>
                            <span style="color: var(--text-dim); font-size: 12px;">${s.codec || ''}</span>
                        </div>
                        <div style="display: flex; gap: 6px;">
                            <button class="btn-ghost" onclick="play('${encodeURIComponent(JSON.stringify(s))}')">Play</button>
                            <a class="btn-ghost" href="${s.url}" target="_blank">Open Raw</a>
                            <button class="btn-ghost" onclick="copyUrl('${s.url}')">Copy</button>
                        </div>
                    </div>
                `).join("");

                const subs = capRes.captions || [];
                const subPicker = document.getElementById("subPicker");
                subPicker.innerHTML = '<option value="">Subtitles Off</option>' + subs.map(sub => `
                    <option value="${sub.url}">${sub.language}</option>
                `).join("");

            } catch (err) {
                streamList.innerHTML = `<div style="padding: 12px; color: #ef4444; font-size: 13px;">Failed: ${err.message}</div>`;
            }
        }

        function play(encoded) {
            const stream = JSON.parse(decodeURIComponent(encoded));
            const player = document.getElementById("player");
            const container = document.getElementById("playerContainer");
            container.style.display = "flex";
            document.getElementById("playerStatus").textContent = `Playing: ${stream.quality} ${stream.format}`;
            player.src = stream.url;
            player.play().catch(() => {});
        }

        function copyUrl(url) {
            navigator.clipboard.writeText(url).then(() => alert("Stream URL copied to clipboard!"));
        }

        function switchSubtitle(url) {
            const player = document.getElementById("player");
            player.querySelectorAll("track").forEach(t => t.remove());
            if (!url) return;
            const t = document.createElement("track");
            t.kind = "captions";
            t.label = "Selected";
            t.srclang = "en";
            t.src = url;
            t.default = true;
            player.appendChild(t);
        }
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

@app.get("/search")
def search(q: Optional[str] = Query(None), keyword: Optional[str] = Query(None), page: int = 1, per_page: int = 20):
    query_str = (q or keyword or "").strip()
    if not query_str:
        return {"query": "", "page": page, "results": []}
    return {"query": query_str, "page": page, "results": engine.search(query_str, page=page, per_page=per_page)}

@app.get("/search/suggest")
def search_suggest(q: Optional[str] = Query(None), keyword: Optional[str] = Query(None)):
    query_str = (q or keyword or "").strip()
    if not query_str:
        return {"suggestions": []}
    url = f"{API_BASE}/subject/search-suggest"
    headers = {"User-Agent": WEB_USER_AGENT, "Referer": "https://moviebox.ph/"}
    try:
        resp = engine.session.post(url, headers=headers, json={"keyword": query_str, "perPage": 10}, timeout=15)
        raw = resp.json().get("data", {}).get("items", [])
    except Exception:
        raw = []
    suggestions = []
    for item in raw:
        sub = item.get("subject") or {}
        suggestions.append({
            "title": sub.get("title") or item.get("word") or item.get("title"),
            "slug": sub.get("detailPath") or item.get("detailPath"),
            "subject_id": str(sub.get("subjectId") or item.get("subjectId"))
        })
    return {"suggestions": suggestions}

@app.get("/detail/{subject_id}")
def get_detail(subject_id: str):
    return engine.get_details(subject_id)

@app.get("/episodes/{subject_id}")
def get_episodes(subject_id: str):
    return {"subject_id": subject_id, "seasons": engine.get_episodes(subject_id)}

@app.get("/api/stream/{subject_id}")
def get_streams(subject_id: str, se: int = 0, ep: int = 0, detail_path: Optional[str] = None):
    streams = engine.get_streams(subject_id, season=se, episode=ep, detail_path=detail_path)
    if not streams and (se > 0 or ep > 0):
        # Fallback for movies where caller erroneously passed season/episode
        movie_streams = engine.get_streams(subject_id, season=0, episode=0, detail_path=detail_path)
        if movie_streams:
            streams = movie_streams
    return {
        "subject_id": subject_id,
        "season": se,
        "episode": ep,
        "count": len(streams),
        "streams": streams
    }

@app.get("/api/stream/{subject_id}/captions")
def get_captions(subject_id: str, se: int = 0, ep: int = 0, stream_id: Optional[str] = None):
    subs = engine.get_subtitles(subject_id, stream_id=stream_id, season=se, episode=ep)
    if not subs and (se > 0 or ep > 0):
        movie_subs = engine.get_subtitles(subject_id, stream_id=stream_id, season=0, episode=0)
        if movie_subs:
            subs = movie_subs
    return {
        "subject_id": subject_id,
        "season": se,
        "episode": ep,
        "count": len(subs),
        "captions": subs
    }

def get_upstream_headers(request: Optional[Request] = None, referer: str = "https://moviebox.ph/") -> dict:
    # Always pin client IP to Indian ISP (ACT Fibernet India) so upstream BFF consistently serves Indian region data (Bollywood, South Indian, Tollywood, Hindi dubs) regardless of proxy/CDN origin
    client_ip = "49.204.100.1"
    return {
        "User-Agent": WEB_USER_AGENT,
        "Referer": referer,
        "X-Forwarded-For": client_ip,
        "X-Real-IP": client_ip,
        "CF-Connecting-IP": client_ip,
        "True-Client-IP": client_ip,
        "Accept-Language": "en-IN,en;q=0.9,te;q=0.8,hi;q=0.7",
    }

@app.get("/movies")
def get_movies(request: Request, page: int = 1, per_page: int = 24, sort: str = "RECOMMEND"):
    url = f"{API_BASE}/subject/filter"
    payload = {"tabId": 2, "filter": {"sort": sort, "genre": "ALL", "country": "ALL", "year": "ALL", "language": "ALL"}, "page": page, "perPage": per_page}
    resp = engine.session.post(url, headers=get_upstream_headers(request), json=payload, timeout=20)
    data = resp.json().get("data", {})
    raw = data.get("items") or data.get("subjects") or []
    return {
        "page": page,
        "per_page": per_page,
        "total": data.get("pager", {}).get("totalCount") or len(raw),
        "items": [
            {
                "title": s.get("title"),
                "subject_id": str(s.get("subjectId")),
                "detail_path": s.get("detailPath"),
                "cover": s.get("cover", {}).get("url") if isinstance(s.get("cover"), dict) else None,
                "rating": s.get("imdbRatingValue"),
                "year": s.get("releaseDate", "")[:4] if s.get("releaseDate") else None
            }
            for s in raw
        ]
    }

@app.get("/tv-series")
def get_tv_series(request: Request, page: int = 1, per_page: int = 24, sort: str = "RECOMMEND"):
    url = f"{API_BASE}/subject/filter"
    payload = {"tabId": 5, "filter": {"sort": sort, "genre": "ALL", "country": "ALL", "year": "ALL", "language": "ALL"}, "page": page, "perPage": per_page}
    resp = engine.session.post(url, headers=get_upstream_headers(request), json=payload, timeout=20)
    data = resp.json().get("data", {})
    raw = data.get("items") or data.get("subjects") or []
    return {
        "page": page,
        "per_page": per_page,
        "total": data.get("pager", {}).get("totalCount") or len(raw),
        "items": [
            {
                "title": s.get("title"),
                "subject_id": str(s.get("subjectId")),
                "detail_path": s.get("detailPath"),
                "cover": s.get("cover", {}).get("url") if isinstance(s.get("cover"), dict) else None,
                "rating": s.get("imdbRatingValue"),
                "year": s.get("releaseDate", "")[:4] if s.get("releaseDate") else None
            }
            for s in raw
        ]
    }

@app.get("/home")
def get_home(request: Request):
    headers = get_upstream_headers(request)
    url = f"{API_BASE}/home?host=moviebox.ph"
    resp = engine.session.get(url, headers=headers, timeout=20)
    return resp.json()

@app.get("/test-stream")
def test_stream(url: str):
    import requests as py_requests
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://sflix.film/", "Range": "bytes=0-100"}
    r = py_requests.get(url, headers=headers)
    return {"cr_available": cr is not None, "status": r.status_code, "headers": dict(r.headers), "body": r.text[:200]}

@app.get("/test-proxy")
def test_proxy(url: str, referer: Optional[str] = None):
    import requests
    target_referer = referer or "https://sflix.film/"
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": target_referer,
        "Origin": target_referer.rstrip("/"),
        "Range": "bytes=0-100"
    }
    try:
        r = requests.get(url, headers=req_headers, timeout=10)
        return {"status": r.status_code, "headers": dict(r.headers), "text": r.text[:200]}
    except Exception as e:
        return {"error": str(e)}

@app.api_route("/proxy/video", methods=["GET", "HEAD"])
def proxy_video(request: Request, url: str = Query(...), referer: Optional[str] = Query(None), cookie: Optional[str] = Query(None)):
    target_referer = referer or "https://sflix.film/"

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": target_referer,
        "Origin": target_referer.rstrip("/"),
        "X-Forwarded-For": "49.204.100.1",
        "X-Real-IP": "49.204.100.1",
        "CF-Connecting-IP": "49.204.100.1",
        "True-Client-IP": "49.204.100.1",
    }
    if cookie:
        req_headers["Cookie"] = cookie
    if "range" in request.headers:
        req_headers["Range"] = request.headers["range"]

    if cr:
        try:
            resp = cr.get(
                url,
                headers=req_headers,
                impersonate="chrome120",
                stream=True,
                timeout=35
            )
            if resp.status_code >= 400:
                logger.error(f"curl_cffi returned {resp.status_code}: {resp.text[:200]}")
            response_headers = {}
            for h in ["content-type", "content-length", "content-range", "accept-ranges", "cache-control", "etag", "last-modified"]:
                if h in resp.headers:
                    response_headers[h] = resp.headers[h]
            if "accept-ranges" not in response_headers:
                response_headers["accept-ranges"] = "bytes"
            response_headers["Access-Control-Allow-Origin"] = "*"
            response_headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"

            def iter_content():
                try:
                    for chunk in resp.iter_content(chunk_size=131072):
                        if chunk:
                            yield chunk
                finally:
                    resp.close()

            return StreamingResponse(
                iter_content(),
                status_code=resp.status_code,
                headers=response_headers,
                media_type=response_headers.get("content-type", "video/mp4")
            )
        except Exception as e:
            logger.warning(f"curl_cffi proxy_video error: {e}, falling back to httpx")

    try:
        import requests as py_requests
        s = py_requests.Session()
        resp = s.get(url, headers=req_headers, stream=True, timeout=30)
        response_headers = {}
        for h in ["content-type", "content-length", "content-range", "accept-ranges", "cache-control"]:
            if h in resp.headers:
                response_headers[h] = resp.headers[h]
        if "accept-ranges" not in response_headers:
            response_headers["accept-ranges"] = "bytes"
        response_headers["Access-Control-Allow-Origin"] = "*"
        response_headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"

        def req_iter():
            try:
                for chunk in resp.iter_content(chunk_size=131072):
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return StreamingResponse(
            req_iter(),
            status_code=resp.status_code,
            headers=response_headers,
            media_type=response_headers.get("content-type", "video/mp4")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/proxy/subtitle")
async def proxy_subtitle(url: str = Query(...)):
    """Fetches SRT/VTT subtitles, converts SRT to standard WebVTT, and returns text/vtt with UTF-8."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            resp = await client.get(url)
            text = resp.text
            # Convert SRT to WebVTT if it starts with an integer or contains '-->'
            if "-->" in text:
                vtt_text = "WEBVTT\n\n"
                # Replace comma with period in timestamps: 00:01:20,000 --> 00:01:23,000
                vtt_text += re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
            else:
                vtt_text = text

            return Response(
                content=vtt_text,
                media_type="text/vtt; charset=utf-8",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "text/vtt; charset=utf-8"
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Subtitle fetch error: {str(e)}")

import telegram_bot
import base64

_telegram_events: list = []
_last_channel_info: dict = {}

@app.get("/api/telegram/status")
async def telegram_status(token: str = Query(...)):
    """Check if bot token is valid and return bot profile."""
    res = await telegram_bot.get_bot_info(token)
    return res

@app.get("/api/telegram/last_updates")
async def telegram_last_updates():
    """Return recently received webhook events and detected channel information."""
    return {
        "channel": _last_channel_info,
        "count": len(_telegram_events),
        "recent_events": _telegram_events[-15:]
    }

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, token: Optional[str] = Query(None)):
    """Handle incoming Telegram webhook events: /start, auto-approving chat_join_request, etc."""
    global _last_channel_info
    try:
        data = await request.json()
        _telegram_events.append(data)
        if len(_telegram_events) > 100:
            _telegram_events.pop(0)
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    # Detect channel information from any event
    for key in ["channel_post", "my_chat_member", "chat_member", "chat_join_request"]:
        if key in data:
            chat = data[key].get("chat", {})
            if chat.get("id"):
                _last_channel_info = {
                    "chat_id": chat.get("id"),
                    "title": chat.get("title", ""),
                    "type": chat.get("type", "")
                }
                # Also auto sync to Firestore if possible
                try:
                    import requests as req
                    api_key = 'AIzaSyD_X9Rjy7d_l3Ar8VLJQ24tE0Sf_s4GxQQ'
                    patch_url = f'https://firestore.googleapis.com/v1/projects/novaedit-app-2026/databases/(default)/documents/app_config/settings?key={api_key}&updateMask.fieldPaths=telegramChannelId'
                    req.patch(patch_url, json={'fields': {'telegramChannelId': {'stringValue': str(chat.get('id'))}}}, timeout=3.0)
                except Exception:
                    pass

    # Handle chat join request (Auto-Approve Community Members)
    join_request = data.get("chat_join_request")
    if join_request and token:
        chat_id = join_request.get("chat", {}).get("id")
        user_id = join_request.get("from", {}).get("id")
        first_name = join_request.get("from", {}).get("first_name", "Movie Fan")
        if chat_id and user_id:
            # 1. Auto-approve join request
            await telegram_bot.approve_chat_join_request(token, chat_id, user_id)
            # 2. Send instant welcome message
            welcome_msg = (
                f"👋 Hello {first_name}!\n\n"
                "🎬 *Your join request to MovieTime Community has been Approved!* 🍿\n\n"
                "Watch 10,000+ Movies & Series in 1080p Ultra HD for free with Telugu, Hindi & English audio!\n\n"
                "🌐 *Stream Online:* https://movietime-app-2026.netlify.app\n"
                "📱 *Download Android APK:* https://movietime-app-2026.netlify.app/movietime.apk"
            )
            await telegram_bot.send_welcome_message(token, user_id, welcome_msg)
            return {"ok": True, "action": "approved", "user_id": user_id}

    # Handle group & direct messages (Welcome new members, Anti-Spam / Auto-Delete external links, Commands)
    message = data.get("message")
    if message and token:
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        text = message.get("text", "")

        # 1. Welcome new members joining the community
        new_members = message.get("new_chat_members", [])
        if new_members and chat_id:
            for member in new_members:
                if member.get("is_bot") and member.get("id") != 9232684763:
                    await telegram_bot.delete_message(token, chat_id, message_id)
                else:
                    first_name = member.get("first_name", "Movie Fan")
                    member_id = member.get("id")
                    await telegram_bot.send_group_welcome(token, chat_id, first_name, member_id)
            return {"ok": True, "action": "welcome_group_members"}

        # 2. Anti-Spam: Auto-delete unauthorized bots, links, and promotional messages
        is_spam, reason = telegram_bot.is_spam_or_promo(message)
        if is_spam and chat_id and message_id:
            await telegram_bot.delete_message(token, chat_id, message_id)
            sender = message.get("from", {})
            sender_name = sender.get("first_name", "User")
            warning = f"⚠️ *{sender_name}*, external links and promotional messages are automatically removed in MovieTime Community."
            await telegram_bot.send_welcome_message(token, chat_id, warning)
            return {"ok": True, "action": "deleted_spam", "reason": reason}

        # 3. Handle /update or /apk command
        if (text.startswith("/update") or text.startswith("/apk")) and chat_id:
            apk_info = (
                "🚀 *MovieTime v2.5.0 Release is Live!* 🎬\n\n"
                "• ⚡ *Instant Playback*: Decoupled edge CDNs start streaming in <1s.\n"
                "• 📲 *In-App 1-Click Updater*: Auto-downloads & installs directly on device.\n"
                "• 🎬 *Immersive Fullscreen*: Hidden system status and navigation bars.\n"
                "• 🍿 *Indian High-Speed CDN Mirrors*: No freezing on Haiku, Ranabaali, etc.\n\n"
                "📱 *Download APK (v2.5.0):* https://movietime-app-2026.netlify.app/movietime.apk\n"
                "🌐 *Watch Online:* https://movietime-app-2026.netlify.app"
            )
            await telegram_bot.send_welcome_message(token, chat_id, apk_info)
            return {"ok": True, "action": "apk_info_sent"}

        # 4. Handle /start command in direct message
        if text.startswith("/start") and chat_id:
            await telegram_bot.send_welcome_message(token, chat_id)
            return {"ok": True, "action": "welcome_sent"}

    return {"ok": True}

@app.post("/api/telegram/set_photo")
async def telegram_set_photo(request: Request):
    """Set channel avatar photo."""
    body = await request.json()
    token = body.get("token")
    channel_id = body.get("channel_id") or _last_channel_info.get("chat_id")
    image_base64 = body.get("image_base64")
    image_url = body.get("image_url")

    if not token or not channel_id:
        raise HTTPException(status_code=400, detail="token and channel_id are required")

    photo_bytes = None
    if image_base64:
        photo_bytes = base64.b64decode(image_base64)
    elif image_url:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(image_url)
            photo_bytes = resp.content

    if not photo_bytes:
        raise HTTPException(status_code=400, detail="No photo provided")

    res = await telegram_bot.set_chat_photo(token, channel_id, photo_bytes)
    return res

@app.post("/api/telegram/broadcast")
async def telegram_broadcast(request: Request):
    """Broadcast movie or announcement to Telegram channel."""
    body = await request.json()
    token = body.get("token")
    channel_id = body.get("channel_id") or _last_channel_info.get("chat_id")
    text = body.get("text", "")
    photo_url = body.get("photo_url")
    image_base64 = body.get("image_base64")

    if not token or not channel_id:
        raise HTTPException(status_code=400, detail="token and channel_id are required")

    if image_base64:
        photo_bytes = base64.b64decode(image_base64)
        res = await telegram_bot.send_photo_bytes(token, channel_id, photo_bytes, text)
        return res

    res = await telegram_bot.broadcast_to_channel(token, channel_id, text, photo_url)
    return res

LIVE_TV_CHANNELS = [
    {
        "id": "10tv-telugu",
        "name": "10 TV Telugu News",
        "language": "Telugu",
        "category": "Telugu",
        "logo": "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?w=160&h=160&fit=crop",
        "url": "https://mumbai-edge.smartplaytv.in/10TV/index.m3u8",
        "quality": "720p HD",
        "description": "24/7 Live Telugu News, breaking updates and discussions.",
    },
    {
        "id": "6tv-telugu",
        "name": "6 TV Telugu",
        "language": "Telugu",
        "category": "Telugu",
        "logo": "https://images.unsplash.com/photo-1594909122845-11baa439b7bf?w=160&h=160&fit=crop",
        "url": "https://mumbai-edge.smartplaytv.in/6TVTelugu/index.m3u8",
        "quality": "576p HD",
        "description": "Live Andhra Pradesh & Telangana regional news and current affairs.",
    },
    {
        "id": "99tv-telugu",
        "name": "99 TV Telugu",
        "language": "Telugu",
        "category": "Telugu",
        "logo": "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=160&h=160&fit=crop",
        "url": "https://cdn.pishow.tv/ott/live/1211/master.m3u8",
        "quality": "720p HD",
        "description": "Telugu 24x7 news and analytical debates.",
    },
    {
        "id": "aaj-tak",
        "name": "Aaj Tak HD",
        "language": "Hindi",
        "category": "News",
        "logo": "https://images.unsplash.com/photo-1586339949916-3e9457bef6d3?w=160&h=160&fit=crop",
        "url": "https://d1rc86nwwc9fag.cloudfront.net/vglive-sk-791258/master.m3u8",
        "quality": "720p HD",
        "description": "India's #1 Hindi news channel with 24/7 live national coverage.",
    },
    {
        "id": "9x-jalwa",
        "name": "9X Jalwa (Bollywood Hits)",
        "language": "Hindi",
        "category": "Music",
        "logo": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=160&h=160&fit=crop",
        "url": "https://wiselp.wiseplayout.com/9X_Jalwa/master.m3u8",
        "quality": "1080p Ultra HD",
        "description": "Non-stop 24/7 Bollywood musical blockbusters and evergreen hit songs.",
    },
    {
        "id": "9x-jhakaas",
        "name": "9X Jhakaas Hits",
        "language": "Hindi",
        "category": "Music",
        "logo": "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=160&h=160&fit=crop",
        "url": "https://amg01281-9xmediapvtltd-9xjhakaas-samsungin-ci2cs.amagi.tv/playlist/amg01281-9xmediapvtltd-9xjhakaas-samsungin/playlist.m3u8",
        "quality": "1080p Ultra HD",
        "description": "High-energy music videos and chartbusters in 1080p.",
    },
    {
        "id": "9x-tashan",
        "name": "9X Tashan (Desi Beats)",
        "language": "Hindi",
        "category": "Music",
        "logo": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=160&h=160&fit=crop",
        "url": "https://amg01281-9xmediapvtltd-9xtashan-samsungin-xz1sd.amagi.tv/playlist/amg01281-9xmediapvtltd-9xtashan-samsungin/playlist.m3u8",
        "quality": "1080p Ultra HD",
        "description": "Top party hits, Punjabi and desi chartbusters.",
    },
    {
        "id": "7s-music",
        "name": "7S Music Hits",
        "language": "Hindi",
        "category": "Music",
        "logo": "https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?w=160&h=160&fit=crop",
        "url": "https://cdn.pishow.tv/ott/live/1257/master.m3u8",
        "quality": "576p HD",
        "description": "All-time popular movie songs and music videos.",
    },
    {
        "id": "4tv-news",
        "name": "4TV News Live",
        "language": "Hindi",
        "category": "News",
        "logo": "https://images.unsplash.com/photo-1505373877841-8d25f7d46678?w=160&h=160&fit=crop",
        "url": "https://fta.ektv.in/live/647/master.m3u8",
        "quality": "576p HD",
        "description": "Live political coverage and national headline broadcasts.",
    },
    {
        "id": "24-news",
        "name": "24 News TV",
        "language": "Hindi",
        "category": "News",
        "logo": "https://images.unsplash.com/photo-1495020689067-958852a7765e?w=160&h=160&fit=crop",
        "url": "https://mumt07.tangotv.in/zHjX9OFlTWENTYFOURNEWS/index.m3u8",
        "quality": "576p HD",
        "description": "Live 24-hour news round-the-clock.",
    },
    {
        "id": "redbull-tv",
        "name": "Red Bull Sports & Racing Live",
        "language": "English",
        "category": "Sports",
        "logo": "https://images.unsplash.com/photo-1517649763962-0c623266ddc0?w=160&h=160&fit=crop",
        "url": "https://rbmn-live.akamaized.net/hls/live/590964/BoRB-AT/master.m3u8",
        "quality": "1080p Ultra HD",
        "description": "24/7 Action sports, extreme racing, F1 specials, and adrenaline adventures.",
    }
]

@app.get("/api/livetv")
@app.get("/livetv")
def get_live_tv():
    return {
        "categories": ["All", "Telugu", "News", "Music", "Sports"],
        "channels": LIVE_TV_CHANNELS
    }

@app.get("/movietime.apk")
async def download_apk():
    """Serve official MovieTime Android APK for direct high-speed download."""
    apk_path = os.path.join(os.path.dirname(__file__), "movietime.apk")
    if os.path.exists(apk_path):
        return FileResponse(
            apk_path,
            media_type="application/vnd.android.package-archive",
            filename="movietime.apk"
        )
    raise HTTPException(status_code=404, detail="APK file not found on server")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
