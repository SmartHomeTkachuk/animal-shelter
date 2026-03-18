import telebot
from telebot import apihelper
import sqlite3
from telebot import types
import config
import re
import time
from contextlib import contextmanager

# Устанавливаем максимальные таймауты (в секундах)
apihelper.READ_TIMEOUT = 120
apihelper.CONNECT_TIMEOUT = 120
apihelper.RETRY_ON_TIMEOUT = True

# ---------- Инициализация ----------
bot = telebot.TeleBot(config.BOT_TOKEN)
ADMIN_IDS = config.ADMIN_IDS
DB_PATH = config.DB_PATH
PAGE_SIZE = config.PAGE_SIZE

# Временное хранилище данных при создании объявления
user_temp = {}

# ---------- Умный помощник (AI) ----------
def analyze_animal_description(text):
    """Простой анализ текста на основе ключевых слов (без внешних API)"""
    text_lower = text.lower()
    result = {
        'animal_type': 'Другое',
        'urgency': 'Обычное',
        'keywords': []
    }
    
    animal_keywords = {
        'Собака': ['собак', 'пёс', 'пса', 'щен', 'лабрадор', 'овчарк', 'дворняг', 'хаски'],
        'Кошка': ['кошк', 'кот', 'котён', 'котик', 'кошка'],
        'Птица': ['попуга', 'птиц', 'ворон', 'голуб', 'сова'],
        'Грызун': ['хомяк', 'крыс', 'морск', 'свинк', 'шиншилл']
    }
    
    for animal, words in animal_keywords.items():
        if any(word in text_lower for word in words):
            result['animal_type'] = animal
            result['keywords'] = [w for w in words if w in text_lower]
            break
    
    urgent_words = ['срочн', 'помогит', 'спас', 'беда', 'улиц', 'замерз', 'брошен']
    if any(word in text_lower for word in urgent_words):
        result['urgency'] = 'Срочно!'
    
    return result

