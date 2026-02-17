from urllib import parse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
import json
import re

# --- 1. Pydantic Модели (для валидации и документации) ---

class Characteristic(BaseModel):
    name: str
    value: str


class Product(BaseModel):
    title: str
    price: Optional[str] = None
    characteristics: List[Characteristic] = []


class SearchResponse(BaseModel):
    query: str
    products: List[Product]


# --- 2. Парсер ---

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://market.yandex.ru/",
}




# ... остальные импорты ...

def parse_yandex_market(query: str, limit: int = 5) -> List[Product]:
    url = f"https://market.yandex.ru/search?text={parse.quote(query)}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Ошибка запроса: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    products = []

    # 1. Находим все карточки товаров по стабильному атрибуту
    cards = soup.find_all('article', {'data-auto': 'searchOrganic'})

    for card in cards[:limit]:
        try:
            title = None
            price = None
            specs = []

            # 2. Ищем внутри карточки JSON-данные
            # Они могут быть в <noframes> или в <script>
            # Обычно Яндекс кладет их в noframes с атрибутом data-apiary="patch"
            json_blocks = card.find_all('noframes', {'data-apiary': 'patch'})
            # Также проверим скрипты, вдруг структура изменилась
            if not json_blocks:
                json_blocks = card.find_all('script', {'type': 'application/json'})

            found_data = {}

            for block in json_blocks:
                content = block.string
                if not content:
                    continue
                try:
                    # Пытаемся распарсить JSON
                    data = json.loads(content.strip())
                    # Рекурсивно ищем нужные поля в структуре виджетов
                    # Структура обычно: {"widgets": {"@Name": {...}}}

                    # Проходимся по всем виджетам внутри блока
                    widgets = data.get('widgets', {})
                    for widget_name, widget_data in widgets.items():
                        # Внутри виджета часто лежит ключ, совпадающий с ID, а потом данные
                        for key, value in widget_data.items():
                            if isinstance(value, dict):
                                # Ищем title и price
                                if 'title' in value and not title:
                                    title = value['title']
                                if 'price' in value:
                                    p_val = value['price'].get('value')
                                    p_curr = value['price'].get('currency', 'RUR')
                                    if p_val:
                                        price = f"{p_val} {p_curr}"

                                # Ищем характеристики (могут называться по-разному)
                                # Часто они в поле 'specs', 'features' или 'characteristics'
                                # Или в тексте описания
                                if 'specs' in value:
                                    for spec in value['specs']:
                                        if isinstance(spec, dict) and 'name' in spec:
                                            specs.append(
                                                Characteristic(name=spec['name'], value=str(spec.get('value', ''))))
                                # Иногда характеристики прячутся в других полях, нужно смотреть структуру
                                # Если в JSON их нет, придется парсить HTML fallback-ом
                    found_data.update(data)
                except json.JSONDecodeError:
                    continue

            # Fallback: Если в JSON не нашли название (редко), пробуем HTML
            if not title:
                title_tag = card.find('span', {'itemprop': 'name'})
                if title_tag:
                    title = title_tag.get_text(strip=True)

            # Fallback для цены: ищем в HTML символ рубля
            if not price:
                price_tag = card.find(string=re.compile(r'\d+\s*₽'))
                if price_tag:
                    price = price_tag.strip()

            # Fallback для характеристик: если в JSON пусто, парсим HTML пары span
            if not specs:
                # Ищем блоки с классом, содержащим 'spec' или просто пары span с двоеточием
                # Используем старый метод поиска пар
                all_spans = card.find_all('span', class_=lambda x: x and 'ds-text' in x)
                temp_specs = []
                for i in range(len(all_spans) - 1):
                    txt = all_spans[i].get_text(strip=True)
                    if ':' in txt and len(txt) < 50:
                        val = all_spans[i + 1].get_text(strip=True)
                        if val:
                            temp_specs.append(Characteristic(name=txt.replace(':', '').strip(), value=val))
                # Убираем дубли
                specs = list({s.name: s for s in temp_specs}.values())

            if title:
                products.append(Product(
                    title=title,
                    price=price,
                    characteristics=specs
                ))

        except Exception as e:
            print(f"Ошибка обработки карточки: {e}")
            continue

    return products
app = FastAPI(title="Yandex Market Parser")


@app.get("/search", response_model=SearchResponse)
async def search_products(q: str, limit: int = 5):
    """
    Парсит первые N товаров с Яндекс Маркета по запросу.
    """
    items = parse_yandex_market(q, limit)
    return SearchResponse(query=q, products=items)


if __name__ == "__main__":
    import uvicorn

    # Запуск сервера: uvicorn main:app --reload
    uvicorn.run(app, port=8000)