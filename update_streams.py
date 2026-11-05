import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

CHANNELS_FILE = "channels.txt"
PLAYLIST_FILE = "tv.m3u8"
TIMEOUT_MS = 30000
WAIT_AFTER_LOAD_MS = 8000

# Здесь мы указываем ID, которые должны быть в ссылке. 
# Если сайт отдаст другой ID, скрипт попробует перезагрузить страницу.
EXPECTED_IDS = {
    "Мультиландия": "2045",
    "Nick Jr.": "210",
    "Viju+ Serial": "2033"  # укажите правильный ID, если знаете
}

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
    clicked = page.evaluate("""
        () => {
            const v = document.querySelector('video');
            if (v) { v.click(); return true; }
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


def get_m3u8_from_page(context, channel_url):
    """Одна попытка открыть страницу и поймать ссылку."""
    captured = None
    page = context.new_page()

    def handle_response(response):
        nonlocal captured
        if captured:
            return
        url = response.url
        content_type = response.headers.get("content-type", "")
        if response.status == 200 and (".m3u8" in url or "mpegurl" in content_type):
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
        pass
    finally:
        page.remove_listener("response", handle_response)
        page.close()

    return captured


def intercept_m3u8(context, channel_name, channel_url):
    """Пытается получить правильную ссылку, делая несколько попыток при ошибке."""
    expected_id = EXPECTED_IDS.get(channel_name)
    
    # Делаем до 3 попыток
    for attempt in range(1, 4):
        m3u8 = get_m3u8_from_page(context, channel_url)
        
        if not m3u8:
            print(f"  Попытка {attempt}: ссылка не найдена.", file=sys.stderr)
            continue
            
        # Если для канала указан ожидаемый ID, проверяем, совпадает ли он
        if expected_id:
            # Извлекаем ID канала из ссылки (обычно это число перед номером сессии)
            # Пример: .../2045/1793908609/index.m3u8
            match = re.search(r'/(\d+)/\d+/index\.m3u8', m3u8)
            if match:
                actual_id = match.group(1)
                if actual_id == expected_id:
                    print(f"  Попытка {attempt}: найдена ПРАВИЛЬНАЯ ссылка (ID {actual_id}).")
                    return m3u8
                else:
                    print(f"  Попытка {attempt}: сайт отдал чужой ID ({actual_id} вместо {expected_id}). Перезагружаем...", file=sys.stderr)
                    continue
            else:
                # Если не удалось распарсить ID, просто возвращаем то, что есть
                return m3u8
        else:
            # Если ID не задан, возвращаем первую попавшуюся
            return m3u8

    print(f"  Не удалось получить правильную ссылку за 3 попытки.", file=sys.stderr)
    return None


def update_playlist(updates):
    path = Path(PLAYLIST_FILE)
    if not path.exists():
        print(f"Плейлист {PLAYLIST_FILE} не найден.", file=sys.stderr)
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
            m3u8 = intercept_m3u8(context, name, url)
            
            if m3u8:
                updates[name] = m3u8
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
