from urllib import parse
import json
import re
import time
import random
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from pydantic import BaseModel


# --- Pydantic модели ---
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


# --- Конфиг ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://market.yandex.ru/",
}


def _parse_cards(soup: BeautifulSoup, limit: int, seen_titles: set) -> List[Product]:
    """Вспомогательная функция: парсит карточки из одного HTML"""
    products = []
    cards = soup.find_all('article', {'data-auto': 'searchOrganic'})

    for card in cards:
        if len(products) >= limit:
            break

        try:
            title = None
            price = None
            specs = []

            # Поиск JSON в noframes/script
            json_blocks = card.find_all('noframes', {'data-apiary': 'patch'})
            if not json_blocks:
                json_blocks = card.find_all('script', {'type': 'application/json'})

            for block in json_blocks:
                content = block.string
                if not content:
                    continue
                try:
                    data = json.loads(content.strip())
                    widgets = data.get('widgets', {})
                    for widget_data in widgets.values():
                        if not isinstance(widget_data, dict):
                            continue
                        for value in widget_data.values():
                            if not isinstance(value, dict):
                                continue
                            if 'title' in value and not title:
                                title = value['title']
                            if 'price' in value and not price:
                                p_val = value['price'].get('value')
                                p_curr = value['price'].get('currency', 'RUR')
                                if p_val:
                                    price = f"{p_val} {p_curr}"
                            if 'specs' in value:
                                for spec in value['specs']:
                                    if isinstance(spec, dict) and 'name' in spec:
                                        specs.append(
                                            Characteristic(
                                                name=spec['name'],
                                                value=str(spec.get('value', ''))
                                            )
                                        )
                except json.JSONDecodeError:
                    continue

            # Fallback для title
            if not title:
                title_tag = card.find('span', {'itemprop': 'name'})
                if title_tag:
                    title = title_tag.get_text(strip=True)

            # Fallback для price
            if not price:
                price_tag = card.find(string=re.compile(r'\d+\s*₽'))
                if price_tag:
                    price = price_tag.strip()

            # Fallback для specs
            if not specs:
                all_spans = card.find_all('span', class_=lambda x: x and 'ds-text' in x)
                temp_specs = []
                for i in range(len(all_spans) - 1):
                    txt = all_spans[i].get_text(strip=True)
                    if ':' in txt and len(txt) < 50:
                        val = all_spans[i + 1].get_text(strip=True)
                        if val:
                            temp_specs.append(
                                Characteristic(name=txt.replace(':', '').strip(), value=val)
                            )
                specs = list({s.name: s for s in temp_specs}.values())

            if title and title not in seen_titles:
                seen_titles.add(title)
                products.append(Product(title=title, price=price, characteristics=specs))

        except Exception as e:
            print(f"⚠️ Ошибка обработки карточки: {e}")
            continue

    return products


def parse_yandex_market(query: str, limit: int = 5, max_pages: int = 100) -> List[Product]:
    """
    Парсит товары с Яндекс Маркета через requests с поддержкой пагинации.
    """
    products = []
    seen_titles = set()
    page = 1

    while len(products) < limit and page <= max_pages:
        # 🔧 Формируем URL с пагинацией
        url = f"https://market.yandex.ru/search?text={parse.quote(query)}&page={page}"

        try:
            time.sleep(random.uniform(0.5, 1.5))  # "человеческая" задержка
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"❌ Ошибка запроса страницы {page}: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        new_products = _parse_cards(soup, limit - len(products), seen_titles)

        print(f"📄 Страница {page}: найдено {len(new_products)} новых товаров")

        if not new_products:
            # Новые товары не появились — дальше листать нет смысла
            print(f"ℹ️ Товары закончились на странице {page}")
            break

        products.extend(new_products)
        page += 1

    return products[:limit]


# --- FastAPI app ---
app = FastAPI(title="Yandex Market Parser")


@app.get("/search", response_model=SearchResponse)
async def search_products(q: str, limit: int = 5):
    """
    Парсит товары с Яндекс Маркета по запросу с поддержкой пагинации.
    """
    items = parse_yandex_market(q, limit)
    return SearchResponse(query=q, products=items)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, port=8000)