'use strict';

const uploadDialog = document.getElementById('uploadDialog');
for (const link of document.querySelectorAll('[data-check]')) {
  link.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    uploadDialog.showModal();
  });
}

const defects = [...document.querySelectorAll('[data-defect]')];
for (const button of defects) {
  button.addEventListener('click', () => {
    for (const item of defects) {
      item.setAttribute('aria-pressed', String(item.dataset.defect === button.dataset.defect));
    }
  });
}

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
