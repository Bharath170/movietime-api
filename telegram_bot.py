import httpx
from typing import Optional, Dict, Any

TELEGRAM_API_BASE = "https://api.telegram.org"

async def get_bot_info(bot_token: str) -> Dict[str, Any]:
    """Verify bot token and return bot username and status."""
    if not bot_token:
        return {"ok": False, "error": "Bot token is missing"}
    
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/getMe"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def approve_chat_join_request(bot_token: str, chat_id: str | int, user_id: str | int) -> Dict[str, Any]:
    """Auto-approve user's request to join private channel or group."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/approveChatJoinRequest"
    payload = {"chat_id": chat_id, "user_id": user_id}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def send_welcome_message(bot_token: str, user_id: str | int, custom_msg: Optional[str] = None) -> Dict[str, Any]:
    """Send direct welcome message to the approved user with website and APK links."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    default_text = (
        "🎬 *Welcome to MovieTime Official Community!* 🍿\n\n"
        "Stream 10,000+ Movies & Series in 1080p Ultra HD with Telugu, Hindi & English audio!\n\n"
        "🌐 *Watch Online:* [movietime-app-2026.netlify.app](https://movietime-app-2026.netlify.app)\n"
        "📱 *Download Android APK:* [Download Now](https://movietime-app-2026.netlify.app/movietime.apk)\n\n"
        "Enjoy zero buffering, fast downloads, and daily new releases!"
    )
    payload = {
        "chat_id": user_id,
        "text": custom_msg or default_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "🌐 Open MovieTime Web", "url": "https://movietime-app-2026.netlify.app"},
                    {"text": "📱 Download APK", "url": "https://movietime-app-2026.netlify.app/movietime.apk"}
                ]
            ]
        }
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def broadcast_to_channel(
    bot_token: str,
    channel_id: str | int,
    text: str,
    photo_url: Optional[str] = None
) -> Dict[str, Any]:
    """Broadcast an announcement or movie release to the channel."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "▶ Watch in 1080p", "url": "https://movietime-app-2026.netlify.app"},
                        {"text": "📥 Download App", "url": "https://movietime-app-2026.netlify.app/movietime.apk"}
                    ]
                ]
            }
            if photo_url:
                url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto"
                payload = {
                    "chat_id": channel_id,
                    "photo": photo_url,
                    "caption": text,
                    "parse_mode": "Markdown",
                    "reply_markup": reply_markup
                }
                resp = await client.post(url, json=payload)
                return resp.json()
            else:
                url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": channel_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": reply_markup
                }
                resp = await client.post(url, json=payload)
                return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def set_chat_photo(
    bot_token: str,
    channel_id: str | int,
    photo_bytes: bytes,
    filename: str = "channel_logo.jpg"
) -> Dict[str, Any]:
    """Update channel profile photo / avatar."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/setChatPhoto"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            files = {"photo": (filename, photo_bytes, "image/jpeg")}
            data = {"chat_id": str(channel_id)}
            resp = await client.post(url, data=data, files=files)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def send_photo_bytes(
    bot_token: str,
    channel_id: str | int,
    photo_bytes: bytes,
    caption: str,
    filename: str = "announcement.jpg"
) -> Dict[str, Any]:
    """Upload and broadcast a photo with caption directly to the channel."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto"
    import json
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "🎬 Open MovieTime Web", "url": "https://movietime-app-2026.netlify.app"},
                {"text": "📱 Download Android App", "url": "https://movietime-app-2026.netlify.app/movietime.apk"}
            ]
        ]
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            files = {"photo": (filename, photo_bytes, "image/jpeg")}
            data = {
                "chat_id": str(channel_id),
                "caption": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup)
            }
            resp = await client.post(url, data=data, files=files)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}
