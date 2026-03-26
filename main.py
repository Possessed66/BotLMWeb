import os
import sqlite3
import json
import uuid
import logging
import bcrypt
import jwt
import pathlib
from jinja2 import Template, Environment, FileSystemLoader
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
from fastapi.security import HTTPBearer

load_dotenv(dotenv_path='/root/BotLMWeb/secret.env')

# === КОНФИГУРАЦИЯ ===
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("Не найдена SECRET_KEY в env")
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
    position = Column(String, nullable=True)
    department = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class OrderQueue(Base):
    __tablename__ = "order_queue"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    order_data = Column(Text, nullable=False)
    status = Column(String, default='pending')
    attempt_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ===
Base.metadata.create_all(bind=engine)

# === FASTAPI ===
app = FastAPI(title="Ростовский Бот — Веб-версия", version="1.0.0")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
        # Проверяем, не пустой ли токен
        if not token:
            return None

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    # Правильный класс исключения для PyJWT
    except jwt.exceptions.PyJWTError: # <-- Вот тут ошибка
        return None

def get_current_user(token: str = None):
    if not token: # <-- Если token None или пустой
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
    conn = sqlite3.connect("/root/BotLMWeb/articles.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_product_data_from_db(article: str, shop: str) -> Optional[Dict[str, Any]]:
    full_key_exact = f"{article}{shop}"
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT full_key, store_number, department, article_code, name, gamma,
               supplier_code, supplier_name, is_top_store
        FROM articles
        WHERE full_key = ?
    """, (full_key_exact,))
    row = cursor.fetchone()

    if not row:
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

# === ВОРКЕР ===
def process_order_queue():
    db = SessionLocal()
    pending_orders = db.query(OrderQueue).filter(OrderQueue.status == 'pending').limit(5).all()

    for order_item in pending_orders:
        order_id = order_item.id
        user_id = order_item.user_id
        order_data = json.loads(order_item.order_data)

        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            google_creds_json = os.getenv("GOOGLE_CREDENTIALS")
            if not google_creds_json:
                raise EnvironmentError("Переменная окружения GOOGLE_CREDENTIALS не найдена")

            creds_dict = json.loads(google_creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open('Копия Заказы МЗ 0.2')
            worksheet = spreadsheet.worksheet(order_data['department'])

            next_row = len(worksheet.col_values(1)) + 1

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

            order_item.status = 'completed'
            order_item.processed_at = datetime.utcnow()
            db.commit()

        except Exception as e:
            order_item.status = 'failed'
            order_item.error_message = str(e)
            order_item.attempt_count += 1
            db.commit()
            if order_item.attempt_count >= 5:
                pass

    db.close()

# === МАРШРУТЫ ===

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/app")
    return RedirectResponse(url="/app")



template_path = pathlib.Path(__file__).parent / "templates" / "app.html"
with open(template_path, 'r', encoding='utf-8') as f:
    template_content = f.read()


templates_env = Environment(loader=FileSystemLoader("templates"))
# Компилируем шаблон
jinja_template = Template(template_content)

@app.get("/app", response_class=HTMLResponse)
async def app_ui(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/login")
    user_dict = {
        "username": user.username,
        "position": user.position or "без должности"
    }

    # Загружаем шаблон *внутри* функции
    template = templates_env.get_template("app.html")

    # Рендерим с явной передачей request и url_for
    rendered_html = template.render(request=request, user=user_dict, url_for=request.url_for)
    return HTMLResponse(content=rendered_html)

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
    response.set_cookie(key="access_token", value=token, httponly=False, secure=False, samesite="lax", max_age=1800)
    return response

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

# --- API: Поиск товара (с токеном из заголовка) ---
security = HTTPBearer()

@app.post("/api/search")
async def search_article(article: str = Form(...), shop: str = Form(...), credentials: HTTPBearer = Depends(security)):
    token = credentials.credentials
    user = get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    product_info = get_product_info(article, shop)
    if product_info:
        return {"found": True, "data": product_info}
    return {"found": False, "message": f"Артикул {article} не найден для магазина {shop}"}

# --- API: Создание заказа (тоже с токеном из заголовка) ---
@app.post("/api/order")
async def create_order(
    article: str = Form(...),
    shop: str = Form(...),
    department: str = Form(...),
    quantity: int = Form(...),
    order_reason: str = Form(...),
    credentials: HTTPBearer = Depends(security)
):
    token = credentials.credentials
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

@app.get("/logout")
async def logout(response: RedirectResponse):
    response = RedirectResponse(url="/login", status_code=303)
    # Удаляем куку, установив её срок действия в прошлое
    response.set_cookie(
        key="access_token",
        value="",
        httponly=False,  # Должно совпадать с тем, как вы установили куку
        max_age=0,       # Установить срок действия в 0 (удалить)
        samesite="lax",
        path="/"         # Убедитесь, что путь совпадает
    )
    return response
