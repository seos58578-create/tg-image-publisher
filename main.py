import asyncio
import hashlib
import os
import sqlite3
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from telegram import Bot


# Railway 环境变量中配置这些值
SOURCE_URL = os.environ["SOURCE_URL"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

# 可选：每轮最多发布几张图片
MAX_IMAGES_PER_RUN = int(os.getenv("MAX_IMAGES_PER_RUN", "5"))

# 推荐在 Railway Volume 挂载路径 /data，数据库才能跨重启保留
DATA_DIR = os.getenv("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "posted.sqlite3")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ImagePublisher/1.0)"
}


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posted (
                image_hash TEXT PRIMARY KEY,
                image_url TEXT NOT NULL
            )
        """)


def get_images():
    response = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    found = []

    for img in soup.select("img"):
        # 有些网站将图片地址放在 data-src 或 srcset 中
        raw_url = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-original")
        )

        if not raw_url:
            srcset = img.get("srcset")
            if srcset:
                raw_url = srcset.split(",")[0].strip().split(" ")[0]

        if not raw_url:
            continue

        image_url = urljoin(SOURCE_URL, raw_url)
        parsed = urlparse(image_url)

        if parsed.scheme not in ("http", "https"):
            continue

        # 忽略常见的追踪像素和图标；可按目标网站调整
        if any(x in image_url.lower() for x in ("favicon", "avatar", "logo")):
            continue

        if image_url not in found:
            found.append(image_url)

    return found


def image_hash(image_url):
    return hashlib.sha256(image_url.encode("utf-8")).hexdigest()


def already_posted(image_url):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM posted WHERE image_hash = ?",
            (image_hash(image_url),),
        ).fetchone()
        return row is not None


def mark_posted(image_url):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posted (image_hash, image_url) VALUES (?, ?)",
            (image_hash(image_url), image_url),
        )


async def publish_images():
    bot = Bot(token=BOT_TOKEN)
    images = get_images()
    published = 0

    for image_url in images:
        if already_posted(image_url):
            continue

        try:
            # Telegram 可以直接读取公开可访问的图片 URL
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=image_url,
                caption=f"图片来源：{SOURCE_URL}",
            )
            mark_posted(image_url)
            published += 1

            if published >= MAX_IMAGES_PER_RUN:
                break

            # 控制发布速度，避免短时间大量发送
            await asyncio.sleep(2)

        except Exception as exc:
            print(f"发布失败：{image_url}；原因：{exc}")

    print(f"本轮完成，发布 {published} 张图片。")


async def main():
    init_db()

    # 每 30 分钟运行一次；可通过环境变量 INTERVAL_SECONDS 调整
    interval = int(os.getenv("INTERVAL_SECONDS", "1800"))

    while True:
        try:
            await publish_images()
        except Exception as exc:
            print(f"本轮采集失败：{exc}")

        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
