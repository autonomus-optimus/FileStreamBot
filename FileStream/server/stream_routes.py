import time
import math
import logging
import mimetypes
import traceback
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from FileStream.bot import multi_clients, work_loads, FileStream
from FileStream.config import Telegram, Server
from FileStream.server.exceptions import FIleNotFound, InvalidHash
from FileStream import utils, StartTime, __version__
from FileStream.utils.render_template import render_page
from typing import Dict, Any
import asyncio

routes = web.RouteTableDef()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Cache for ByteStreamer objects
class_cache: Dict[int, Any] = {}

# Lock for thread-safe access to shared resources
cache_lock = asyncio.Lock()

@routes.get("/status", allow_head=True)
async def root_route_handler(_):
    """Handler for the /status endpoint."""
    return web.json_response(
        {
            "server_status": "running",
            "uptime": utils.get_readable_time(time.time() - StartTime),
            "telegram_bot": "@" + FileStream.username,
            "connected_bots": len(multi_clients),
            "loads": {
                f"bot{c + 1}": l
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            },
            "version": __version__,
        }
    )

@routes.get("/watch/{path}", allow_head=True)
async def stream_handler(request: web.Request):
    """Handler for the /watch/{path} endpoint."""
    try:
        path = request.match_info["path"]
        return web.Response(text=await render_page(path), content_type="text/html")
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except Exception as e:
        logger.error(f"Error in /watch handler: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text="Internal Server Error")

@routes.get("/dl/{path}", allow_head=True)
async def download_handler(request: web.Request):
    """Handler for the /dl/{path} endpoint."""
    try:
        path = request.match_info["path"]
        return await media_streamer(request, path)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except Exception as e:
        logger.error(f"Error in /dl handler: {e}", exc_info=True)
        raise web.HTTPInternalServerError(text="Internal Server Error")

async def media_streamer(request: web.Request, db_id: str) -> web.Response:
    """Stream media files in chunks."""
    range_header = request.headers.get("Range", 0)
    
    # Select the client with the least workload
    async with cache_lock:
        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        
        if Telegram.MULTI_CLIENT:
            logger.info(f"Client {index} is now serving {request.headers.get('X-FORWARDED-FOR', request.remote)}")

        # Use cached ByteStreamer or create a new one
        if faster_client in class_cache:
            tg_connect = class_cache[faster_client]
            logger.debug(f"Using cached ByteStreamer object for client {index}")
        else:
            logger.debug(f"Creating new ByteStreamer object for client {index}")
            tg_connect = utils.ByteStreamer(faster_client)
            class_cache[faster_client] = tg_connect

    # Get file properties
    file_id = await tg_connect.get_file_properties(db_id, multi_clients)
    file_size = file_id.file_size

    # Handle range requests
    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    # Validate range
    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            text="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Calculate chunk size and offset
    chunk_size = 1024 * 1024  # 1 MB
    until_bytes = min(until_bytes, file_size - 1)
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1
    req_length = until_bytes - from_bytes + 1

    # Stream the file in chunks
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, math.ceil(req_length / chunk_size), chunk_size
    )

    # Determine MIME type and file name
    mime_type = file_id.mime_type or mimetypes.guess_type(file_id.file_name)[0] or "application/octet-stream"
    file_name = utils.get_name(file_id)
    disposition = "attachment"  # Default to download

    # Uncomment to allow inline playback for media files
    # if "video/" in mime_type or "audio/" in mime_type:
    #     disposition = "inline"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": mime_type,
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )
