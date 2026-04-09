import time
import os
from playwright.sync_api import sync_playwright


LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")


def log(msg):
    print(msg, flush=True)


def safe_close_popup(page):
    selectors = [
        'button:has-text("×")',
        'text=×',
        '[aria-label="Close"]',
        '[aria-label="Закрыть"]',
    ]

    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=1000)
            page.wait_for_timeout(700)
            return True
        except:
            pass

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
        return True
    except:
        pass

    return False


def extract_block_text(page, index):
    try:
        button = page.locator("text=Забронировать время").nth(index)
        container = button.locator("xpath=ancestor::div[1]")
        return container.inner_text(timeout=2000)
    except:
        return None


def analyze_popup_text(text):
    text = text.lower()

    no_slots_markers = [
        "все свободные места для записи закончились",
        "на данный момент все свободные места для записи закончились",
    ]

    success_markers = [
        "выберите время",
        "доступное время",
        "подтвердите, что вы не робот",
        "я не робот",
        "captcha",
        "капча",
        "recaptcha",
    ]

    if any(marker in text for marker in no_slots_markers):
        return "NO_SLOTS"

    if any(marker in text for marker in success_markers):
        return "REAL_SLOT"

    return "UNKNOWN"


def run_debug_check():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            log("=== START DEBUG CHECK ===")

            page.goto("https://donor-mos.online/", timeout=60000)
            page.wait_for_timeout(2000)
            log("Открыта главная страница")

            page.fill('input[type="text"]', LOGIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click("button:has-text('Авторизоваться')")
            page.wait_for_timeout(5000)
            log("Попытка авторизации выполнена")

            page.goto("https://donor-mos.online/account/", timeout=60000)
            page.wait_for_timeout(4000)
            log("Открыт личный кабинет")

            page_text = page.content().lower()
            if "авторизоваться" in page_text and "пароль" in page_text:
                log("Похоже, авторизация не удалась: снова видна форма входа")
                browser.close()
                return

            buttons = page.locator("text=Забронировать время")
            count = buttons.count()
            log(f"Найдено кнопок 'Забронировать время': {count}")

            if count == 0:
                log("Кнопок не найдено, проверка завершена")
                browser.close()
                return

            for i in range(count):
                log("")
                log(f"--- Проверка кнопки #{i + 1} ---")

                page.goto("https://donor-mos.online/account/", timeout=60000)
                page.wait_for_timeout(3000)

                buttons = page.locator("text=Забронировать время")
                current_count = buttons.count()
                log(f"После обновления страницы доступно кнопок: {current_count}")

                if i >= current_count:
                    log("Эта кнопка исчезла после обновления страницы, пропускаю")
                    continue

                block_text = extract_block_text(page, i)
                if block_text:
                    log("Текст блока рядом с кнопкой:")
                    log(block_text[:1000])
                else:
                    log("Не удалось вытащить текст блока")

                try:
                    buttons.nth(i).click(timeout=5000)
                    log("Клик по кнопке выполнен")
                except Exception as e:
                    log(f"Не удалось кликнуть по кнопке: {e}")
                    continue

                page.wait_for_timeout(3000)

                popup_text = page.content()
                result = analyze_popup_text(popup_text)

                log(f"Результат анализа после клика: {result}")

                popup_lower = popup_text.lower()

                interesting_markers = [
                    "все свободные места для записи закончились",
                    "выберите время",
                    "доступное время",
                    "подтвердите, что вы не робот",
                    "я не робот",
                    "captcha",
                    "капча",
                    "recaptcha",
                ]

                found = [m for m in interesting_markers if m in popup_lower]
                if found:
                    log(f"Найдены маркеры: {found}")
                else:
                    log("Явные маркеры не найдены")

                log("Фрагмент HTML/текста после клика:")
                snippet = popup_text.replace("\n", " ")
                log(snippet[:1500])

                closed = safe_close_popup(page)
                log(f"Попытка закрыть попап: {'успешно' if closed else 'не удалось / не требовалось'}")

            log("")
            log("=== DEBUG CHECK FINISHED ===")
            browser.close()

        except Exception as e:
            log(f"ОШИБКА: {e}")
            try:
                browser.close()
            except:
                pass


print("DEBUG BOT STARTED", flush=True)

while True:
    run_debug_check()
    print("Жду 60 секунд до следующей проверки...", flush=True)
    time.sleep(60)
