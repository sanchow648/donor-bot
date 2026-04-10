import time
import os
import json
import re
import requests
import multiprocessing
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOGIN = os.getenv("LOGIN")
PASSWORD = os.getenv("PASSWORD")
STORAGE_STATE_JSON = os.getenv("STORAGE_STATE_JSON")

ACCOUNT_URL = "https://donor-mos.online/account/"
LOGIN_URL = "https://donor-mos.online/"
RUNTIME_STATE_FILE = "/tmp/donor_runtime_state.json"

CHECK_TIMEOUT_SECONDS = 120
SLEEP_BETWEEN_CHECKS_SECONDS = 30

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
HEARTBEAT_HOURS = {11, 23}


def now_moscow():
    return datetime.now(MOSCOW_TZ)


def log(message):
    print(message, flush=True)


def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text},
        timeout=15
    )


def safe_close_popup(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


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

    if STORAGE_STATE_JSON:
        try:
            state_data = json.loads(STORAGE_STATE_JSON)
            with open(RUNTIME_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False)
            log("Runtime-сессия восстановлена из STORAGE_STATE_JSON")
        except Exception as e:
            log(f"Не удалось восстановить runtime-сессию: {e}")


def login_and_refresh_session(context):
    if not LOGIN or not PASSWORD:
        raise RuntimeError("Не заданы LOGIN/PASSWORD")

    log("🔐 логинюсь")
    page = context.new_page()
    page.set_default_timeout(20000)

    page.goto(LOGIN_URL, timeout=60000)
    page.wait_for_timeout(1500)

    page.fill('input[type="text"]', LOGIN)
    page.fill('input[type="password"]', PASSWORD)
    page.click("button:has-text('Авторизоваться')")
    page.wait_for_timeout(5000)

    page.goto(ACCOUNT_URL, timeout=60000)
    page.wait_for_timeout(2500)

    if page_shows_login_form(page):
        raise RuntimeError("После логина сайт всё ещё показывает форму входа")

    save_context_state(context)
    log("✅ залогинился, сессия обновлена")
    return page


def open_account_page(browser):
    log("🌐 открываю account")

    if os.path.exists(RUNTIME_STATE_FILE):
        context = browser.new_context(storage_state=RUNTIME_STATE_FILE)
        log("Использую runtime-сессию")
    else:
        context = browser.new_context()
        log("Runtime-сессии нет, открою чистый контекст")

    page = context.new_page()
    page.set_default_timeout(20000)

    page.goto(ACCOUNT_URL, timeout=60000)
    page.wait_for_timeout(2000)

    if page_shows_login_form(page):
        log("Сессия протухла → логинюсь заново")
        context.close()
        context = browser.new_context()
        page = login_and_refresh_session(context)

    return context, page


def get_booking_buttons(page):
    return page.locator("text=Забронировать время")


def extract_date(button, i):
    try:
        text = button.locator("xpath=ancestor::*[1]").inner_text(timeout=3000)
        match = re.findall(r"\d{2}/\d{2}/\d{4}", text)
        if match:
            return match[-1]
    except Exception:
        pass
    return f"кнопка #{i + 1}"


def _check_worker(queue):
    dates = []

    try:
        log("▶️ старт воркера")
        seed_runtime_state_from_env()

        with sync_playwright() as p:
            log("🚀 запускаю браузер")
            browser = p.chromium.launch(headless=True)

            context, page = open_account_page(browser)

            log("📄 читаю список кнопок")
            buttons = get_booking_buttons(page)
            count = buttons.count()

            log(f"Найдено кнопок: {count}")

            for i in range(count):
                log(f"🔄 обновляю страницу перед кнопкой #{i + 1}")
                page.goto(ACCOUNT_URL, timeout=60000)
                page.wait_for_timeout(1500)

                if page_shows_login_form(page):
                    log("Во время проверки сессия слетела → перелогиниваюсь")
                    context.close()
                    context = browser.new_context()
                    page = login_and_refresh_session(context)
                    page.set_default_timeout(20000)
                    page.goto(ACCOUNT_URL, timeout=60000)
                    page.wait_for_timeout(1500)

                buttons = get_booking_buttons(page)
                current_count = buttons.count()

                if i >= current_count:
                    log(f"Кнопка #{i + 1} исчезла после обновления")
                    continue

                button = buttons.nth(i)
                date = extract_date(button, i)

                log(f"Проверяю {date}")

                try:
                    button.click(timeout=5000)
                except Exception as e:
                    log(f"Не удалось кликнуть по {date}: {e}")
                    continue

                page.wait_for_timeout(1500)

                html = page.content().lower()

                if "все свободные места для записи закончились" in html:
                    log(f"{date}: мест нет")
                else:
                    log(f"{date}: слот есть")
                    dates.append(date)

                safe_close_popup(page)

            save_context_state(context)
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

            queue.put({"ok": True, "dates": dates})

    except Exception as e:
        queue.put({"ok": False, "error": str(e), "dates": []})


def run_check():
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_check_worker, args=(queue,))
    process.start()
    process.join(CHECK_TIMEOUT_SECONDS)

    if process.is_alive():
        process.terminate()
        process.join()
        return {"ok": False, "timeout": True, "dates": []}

    if queue.empty():
        return {"ok": False, "error": "Процесс завершился без результата", "dates": []}

    return queue.get()


def build_alert(dates):
    unique_dates = sorted(set(dates))
    return "🔥 СЛОТЫ!\n" + "\n".join(unique_dates)


def build_heartbeat(last_time, ok, dates):
    status = "✅ Бот работает" if ok else "⚠️ Бот работает, но последняя проверка была с ошибкой"

    if dates:
        slots = "Даты: " + ", ".join(sorted(set(dates)))
    else:
        slots = "Слотов нет"

    return f"{status}\n\nПоследняя проверка: {last_time}\n{slots}"


if __name__ == "__main__":
    log("БОТ 3.6 ЗАПУЩЕН")

    last_alert = None
    last_heartbeat_key = None

    last_ok = True
    last_dates = []
    last_time = ""

    while True:
        now = now_moscow()

        log(f"Старт проверки: {now.strftime('%H:%M:%S')}")

        result = run_check()

        last_time = now_moscow().strftime("%H:%M:%S")

        if result.get("timeout"):
            log("Завис → убит")
            last_ok = False
        elif not result.get("ok"):
            log(f"Ошибка: {result.get('error', 'неизвестная ошибка')}")
            last_ok = False
        else:
            last_ok = True

        dates = result.get("dates", [])
        last_dates = dates

        if dates:
            sig = "|".join(sorted(set(dates)))
            if sig != last_alert:
                send_message(build_alert(dates))
                last_alert = sig
                log("Отправлено уведомление о слотах")
            else:
                log("Слоты есть, но уведомление уже отправлялось")
        else:
            log("Слотов не найдено")
            last_alert = None

        current_hour = now.hour
        current_day = now.strftime("%Y-%m-%d")
        heartbeat_key = f"{current_day}-{current_hour}"

        if current_hour in HEARTBEAT_HOURS and last_heartbeat_key != heartbeat_key:
            send_message(build_heartbeat(last_time, last_ok, last_dates))
            last_heartbeat_key = heartbeat_key
            log("Отправлен heartbeat")

        log(f"Жду {SLEEP_BETWEEN_CHECKS_SECONDS} секунд...\n")
        time.sleep(SLEEP_BETWEEN_CHECKS_SECONDS)