# ---------- Работа с БД ----------
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def upgrade_db():
    """Добавляет новые поля в таблицу ads, если их нет"""
    with get_db() as conn:
        cur = conn.cursor()
        # Проверяем наличие колонки category
        cur.execute("PRAGMA table_info(ads)")
        columns = [col[1] for col in cur.fetchall()]
        if 'category' not in columns:
            cur.execute("ALTER TABLE ads ADD COLUMN category TEXT DEFAULT 'lost'")
            print("✅ Добавлена колонка category")
        if 'photo_file_id' not in columns:
            cur.execute("ALTER TABLE ads ADD COLUMN photo_file_id TEXT")
            print("✅ Добавлена колонка photo_file_id")

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            city TEXT NOT NULL,
            phone TEXT,
            animal_type TEXT DEFAULT 'Другое',
            category TEXT DEFAULT 'lost',
            photo_file_id TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            ad_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, ad_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (ad_id) REFERENCES ads (id)
        )''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            category TEXT DEFAULT 'Общие'
        )''')
        
        cursor.execute('SELECT COUNT(*) FROM faq')
        if cursor.fetchone()[0] == 0:
            faqs = [
                ('Как взять животное?', 'Заполните анкету и свяжитесь с приютом.', 'Общие'),
                ('Какие нужны документы?', 'Паспорт и документы на жилье.', 'Документы'),
                ('Можно ли вернуть животное?', 'Да, в течение 14 дней.', 'Возврат'),
                ('Как помочь приюту?', 'Деньгами, кормом или волонтерством.', 'Помощь'),
            ]
            cursor.executemany('INSERT INTO faq (question, answer, category) VALUES (?, ?, ?)', faqs)
    
    # Вызываем обновление структуры (на случай старых баз)
    upgrade_db()

# ---------- Клавиатуры ----------
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton('🔍 Найти пропавшее животное'),
        types.KeyboardButton('🏠 Взять животное из приюта'),
        types.KeyboardButton('📝 Добавить объявление'),
        types.KeyboardButton('⭐ Избранное'),
        types.KeyboardButton('📋 Мои объявления'),
        types.KeyboardButton('❓ FAQ'),
        types.KeyboardButton('📞 Контакты')
    )
    return markup

# ---------- Команды ----------
@bot.message_handler(commands=['start'])
def start(message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                       (message.from_user.id, message.from_user.username,
                        message.from_user.first_name, message.from_user.last_name))
    bot.send_message(
        message.chat.id,
        "🏠 *Добро пожаловать в бот 'Приют по всей России'!*\n\n"
        "🐾 *Новые возможности:*\n"
        "• Отдельный поиск пропавших животных и питомцев из приюта\n"
        "• Удобные карточки с фото и счётчиком\n"
        "• Фильтр по типу животного\n\n"
        "Выберите действие:",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

# ---------- Показ карточки объявления ----------
def show_card(chat_id, category, filter_animal, index):
    """
    category: 'lost' или 'shelter'
    filter_animal: конкретный тип или None (для всех)
    index: порядковый номер (0-based)
    """
    with get_db() as conn:
        cur = conn.cursor()
        # Базовый запрос
        query = "SELECT * FROM ads WHERE is_active = 1 AND category = ?"
        params = [category]
        if filter_animal and filter_animal != 'Все':
            query += " AND animal_type = ?"
            params.append(filter_animal)
        
        # Получаем общее количество
        cur.execute(f"SELECT COUNT(*) FROM ({query})", params)
        total = cur.fetchone()[0]
        if total == 0:
            bot.send_message(chat_id, "😔 Нет объявлений в этой категории.")
            return
        
        # Корректируем индекс
        if index < 0:
            index = 0
        if index >= total:
            index = total - 1
        
        # Получаем одно объявление со смещением
        query += " ORDER BY created_at DESC LIMIT 1 OFFSET ?"
        cur.execute(query, params + [index])
        ad = cur.fetchone()
    
    if not ad:
        return
    
    # Формируем подпись
    caption = f"*{ad['title']}*\n\n"
    caption += f"📍 *Город:* {ad['city']}\n"
    caption += f"🐾 *Тип:* {ad['animal_type']}\n"
    if ad['phone']:
        caption += f"📞 *Телефон:* {ad['phone']}\n"
    caption += f"\n*Описание:*\n{ad['description']}\n\n"
    caption += f"📅 *Дата:* {ad['created_at'][:10]}\n"
    caption += f"\n*Объявление {index+1} из {total}*"
    
    # Клавиатура навигации
    markup = types.InlineKeyboardMarkup(row_width=3)
    nav_buttons = []
    if index > 0:
        nav_buttons.append(types.InlineKeyboardButton("◀️", callback_data=f"card_{category}_{filter_animal}_{index-1}"))
    nav_buttons.append(types.InlineKeyboardButton(f"{index+1}/{total}", callback_data="noop"))
    if index < total-1:
        nav_buttons.append(types.InlineKeyboardButton("▶️", callback_data=f"card_{category}_{filter_animal}_{index+1}"))
    markup.add(*nav_buttons)
    
    # Кнопка фильтра
    filter_btn = types.InlineKeyboardButton("🔍 Фильтр", callback_data=f"filter_{category}_{filter_animal}_{index}")
    markup.add(filter_btn)
    
    # Отправка
    if ad['photo_file_id']:
        bot.send_photo(chat_id, ad['photo_file_id'], caption=caption, parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, caption, parse_mode='Markdown', reply_markup=markup)

# ---------- Обработчики новых кнопок ----------
@bot.message_handler(func=lambda message: message.text == '🔍 Найти пропавшее животное')
def lost_animals(message):
    show_card(message.chat.id, 'lost', None, 0)

@bot.message_handler(func=lambda message: message.text == '🏠 Взять животное из приюта')
def shelter_animals(message):
    show_card(message.chat.id, 'shelter', None, 0)

# ---------- Навигация по карточкам ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith('card_'))
def card_navigation(call):
    parts = call.data.split('_')
    # формат: card_{category}_{filter}_{index}
    category = parts[1]
    filter_animal = parts[2] if parts[2] != 'None' else None
    index = int(parts[3])
    # Обновляем сообщение (удаляем старое и отправляем новое)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    show_card(call.message.chat.id, category, filter_animal, index)

# ---------- Фильтр ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith('filter_'))
def filter_menu(call):
    parts = call.data.split('_')
    category = parts[1]
    filter_animal = parts[2] if parts[2] != 'None' else None
    index = int(parts[3])
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    animal_types = ['Все', 'Собака', 'Кошка', 'Птица', 'Грызун', 'Другое']
    for at in animal_types:
        callback_data = f"applyfilter_{category}_{at}_{index}"
        markup.add(types.InlineKeyboardButton(at, callback_data=callback_data))
    
    bot.edit_message_text(
        "Выберите тип животного для фильтрации:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('applyfilter_'))
def apply_filter(call):
    parts = call.data.split('_')
    category = parts[1]
    filter_animal = parts[2]
    index = int(parts[3])
    # Если выбран "Все", передаём None
    if filter_animal == 'Все':
        filter_animal = None
    bot.delete_message(call.message.chat.id, call.message.message_id)
    show_card(call.message.chat.id, category, filter_animal, 0)

# ---------- Добавление объявления (с фото и выбором категории) ----------
@bot.message_handler(func=lambda message: message.text == '📝 Добавить объявление')
def add_advertisement_start(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🐾 Я нашел животное на улице (пропажа)", callback_data="add_lost"))
    markup.add(types.InlineKeyboardButton("🏠 Я хочу найти дом для животного (приют)", callback_data="add_shelter"))
    bot.send_message(message.chat.id, "Выберите цель объявления:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ['add_lost', 'add_shelter'])
def choose_category(call):
    chat_id = call.message.chat.id
    category = 'lost' if call.data == 'add_lost' else 'shelter'
    user_temp[chat_id] = {'category': category}
    bot.edit_message_text(
        "Отправьте фотографию животного (обязательно).",
        chat_id=chat_id,
        message_id=call.message.message_id
    )
    # Регистрируем следующий шаг для получения фото
    bot.register_next_step_handler_by_chat_id(chat_id, handle_photo)

def handle_photo(message):
    chat_id = message.chat.id
    if chat_id not in user_temp:
        bot.send_message(chat_id, "❌ Ошибка: начните добавление заново.", reply_markup=main_menu())
        return
    
    if message.content_type != 'photo':
        bot.send_message(chat_id, "❌ Пожалуйста, отправьте фотографию.")
        bot.register_next_step_handler_by_chat_id(chat_id, handle_photo)
        return
    
    # Берём file_id самого большого фото
    file_id = message.photo[-1].file_id
    user_temp[chat_id]['photo'] = file_id
    
    bot.send_message(
        chat_id,
        "📝 Теперь отправьте информацию о животном в следующем формате:\n\n"
        "*Заголовок*\n"
        "*Город*\n"
        "*Тип животного* (Собака/Кошка/Птица/Грызун/Другое)\n"
        "*Описание*\n"
        "*Телефон* (необязательно)\n\n"
        "*Пример:*\n"
        "Ищет дом лабрадор\n"
        "Москва\n"
        "Собака\n"
        "Добрый лабрадор Елисей 2 года, привит, ищет добрые руки\n"
        "+79161234567",
        parse_mode='Markdown'
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_advertisement_with_photo)

def process_advertisement_with_photo(message):
    chat_id = message.chat.id
    if chat_id not in user_temp:
        bot.send_message(chat_id, "❌ Ошибка: начните добавление заново.", reply_markup=main_menu())
        return
    
    try:
        lines = message.text.split('\n')
        if len(lines) < 4:
            bot.send_message(chat_id, "❌ Нужно заполнить все поля: Заголовок, Город, Тип, Описание")
            bot.register_next_step_handler_by_chat_id(chat_id, process_advertisement_with_photo)
            return
        
        title = lines[0].strip()[:100]
        city = lines[1].strip()[:50]
        animal_type = lines[2].strip()[:30]
        description = lines[3].strip()[:1000]
        phone = lines[4].strip() if len(lines) > 4 else None
        
        if not title or not city or not animal_type or not description:
            bot.send_message(chat_id, "❌ Поля не могут быть пустыми")
            bot.register_next_step_handler_by_chat_id(chat_id, process_advertisement_with_photo)
            return
        
        # Используем ИИ для уточнения типа
        if animal_type not in config.DEFAULT_ANIMAL_TYPES:
            ai_analysis = analyze_animal_description(description)
            if ai_analysis['animal_type'] != 'Другое':
                animal_type = ai_analysis['animal_type']
                bot.send_message(chat_id, f"🔍 Я определил тип животного как *{animal_type}*", parse_mode='Markdown')
            else:
                animal_type = 'Другое'
        
        # Получаем данные из временного хранилища
        category = user_temp[chat_id]['category']
        photo_file_id = user_temp[chat_id]['photo']
        
        with get_db() as conn:
            cursor = conn.cursor()
            # Получаем или создаём пользователя
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (message.from_user.id,))
            user = cursor.fetchone()
            if not user:
                cursor.execute('INSERT INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                               (message.from_user.id, message.from_user.username,
                                message.from_user.first_name, message.from_user.last_name))
                user_id = cursor.lastrowid
            else:
                user_id = user[0]
            
            cursor.execute('''
                INSERT INTO ads (user_id, title, description, city, phone, animal_type, category, photo_file_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, title, description, city, phone, animal_type, category, photo_file_id))
            ad_id = cursor.lastrowid
        
        # Очищаем временные данные
        del user_temp[chat_id]
        
        bot.send_message(
            chat_id,
            f"✅ *Объявление успешно создано!*\n\n"
            f"*ID:* {ad_id}\n"
            f"*Заголовок:* {title}\n"
            f"*Город:* {city}\n"
            f"*Тип:* {animal_type}\n"
            f"*Категория:* {'Пропажа' if category=='lost' else 'Приют'}\n\n"
            f"Теперь его могут видеть другие пользователи.",
            parse_mode='Markdown',
            reply_markup=main_menu()
        )
    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
        # Очищаем временные данные при ошибке
        if chat_id in user_temp:
            del user_temp[chat_id]

