# Изменения: Обработка истёкших куки и уведомление пользователя

## Проблема
При истечении JWT-токена (куки `access_token`):
- Не обрабатывался logout корректно
- Пользователь не получал никакого уведомления о причине перенаправления на страницу входа
- Просто происходил редирект без объяснения причин

## Решение

### 1. Бэкенд (`main.py`)

#### Новая функция `decode_access_token()`
```python
def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except jwt.ExpiredSignatureError:
        # Токен истёк
        return "EXPIRED"
    except jwt.exceptions.PyJWTError:
        # Другие ошибки токена (невалидный, повреждённый и т.д.)
        return "INVALID"
```

#### Новая функция `get_token_status()`
Возвращает кортеж `(user, error_message)`:
- `user` - объект пользователя или `None`
- `error_message` - понятное сообщение об ошибке или `None` если всё ок

Возможные сообщения:
- `"Сессия истекла. Пожалуйста, войдите снова."` - токен истёк
- `"Невалидная сессия. Пожалуйста, войдите снова."` - токен повреждён
- `"Требуется авторизация"` - токена нет
- `"Пользователь не найден"` - пользователь удалён из БД

#### Обновлённые эндпоинты
Все защищённые маршруты теперь используют `get_token_status()`:
- `/` (root)
- `/app`
- `/notifications`
- `/api/search`
- `/api/order`
- `/api/notifications`
- `/api/notifications/read`

#### Улучшенный logout
```python
@app.get("/logout")
async def logout():
    response = RedirectResponse(
        url="/login?error_msg=Вы успешно вышли из системы", 
        status_code=303
    )
    response.delete_cookie(key="access_token")
    return response
```

### 2. Фронтенд (`templates/login.html`)

#### Добавлен стиль для успешных сообщений
```css
.success {
    color: #16a34a;
    font-size: 0.9rem;
    margin-top: 8px;
    text-align: center;
    background-color: #f0fdf4;
    padding: 8px;
    border-radius: 6px;
    border-left: 4px solid #16a34a;
}
```

#### Условное отображение сообщений
```html
{% if error %}
    {% if "вышли" in error or "Вышли" in error %}
        <div class="success">{{ error }}</div>
    {% else %}
        <div class="error">{{ error }}</div>
    {% endif %}
{% endif %}
```

## Результат

### Сценарий 1: Истечение токена
1. Пользователь заходит на `/app` с истёкшим токеном
2. Система определяет истечение токена
3. Перенаправляет на `/login?error_msg=Сессия%20истекла.%20Пожалуйста%2C%20войдите%20снова.`
4. Пользователь видит зелёное сообщение: "Сессия истекла. Пожалуйста, войдите снова."

### Сценарий 2: Ручной logout
1. Пользователь нажимает "Выйти"
2. Куки удаляются
3. Перенаправление на `/login?error_msg=Вы%20успешно%20вышли%20из%20системы`
4. Пользователь видит зелёное сообщение: "Вы успешно вышли из системы"

### Сценарий 3: Невалидный токен
1. Пользователь пытается получить доступ с повреждённым токеном
2. Система определяет невалидность
3. Перенаправляет на `/login?error_msg=Невалидная%20сессия.%20Пожалуйста%2C%20войдите%20снова.`
4. Пользователь видит красное сообщение об ошибке

## Тесты
Все изменения протестированы:
- ✓ Валидный токен распознаётся корректно
- ✓ Истёкший токен возвращает "EXPIRED"
- ✓ Невалидный токен возвращает "INVALID"
- ✓ `get_token_status()` возвращает правильные сообщения
- ✓ Шаблон login.html отображает успех/ошибку правильно
- ✓ Редиректы работают с параметром `error_msg`
- ✓ Logout удаляет куки через `Set-Cookie` заголовок
