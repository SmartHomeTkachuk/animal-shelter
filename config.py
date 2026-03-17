import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "8372764453:AAHzNe7odoWW9TmenBorXZ8o61EQf-2nyNU")

# 👤 ID администраторов (можно узнать у @userinfobot)
ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "8343963611").split(",") if id]

# 🗄️ Путь к файлу базы данных SQLite
DB_PATH = os.getenv("DB_PATH", "shelter.db")

# 📄 Количество объявлений на одной странице
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 10))

# 🐾 Типы животных (используются по умолчанию)
DEFAULT_ANIMAL_TYPES = ['Собака', 'Кошка', 'Птица', 'Грызун', 'Другое']

# Для парсера сайта приюта "Друг"
SHELTER_URL = "https://priyut-drug.ru/"
SHELTER_USER_ID = 2  # ID пользователя в БД, от чьего имени будут публиковаться объявления