# ---------- Мои объявления ----------
@bot.message_handler(func=lambda message: message.text == '📋 Мои объявления')
def my_ads(message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (message.from_user.id,))
        user = cursor.fetchone()
        if not user:
            bot.send_message(message.chat.id, "Сначала нажмите /start")
            return
        user_id = user[0]
        
        cursor.execute('SELECT * FROM ads WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        ads = cursor.fetchall()
    
    if not ads:
        bot.send_message(message.chat.id, "📭 У вас пока нет объявлений.")
        return
    
    for ad in ads:
        ad_id, user_id, title, description, city, phone, animal_type, category, photo_file_id, is_active, created_at = ad
        status = "✅ Активно" if is_active else "❌ Неактивно"
        cat_rus = "Пропажа" if category == 'lost' else "Приют"
        text = f"*{title}* ({status})\n📍 *Город:* {city}\n🐾 *Тип:* {animal_type}\n📂 *Категория:* {cat_rus}\n"
        if phone:
            text += f"📞 *Телефон:* {phone}\n"
        text += f"\n*Описание:*\n{description}\n\n📅 *Дата:* {created_at[:10]}\n🆔 *ID:* {ad_id}"
        
        markup = types.InlineKeyboardMarkup()
        if is_active:
            markup.add(types.InlineKeyboardButton("❌ Деактивировать", callback_data=f"deactivate_{ad_id}"))
        else:
            markup.add(types.InlineKeyboardButton("✅ Активировать", callback_data=f"activate_{ad_id}"))
        
        if photo_file_id:
            bot.send_photo(message.chat.id, photo_file_id, caption=text, parse_mode='Markdown', reply_markup=markup)
        else:
            bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=markup)
    
    bot.send_message(message.chat.id, f"📊 Всего объявлений: {len(ads)}")

