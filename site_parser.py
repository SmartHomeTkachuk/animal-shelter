import requests
import re
import json
from bs4 import BeautifulSoup
from bot import get_db, analyze_animal_description
import config

class SiteParser:
    def __init__(self, base_url="https://priyut-drug.ru/"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        self.seen_file = "site_seen.txt"
        self.seen_ids = self._load_seen()

    def _load_seen(self):
        try:
            with open(self.seen_file, 'r') as f:
                return set(line.strip() for line in f)
        except FileNotFoundError:
            return set()

    def _save_seen(self, item_id):
        with open(self.seen_file, 'a') as f:
            f.write(item_id + '\n')
        self.seen_ids.add(item_id)

    def _get_page(self, url):
        try:
            r = self.session.get(url, timeout=10)
            r.encoding = 'utf-8'
            r.raise_for_status()
            # Используем стандартный парсер
            return BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            print(f"Ошибка загрузки {url}: {e}")
            return None

    def ensure_shelter_user(self):
        """Создаёт пользователя 'Приют Друг' (user_id = config.SHELTER_USER_ID), если его нет"""
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE telegram_id = ?", (config.SHELTER_USER_ID,))
            if not cur.fetchone():
                cur.execute('''
                    INSERT INTO users (telegram_id, username, first_name, last_name)
                    VALUES (?, ?, ?, ?)
                ''', (config.SHELTER_USER_ID, 'priyut_drug', 'Приют Друг', ''))
                print(f"✅ Создан пользователь 'Приют Друг' с ID {config.SHELTER_USER_ID}")

    def extract_animals_from_main(self):
        """Парсит блок 'Питомцы приюта' на главной странице"""
        soup = self._get_page(self.base_url)
        if not soup:
            return []

        animals = []

        # Ищем секцию, содержащую заголовок "Питомцы приюта"
        # На сайте Tilda часто блоки имеют id="recXXXXXX"
        section = soup.find('div', id=re.compile(r'rec\d+'))
        if section:
            # Проверяем, есть ли внутри текст о питомцах
            if not section.get_text() or 'питомц' not in section.get_text().lower():
                section = None

        if not section:
            # Fallback: ищем любой div с классом, содержащим "text" или "block"
            for div in soup.find_all('div', class_=re.compile(r'(text|block)')):
                if 'питомц' in div.get_text().lower():
                    section = div
                    break

        if not section:
            # Самый простой вариант: берём всю страницу и ищем абзацы с ключевыми словами
            paragraphs = soup.find_all(['p', 'div'], string=re.compile(r'[Сс]обак|[Кк]ошк|щен|кот', re.UNICODE))
            if paragraphs:
                # будем обрабатывать каждый такой абзац как отдельное животное
                for p in paragraphs[:10]:  # ограничим 10
                    text = p.get_text(strip=True)
                    if len(text) < 30 or 'Подробнее' in text:
                        continue
                    item_id = f"site_{hash(text[:50])}"
                    if item_id in self.seen_ids:
                        continue

                    analysis = analyze_animal_description(text)
                    if analysis['animal_type'] == 'Другое':
                        continue

                    title = text.split('\n')[0][:100]
                    if len(title) < 5:
                        title = text[:50] + '...'

                    city = "Санкт-Петербург"  # приют в СПб
                    phone_match = re.search(r'\+7[0-9]{10}|8[0-9]{10}', text)
                    phone = phone_match.group(0) if phone_match else None

                    animals.append({
                        'title': title,
                        'description': text[:1000],
                        'city': city,
                        'phone': phone,
                        'animal_type': analysis['animal_type'],
                        'source_url': self.base_url,
                        'item_id': item_id
                    })
                    self._save_seen(item_id)
                return animals

        # Если нашли секцию, обрабатываем её дочерние элементы
        if section:
            # Ищем все блоки с текстом внутри секции
            for elem in section.find_all(['div', 'p', 'h3', 'h4']):
                text = elem.get_text(strip=True)
                if len(text) < 30 or 'Подробнее' in text or 'Load more' in text:
                    continue
                item_id = f"site_{hash(text[:50])}"
                if item_id in self.seen_ids:
                    continue

                analysis = analyze_animal_description(text)
                if analysis['animal_type'] == 'Другое':
                    continue

                title = text.split('\n')[0][:100]
                if len(title) < 5:
                    title = text[:50] + '...'

                city = "Санкт-Петербург"
                phone_match = re.search(r'\+7[0-9]{10}|8[0-9]{10}', text)
                phone = phone_match.group(0) if phone_match else None

                animals.append({
                    'title': title,
                    'description': text[:1000],
                    'city': city,
                    'phone': phone,
                    'animal_type': analysis['animal_type'],
                    'source_url': self.base_url,
                    'item_id': item_id
                })
                self._save_seen(item_id)

        return animals

    def extract_contacts(self):
        """Извлекает контактные данные с главной страницы"""
        soup = self._get_page(self.base_url)
        if not soup:
            return {}

        text = soup.get_text()
        contacts = {}

        cards = re.findall(r'(\d{4}\s?\d{4}\s?\d{4}\s?\d{4})', text)
        contacts['donation_cards'] = cards

        inn = re.search(r'ИНН\s*(\d+)', text)
        if inn:
            contacts['inn'] = inn.group(1)

        rs = re.search(r'Р/С\s*(\d+)', text)
        if rs:
            contacts['bank_account'] = rs.group(1)

        return contacts

    def update_database(self):
        print("🔄 Парсинг сайта priyut-drug.ru...")
        self.ensure_shelter_user()
        animals = self.extract_animals_from_main()
        added = 0

        for a in animals:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT id FROM ads 
                    WHERE title = ? AND city = ? AND user_id = ?
                ''', (a['title'], a['city'], config.SHELTER_USER_ID))
                if cur.fetchone():
                    continue

                cur.execute('''
                    INSERT INTO ads (user_id, title, description, city, phone, animal_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    config.SHELTER_USER_ID,
                    a['title'],
                    a['description'] + f"\n\nИсточник: {a['source_url']}",
                    a['city'],
                    a['phone'],
                    a['animal_type']
                ))
                added += 1
                print(f"  ➕ Добавлен: {a['title']}")

        contacts = self.extract_contacts()
        if contacts:
            with open('shelter_contacts.txt', 'w', encoding='utf-8') as f:
                json.dump(contacts, f, ensure_ascii=False, indent=2)
            print("📞 Контакты приюта сохранены")

        print(f"✅ Готово. Добавлено новых животных: {added}")
        return added

if __name__ == '__main__':
    parser = SiteParser()
    parser.update_database()