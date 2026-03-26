// Вспомогательная функция для получения куки по имени
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

let currentProduct = null;

async function searchProduct() {
    const article = document.getElementById('article').value.trim();
    const shop = document.getElementById('shop').value.trim();

    const errorDiv = document.getElementById('searchError');
    const btn = document.querySelector('#productSection ~ .card button'); // Кнопка "Найти товар"

    errorDiv.classList.add('hidden');
    errorDiv.textContent = '';

    if (!article || !shop) {
        errorDiv.textContent = 'Заполните артикул и номер магазина';
        errorDiv.classList.remove('hidden');
        return;
    }

    try {
        const token = getCookie("access_token");

        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': `Bearer ${token}` // <-- Отправляем токен в заголовке
            },
            body: new URLSearchParams({ article, shop })
        });

        const result = await response.json();

        if (response.ok && result.found) {
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
        } else {
            errorDiv.textContent = result.message || 'Товар не найден';
            errorDiv.classList.remove('hidden');
            document.getElementById('productSection').classList.add('hidden');
        }
    } catch (e) {
        console.error("Ошибка при поиске:", e);
        errorDiv.textContent = 'Ошибка соединения';
        errorDiv.classList.remove('hidden');
    }
}

document.getElementById('orderForm').onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    const statusDiv = document.getElementById('orderStatus');
    const btn = e.submitter;

    btn.disabled = true;
    btn.textContent = 'Отправка...';

    try {
        const token = getCookie("access_token"); // <-- Получаем токен и для отправки заказа

        // Для отправки заказа нам нужно добавить токен в заголовок.
        // FormData не позволяет добавить заголовки напрямую.
        // Поэтому преобразуем FormData в объект и отправим как form-encoded с заголовком.

        const formObj = {};
        for (const [key, value] of formData.entries()) {
            formObj[key] = value;
        }

        const response = await fetch('/api/order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': `Bearer ${token}` // <-- Отправляем токен и тут
            },
            body: new URLSearchParams(formObj)
        });

        const result = await response.json();

        if (response.ok) {
            statusDiv.innerHTML = `✅ <b>Заказ принят!</b><br> ID очереди: <code>${result.queue_id}</code>`;
            statusDiv.className = 'status success';
            statusDiv.classList.remove('hidden');

            setTimeout(() => {
                document.getElementById('productSection').classList.add('hidden');
                document.getElementById('article').value = '';
                statusDiv.classList.add('hidden');
            }, 3000);
        } else {
            statusDiv.textContent = `❌ ${result.detail || 'Ошибка создания заказа'}`;
            statusDiv.className = 'status error';
            statusDiv.classList.remove('hidden');
        }
    } catch (e) {
        console.error("Ошибка при отправке заказа:", e);
        statusDiv.textContent = '❌ Ошибка сети';
        statusDiv.className = 'status error';
        statusDiv.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = '✅ Подтвердить и отправить';
    }
};

// Поиск по Enter
document.getElementById('article').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') searchProduct();
});

// Выход
document.querySelector('.header a').onclick = () => {
    document.cookie = "access_token=; Max-Age=0; path=/";
};