# ---------- Избранное ----------
@bot.message_handler(func=lambda message: message.text == '⭐ Избранное')
def show_favorites(message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (message.from_user.id,))
        user = cursor.fetchone()
        if not user:
            bot.send_message(message.chat.id, "Сначала нажмите /start")
            return
        user_id = user[0]
        
        cursor.execute('''
            SELECT a.*, u.first_name FROM ads a
            JOIN favorites f ON a.id = f.ad_id
            LEFT JOIN users u ON a.user_id = u.id
            WHERE f.user_id = ? AND a.is_active = 1
            ORDER BY f.created_at DESC
        ''', (user_id,))
        ads = cursor.fetchall()
    
    if not ads:
        bot.send_message(message.chat.id, "⭐ У вас пока нет избранных объявлений.")
        return
    
    for ad in ads:
        ad_id, user_id, title, description, city, phone, animal_type, category, photo_file_id, is_active, created_at, first_name = ad
        cat_rus = "Пропажа" if category == 'lost' else "Приют"
        text = f"*{title}*\n📍 *Город:* {city}\n🐾 *Тип:* {animal_type}\n📂 *Категория:* {cat_rus}\n👤 *Автор:* {first_name or 'Неизвестно'}\n"
        if phone:
            text += f"📞 *Телефон:* {phone}\n"
        text += f"\n*Описание:*\n{description[:200]}...\n\n📅 *Дата:* {created_at[:10]}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ Убрать", callback_data=f"unfav_{ad_id}"))
        
        if photo_file_id:
            bot.send_photo(message.chat.id, photo_file_id, caption=text, parse_mode='Markdown', reply_markup=markup)
        else:
            bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=markup)

