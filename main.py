import time
import os
import json
import re
import tempfile
import requests
from playwright.sync_api import sync_playwright


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")
STORAGE_STATE_JSON = os.getenv("STORAGE_STATE_JSON")

ACCOUNT_URL = "https://donor-mos.online/account/"
LOGIN_URL = "https://donor-mos.online/"
RUNTIME_STATE_FILE = "/tmp/donor_runtime_state.json"


def log(message):
    print(message, flush=True)


def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text},
        timeout=15
    )


def safe_close_popup(page):
    selectors = [
        'button:has-text("×")',
        'text=×',
        '[aria-label="Close"]',
        '[aria-label="Закрыть"]',
    ]

    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=700)
            page.wait_for_timeout(300)
            return
        except:
            pass

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except:
        pass


def normalize_spaces(text):
    return re.sub(r"\s+", " ", text).strip()


def page_shows_login_form(page):
    html = page.content().lower()
    return "авторизоваться" in html and "пароль" in html


def save_context_state(context):
    try:
        context.storage_state(path=RUNTIME_STATE_FILE)
        log("Runtime-сессия сохранена")
    except Exception as e:
        log(f"Не удалось сохранить runtime-сессию: {e}")


def seed_runtime_state_from_env():
    if os.path.exists(RUNTIME_STATE_FILE):
        return

    if not STORAGE_STATE_JSON:
        return

    try:
        state_data = json.loads(STORAGE_STATE_JSON)
        with open(RUNTIME_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False)
        log("Runtime-сессия инициализирована из STORAGE_STATE_JSON")
    except Exception as e:
        log(f"Не удалось инициализировать runtime-сессию из env: {e}")


def build_context_from_runtime(browser):
    if not os.path.exists(RUNTIME_STATE_FILE):
        return browser.new_context()

    return browser.new_context(storage_state=RUNTIME_STATE_FILE)


def login_and_refresh_session(context):
    if not LOGIN or not PASSWORD:
        raise RuntimeError("Нет LOGIN/PASSWORD для обновления сессии")

    page = context.new_page()
    page.goto(LOGIN_URL, timeout=60000)
    page.wait_for_timeout(1500)

    page.fill('input[type="text"]', LOGIN)
    page.fill('input[type="password"]', PASSWORD)
    page.click("button:has-text('Авторизоваться')")
    page.wait_for_timeout(4000)

    page.goto(ACCOUNT_URL, timeout=60000)
    page.wait_for_timeout(2000)

    if page_shows_login_form(page):
        raise RuntimeError("Логин не удался: сайт снова показывает форму входа")

    save_context_state(context)
    log("Сессия обновлена через LOGIN/PASSWORD")
    return page


def open_account_page(browser):
    """
    Пытаемся сначала зайти по runtime-сессии.
    Если она протухла, перелогиниваемся и обновляем runtime-сессию.
    """
    context = build_context_from_runtime(browser)
    page = context.new_page()

    page.goto(ACCOUNT_URL, timeout=60000)
    page.wait_for_timeout(1500)

    if page_shows_login_form(page):
        log("Сессия протухла, пробую перелогиниться...")
        context.close()

        context = browser.new_context()
        page = login_and_refresh_session(context)

    return context, page


def extract_date_for_button(button_locator, fallback_index):
    try:
        result = button_locator.evaluate(
            """
            (el) => {
                function normalize(s) {
                    return (s || "").replace(/\\s+/g, " ").trim();
                }

                function datesFrom(text) {
                    const m = text.match(/\\b\\d{2}\\/\\d{2}\\/\\d{4}\\b/g);
                    return m || [];
                }

                const buttonText = normalize(el.innerText || "Забронировать время");
                let node = el;

                while (node) {
                    const text = normalize(node.innerText || "");
                    const dates = datesFrom(text);

                    if (dates.length === 1) {
                        return dates[0];
                    }

                    if (dates.length > 1) {
                        const pos = text.indexOf(buttonText);
                        if (pos >= 0) {
                            const before = text.slice(0, pos);
                            const beforeDates = datesFrom(before);
                            if (beforeDates.length > 0) {
                                return beforeDates[beforeDates.length - 1];
                            }
                        }
                        return dates[dates.length - 1];
                    }

                    node = node.parentElement;
                }

                return null;
            }
            """
        )

        if result:
            return result
    except:
        pass

    return f"кнопка #{fallback_index + 1}"


def get_booking_buttons(page):
    return page.locator("text=Забронировать время")


def check_slots():
    available_dates = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            seed_runtime_state_from_env()
            context, page = open_account_page(browser)

            buttons = get_booking_buttons(page)
            count = buttons.count()

            log(f"Найдено кнопок: {count}")

            if count == 0:
                context.close()
                browser.close()
                return []

            for i in range(count):
                page.goto(ACCOUNT_URL, timeout=60000)
                page.wait_for_timeout(1200)

                if page_shows_login_form(page):
                    log("Во время проверки сессия слетела, перелогиниваюсь...")
                    context.close()
                    context = browser.new_context()
                    page = login_and_refresh_session(context)
                    page.goto(ACCOUNT_URL, timeout=60000)
                    page.wait_for_timeout(1200)

                buttons = get_booking_buttons(page)
                current_count = buttons.count()

                if i >= current_count:
                    continue

                button = buttons.nth(i)
                button_date = extract_date_for_button(button, i)

                log(f"Проверяю {button_date}")

                try:
                    button.click(timeout=4000)
                except Exception as e:
                    log(f"Не удалось кликнуть по {button_date}: {e}")
                    continue

                page.wait_for_timeout(1200)

                popup_text = page.content().lower()

                if "все свободные места для записи закончились" in popup_text:
                    log(f"{button_date}: мест нет")
                    safe_close_popup(page)
                    continue

                log(f"{button_date}: похоже, слот есть")
                available_dates.append(button_date)
                safe_close_popup(page)

            save_context_state(context)
            context.close()
            browser.close()
            return available_dates

        except Exception as e:
            log(f"Ошибка в check_slots: {e}")
            try:
                browser.close()
            except:
                pass
            return []


def make_alert_signature(dates):
    return "|".join(sorted(set(dates)))


def build_alert_text(dates):
    unique_dates = sorted(set(dates))

    if len(unique_dates) == 1:
        return (
            "🔥 Похоже, появился слот.\n\n"
            f"Дата: {unique_dates[0]}\n\n"
            "Срочно зайди в donor-mos и попробуй записаться."
        )

    dates_text = "\n".join(f"• {d}" for d in unique_dates)

    return (
        "🔥 Похоже, появились слоты на несколько дат.\n\n"
        f"{dates_text}\n\n"
        "Срочно зайди в donor-mos и попробуй записаться."
    )


log("БОТ 3.2 ЗАПУЩЕН")

last_alert_signature = None

while True:
    started_at = time.strftime("%H:%M:%S")
    log(f"Старт проверки: {started_at}")

    available_dates = check_slots()

    finished_at = time.strftime("%H:%M:%S")
    log(f"Конец проверки: {finished_at}")

    if available_dates:
        current_signature = make_alert_signature(available_dates)
        log(f"Найдены доступные даты: {', '.join(sorted(set(available_dates)))}")

        if current_signature != last_alert_signature:
            send_message(build_alert_text(available_dates))
            last_alert_signature = current_signature
            log("Отправлено уведомление в Telegram")
        else:
            log("Слоты есть, но уведомление уже отправлялось ранее")
    else:
        log("Слотов не найдено")
        last_alert_signature = None

    log("Жду 30 секунд до следующей проверки...")
    time.sleep(30)
