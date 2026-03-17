import telebot
import sqlite3
from telebot import types
import config
import re
import time
from contextlib import contextmanager

# ---------- Инициализация ----------
bot = telebot.TeleBot(config.BOT_TOKEN)
ADMIN_IDS = config.ADMIN_IDS
DB_PATH = config.DB_PATH
PAGE_SIZE = config.PAGE_SIZE

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

# ---------- Клавиатуры ----------
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton('🔍 Найти животное'),
        types.KeyboardButton('📝 Добавить объявление'),
        types.KeyboardButton('⭐ Избранное'),
        types.KeyboardButton('📋 Мои объявления'),
        types.KeyboardButton('❓ FAQ'),
        types.KeyboardButton('📞 Контакты')
    )
    return markup

def pagination_keyboard(page, total_pages, data_prefix):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    if page > 1:
        buttons.append(types.InlineKeyboardButton("◀️", callback_data=f"{data_prefix}_page_{page-1}"))
    buttons.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        buttons.append(types.InlineKeyboardButton("▶️", callback_data=f"{data_prefix}_page_{page+1}"))
    markup.add(*buttons)
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
        "Здесь вы можете найти дом для животного или найти питомца.\n\n"
        "Выберите действие:",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

# ---------- Поиск с пагинацией ----------
@bot.message_handler(func=lambda message: message.text == '🔍 Найти животное')
def search_animals(message):
    show_page(message.chat.id, 1)

def show_page(chat_id, page, filter_type=None):
    with get_db() as conn:
        cursor = conn.cursor()
        # Базовый запрос
        query = "SELECT a.*, u.first_name FROM ads a LEFT JOIN users u ON a.user_id = u.id WHERE a.is_active = 1"
        params = []
        if filter_type:
            query += " AND a.animal_type = ?"
            params.append(filter_type)
        query += " ORDER BY a.created_at DESC LIMIT ? OFFSET ?"
        params.extend([PAGE_SIZE, (page-1)*PAGE_SIZE])
        
        cursor.execute(query, params)
        ads = cursor.fetchall()
        
        # Общее количество для пагинации
        count_query = "SELECT COUNT(*) FROM ads WHERE is_active = 1" + (" AND animal_type = ?" if filter_type else "")
        cursor.execute(count_query, params[:1] if filter_type else [])
        total_ads = cursor.fetchone()[0]
        total_pages = (total_ads + PAGE_SIZE - 1) // PAGE_SIZE
    
    if not ads:
        bot.send_message(chat_id, "📭 Пока нет объявлений. Вы можете создать первое!")
        return
    
    for ad in ads:
        ad_id, user_id, title, description, city, phone, animal_type, is_active, created_at, first_name = ad
        
        text = f"*{title}*\n"
        text += f"📍 *Город:* {city}\n"
        text += f"🐾 *Тип:* {animal_type}\n"
        text += f"👤 *Автор:* {first_name or 'Неизвестно'}\n"
        if phone:
            text += f"📞 *Телефон:* {phone}\n"
        text += f"\n*Описание:*\n{description[:200]}...\n\n"
        text += f"📅 *Дата:* {created_at[:10]}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⭐ В избранное", callback_data=f"fav_{ad_id}"),
            types.InlineKeyboardButton("📞 Контакты", callback_data=f"contact_{ad_id}")
        )
        
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)
    
    # Кнопки пагинации
    if total_pages > 1:
        markup = pagination_keyboard(page, total_pages, "search")
        bot.send_message(chat_id, f"Страница {page} из {total_pages}", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('search_page_'))
def handle_pagination(call):
    page = int(call.data.split('_')[2])
    bot.delete_message(call.message.chat.id, call.message.message_id)
    show_page(call.message.chat.id, page)