# ---------- FAQ ----------
@bot.message_handler(func=lambda message: message.text == '❓ FAQ')
def show_faq(message):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT category FROM faq')
        categories = cursor.fetchall()
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    for cat in categories:
        markup.add(types.InlineKeyboardButton(cat[0], callback_data=f"faq_{cat[0]}"))
    
    bot.send_message(message.chat.id, "❓ *Часто задаваемые вопросы*\n\nВыберите категорию:",
                     parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('faq_'))
def show_faq_category(call):
    category = call.data.split('_', 1)[1]
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT question, answer FROM faq WHERE category = ?', (category,))
        faqs = cursor.fetchall()
    
    text = f"*{category}*\n\n"
    for i, (q, a) in enumerate(faqs, 1):
        text += f"{i}. *{q}*\n{a}\n\n"
    
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                          parse_mode='Markdown')

# ---------- Контакты ----------
@bot.message_handler(func=lambda message: message.text == '📞 Контакты')
def show_contacts(message):
    text = """
🐕 <b>Проверенные приюты России</b>

📍 <b>Москва и область:</b>
• Приют "Зеленоград"
  Адрес: г. Зеленоград, Фирсановское ш., вл. 5А
  Телефон заведующего: 8 909 918-59-24 (будни 8:00-17:00)
  Посещение: ежедневно 13:00-17:00, кроме пн, пт
  С собой: паспорт РФ

📍 <b>Ленинградская область:</b>
• Фонд "Галкино Подворье"
  Адрес: Ленинградская обл, Всеволожский р-н, село Павлово, ул Быкова, д 54
  Телефон: +7 (911) 270-60-66 (13:00-18:00)
  Email: galkino_podvorjeps@mail.ru
  Сайт: galkinopodvorje.com
  Приют: ~60 собак и 18 кошек, всегда нужны волонтеры

📍 <b>Экстренная помощь:</b>
• Единый телефон спасения для животных: 112 (скажите, что животное в беде)
• Ветеринарная помощь: ищите ближайшую госветстанцию в вашем городе

⏰ <b>Хотите помочь?</b>
Волонтеры нужны всегда: выгул, фото, транспорт, передержка. Звоните в приюты!
"""
    bot.send_message(
        message.chat.id,
        text,
        parse_mode='HTML',
        disable_web_page_preview=True
    )

