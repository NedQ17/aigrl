# db_manager.py
import sqlite3
from config import DB_NAME, DAILY_LIMIT # <-- DAILY_LIMIT должен быть здесь
from datetime import datetime, date, timedelta

def init_db():
    """Создает необходимые таблицы в SQLite."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS limits (
            user_id INTEGER PRIMARY KEY,
            date TEXT,
            count INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            start_date TEXT,
            end_date TEXT
        )
    """)

    conn.commit()
    conn.close()


def is_user_subscribed(user_id):
    """Проверяет, активна ли подписка у пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT end_date FROM subscriptions WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        end_date = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        return end_date > datetime.now()
    return False


def activate_subscription(user_id, duration_days=30):
    """Активирует или продлевает подписку на N дней."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT end_date FROM subscriptions WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    now = datetime.now()

    if result and datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S') > now:
        start_from = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
    else:
        start_from = now

    new_end = start_from + timedelta(days=duration_days)
    cursor.execute(
        "INSERT OR REPLACE INTO subscriptions (user_id, start_date, end_date) VALUES (?, ?, ?)",
        (user_id, now.strftime('%Y-%m-%d %H:%M:%S'), new_end.strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id, limit=5):
    """Возвращает последние N сообщений."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    history_raw = cursor.fetchall()
    conn.close()

    history = [{"role": row[0], "content": row[1]} for row in reversed(history_raw)]
    return history


def save_message(user_id, role, content):
    """Сохраняет сообщение в историю."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()
    conn.close()


def check_and_increment_limit(user_id, daily_limit):
    """Проверяет и инкрементирует дневной лимит."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = date.today().isoformat()

    cursor.execute("SELECT count, date FROM limits WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result and result[1] == today:
        current_count = result[0]
        # 1. Если счетчик уже достиг лимита, возвращаем False
        if current_count >= daily_limit: # Изменено на ">=" для строгости
            conn.close()
            return False
        
        # 2. Инкрементируем существующий счетчик
        cursor.execute(
            "UPDATE limits SET count = count + 1 WHERE user_id = ?",
            (user_id,) # Убираем date, так как она уже today
        )
    else:
        # 3. Нет записи или запись за прошлый день: сбрасываем счетчик, но сразу инкрементируем до 1
        current_count = 0
        # Если 0 < daily_limit, то разрешаем сообщение и устанавливаем счетчик в 1
        if 1 <= daily_limit:
            cursor.execute(
                "INSERT OR REPLACE INTO limits (user_id, date, count) VALUES (?, ?, ?)",
                (user_id, today, 1) # Устанавливаем count = 1 (уже инкрементировали)
            )
        else: # Лимит 0 (маловероятно, но на всякий случай)
            conn.close()
            return False

    conn.commit()
    conn.close()
    return True


def increase_limit(user_id, count_to_add):
    """Сбрасывает часть счетчика, effectively добавляя лимит."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = date.today().isoformat()

    cursor.execute("SELECT count, date FROM limits WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result and result[1] == today:
        current_count = result[0]
    else:
        # Если записи нет или она за прошлый день, устанавливаем счетчик в 0
        current_count = 0

    # Разрешаем счетчику быть отрицательным. 
    # Если было 50 (лимит), а добавили 100, станет 50 - 100 = -50.
    # Это даст пользователю 100 сообщений, прежде чем счетчик достигнет 50 снова.
    new_count = current_count - count_to_add # ИСПРАВЛЕНО

    cursor.execute(
        "INSERT OR REPLACE INTO limits (user_id, date, count) VALUES (?, ?, ?)",
        (user_id, today, new_count)
    )
    conn.commit()
    conn.close()


def clear_user_history(user_id):
    """Удаляет всю историю сообщений пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    print(f"[DEBUG] История сообщений пользователя {user_id} успешно очищена.")


def get_user_status(user_id):
    """
    Возвращает статус подписки (дни до конца или None) 
    и оставшееся количество сообщений на сегодня.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.now()

    # 1. Получаем статус подписки
    cursor.execute("SELECT end_date FROM subscriptions WHERE user_id = ?", (user_id,))
    sub_result = cursor.fetchone()

    days_left = None
    if sub_result:
        # Используем формат, который сохраняет activate_subscription
        sub_end_date_str = sub_result[0] 
        # Используем '%Y-%m-%d %H:%M:%S' так как в activate_subscription нет микросекунд
        sub_end_date = datetime.strptime(sub_end_date_str, '%Y-%m-%d %H:%M:%S')

        if sub_end_date > today:
            time_left = sub_end_date - today
            # Добавляем 1, чтобы округлить до целого дня
            days_left = time_left.days + 1 

    # 2. Получаем оставшиеся сообщения
    cursor.execute("SELECT count, date FROM limits WHERE user_id = ?", (user_id,))
    limit_result = cursor.fetchone()

    current_count = 0

    # Проверяем, что запись актуальна (сегодняшняя)
    if limit_result and limit_result[1] == date.today().isoformat():
        current_count = limit_result[0]

    messages_left = None

    if days_left is not None and days_left > 0:
        messages_left = "∞ (Безлимит)"
    else:
        # Если счетчик отрицательный (после покупки), остаток = DAILY_LIMIT + abs(count)
        # Если счетчик положительный (после использования), остаток = DAILY_LIMIT - count
        messages_left_count = DAILY_LIMIT - current_count
        messages_left = max(0, messages_left_count)

        if messages_left_count > DAILY_LIMIT:
             messages_left = messages_left_count

    conn.close()
    return days_left, messages_left