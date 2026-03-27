from main import SessionLocal, User, get_password_hash

username = input("Введите логин пользователя: ")
new_pass = input("Введите новый пароль: ")

db = SessionLocal()
user = db.query(User).filter(User.username == username).first()
if user:
    user.hashed_password = get_password_hash(new_pass)
    db.commit()
    print(f"Пароль для {username} успешно изменен.")
else:
    print("Пользователь не найден.")
db.close()
