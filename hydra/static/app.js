// Копирование, диалоги, подтверждения. Никаких зависимостей.
document.addEventListener('click', function (e) {
  const copyBtn = e.target.closest('[data-copy]');
  if (copyBtn) {
    const sel = copyBtn.getAttribute('data-copy');
    const el = sel ? document.querySelector(sel) : copyBtn;
    const text = el.dataset.text || el.textContent || '';
    navigator.clipboard.writeText(text.trim()).then(() => {
      const old = copyBtn.textContent;
      copyBtn.textContent = 'Скопировано';
      setTimeout(() => { copyBtn.textContent = old; }, 1200);
    });
    return;
  }

  const openBtn = e.target.closest('[data-open]');
  if (openBtn) {
    const d = document.getElementById(openBtn.getAttribute('data-open'));
    if (d) d.showModal();
    return;
  }

  const closeBtn = e.target.closest('[data-close]');
  if (closeBtn) {
    const d = closeBtn.closest('dialog');
    if (d) d.close();
    return;
  }
});

document.addEventListener('submit', function (e) {
  const form = e.target;
  const text = form.getAttribute('data-confirm');
  if (text && !window.confirm(text)) {
    e.preventDefault();
    return;
  }
  const btn = form.querySelector('button[type=submit]');
  if (btn && form.getAttribute('data-busy') !== 'off') {
    setTimeout(() => { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = '…'; }, 10);
  }
});

// авто-сабмит фильтров
document.addEventListener('change', function (e) {
  const el = e.target;
  if (el.matches('[data-autosubmit]')) el.form.submit();
});

// закрытие диалога по клику на фон
document.addEventListener('mousedown', function (e) {
  if (e.target.tagName === 'DIALOG') {
    const r = e.target.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
      e.target.close();
    }
  }
});

// показывать поля, относящиеся к выбранному протоколу/транспорту
function syncInboundForm() {
  const proto = document.querySelector('[name=protocol]');
  if (!proto) return;
  const net = document.querySelector('[name=network]');
  const sec = document.querySelector('[name=security]');
  const p = proto.value, n = net ? net.value : '', s = sec ? sec.value : '';
  const show = (name, on) => {
    document.querySelectorAll('[data-show="' + name + '"]').forEach(el => {
      el.classList.toggle('hidden', !on);
    });
  };
  show('net', p !== 'hysteria2');
  show('sec', p !== 'hysteria2');
  show('path', p !== 'hysteria2' && (n === 'ws' || n === 'xhttp'));
  show('grpc', p !== 'hysteria2' && n === 'grpc');
  show('reality', p !== 'hysteria2' && s === 'reality');
  show('obfs', p === 'hysteria2');
  show('flow', p === 'vless' && (n === 'raw' || n === 'tcp'));
}
document.addEventListener('change', function (e) {
  if (e.target.matches('[name=protocol],[name=network],[name=security]')) syncInboundForm();
});
document.addEventListener('DOMContentLoaded', syncInboundForm);
