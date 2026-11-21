'use strict';

const viewButtons = [...document.querySelectorAll('[data-view]')];
const reportPanels = document.querySelectorAll('[data-panel]');
const findings = [...document.querySelectorAll('#damage-list [data-damage]')];
const damagePhoto = document.getElementById('damage-photo');
const damageCaption = document.getElementById('damage-caption');

function showReportView(view) {
  for (const button of viewButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
  }
  for (const panel of reportPanels) {
    panel.hidden = panel.dataset.panel !== view;
  }
}

function showDamage(index) {
  const finding = findings[index];
  damagePhoto.src = finding.querySelector('img').src;
  const name = [...finding.querySelector('.damage-name').childNodes]
    .map(node => node.textContent.trim()).filter(Boolean).join(' ');
  damagePhoto.alt = name;
  damageCaption.textContent = [name, finding.querySelector('strong').textContent].join(' · ');
  showReportView('photo');
}

const summary = document.createElement('ol');
for (const [index, finding] of findings.entries()) {
  const item = document.createElement('li');
  const name = finding.querySelector('.damage-name').innerText.replace(/\s+/g, ' ').trim();
  item.textContent = name + ' — ' + finding.querySelector('strong').textContent;
  summary.append(item);
  finding.addEventListener('click', () => showDamage(index));
}
document.querySelector('.report-summary').append(summary);

for (const button of viewButtons) {
  button.addEventListener('click', () => {
    if (button.dataset.view === 'photo' && !damageCaption.textContent) {
      showDamage(0);
    } else {
      showReportView(button.dataset.view);
    }
  });
}
const angleButtons = [...document.querySelectorAll('[data-angle]')];
const reportAngle = document.getElementById('report-angle');
for (const button of angleButtons) {
  button.addEventListener('click', () => {
    for (const angle of angleButtons) angle.setAttribute('aria-pressed', String(angle === button));
    reportAngle.src = button.querySelector('img').src;
    reportAngle.alt = button.getAttribute('aria-label');
    document.getElementById('map-points').hidden = button.dataset.angle !== 'report';
  });
}
for (const button of document.querySelectorAll('.map-points button')) {
  button.addEventListener('click', () => showDamage(Number(button.dataset.damage)));
}

const faq = [...document.querySelectorAll('.faq-grid details')];
const expandFaq = document.getElementById('expand-faq');
function updateFaqLabel() {
  expandFaq.firstChild.textContent = faq.every(item => item.open) ? 'Свернуть все ' : 'Все вопросы ';
}
expandFaq.addEventListener('click', () => {
  const open = faq.some(item => !item.open);
  for (const item of faq) item.open = open;
  updateFaqLabel();
});
for (const item of faq) item.addEventListener('toggle', updateFaqLabel);

const input = document.getElementById('photo-input');
const selectPhoto = document.getElementById('select-photo');
const dropzone = document.getElementById('dropzone');
const status = document.getElementById('upload-status');

function savePhoto(file) {
  // Match the handoff already read by the main analysis page.
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('carscan_promo_v1', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('pending');
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const transaction = db.transaction('pending', 'readwrite');
      transaction.oncomplete = () => { db.close(); resolve(); };
      transaction.onabort = () => { db.close(); reject(transaction.error); };
      transaction.objectStore('pending').put(file, 'file');
    };
  });
}

async function submitPhoto(file) {
  if (!file || selectPhoto.disabled) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    status.textContent = 'Выберите фото в формате JPEG, PNG или WebP.';
    return;
  }
  if (file.size < 10 * 1024 || file.size > 10 * 1024 * 1024) {
    status.textContent = 'Размер фото должен быть от 10 КБ до 10 МБ.';
    return;
  }
  selectPhoto.disabled = true;
  status.textContent = 'Открываем страницу анализа…';
  try {
    await savePhoto(file);
    location.assign('/');
  } catch {
    status.textContent = 'Не удалось открыть фото. Попробуйте выбрать файл ещё раз.';
    selectPhoto.disabled = false;
  }
}

selectPhoto.addEventListener('click', () => input.click());
input.addEventListener('change', () => {
  const file = input.files[0];
  input.value = '';
  submitPhoto(file);
});
dropzone.addEventListener('dragover', event => {
  event.preventDefault();
  dropzone.classList.add('is-over');
});
dropzone.addEventListener('dragleave', event => {
  if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove('is-over');
});
dropzone.addEventListener('drop', event => {
  event.preventDefault();
  dropzone.classList.remove('is-over');
  submitPhoto(event.dataTransfer.files[0]);
});
