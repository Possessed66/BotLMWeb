import os
import sqlite3
import json
import uuid
import logging
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv




load_dotenv(dotenv_path='/root/BotLMWeb/secret.env')


# === КОНФИГУРАЦИЯ ===
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# === БАЗА ДАННЫХ ===
DATABASE_URL = "sqlite:///./database.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# === МОДЕЛИ SQLAlchemy ===
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    position = Column(String, nullable=True)  # 'менеджер', 'заведующий' и т.п.
    department = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class OrderQueue(Base):
    __tablename__ = "order_queue"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    order_data = Column(Text, nullable=False)  # JSON строка
    status = Column(String, default='pending')  # pending, processing, completed, failed
    attempt_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ===
Base.metadata.create_all(bind=engine)

# === FASTAPI ===
app = FastAPI(title="Ростовский Бот — Веб-версия", version="1.0.0")
templates = Jinja2Templates(directory="templates")

# === ФУНКЦИИ АВТОРИЗАЦИИ ===
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except jwt.JWTError:
        return None

def get_current_user(token: str = None):
    if not token:
        return None
    username = decode_access_token(token)
    if not username:
        return None
    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    db.close()
    return user

# === ФУНКЦИИ РАБОТЫ С ТВОЕЙ СХЕМОЙ БД ===
def get_db_connection():
    conn = sqlite3.connect("/root/BotLMWeb/articles.db")  # Путь к твоей существующей БД
    conn.row_factory = sqlite3.Row  # Позволяет обращаться к колонкам по имени
    return conn

# === КОПИЯ ТВОИХ ФУНКЦИЙ ===
def get_product_data_from_db(article: str, shop: str) -> Optional[Dict[str, Any]]:
    full_key_exact = f"{article}{shop}"
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Поиск по точному ключу
    cursor.execute("""
        SELECT full_key, store_number, department, article_code, name, gamma, 
               supplier_code, supplier_name, is_top_store
        FROM articles 
        WHERE full_key = ?
    """, (full_key_exact,))
    row = cursor.fetchone()

    if not row:
        # 2. Поиск по префиксу
        cursor.execute("""
            SELECT full_key, store_number, department, article_code, name, gamma, 
                   supplier_code, supplier_name, is_top_store
            FROM articles 
            WHERE full_key LIKE ?
            ORDER BY full_key
            LIMIT 1
        """, (f"{article}%",))
        row = cursor.fetchone()

    conn.close()

    if row:
        return {
            "Магазин": row['store_number'],
            "Отдел": row['department'],
            "Артикул": row['article_code'],
            "Название": row['name'],
            "Гамма": row['gamma'],
            "Номер осн. пост.": row['supplier_code'],
            "Название осн. пост.": row['supplier_name'],
            "Топ в магазине": str(row['is_top_store'])
        }
    return None

