"""Import publicly available photos from an Avito listing."""
import asyncio
import base64
import json
import re
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
LISTING_HOSTS = {"avito.ru", "www.avito.ru", "m.avito.ru"}
IMAGE_HOST = re.compile(r"(?:[a-z0-9-]+\.)*(?:img|cdn)\.avito\.st", re.ASCII)
MAX_PHOTOS = 5


class ListingImportError(ValueError):
    pass


def allowed_url(value, *, listing):
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or "\\" in value or any(ord(char) < 32 for char in value)):
            return None
        if listing:
            if host not in LISTING_HOSTS or not re.search(r"_\d+/?$", parts.path, re.ASCII):
                return None
        elif not IMAGE_HOST.fullmatch(host):
            return None
        return urlunsplit(("https", host, parts.path, "" if listing else parts.query, ""))
    except (ValueError, AttributeError):
        return None


class ListingPhotos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.schema = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property") in {"og:image", "og:image:url", "og:image:secure_url"}:
            self.images.append(attrs.get("content"))
        if tag == "img" and (
            attrs.get("itemprop") == "image" or "gallery" in (attrs.get("data-marker") or "")
        ):
            self.images.append(attrs.get("data-src") or attrs.get("src"))
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.schema = []

    def handle_data(self, text):
        if self.schema is not None:
            self.schema.append(text)

    def handle_endtag(self, tag):
        if tag != "script" or self.schema is None:
            return
        raw = "".join(self.schema)
        self.schema = None
        try:
            pending = [json.loads(raw)]
        except (ValueError, RecursionError):
            return
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(reversed(item))
            elif isinstance(item, dict):
                for key in ("@graph", "mainEntity"):
                    if key in item:
                        pending.append(item[key])
                images = item.get("image", [])
                for image in images if isinstance(images, list) else [images]:
                    self.images.append((image.get("contentUrl") or image.get("url")) if isinstance(image, dict) else image)


async def download(client, url, *, listing, max_bytes):
    for _ in range(4):
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                target = response.headers.get("location")
                url = allowed_url(urljoin(url, target), listing=listing) if target else None
                if url is None:
                    break
                continue
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime not in ({"text/html", "application/xhtml+xml"} if listing else PHOTO_TYPES):
                raise ListingImportError("Неподдерживаемый формат ответа Авито")
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ListingImportError("Ответ Авито превышает допустимый размер")
            return bytes(data), mime
    raise ListingImportError("Авито перенаправило запрос за пределы объявления или галереи")


def is_photo(data, mime):
    return {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }[mime]


async def import_listing_photos(value):
    url = allowed_url(value, listing=True)
    if url is None:
        raise ListingImportError("Вставьте прямую HTTPS-ссылку на объявление Авито")
    photos, seen = [], set()
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(timeout=6, follow_redirects=False) as client:
            html, _ = await download(client, url, listing=True, max_bytes=5 * 1024 * 1024)
            parser = ListingPhotos()
            parser.feed(html.decode("utf-8", errors="replace"))
            urls = dict.fromkeys(filter(None, (allowed_url(src, listing=False) for src in parser.images if isinstance(src, str))))
            for image_url in list(urls)[:10]:
                try:
                    data, mime = await download(client, image_url, listing=False, max_bytes=3 * 1024 * 1024)
                except (httpx.HTTPError, ListingImportError):
                    continue
                digest = sha256(data).digest()
                if len(data) < 10 * 1024 or not is_photo(data, mime) or digest in seen:
                    continue
                seen.add(digest)
                photos.append({
                    "name": f"avito-{len(photos) + 1}.{PHOTO_TYPES[mime]}",
                    "mime": mime,
                    "base64": base64.b64encode(data).decode("ascii"),
                })
                if len(photos) == MAX_PHOTOS:
                    break
    except (httpx.HTTPError, TimeoutError):
        pass
    if photos:
        return photos
    raise ListingImportError("Не удалось получить фото с Авито. Сохраните фото из объявления и загрузите их вручную.")