# ---------- Добавление объявления ----------
@bot.message_handler(func=lambda message: message.text == '📝 Добавить объявление')
def add_advertisement(message):
    msg = bot.send_message(
        message.chat.id,
        "📝 *Создание нового объявления*\n\n"
        "Отправьте информацию в следующем формате:\n\n"
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
    bot.register_next_step_handler(msg, process_advertisement)

def process_advertisement(message):
    try:
        lines = message.text.split('\n')
        if len(lines) < 4:
            bot.send_message(message.chat.id, "❌ Нужно заполнить все поля: Заголовок, Город, Тип, Описание")
            return
        
        title = lines[0].strip()[:100]
        city = lines[1].strip()[:50]
        animal_type = lines[2].strip()[:30]
        description = lines[3].strip()[:1000]
        phone = lines[4].strip() if len(lines) > 4 else None
        
        if not title or not city or not animal_type or not description:
            bot.send_message(message.chat.id, "❌ Поля не могут быть пустыми")
            return
        
        # Используем ИИ для уточнения типа, если пользователь ввёл что-то своё
        if animal_type not in config.DEFAULT_ANIMAL_TYPES:
            ai_analysis = analyze_animal_description(description)
            if ai_analysis['animal_type'] != 'Другое':
                animal_type = ai_analysis['animal_type']
                bot.send_message(message.chat.id, f"🔍 Я определил тип животного как *{animal_type}* (можно изменить позже)", parse_mode='Markdown')
            else:
                animal_type = 'Другое'
        
        with get_db() as conn:
            cursor = conn.cursor()
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
                INSERT INTO ads (user_id, title, description, city, phone, animal_type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, title, description, city, phone, animal_type))
            ad_id = cursor.lastrowid
        
        bot.send_message(
            message.chat.id,
            f"✅ *Объявление успешно создано!*\n\n"
            f"*ID:* {ad_id}\n"
            f"*Заголовок:* {title}\n"
            f"*Город:* {city}\n"
            f"*Тип:* {animal_type}\n\n"
            f"Теперь его могут видеть другие пользователи.",
            parse_mode='Markdown',
            reply_markup=main_menu()
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")

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
        ad_id, user_id, title, description, city, phone, animal_type, is_active, created_at = ad
        status = "✅ Активно" if is_active else "❌ Неактивно"
        text = f"*{title}* ({status})\n\n📍 *Город:* {city}\n🐾 *Тип:* {animal_type}\n"
        if phone:
            text += f"📞 *Телефон:* {phone}\n"
        text += f"\n*Описание:*\n{description}\n\n📅 *Дата:* {created_at[:10]}\n🆔 *ID:* {ad_id}"
        
        markup = types.InlineKeyboardMarkup()
        if is_active:
            markup.add(types.InlineKeyboardButton("❌ Деактивировать", callback_data=f"deactivate_{ad_id}"))
        else:
            markup.add(types.InlineKeyboardButton("✅ Активировать", callback_data=f"activate_{ad_id}"))
        
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
        ad_id, user_id, title, description, city, phone, animal_type, is_active, created_at, first_name = ad
        text = f"*{title}*\n📍 *Город:* {city}\n🐾 *Тип:* {animal_type}\n👤 *Автор:* {first_name or 'Неизвестно'}\n"
        if phone:
            text += f"📞 *Телефон:* {phone}\n"
        text += f"\n*Описание:*\n{description[:200]}...\n\n📅 *Дата:* {created_at[:10]}"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ Убрать", callback_data=f"unfav_{ad_id}"))
        
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

# ---------- Контакты (реальные) ----------
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

# ---------- Callback обработчики ----------
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    # Избранное
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
    
    # Контакты автора
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
    
    # Активация/деактивация
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
    
    text = f"📊 *Статистика*\n👥 Пользователей: {users}\n📝 Всего объявлений: {ads}\n✅ Активных: {active_ads}"
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
        cursor.execute('DELETE FROM users WHERE telegram_id NOT IN (SELECT telegram_id FROM users WHERE telegram_id IN ({}) )'.format(','.join('?'*len(ADMIN_IDS))), ADMIN_IDS)
    bot.edit_message_text("✅ База данных очищена", call.message.chat.id, call.message.message_id)

# ---------- Запуск ----------
if __name__ == '__main__':
    print("="*50)
    print("🚀 Запуск бота 'Приют по всей России'")
    init_db()
    print("✅ База данных готова")
    print("="*50)
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except KeyboardInterrupt:
        print("\n⛔ Бот остановлен")