# ---------- Callback обработчики (избранное, активация) ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith(('fav_', 'unfav_', 'contact_', 'deactivate_', 'activate_', 'noop')))
def misc_callbacks(call):
    if call.data.startswith('fav_'):
        ad_id = int(call.data.split('_')[1])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (call.from_user.id,))
            user = cursor.fetchone()
            if user:
                try:
                    cursor.execute('INSERT INTO favorites (user_id, ad_id) VALUES (?, ?)', (user[0], ad_id))
                    bot.answer_callback_query(call.id, "✅ Добавлено в избранное")
                except sqlite3.IntegrityError:
                    bot.answer_callback_query(call.id, "ℹ️ Уже в избранном")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
    
    elif call.data.startswith('unfav_'):
        ad_id = int(call.data.split('_')[1])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE telegram_id = ?', (call.from_user.id,))
            user = cursor.fetchone()
            if user:
                cursor.execute('DELETE FROM favorites WHERE user_id = ? AND ad_id = ?', (user[0], ad_id))
                bot.answer_callback_query(call.id, "✅ Убрано из избранного")
                bot.delete_message(call.message.chat.id, call.message.message_id)
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
    
    elif call.data.startswith('contact_'):
        ad_id = int(call.data.split('_')[1])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT phone FROM ads WHERE id = ?', (ad_id,))
            result = cursor.fetchone()
        if result and result[0]:
            bot.send_message(call.message.chat.id, f"📞 Телефон: {result[0]}")
        else:
            bot.send_message(call.message.chat.id, "📞 Контакт не указан")
        bot.answer_callback_query(call.id)
    
    elif call.data.startswith('deactivate_'):
        ad_id = int(call.data.split('_')[1])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE ads SET is_active = 0 WHERE id = ?', (ad_id,))
        bot.answer_callback_query(call.id, "❌ Объявление деактивировано")
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("✅ Активировать", callback_data=f"activate_{ad_id}")
            )
        )
    
    elif call.data.startswith('activate_'):
        ad_id = int(call.data.split('_')[1])
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE ads SET is_active = 1 WHERE id = ?', (ad_id,))
        bot.answer_callback_query(call.id, "✅ Объявление активировано")
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("❌ Деактивировать", callback_data=f"deactivate_{ad_id}")
            )
        )
    
    elif call.data == "noop":
        bot.answer_callback_query(call.id)

# ---------- Админ-команды ----------
@bot.message_handler(commands=['stats'])
def stats(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    with get_db() as conn:
        cursor = conn.cursor()
        users = cursor.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        ads = cursor.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
        active_ads = cursor.execute('SELECT COUNT(*) FROM ads WHERE is_active = 1').fetchone()[0]
        lost = cursor.execute("SELECT COUNT(*) FROM ads WHERE category='lost'").fetchone()[0]
        shelter = cursor.execute("SELECT COUNT(*) FROM ads WHERE category='shelter'").fetchone()[0]
    
    text = f"📊 *Статистика*\n👥 Пользователей: {users}\n📝 Всего объявлений: {ads}\n✅ Активных: {active_ads}\n🔍 Пропавшие: {lost}\n🏠 Приют: {shelter}"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['cleardb'])
def clear_db(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Да", callback_data="clear_yes"),
        types.InlineKeyboardButton("❌ Нет", callback_data="clear_no")
    )
    bot.send_message(message.chat.id, "⚠️ Очистить БД? (админ сохранится)", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ['clear_yes', 'clear_no'])
def clear_db_confirm(call):
    if call.data == 'clear_no':
        bot.edit_message_text("❌ Отменено", call.message.chat.id, call.message.message_id)
        return
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM ads')
        # Сохраняем только администратора
        cursor.execute('DELETE FROM users WHERE telegram_id NOT IN ({})'.format(','.join('?'*len(ADMIN_IDS))), ADMIN_IDS)
    bot.edit_message_text("✅ База данных очищена", call.message.chat.id, call.message.message_id)

# ---------- Запуск ----------
if __name__ == '__main__':
    print("="*50)
    print("🚀 Запуск бота 'Приют по всей России' (новая версия)")
    init_db()
    print("✅ База данных готова и обновлена")
    print("="*50)
    while True:
        try:
            bot.infinity_polling()
        except KeyboardInterrupt:
            print("\n⛔ Бот остановлен")
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}. Перезапуск через 5 секунд...")
            time.sleep(5)