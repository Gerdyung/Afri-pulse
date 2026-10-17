const giftForm = document.getElementById('giftForm');
const giftButton = document.getElementById('giftBtn');
const giftMessage = document.getElementById('giftMessage');
const giftFeed = document.getElementById('giftFeed');

if (giftButton && giftForm) {
    giftButton.addEventListener('click', () => {
        giftForm.classList.toggle('hidden');
        if (!giftForm.classList.contains('hidden')) {
            giftForm.querySelector('[name="amount"]').focus();
        }
    });

    giftForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = giftForm.querySelector('[type="submit"]');
        const checkoutWindow = window.open('', '_blank');
        submitButton.disabled = true;
        giftMessage.textContent = 'Connecting to secure checkout...';

        try {
            const response = await fetch('/gift/initialize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    room: giftForm.dataset.room || (document.getElementById('room') || {}).value || 'default',
                    amount: giftForm.elements.amount.value,
                    email: giftForm.elements.email.value
                })
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.message || 'Unable to start checkout.');
            }
            if (checkoutWindow) {
                checkoutWindow.location = result.authorization_url;
                giftMessage.textContent = 'Checkout opened in a new tab. Your gift will appear after payment is confirmed.';
            } else {
                window.location.assign(result.authorization_url);
            }
        } catch (error) {
            if (checkoutWindow) checkoutWindow.close();
            giftMessage.textContent = error.message || 'Unable to start checkout. Please try again.';
        } finally {
            submitButton.disabled = false;
        }
    });
}

const giftStatus = new URLSearchParams(window.location.search).get('gift');
if (giftMessage && giftStatus === 'sent') {
    giftMessage.textContent = 'Payment confirmed — your gift has been sent!';
} else if (giftMessage && giftStatus === 'failed') {
    giftMessage.textContent = 'Payment could not be confirmed. Check your receipt or contact support before retrying.';
}

if (giftFeed && window.liveSocket) {
    window.liveSocket.on('gift', (gift) => {
        const amount = Number(gift.amount);
        const formattedAmount = Number.isFinite(amount)
            ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(amount / 100)
            : '';
        const notification = document.createElement('div');
        notification.className = 'gift-toast';
        notification.setAttribute('role', 'status');

        const sparkle = document.createElement('span');
        sparkle.className = 'gift-toast__icon';
        sparkle.setAttribute('aria-hidden', 'true');
        sparkle.textContent = '🎁';

        const copy = document.createElement('span');
        copy.className = 'gift-toast__copy';
        const title = document.createElement('strong');
        title.className = 'gift-toast__title';
        title.textContent = 'Gift sent!';
        const detail = document.createElement('span');
        detail.className = 'gift-toast__detail';
        detail.textContent = formattedAmount ? `A viewer sent ₦${formattedAmount}` : 'A viewer sent a gift';
        copy.append(title, detail);
        notification.append(sparkle, copy);

        for (let index = 0; index < 7; index += 1) {
            const particle = document.createElement('span');
            particle.className = 'gift-toast__particle';
            particle.style.setProperty('--particle-index', String(index));
            particle.setAttribute('aria-hidden', 'true');
            notification.appendChild(particle);
        }

        giftFeed.appendChild(notification);

        while (giftFeed.children.length > 3) {
            const oldestToast = giftFeed.firstElementChild;
            oldestToast.classList.add('gift-toast--leaving');
            window.setTimeout(() => oldestToast.remove(), 450);
        }

        window.setTimeout(() => {
            notification.classList.add('gift-toast--leaving');
            window.setTimeout(() => notification.remove(), 450);
        }, 6500);
    });
}
