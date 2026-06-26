from __future__ import annotations

import sys
import json
import asyncio
import logging
from pathlib import Path
import urllib.request
import urllib.parse
import tempfile

from app.config import Settings
from app.db.repo import Database
from app.db.models import MediaKind, CropMode
from app.services.media_service import MediaService
from app.services.pack_service import PackService
from app.services.telegram_sticker_api import TelegramStickerApi
from aiogram import Bot

# Direct logs to stderr so that stdout remains clean for JSON-RPC messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("mcp_server")


class McpServer:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.db: Database | None = None
        self.bot: Bot | None = None
        self.tg_api: TelegramStickerApi | None = None
        self.pack_service: PackService | None = None
        self.media_service: MediaService | None = None
        self.klipy_service = None

    async def initialize(self) -> None:
        logger.info("Initializing MCP Server services...")
        self.settings = Settings.from_env()
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)

        self.db = Database(self.settings.db_path)
        await self.db.connect()
        await self.db.initialize()

        self.bot = Bot(token=self.settings.bot_token)
        me = await self.bot.get_me()
        logger.info(f"Connected to bot: @{me.username}")

        self.tg_api = TelegramStickerApi(self.bot)
        self.pack_service = PackService(db=self.db, tg_api=self.tg_api, bot_username=me.username)
        self.media_service = MediaService(temp_dir=self.settings.temp_dir)

        from app.services.klipy_service import KlipyService
        self.klipy_service = KlipyService(
            api_key=self.settings.klipy_api_key,
            client_key=self.settings.klipy_client_key,
            locale=self.settings.klipy_locale,
            country=self.settings.klipy_country,
            content_filter=self.settings.klipy_content_filter,
        )
        logger.info("MCP Server initialized successfully.")

    async def close(self) -> None:
        logger.info("Closing MCP Server...")
        if self.db:
            await self.db.close()
        if self.bot:
            await self.bot.session.close()
        logger.info("MCP Server closed.")

    async def list_packs(self) -> list[dict]:
        if not self.db:
            raise RuntimeError("Database not initialized")

        rows = await self.db._fetchall(
            """
            SELECT p.id, p.title, p.short_name, p.tg_set_name, p.status, p.is_active, p.user_id, u.tg_user_id, u.username_lc
            FROM packs p
            LEFT JOIN users u ON p.user_id = u.id
            ORDER BY p.updated_at DESC, p.id DESC
            """,
            ()
        )
        packs_list = []
        for r in rows:
            packs_list.append({
                "id": r["id"],
                "title": r["title"],
                "short_name": r["short_name"],
                "tg_set_name": r["tg_set_name"] or "",
                "status": r["status"],
                "is_active": bool(r["is_active"]),
                "owner_tg_user_id": r["tg_user_id"],
                "owner_username": r["username_lc"] or ""
            })
        return packs_list

    async def search_stickers(self, query: str, limit: int = 10) -> list[dict]:
        # 1. Search Tenor (prioritized)
        tenor_results = await self.search_tenor(query, limit)
        if tenor_results:
            return tenor_results

        # 2. Fallback to Klipy
        if self.klipy_service:
            klipy_results = await self.search_klipy(query, limit)
            return klipy_results

        return []

    async def search_tenor(self, query: str, limit: int) -> list[dict]:
        api_key = "AIzaSyCZt6SSh5VgVPzD9fhyzG1DprdPRhtoaR4"  # Publicly embedded Tenor Web Client API Key
        client_key = "tenor_web"
        params = {
            "q": query,
            "key": api_key,
            "client_key": client_key,
            "limit": str(limit),
            "media_filter": "gif,tinygif,mp4,tinymp4"
        }
        url = f"https://tenor.googleapis.com/v2/search?{urllib.parse.urlencode(params)}"

        def _fetch():
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            data = await asyncio.to_thread(_fetch)
            results = []
            for res in data.get("results", []):
                media = res.get("media_formats", {})

                # Prefer MP4/webm for video stickers if available, otherwise GIF
                mp4_url = media.get("mp4", {}).get("url")
                gif_url = media.get("gif", {}).get("url")
                tinygif_url = media.get("tinygif", {}).get("url")

                url = mp4_url or gif_url
                if not url:
                    continue

                title = res.get("title") or res.get("content_description") or query
                results.append({
                    "url": url,
                    "thumbnail_url": tinygif_url or gif_url or mp4_url,
                    "title": title,
                    "media_kind": "video",
                    "source": "tenor"
                })
            return results
        except Exception as e:
            logger.error(f"Tenor search failed: {e}", exc_info=True)
            return []

    async def search_klipy(self, query: str, limit: int) -> list[dict]:
        try:
            res = await self.klipy_service.search_inline_gifs(query)
            results = []
            for gif in res.results[:limit]:
                results.append({
                    "url": gif.mpeg4_url,
                    "thumbnail_url": gif.thumbnail_url,
                    "title": query,
                    "media_kind": "video",
                    "source": "klipy"
                })
            return results
        except Exception as e:
            logger.error(f"Klipy search failed: {e}", exc_info=True)
            return []

    async def add_sticker(self, pack_id: int, media_url: str, emoji: str, crop_mode: str = "fit") -> dict:
        if not self.db or not self.pack_service or not self.media_service:
            raise RuntimeError("MCP Services not initialized")

        # Get pack owner
        row = await self.db._fetchone(
            """
            SELECT p.id, p.title, p.short_name, p.tg_set_name, p.status, p.is_active, p.user_id, u.tg_user_id, u.username_lc
            FROM packs p
            LEFT JOIN users u ON p.user_id = u.id
            WHERE p.id = ?
            LIMIT 1
            """,
            (pack_id,)
        )
        if not row:
            raise ValueError(f"Pack with ID {pack_id} not found")

        owner_tg_user_id = row["tg_user_id"]
        username_lc = row["username_lc"]

        # Activate this pack for the owner/editor so that add_processed_sticker works
        activated = await self.pack_service.activate_pack(tg_user_id=owner_tg_user_id, pack_id=pack_id, username=username_lc)
        if not activated:
            raise RuntimeError(f"Failed to activate pack {pack_id}")

        # Download the file to a temp directory
        temp_dir = Path(tempfile.mkdtemp(dir=self.settings.temp_dir))
        input_path = temp_dir / "downloaded_temp"

        def _download():
            req = urllib.request.Request(media_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                with open(input_path, "wb") as f:
                    f.write(response.read())
                return content_type

        try:
            content_type = await asyncio.to_thread(_download)
        except Exception as e:
            MediaService.cleanup_job_dir(temp_dir)
            raise RuntimeError(f"Failed to download media: {e}")

        # Infer media kind
        suffix = Path(urllib.parse.urlparse(media_url).path).suffix
        media_kind = MediaService.infer_media_kind(content_type, suffix or "file.mp4")
        if not media_kind:
            media_kind = MediaKind.VIDEO

        # Convert/process using MediaService
        job_dir = Path(tempfile.mkdtemp(dir=self.settings.temp_dir))
        crop_mode_enum = CropMode.SQUARE if crop_mode == "square" else CropMode.FIT

        try:
            if media_kind == MediaKind.IMAGE:
                processed = self.media_service.process_image(input_path, job_dir, crop_mode_enum)
            else:
                processed = self.media_service.process_video(input_path, job_dir, crop_mode_enum)

            # Add sticker via PackService
            pack = await self.pack_service.add_processed_sticker(
                tg_user_id=owner_tg_user_id,
                media_kind=processed.media_kind,
                sticker_path=processed.path,
                emoji=emoji,
                username=username_lc
            )

            # Compute hash and write to db
            def _hash_file(path: Path) -> str:
                import hashlib
                h = hashlib.sha256()
                with path.open("rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
                return h.hexdigest()

            source_hash = _hash_file(processed.path)
            await self.db.add_sticker_record(
                pack_id=pack.id,
                media_kind=processed.media_kind,
                emoji=emoji,
                telegram_file_id=None,
                source_hash=source_hash,
            )

            link = f"https://t.me/addstickers/{pack.tg_set_name}" if pack.tg_set_name else ""
            return {
                "success": True,
                "message": f"Sticker successfully added to pack '{pack.title}' under emoji '{emoji}'",
                "pack_link": link,
                "pack_title": pack.title
            }
        except Exception as e:
            logger.error(f"Error processing or adding sticker: {e}", exc_info=True)
            raise
        finally:
            MediaService.cleanup_job_dir(job_dir)
            MediaService.cleanup_job_dir(temp_dir)

    async def handle_request(self, request_str: str) -> str | None:
        try:
            req = json.loads(request_str)
        except Exception as e:
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None
            })

        if isinstance(req, list):
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Batch requests not supported"},
                "id": None
            })

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            await self.initialize()
            return json.dumps({
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "sticker-bot-mcp",
                        "version": "0.1.0"
                    }
                },
                "id": req_id
            })

        elif method == "notifications/initialized":
            return None

        elif method == "tools/list":
            return json.dumps({
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {
                            "name": "search_stickers",
                            "description": "Search for media (images, GIFs, videos) on Tenor (prioritized) and Klipy to add as stickers.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search term (e.g. 'cat', 'marin kitagawa jumping')"
                                    },
                                    "limit": {
                                        "type": "integer",
                                        "description": "Maximum number of stickers to return (default 10)"
                                    }
                                },
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "list_packs",
                            "description": "List all sticker packs in the database, with their IDs, names, status, and active status.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {}
                            }
                        },
                        {
                            "name": "add_sticker",
                            "description": "Download a media file from a URL, convert/crop it to Telegram sticker requirements, and add it to the specified sticker pack.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "pack_id": {
                                        "type": "integer",
                                        "description": "The ID of the pack from the database (defaults to 4)."
                                    },
                                    "media_url": {
                                        "type": "string",
                                        "description": "The direct URL of the media file (image, GIF, or video) to download and add."
                                    },
                                    "emoji": {
                                        "type": "string",
                                        "description": "The emoji(s) to associate with the sticker (e.g. '🐱', '🔥')."
                                    },
                                    "crop_mode": {
                                        "type": "string",
                                        "enum": ["fit", "square"],
                                        "description": "How to crop/resize the media (default 'fit')."
                                    }
                                },
                                "required": ["media_url", "emoji"]
                            }
                        }
                    ]
                },
                "id": req_id
            })

        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                if name == "search_stickers":
                    query = arguments.get("query")
                    limit = int(arguments.get("limit", 10))
                    res = await self.search_stickers(query, limit)
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(res, indent=2, ensure_ascii=False)
                                }
                            ]
                        },
                        "id": req_id
                    })
                elif name == "list_packs":
                    res = await self.list_packs()
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(res, indent=2, ensure_ascii=False)
                                }
                            ]
                        },
                        "id": req_id
                    })
                elif name == "add_sticker":
                    pack_id = int(arguments.get("pack_id", 4))
                    media_url = arguments.get("media_url")
                    emoji = arguments.get("emoji")
                    crop_mode = arguments.get("crop_mode", "fit")
                    res = await self.add_sticker(pack_id, media_url, emoji, crop_mode)
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(res, indent=2, ensure_ascii=False)
                                }
                            ]
                        },
                        "id": req_id
                    })
                else:
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": f"Method not found: {name}"},
                        "id": req_id
                    })
            except Exception as e:
                logger.error(f"Error executing tool {name}: {e}", exc_info=True)
                return json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": req_id
                })

        else:
            return json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": req_id
            })


async def run_server() -> None:
    server = McpServer()
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            response = await server.handle_request(line)
            if response:
                sys.stdout.write(response + "\n")
                sys.stdout.flush()
    except Exception as e:
        logger.error(f"Server loop error: {e}", exc_info=True)
    finally:
        await server.close()


if __name__ == "__main__":
    asyncio.run(run_server())