def get_supplier_data_from_db(supplier_id: str, shop: str) -> Optional[Dict[str, Any]]:
    supplier_id = str(supplier_id).strip()
    if not supplier_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    supplier_table_name = f"Даты выходов заказов {shop}"

    query = f'''
        SELECT "Номер осн. пост.", "Название осн. пост.", "Срок доставки в магазин",
               "День выхода заказа", "День выхода заказа 2", "День выхода заказа 3",
               "Каникулы список", "Исключения список"
        FROM "{supplier_table_name}"
        WHERE "Номер осн. пост." = ?
    '''

    try:
        cursor.execute(query, (supplier_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    except Exception:
        # Если таблица не существует, возвращаем заглушку
        return {
            "Номер осн. пост.": supplier_id,
            "Название осн. пост.": "Не найден",
            "Срок доставки в магазин": 3,
            "День выхода заказа": 1,
            "Каникулы список": "",
            "Исключения список": ""
        }
    finally:
        conn.close()

def calculate_delivery_date(supplier_data: dict) -> tuple[str, str]:
    # Упрощённый алгоритм для прототипа
    # В проде можно подключить твой полный код
    today = datetime.now().date()
    order_date = today.strftime("%d.%m.%Y")
    delivery_date = (today + timedelta(days=supplier_data.get("Срок доставки в магазин", 3))).strftime("%d.%m.%Y")
    return order_date, delivery_date

def get_product_info(article: str, shop: str) -> Optional[Dict[str, Any]]:
    product_data = get_product_data_from_db(article, shop)
    if not product_data:
        return None

    supplier_id = str(product_data.get("Номер осн. пост.", "")).strip()
    if not supplier_id:
        return {
            'Артикул': article,
            'Название': product_data.get('Название', ''),
            'Отдел': str(product_data.get('Отдел', '')),
            'Магазин': shop,
            'Поставщик': 'Товар РЦ',
            'Топ в магазине': product_data.get('Топ в магазине', '0'),
            'Дата заказа': 'Не определена (поставщик не найден)',
            'Дата поставки': 'Не определена (поставщик не найден)',
        }

    supplier_data = get_supplier_data_from_db(supplier_id, shop)
    if not supplier_data:
        return {
            'Артикул': article,
            'Название': product_data.get('Название', ''),
            'Отдел': str(product_data.get('Отдел', '')),
            'Магазин': shop,
            'Поставщик': 'Товар РЦ',
            'Топ в магазине': product_data.get('Топ в магазине', '0'),
            'Дата заказа': 'Не определена (поставщик не найден)',
            'Дата поставки': 'Не определена (поставщик не найден)',
        }

    order_date, delivery_date = calculate_delivery_date(supplier_data)

    return {
        'Артикул': article,
        'Название': product_data.get('Название', ''),
        'Отдел': str(product_data.get('Отдел', '')),
        'Магазин': shop,
        'Поставщик': supplier_data.get("Название осн. пост.", "Не указано").strip(),
        'Дата заказа': order_date,
        'Дата поставки': delivery_date,
        'Номер поставщика': supplier_id,
        'Топ в магазине': product_data.get('Топ в магазине', '0'),
    }

# === ГОЛОСНОЙ ВОРКЕР (копия из твоего бота, адаптированная) ===
def process_order_queue():
    """Функция для фонового запуска (например, через Celery или cron)"""
    db = SessionLocal()
    pending_orders = db.query(OrderQueue).filter(OrderQueue.status == 'pending').limit(5).all()

    for order_item in pending_orders:
        order_id = order_item.id
        user_id = order_item.user_id
        order_data = json.loads(order_item.order_data)

        try:
            # Тут твой код из воркера
            # 1. Подключаемся к Google Sheets
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            google_creds_json = os.getenv("GOOGLE_CREDENTIALS")
            if not google_creds_json:
                raise EnvironmentError("Переменная окружения GOOGLE_CREDENTIALS не найдена")

            # Загружаем JSON из строки

            creds_dict = json.loads(google_creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open('Копия Заказы МЗ 0.2')  # Имя таблицы
            worksheet = spreadsheet.worksheet(order_data['department'])

            # 2. Находим следующую строку
            next_row = len(worksheet.col_values(1)) + 1

            # 3. Формируем обновления (как в твоём коде)
            updates = [
                {'range': f'A{next_row}', 'values': [[order_data['selected_shop']]]},
                {'range': f'B{next_row}', 'values': [[int(order_data['article'])]]},
                {'range': f'C{next_row}', 'values': [[order_data['order_reason']]]},
                {'range': f'D{next_row}', 'values': [[datetime.now().strftime("%d.%m.%Y %H:%M")]]},
                {'range': f'E{next_row}', 'values': [[f"{order_data['user_name']}, {order_data['user_position']}"]]},
                {'range': f'K{next_row}', 'values': [[int(order_data['quantity'])]]},
                {'range': f'R{next_row}', 'values': [[user_id]]}
            ]
            worksheet.batch_update(updates)

            # 4. Обновляем статус
            order_item.status = 'completed'
            order_item.processed_at = datetime.utcnow()
            db.commit()

        except Exception as e:
            order_item.status = 'failed'
            order_item.error_message = str(e)
            order_item.attempt_count += 1
            db.commit()
            if order_item.attempt_count >= 5:
                # Отправить уведомление админу
                pass

    db.close()

# === МАРШРУТЫ ===

# --- Главная страница ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/register", response_class=HTMLResponse)
async def get_register_page(request: Request):
    html = '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Регистрация</title>
        <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}</style>
    </head>
    <body>
        <h2>Регистрация</h2>
        <form method="post" action="/register">
            <input type="text" name="username" placeholder="Логин" required />
            <input type="email" name="email" placeholder="Email" required />
            <input type="password" name="password" placeholder="Пароль" required />
            <input type="text" name="position" placeholder="Должность" />
            <button type="submit">Зарегистрироваться</button>
        </form>
        <p><a href="/login">Уже есть аккаунт? Войти</a></p>
    </body>
    </html>
    '''
    return HTMLResponse(content=html)

@app.post("/register")
async def register(username: str = Form(...), email: str = Form(...), password: str = Form(...), position: str = Form(...)):
    db = SessionLocal()
    existing_user = db.query(User).filter((User.username == username) | (User.email == email)).first()
    if existing_user:
        db.close()
        html = '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Регистрация</title>
            <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}.error{color:red;}</style>
        </head>
        <body>
            <h2>Регистрация</h2>
            <form method="post" action="/register">
                <input type="text" name="username" placeholder="Логин" required />
                <input type="email" name="email" placeholder="Email" required />
                <input type="password" name="password" placeholder="Пароль" required />
                <input type="text" name="position" placeholder="Должность" />
                <button type="submit">Зарегистрироваться</button>
            </form>
            <p class="error">Ошибка: Пользователь уже существует</p>
            <p><a href="/login">Уже есть аккаунт? Войти</a></p>
        </body>
        </html>
        '''
        return HTMLResponse(content=html)
    
    hashed_pw = get_password_hash(password)
    new_user = User(
        username=username,
        email=email,
        hashed_password=hashed_pw,
        position=position
    )
    db.add(new_user)
    db.commit()
    db.close()
    return RedirectResponse(url="/login", status_code=303)

# --- Страница входа ---
@app.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    html = '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Вход</title>
        <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}</style>
    </head>
    <body>
        <h2>Вход</h2>
        <form method="post" action="/login">
            <input type="text" name="username" placeholder="Логин" required />
            <input type="password" name="password" placeholder="Пароль" required />
            <button type="submit">Войти</button>
        </form>
        <p><a href="/register">Зарегистрироваться</a></p>
    </body>
    </html>
    '''
    return HTMLResponse(content=html)

@app.post("/login")
async def login(response: RedirectResponse, username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    db.close()

    if not user or not verify_password(password, user.hashed_password):
        html = '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Вход</title>
            <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}.error{color:red;}</style>
        </head>
        <body>
            <h2>Вход</h2>
            <form method="post" action="/login">
                <input type="text" name="username" placeholder="Логин" required />
                <input type="password" name="password" placeholder="Пароль" required />
                <button type="submit">Войти</button>
            </form>
            <p class="error">Ошибка: Неверные учетные данные</p>
            <p><a href="/register">Зарегистрироваться</a></p>
        </body>
        </html>
        '''
        return HTMLResponse(content=html)
    
    token_data = {"sub": user.username}
    token = create_access_token(data=token_data)
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=1800)  # 30 мин
    return response

# --- API: Поиск товара ---
@app.post("/api/search")
async def search_article(article: str, shop: str, token: str = None):
    user = get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    product_info = get_product_info(article, shop)
    if product_info:
        return {"found": True, "data": product_info}
    return {"found": False, "message": f"Артикул {article} не найден для магазина {shop}"}

# --- API: Создание заказа ---
@app.post("/api/order")
async def create_order(
    article: str = Form(...),
    shop: str = Form(...),
    department: str = Form(...),
    quantity: int = Form(...),
    order_reason: str = Form(...),
    token: str = None
):
    user = get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    order_data = {
        "selected_shop": shop,
        "article": article,
        "order_reason": order_reason,
        "department": department,
        "quantity": quantity,
        "user_name": user.username,
        "user_position": user.position or "сотрудник",
        "user_id": user.id
    }

    db = SessionLocal()
    queue_entry = OrderQueue(
        user_id=user.id,
        order_data=json.dumps(order_data, ensure_ascii=False)
    )
    db.add(queue_entry)
    db.commit()
    db.refresh(queue_entry)
    db.close()

    return {"status": "queued", "queue_id": queue_entry.id}

# --- Встроенный HTML шаблон для главной страницы ---
@app.get("/app", response_class=HTMLResponse)
async def app_ui(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/login")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Ростовский Бот</title>
        <style>
            :root {{ --primary: #2563eb; --success: #16a34a; --error: #dc2626; --bg: #f8fafc; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: var(--bg); color: #1e293b; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #cbd5e1; }}
            .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            h2 {{ margin: 0 0 16px; font-size: 1.25rem; color: #0f172a; }}
            .form-group {{ margin-bottom: 16px; }}
            label {{ display: block; margin-bottom: 6px; font-weight: 500; font-size: 0.9rem; }}
            input, select {{ width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 1rem; box-sizing: border-box; }}
            input:focus {{ outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }}
            button {{ background: var(--primary); color: white; border: none; padding: 12px 20px; border-radius: 8px; font-size: 1rem; font-weight: 500; cursor: pointer; width: 100%; transition: background 0.2s; }}
            button:hover {{ background: #1d4ed8; }} button:disabled {{ background: #94a3b8; cursor: not-allowed; }}
            .product-info {{ display: none; background: #f1f5f9; border-radius: 8px; padding: 16px; margin: 16px 0; }}
            .product-info.show {{ display: block; }}
            .info-row {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.95rem; }}
            .info-label {{ color: #64748b; }}
            .status {{ margin-top: 12px; padding: 10px; border-radius: 6px; font-size: 0.9rem; }}
            .status.success {{ background: #dcfce7; color: #166534; }}
            .status.error {{ background: #fee2e2; color: #991b1b; }}
            .hidden {{ display: none; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🛒 Ростовский Бот</h1>
            <a href="/logout"><button style="width:auto; padding: 8px 16px;">Выйти</button></a>
        </div>
        <p>Добро пожаловать, <strong>{user.username}</strong> ({user.position or 'без должности'})</p>

        <div class="card">
            <h2>🔍 Найти товар</h2>
            <div class="form-group">
                <label>Номер магазина *</label>
                <input type="text" id="shop" placeholder="Например: 7, 14, 69, 94">
            </div>
            <div class="form-group">
                <label>Артикул *</label>
                <input type="text" id="article" placeholder="Например: 100500">
            </div>
            <button onclick="searchProduct()">Найти товар</button>
            <div id="searchError" class="status error hidden"></div>
        </div>

        <div id="productSection" class="card hidden">
            <h2>📦 Информация о товаре</h2>
            <div class="product-info show">
                <div class="info-row"><span class="info-label">Артикул:</span> <span id="pArticle"></span></div>
                <div class="info-row"><span class="info-label">Название:</span> <span id="pName"></span></div>
                <div class="info-row"><span class="info-label">Отдел:</span> <span id="pDepartment"></span></div>
                <div class="info-row"><span class="info-label">Поставщик:</span> <span id="pSupplier"></span></div>
                <div class="info-row"><span class="info-label">Дата заказа:</span> <span id="pOrderDate"></span></div>
                <div class="info-row"><span class="info-label">Дата поставки:</span> <span id="pDeliveryDate"></span></div>
            </div>

            <form id="orderForm">
                <input type="hidden" id="formShop" name="shop">
                <input type="hidden" id="formArticle" name="article">
                <input type="hidden" id="formDepartment" name="department">

                <h2>🛒 Оформить заказ</h2>
                <div class="form-group">
                    <label>Количество *</label>
                    <input type="number" name="quantity" min="1" value="1">
                </div>
                <div class="form-group">
                    <label>Причина заказа *</label>
                    <input type="text" name="order_reason" placeholder="Например: остаток < 3, сезонный спрос">
                </div>
                <button type="submit">✅ Подтвердить и отправить</button>
            </form>
            <div id="orderStatus" class="status hidden"></div>
        </div>

        <script>
            let currentProduct = null;

            async function searchProduct() {{
                const article = document.getElementById('article').value.trim();
                const shop = document.getElementById('shop').value.trim();
                const errorDiv = document.getElementById('searchError');
                const btn = document.querySelector('#productSection ~ .card button');

                errorDiv.classList.add('hidden');
                errorDiv.textContent = '';

                if (!article || !shop) {{
                    errorDiv.textContent = 'Заполните артикул и номер магазина';
                    errorDiv.classList.remove('hidden');
                    return;
                }}

                try {{
                    const response = await fetch('/api/search', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                        body: new URLSearchParams({{ article, shop }})
                    }});

                    const result = await response.json();

                    if (response.ok && result.found) {{
                        currentProduct = result.data;

                        document.getElementById('pArticle').textContent = result.data['Артикул'];
                        document.getElementById('pName').textContent = result.data['Название'];
                        document.getElementById('pDepartment').textContent = result.data['Отдел'];
                        document.getElementById('pSupplier').textContent = result.data['Поставщик'];
                        document.getElementById('pOrderDate').textContent = result.data['Дата заказа'];
                        document.getElementById('pDeliveryDate').textContent = result.data['Дата поставки'];

                        document.getElementById('formShop').value = result.data['Магазин'];
                        document.getElementById('formArticle').value = result.data['Артикул'];
                        document.getElementById('formDepartment').value = result.data['Отдел'];

                        document.getElementById('productSection').classList.remove('hidden');
                        document.getElementById('orderStatus').classList.add('hidden');
                    }} else {{
                        errorDiv.textContent = result.message || 'Товар не найден';
                        errorDiv.classList.remove('hidden');
                        document.getElementById('productSection').classList.add('hidden');
                    }}
                }} catch (e) {{
                    errorDiv.textContent = 'Ошибка соединения';
                    errorDiv.classList.remove('hidden');
                }}
            }}

            document.getElementById('orderForm').onsubmit = async (e) => {{
                e.preventDefault();
                const formData = new FormData(e.target);
                const statusDiv = document.getElementById('orderStatus');
                const btn = e.submitter;

                btn.disabled = true;
                btn.textContent = 'Отправка...';

                try {{
                    const response = await fetch('/api/order', {{
                        method: 'POST',
                        body: formData
                    }});

                    const result = await response.json();

                    if (response.ok) {{
                        statusDiv.innerHTML = `✅ <b>Заказ принят!</b><br> ID очереди: <code>${{result.queue_id}}</code>`;
                        statusDiv.className = 'status success';
                        statusDiv.classList.remove('hidden');
                        
                        setTimeout(() => {{
                            document.getElementById('productSection').classList.add('hidden');
                            document.getElementById('article').value = '';
                            statusDiv.classList.add('hidden');
                        }}, 3000);
                    }} else {{
                        statusDiv.textContent = `❌ ${{result.detail || 'Ошибка создания заказа'}}`;
                        statusDiv.className = 'status error';
                        statusDiv.classList.remove('hidden');
                    }}
                }} catch (e) {{
                    statusDiv.textContent = '❌ Ошибка сети';
                    statusDiv.className = 'status error';
                    statusDiv.classList.remove('hidden');
                }} finally {{
                    btn.disabled = false;
                    btn.textContent = '✅ Подтвердить и отправить';
                }}
            }};
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# --- Шаблоны для регистрации/входа (встроенные) ---
@app.get("/login_html", response_class=HTMLResponse)
async def login_html(request: Request):
    html = '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Вход</title>
        <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}</style>
    </head>
    <body>
        <h2>Вход</h2>
        <form method="post" action="/login">
            <input type="text" name="username" placeholder="Логин" required />
            <input type="password" name="password" placeholder="Пароль" required />
            <button type="submit">Войти</button>
        </form>
        <p><a href="/register">Зарегистрироваться</a></p>
    </body>
    </html>
    '''
    return HTMLResponse(content=html)

@app.get("/register_html", response_class=HTMLResponse)
async def register_html(request: Request):
    html = '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Регистрация</title>
        <style>body{font-family:sans-serif;max-width:400px;margin:50px auto;padding:20px;}input,button{width:100%;padding:10px;margin:10px 0;border-radius:5px;border:1px solid #ccc;}</style>
    </head>
    <body>
        <h2>Регистрация</h2>
        <form method="post" action="/register">
            <input type="text" name="username" placeholder="Логин" required />
            <input type="email" name="email" placeholder="Email" required />
            <input type="password" name="password" placeholder="Пароль" required />
            <input type="text" name="position" placeholder="Должность" />
            <button type="submit">Зарегистрироваться</button>
        </form>
        <p><a href="/login">Уже есть аккаунт? Войти</a></p>
    </body>
    </html>
    '''
    return HTMLResponse(content=html)

# --- Запуск воркера (фоном или отдельным процессом) ---
# uvicorn main:app --reload
# Воркер можно запустить отдельно: python -c "from main import process_order_queue; process_order_queue()"
