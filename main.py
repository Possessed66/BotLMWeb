import os
import json
import logging
import secrets
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from contextlib import contextmanager

# === FASTAPI & PYDANTIC ===
from fastapi import FastAPI, Request, HTTPException, Form, Depends, BackgroundTasks, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic_settings import BaseSettings  # pip install pydantic-settings
from pydantic import Field

# === DATABASE ===
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine as raw_engine, text


# === AUTHENTICATION ===
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# === GOOGLE SHEETS ===
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === КОНФИГУРАЦИЯ ===
class Settings(BaseSettings):
    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    DATABASE_URL: str = "sqlite:///./database.db"
    EXISTING_DB_PATH: str = "./articles.db"
    # Имя Google Таблицы
    GOOGLE_SPREADSHEET_NAME: str = "Копия Заказы МЗ 0.2"
    # Переменная с JSON-ключом
    GOOGLE_CREDENTIALS_JSON: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

# === БАЗА ДАННЫХ ===
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
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
app = FastAPI(title="Ростовский Бот — Веб-версия", version="2.0.0")
templates = Jinja2Templates(directory="templates")

# === ФУНКЦИИ АВТОРИЗАЦИИ ===
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    # Увеличим rounds для большей стойкости (по умолчанию 12)
    salt = bcrypt.gensalt(rounds=14)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except jwt.exceptions.PyJWTError:
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

# === РАБОТА С СУЩЕСТВУЮЩЕЙ БАЗОЙ (SQLAlchemy raw SQL для совместимости) ===
from sqlalchemy import create_engine as raw_engine, text

existing_db_engine = raw_engine(f"sqlite:///{settings.EXISTING_DB_PATH}")

def get_product_info_from_existing_db(article: str, shop: str) -> Optional[Dict[str, Any]]:
    full_key_exact = f"{article}{shop}"
    with existing_db_engine.connect() as conn:
        # 1. Поиск по точному ключу
        result = conn.execute(text("""
            SELECT full_key, store_number, department, article_code, name, gamma,
                   supplier_code, supplier_name, is_top_store
            FROM articles
            WHERE full_key = :full_key
        """), {"full_key": full_key_exact})
        row = result.fetchone()

        if not row:
            # 2. Поиск по префиксу
            result = conn.execute(text("""
                SELECT full_key, store_number, department, article_code, name, gamma,
                       supplier_code, supplier_name, is_top_store
                FROM articles
                WHERE full_key LIKE :prefix
                ORDER BY full_key
                LIMIT 1
            """), {"prefix": f"{article}%"})
            row = result.fetchone()

        if row:
            # Возвращаем как в старом коде
            supplier_id = row['supplier_code']
            # Получаем даты поставки
            supplier_data = get_supplier_dates_from_existing_db(supplier_id, shop)
            # Рассчитываем даты
            order_date, delivery_date = calculate_delivery_date_from_supplier_data(supplier_data)

            return {
                'Артикул': row['article_code'],
                'Название': row['name'],
                'Отдел': row['department'],
                'Магазин': row['store_number'],
                'Поставщик': row['supplier_name'],
                'Дата заказа': order_date,
                'Дата поставки': delivery_date,
                'Номер поставщика': supplier_id,
                'Топ в магазине': str(row['is_top_store'])
            }
    return None

def get_supplier_dates_from_existing_db(supplier_id: str, shop: str) -> Dict[str, Any]:
    supplier_id = str(supplier_id).strip()
    if not supplier_id:
        return {}

    table_name = f"Даты выходов заказов {shop}"
    with existing_db_engine.connect() as conn:
        try:
            result = conn.execute(text(f"""
                SELECT "Номер осн. пост.", "Название осн. пост.", "Срок доставки в магазин",
                       "День выхода заказа", "День выхода заказа 2", "День выхода заказа 3",
                       "Каникулы список", "Исключения список"
                FROM '{table_name}'
                WHERE "Номер осн. пост." = :supplier_id
            """), {"supplier_id": supplier_id})
            row = result.fetchone()
            if row:
                return dict(row)
        except Exception:
            logger.warning(f"Таблица '{table_name}' не найдена или ошибка запроса.")
            return {}
    return {}

