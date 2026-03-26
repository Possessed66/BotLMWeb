// === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
let currentProduct = null;

// === ФУНКЦИЯ: Поиск товара ===
async function searchProduct() {
    const article = document.getElementById('article').value.trim();
    const shop = document.getElementById('shop').value.trim();

    const errorDiv = document.getElementById('searchError');
    errorDiv.classList.add('hidden');

    if (!article || !shop) {
        errorDiv.textContent = '⚠️ Заполните артикул и номер магазина';
        errorDiv.classList.remove('hidden');
        return;
    }

    try {
        // Отправляем запрос с куками (включая HttpOnly)
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({ article, shop }),
            credentials: 'include'  // 🔑 Обязательно! Чтобы браузер отправил куку access_token
        });

        const result = await response.json();

        if (response.ok && result.found) {
            currentProduct = result.data;

            document.getElementById('pArticle').textContent = result.data['Артикул'] || '—';
            document.getElementById('pName').textContent = result.data['Название'] || '—';
            document.getElementById('pDepartment').textContent = result.data['Отдел'] || '—';
            document.getElementById('pSupplier').textContent = result.data['Поставщик'] || '—';
            document.getElementById('pOrderDate').textContent = result.data['Дата заказа'] || '—';
            document.getElementById('pDeliveryDate').textContent = result.data['Дата поставки'] || '—';

            document.getElementById('formShop').value = result.data['Магазин'] || '';
            document.getElementById('formArticle').value = result.data['Артикул'] || '';
            document.getElementById('formDepartment').value = result.data['Отдел'] || '';

            document.getElementById('productSection').classList.remove('hidden');
            document.getElementById('orderStatus').classList.add('hidden');
            document.getElementById('newOrderBtn').classList.add('hidden');
            document.querySelector('#orderForm button[type="submit"]').style.display = 'block';
        } else {
            errorDiv.innerHTML = `🔍 ${result.message || 'Товар не найден'}`;
            errorDiv.classList.remove('hidden');
            document.getElementById('productSection').classList.add('hidden');
        }
    } catch (e) {
        console.error('Ошибка поиска:', e);
        errorDiv.textContent = '🌐 Ошибка соединения с сервером';
        errorDiv.classList.remove('hidden');
    }
}

// === ФУНКЦИЯ: Отправка заказа ===
document.getElementById('orderForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const statusDiv = document.getElementById('orderStatus');
    const submitBtn = e.target.querySelector('button[type="submit"]');
    const newOrderBtn = document.getElementById('newOrderBtn');

    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ Отправка...';

    try {
        const formData = new FormData(e.target);
        const formObj = {};
        for (const [key, value] of formData.entries()) {
            formObj[key] = value;
        }

        const response = await fetch('/api/order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams(formObj),
            credentials: 'include'  // 🔑 Обязательно!
        });

        const result = await response.json();

        if (response.ok) {
            statusDiv.innerHTML = `
                ✅ <strong>Заказ принят!</strong><br>
                ID очереди: <code>${result.queue_id}</code>
            `;
            statusDiv.className = 'status success';
            statusDiv.classList.remove('hidden');

            // Скрываем кнопку формы, показываем "Новый заказ"
            submitBtn.style.display = 'none';
            newOrderBtn.classList.remove('hidden');
        } else {
            statusDiv.textContent = `❌ ${result.detail || 'Ошибка создания заказа'}`;
            statusDiv.className = 'status error';
            statusDiv.classList.remove('hidden');
        }
    } catch (e) {
        console.error('Ошибка отправки заказа:', e);
        statusDiv.textContent = '❌ Сетевая ошибка. Проверьте подключение.';
        statusDiv.className = 'status error';
        statusDiv.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = '✅ Подтвердить и отправить';
    }
});

// === ФУНКЦИЯ: Сброс формы (Новый заказ) ===
function resetForm() {
    document.getElementById('productSection').classList.add('hidden');
    document.getElementById('article').value = '';
    document.getElementById('shop').value = '';
    document.getElementById('pArticle').textContent = '';
    document.getElementById('pName').textContent = '';
    document.getElementById('pDepartment').textContent = '';
    document.getElementById('pSupplier').textContent = '';
    document.getElementById('pOrderDate').textContent = '';
    document.getElementById('pDeliveryDate').textContent = '';
    document.getElementById('orderStatus').classList.add('hidden');
    document.getElementById('newOrderBtn').classList.add('hidden');
    document.querySelector('#orderForm button[type="submit"]').style.display = 'block';
    document.getElementById('orderForm').reset();
}

// === Обработчик Enter для поиска ===
document.getElementById('article').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') searchProduct();
});
