import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

CHANNELS_FILE = "channels.txt"
PLAYLIST_FILE = "tv.m3u8"
TIMEOUT_MS = 30000
WAIT_AFTER_LOAD_MS = 8000


def load_channels():
    channels = {}
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, url = line.split("|", 1)
            channels[name.strip()] = url.strip()
    return channels


def try_click_play(page):
    """Пытается кликнуть по <video>, если оно есть. Ничего не печатает."""
    clicked = page.evaluate("""
        () => {
            const v = document.querySelector('video');
            if (v) {
                v.click();
                return true;
            }
            return false;
        }
    """)
    if clicked:
        return True

    for sel in ["video", "button[aria-label*='play' i]", ".vjs-big-play-button"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def intercept_m3u8(context, channel_url):
    captured = None
    # Открываем отдельную вкладку для каждого канала
    page = context.new_page()

    def handle_response(response):
        nonlocal captured
        if captured:
            return
        url = response.url
        content_type = response.headers.get("content-type", "")
        if response.status == 200 and (
            ".m3u8" in url or "mpegurl" in content_type
        ):
            captured = url

    page.on("response", handle_response)
    try:
        page.goto(channel_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        
        page.wait_for_timeout(3000)

        if not captured:
            try:
                page.wait_for_selector("video", timeout=3000)
            except PlaywrightTimeout:
                pass
            try_click_play(page)

        waited = 0
        while not captured and waited < WAIT_AFTER_LOAD_MS:
            page.wait_for_timeout(500)
            waited += 500

    except PlaywrightTimeout:
        print(f"  Таймаут при загрузке {channel_url}", file=sys.stderr)
    finally:
        page.remove_listener("response", handle_response)
        page.close() # Закрываем вкладку после проверки

    return captured


def update_playlist(updates):
    """Обновляет URL у существующих каналов в playlist.m3u8 по названию (tvg-name)."""
    path = Path(PLAYLIST_FILE)
    if not path.exists():
        print(f"Плейлист {PLAYLIST_FILE} не найден, пропускаю обновление.", file=sys.stderr)
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    current_name = None
    updated_count = 0

    for line in lines:
        if line.startswith("#EXTINF"):
            m = re.search(r'tvg-name="([^"]+)"', line)
            if m:
                current_name = m.group(1).strip()
            else:
                current_name = line.rsplit(",", 1)[-1].strip()
            out.append(line)
        elif line.startswith("http") and current_name and current_name in updates:
            out.append(updates[current_name])
            updated_count += 1
            current_name = None
        else:
            out.append(line)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Обновлено каналов в плейлисте: {updated_count}")


def main():
    channels = load_channels()
    updates = {}
    used_urls = set() # Защита от дублей

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        for name, url in channels.items():
            print(f"Обработка: {name}")
            # Передаем context, чтобы функция сама открыла и закрыла вкладку
            m3u8 = intercept_m3u8(context, url)
            
            if m3u8:
                if m3u8 in used_urls:
                    print(f"  ⚠️ ОШИБКА: Найдена ссылка-дубликат. Пропускаем, чтобы не испортить плейлист.", file=sys.stderr)
                else:
                    updates[name] = m3u8
                    used_urls.add(m3u8)
                    print(f"  Найдена ссылка: {m3u8}")
            else:
                print(f"  Ссылка не перехвачена", file=sys.stderr)

        browser.close()

    if updates:
        update_playlist(updates)
    else:
        print("Нет обновлений для записи.", file=sys.stderr)


if __name__ == "__main__":
    main()
