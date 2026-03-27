from main import SessionLocal, User, get_password_hash

def change_password():
    print("--- Смена пароля пользователя ---")
    username = input("Введите логин пользователя: ").strip()
    
    if not username:
        print("Ошибка: Логин не может быть пустым.")
        return

    new_pass = input("Введите новый пароль: ").strip()
    
    if len(new_pass) < 6:
        print("Ошибка: Пароль должен быть не менее 6 символов.")
        return

    db = SessionLocal()
    try:
        # Ищем пользователя
        user = db.query(User).filter(User.username == username).first()
        
        if user:
            # Меняем пароль
            user.hashed_password = get_password_hash(new_pass)
            db.commit()
            print(f"\n✅ Успех! Пароль для '{username}' изменен.")
        else:
            print(f"\n❌ Ошибка: Пользователь '{username}' не найден в базе.")
    except Exception as e:
        print(f"\n❌ Произошла ошибка: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    change_password()