def calculate_delivery_date_from_supplier_data(supplier_data: Dict[str, Any]) -> tuple[str, str]:
    # Упрощённый расчет, как в старом коде
    today = datetime.now().date()
    order_date = today.strftime("%d.%m.%Y")
    delivery_days = supplier_data.get("Срок доставки в магазин", 3)
    delivery_date = (today + timedelta(days=delivery_days)).strftime("%d.%m.%Y")
    return order_date, delivery_date


# === ВОРКЕР (пока ручной запуск) ===
def process_order_queue():
    """Функция для фонового запуска"""
    db = SessionLocal()
    pending_orders = db.query(OrderQueue).filter(OrderQueue.status == 'pending').limit(5).all()

    for order_item in pending_orders:
        order_id = order_item.id
        user_id = order_item.user_id
        order_data = json.loads(order_item.order_data)

        try:
            # Подключение к Google Sheets
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds_dict = json.loads(settings.GOOGLE_CREDENTIALS_JSON)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open(settings.GOOGLE_SPREADSHEET_NAME)
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
            logger.info(f"Заказ {order_id} успешно обработан и записан в Google Таблицу.")

        except Exception as e:
            logger.error(f"Ошибка при обработке заказа {order_id}: {e}")
            order_item.status = 'failed'
            order_item.error_message = str(e)
            order_item.attempt_count += 1
            db.commit()
            if order_item.attempt_count >= 5:
                logger.critical(f"Заказ {order_id} провалился 5 раз. Требуется вмешательство администратора.")

    db.close()

# === МАРШРУТЫ ===

# --- Главная страница ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/app")

@app.get("/app", response_class=HTMLResponse)
async def app_ui(request: Request):
    token = request.cookies.get("access_token")
    user = get_current_user(token)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("app.html", {
        "request": request,
        "user": {
            "username": user.username,
            "position": user.position or "без должности"
        }
    })

@app.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(response: RedirectResponse, username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    db.close()

    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверные учетные данные"
        })

    token_data = {"sub": user.username}
    token = create_access_token(data=token_data)

    response = RedirectResponse(url="/", status_code=303)
    # 🔐 HttpOnly=True для безопасности
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,  # 🔐
        secure=False,   # Если HTTPS, поменяй на True
        samesite="lax",
        max_age=1800    # 30 минут
    )
    return response

@app.get("/logout")
async def logout(response: RedirectResponse):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token")
    return response

@app.get("/register", response_class=HTMLResponse)
async def get_register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(username: str = Form(...), email: str = Form(...), password: str = Form(...), position: str = Form(...)):
    db = SessionLocal()
    existing_user = db.query(User).filter((User.username == username) | (User.email == email)).first()
    if existing_user:
        db.close()
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Пользователь уже существует"
        })

    hashed_pw = get_password_hash(password)
    new_user = User(username=username, email=email, hashed_password=hashed_pw, position=position)
    db.add(new_user)
    db.commit()
    db.close()
    return RedirectResponse(url="/login", status_code=303)

# --- API: Поиск товара (с токеном из заголовка) ---
security = HTTPBearer()

@app.post("/api/search")
async def search_article(
    request: Request,  # <- Добавляем Request как параметр
    access_token: str = Cookie(None), # <- Получаем токен из куки
    article: str = Form(...),  # <- Получаем данные формы напрямую
    shop: str = Form(...)
):
    user = get_current_user(access_token)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    if not article or not shop:
        raise HTTPException(status_code=400, detail="Артикул и магазин обязательны")

    product_info = get_product_info_from_existing_db(article, shop)
    if product_info:
        return {"found": True, "data": product_info}
    return {"found": False, "message": f"Артикул {article} не найден для магазина {shop}"}


# --- API: Создание заказа (с токеном из заголовка) ---
@app.post("/api/order")
async def create_order(
    request: Request, # <- Добавляем Request
    access_token: str = Cookie(None), # <- Получаем токен из куки
    article: str = Form(...),
    shop: str = Form(...),
    department: str = Form(...),
    quantity: int = Form(...),
    order_reason: str = Form(...)
):
    user = get_current_user(access_token) # <- Передаём токен из куки
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    try:
        quantity = int(quantity)
    except ValueError:
        raise HTTPException(status_code=400, detail="Количество должно быть числом")

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

# --- Запуск воркера (временно ручной эндпоинт для теста) ---
@app.get("/run_worker")
async def run_worker():
    # Только для тестирования, не использовать в проде без защиты!
    process_order_queue()
    return {"status": "ok"}
