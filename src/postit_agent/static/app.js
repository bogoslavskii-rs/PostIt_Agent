const state = {
  token: localStorage.getItem("postit_token") || "",
  currentPropertyId: "",
  currentUserEmail: "",
  currentSnapshot: null,
};

const logOutput = document.getElementById("log-output");
const snapshotOutput = document.getElementById("snapshot-output");
const publicationOutput = document.getElementById("publication-output");
const propertyList = document.getElementById("property-list");
const currentPropertyLabel = document.getElementById("current-property-label");
const sessionBadge = document.getElementById("session-badge");
const mediaOutput = document.getElementById("media-output");
const evidenceOutput = document.getElementById("evidence-output");

function log(message, payload) {
  const timestamp = new Date().toLocaleTimeString("ru-RU");
  const block = `[${timestamp}] ${message}${payload ? `\n${JSON.stringify(payload, null, 2)}` : ""}\n\n`;
  logOutput.textContent = block + logOutput.textContent;
}

async function api(path, options = {}) {
  const config = { method: "GET", ...options, headers: { ...(options.headers || {}) } };
  if (state.token) {
    config.headers.Authorization = `Bearer ${state.token}`;
  }
  if (options.body && !(options.body instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, config);
  const contentType = response.headers.get("content-type") || "";
  const rawText = await response.text();
  const data = contentType.includes("application/json") && rawText ? JSON.parse(rawText) : rawText;

  if (!response.ok) {
    const message = data?.error?.message || data?.detail || rawText || "Запрос завершился ошибкой";
    throw new Error(message);
  }
  return data;
}

function selectedPlatforms() {
  return [...document.querySelectorAll(".platform-toggle:checked")].map((input) => input.value);
}

function requireProperty() {
  if (!state.currentPropertyId) {
    throw new Error("Сначала создай или выбери объект.");
  }
}

function setSession(email) {
  state.currentUserEmail = email || "";
  sessionBadge.textContent = email ? `Сессия: ${email}` : "Гость";
}

function profilePayload() {
  return {
    name: document.getElementById("profile-name").value || null,
    agency_name: document.getElementById("profile-agency").value || null,
    city: document.getElementById("profile-city").value || null,
    contact_phone: document.getElementById("profile-contact-phone").value || null,
    contact_email: document.getElementById("profile-contact-email").value || null,
    bio: document.getElementById("profile-bio").value || null,
    platform_accounts: {
      avito: { autoload_enabled: document.getElementById("toggle-avito").checked, manual_export_allowed: true },
      cian: { autoload_enabled: document.getElementById("toggle-cian").checked, manual_export_allowed: true },
      yandex_realty: { autoload_enabled: document.getElementById("toggle-yandex").checked, manual_export_allowed: true },
      youla: { autoload_enabled: document.getElementById("toggle-youla").checked, manual_export_allowed: true },
      domclick: { autoload_enabled: document.getElementById("toggle-domclick").checked, manual_export_allowed: true },
    },
  };
}

function propertyPayload() {
  return {
    confirmed: document.getElementById("property-confirmed").checked,
    address: {
      city: document.getElementById("property-city").value || null,
      street: document.getElementById("property-street").value || null,
      house: document.getElementById("property-house").value || null,
      apartment: document.getElementById("property-apartment").value || null,
    },
    features: {
      rooms: Number(document.getElementById("property-rooms").value) || null,
      area_total: Number(document.getElementById("property-area-total").value) || null,
      area_kitchen: Number(document.getElementById("property-area-kitchen").value) || null,
      floor: Number(document.getElementById("property-floor").value) || null,
      floors_total: Number(document.getElementById("property-floors-total").value) || null,
      building_type: document.getElementById("property-building-type").value || null,
      renovation: document.getElementById("property-renovation").value || null,
    },
    price: Number(document.getElementById("property-price").value) || null,
  };
}

function renderProperties(items) {
  if (!items.length) {
    propertyList.innerHTML = `<div class="property-item"><strong>Пока пусто</strong><span>Создай первый объект справа.</span></div>`;
    return;
  }

  propertyList.innerHTML = items
    .map((item) => {
      const active = item.id === state.currentPropertyId ? "active" : "";
      return `
        <button class="property-item ${active}" data-property-id="${item.id}">
          <strong>${item.title_base || item.address?.street || "Новый объект"}</strong>
          <span>${item.address?.city || "Город не указан"} · ${item.price ? `${item.price.toLocaleString("ru-RU")} ₽` : "цена не указана"}</span>
          <span>${item.status}</span>
        </button>
      `;
    })
    .join("");

  document.querySelectorAll("[data-property-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.currentPropertyId = button.dataset.propertyId;
      await loadCurrentProperty();
      await refreshProperties();
    });
  });
}

function renderSnapshot(snapshot) {
  state.currentSnapshot = snapshot;
  snapshotOutput.textContent = JSON.stringify(snapshot, null, 2);
  currentPropertyLabel.textContent = snapshot.property.title_base || snapshot.property.address?.street || snapshot.property.id;
}

function renderMedia(snapshot) {
  const photos = snapshot?.photos || [];
  const voiceNotes = snapshot?.voice_notes || [];
  if (!photos.length && !voiceNotes.length) {
    mediaOutput.innerHTML = `<div class="card"><strong>Медиа пока нет</strong><p>Загрузи фото и голосовую заметку.</p></div>`;
    return;
  }

  const photoCards = photos
    .map((photo, index) => {
      const warnings = (photo.warnings || []).map((item) => `<li>${item}</li>`).join("");
      return `
        <div class="card media-card">
          <img src="${photo.public_url}" alt="${photo.original_name}" class="media-thumb" />
          <strong>${photo.original_name}</strong>
          <p>Score: ${(photo.quality_score || 0).toFixed(2)} ${photo.is_main ? "· Обложка" : ""}</p>
          <p>Размер: ${photo.analysis?.width || "?"}×${photo.analysis?.height || "?"}</p>
          ${warnings ? `<ul>${warnings}</ul>` : ""}
          <div class="action-row media-actions">
            <button class="mini-btn" data-cover-photo="${photo.id}">Сделать обложкой</button>
            <button class="mini-btn" data-move-photo="${photo.id}" data-direction="-1" ${index === 0 ? "disabled" : ""}>↑</button>
            <button class="mini-btn" data-move-photo="${photo.id}" data-direction="1" ${index === photos.length - 1 ? "disabled" : ""}>↓</button>
            <button class="mini-btn danger-btn" data-delete-media="${photo.id}">Удалить</button>
          </div>
        </div>
      `;
    })
    .join("");

  const voiceCards = voiceNotes
    .map(
      (note) => `
        <div class="card">
          <strong>${note.original_name}</strong>
          <p>STT: ${note.provider || "mock"} · ${note.status}</p>
          <p>${note.transcript || "Транскрипт пока не готов"}</p>
          ${note.error_text ? `<p><strong>Ошибка:</strong> ${note.error_text}</p>` : ""}
          <button class="mini-btn danger-btn" data-delete-media="${note.id}">Удалить</button>
        </div>
      `
    )
    .join("");

  mediaOutput.innerHTML = photoCards + voiceCards;

  document.querySelectorAll("[data-cover-photo]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/v1/properties/${state.currentPropertyId}/media/${button.dataset.coverPhoto}`, {
        method: "PATCH",
        body: JSON.stringify({ is_main: true }),
      });
      await loadCurrentProperty();
    });
  });

  document.querySelectorAll("[data-move-photo]").forEach((button) => {
    button.addEventListener("click", async () => {
      const photoId = button.dataset.movePhoto;
      const direction = Number(button.dataset.direction);
      await movePhoto(photoId, direction);
    });
  });

  document.querySelectorAll("[data-delete-media]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/v1/properties/${state.currentPropertyId}/media/${button.dataset.deleteMedia}`, { method: "DELETE" });
      await loadCurrentProperty();
    });
  });
}

function renderEvidence(items) {
  if (!items?.length) {
    evidenceOutput.innerHTML = `<div class="card"><strong>AI-подсказок пока нет</strong><p>Сначала загрузи голос и нажми «Извлечь поля».</p></div>`;
    return;
  }

  evidenceOutput.innerHTML = items
    .map((item) => {
      const status = item.is_confirmed ? "Подтверждено" : item.is_rejected ? "Отклонено" : "Требует решения";
      return `
        <div class="card evidence-card">
          <strong>${item.field_name}</strong>
          <p>Значение: ${item.value ?? "—"}</p>
          <p>Статус: ${status}</p>
          <p>Confidence: ${item.confidence ?? "—"}</p>
          ${item.source_quote ? `<p><em>Фрагмент: ${item.source_quote}</em></p>` : ""}
          ${
            !item.is_confirmed && !item.is_rejected
              ? `<div class="action-row">
                  <button class="mini-btn" data-confirm-evidence="${item.id}">Подтвердить</button>
                  <button class="mini-btn danger-btn" data-reject-evidence="${item.id}">Отклонить</button>
                </div>`
              : ""
          }
        </div>
      `;
    })
    .join("");

  document.querySelectorAll("[data-confirm-evidence]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/v1/properties/${state.currentPropertyId}/evidence/${button.dataset.confirmEvidence}/confirm`, {
        method: "POST",
      });
      await loadCurrentProperty();
      log("AI-поле подтверждено");
    });
  });

  document.querySelectorAll("[data-reject-evidence]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/v1/properties/${state.currentPropertyId}/evidence/${button.dataset.rejectEvidence}/reject`, {
        method: "POST",
      });
      await loadCurrentProperty();
      log("AI-поле отклонено");
    });
  });
}

function renderPublication(data) {
  if (!data) {
    publicationOutput.innerHTML = "";
    return;
  }

  const exportLink = data.export_url
    ? `<p><a href="${data.export_url}" target="_blank" rel="noreferrer">Скачать ZIP assisted-пакет</a></p>`
    : "";
  const validations = (data.validations || [])
    .map((item) => `<li>${item.platform}: ${item.ready ? "готово" : item.errors.join("; ")}</li>`)
    .join("");
  const jobs = (data.jobs || [])
    .map((job) => {
      const errors = (job.errors || []).length ? `<p><strong>Ошибки:</strong> ${job.errors.join("; ")}</p>` : "";
      const notes = (job.notes || []).length ? `<p><strong>Заметки:</strong> ${job.notes.join("; ")}</p>` : "";
      const feedLink = job.feed_url ? `<a href="${job.feed_url}" target="_blank" rel="noreferrer">Открыть feed</a>` : "";
      return `
        <div class="card">
          <strong>${job.platform}</strong>
          <p>Статус: ${job.status}</p>
          <p>Канал: ${job.channel}</p>
          <div class="link-list">${feedLink}</div>
          ${errors}
          ${notes}
        </div>
      `;
    })
    .join("");

  publicationOutput.innerHTML = `
    <div class="card">
      <strong>Публикационный центр</strong>
      ${exportLink}
      <ul>${validations}</ul>
    </div>
    ${jobs}
  `;
}

async function movePhoto(photoId, direction) {
  const photos = [...(state.currentSnapshot?.photos || [])].sort((left, right) => left.order_index - right.order_index);
  const index = photos.findIndex((item) => item.id === photoId);
  const targetIndex = index + direction;
  if (index < 0 || targetIndex < 0 || targetIndex >= photos.length) {
    return;
  }
  const swap = photos[targetIndex];
  [photos[index], photos[targetIndex]] = [photos[targetIndex], photos[index]];
  const payload = {
    items: photos.map((photo, orderIndex) => ({ media_id: photo.id, order_index: orderIndex })),
    cover_media_id: photos.find((photo) => photo.is_main)?.id || null,
  };
  await api(`/api/v1/properties/${state.currentPropertyId}/media/order`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  if (swap) {
    log("Порядок фотографий обновлен", { moved: photoId, swappedWith: swap.id });
  }
  await loadCurrentProperty();
}

async function loadProfile() {
  const profile = await api("/me/profile");
  document.getElementById("profile-name").value = profile.name || "";
  document.getElementById("profile-agency").value = profile.agency_name || "";
  document.getElementById("profile-city").value = profile.city || "";
  document.getElementById("profile-contact-phone").value = profile.contact_phone || "";
  document.getElementById("profile-contact-email").value = profile.contact_email || "";
  document.getElementById("profile-bio").value = profile.bio || "";
  document.getElementById("toggle-avito").checked = !!profile.platform_accounts?.avito?.autoload_enabled;
  document.getElementById("toggle-cian").checked = profile.platform_accounts?.cian?.autoload_enabled ?? true;
  document.getElementById("toggle-yandex").checked = profile.platform_accounts?.yandex_realty?.autoload_enabled ?? true;
  document.getElementById("toggle-youla").checked = profile.platform_accounts?.youla?.autoload_enabled ?? true;
  document.getElementById("toggle-domclick").checked = !!profile.platform_accounts?.domclick?.autoload_enabled;
}

async function refreshProperties() {
  const items = await api("/properties");
  renderProperties(items);
}

async function loadCurrentProperty() {
  requireProperty();
  const snapshot = await api(`/properties/${state.currentPropertyId}`);
  renderSnapshot(snapshot);
  renderMedia(snapshot);
  renderEvidence(snapshot.evidence || []);
  const jobs = await api(`/api/v1/properties/${state.currentPropertyId}/publications`);
  renderPublication({ jobs, validations: [], export_url: jobs[0]?.export_url || null });
}

document.getElementById("register-button").addEventListener("click", async () => {
  try {
    const response = await api("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email: document.getElementById("auth-email").value,
        password: document.getElementById("auth-password").value,
        phone: document.getElementById("auth-phone").value || null,
      }),
    });
    state.token = response.access_token;
    localStorage.setItem("postit_token", response.access_token);
    setSession(response.user.email);
    await loadProfile();
    await refreshProperties();
    log("Регистрация успешна", response.user);
  } catch (error) {
    log(`Ошибка регистрации: ${error.message}`);
  }
});

document.getElementById("login-button").addEventListener("click", async () => {
  try {
    const response = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: document.getElementById("auth-email").value,
        password: document.getElementById("auth-password").value,
      }),
    });
    state.token = response.access_token;
    localStorage.setItem("postit_token", response.access_token);
    setSession(response.user.email);
    await loadProfile();
    await refreshProperties();
    log("Вход выполнен", response.user);
  } catch (error) {
    log(`Ошибка входа: ${error.message}`);
  }
});

document.getElementById("profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const profile = await api("/me/profile", { method: "PATCH", body: JSON.stringify(profilePayload()) });
    log("Профиль сохранен", profile);
  } catch (error) {
    log(`Ошибка профиля: ${error.message}`);
  }
});

document.getElementById("property-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const property = await api("/properties", { method: "POST", body: JSON.stringify(propertyPayload()) });
    state.currentPropertyId = property.id;
    await refreshProperties();
    await loadCurrentProperty();
    log("Объект создан", property);
  } catch (error) {
    log(`Ошибка объекта: ${error.message}`);
  }
});

document.getElementById("refresh-properties-button").addEventListener("click", async () => {
  try {
    await refreshProperties();
    if (state.currentPropertyId) {
      await loadCurrentProperty();
    }
    log("Список объектов обновлен");
  } catch (error) {
    log(`Ошибка обновления списка: ${error.message}`);
  }
});

document.getElementById("upload-photos-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const input = document.getElementById("photo-files");
    if (!input.files?.length) {
      throw new Error("Выбери хотя бы одну фотографию.");
    }
    const formData = new FormData();
    [...input.files].forEach((file) => formData.append("files", file));
    const snapshot = await api(`/properties/${state.currentPropertyId}/photos`, { method: "POST", body: formData });
    renderSnapshot(snapshot);
    renderMedia(snapshot);
    log("Фотографии загружены", { count: snapshot.photos.length });
  } catch (error) {
    log(`Ошибка загрузки фото: ${error.message}`);
  }
});

document.getElementById("upload-voice-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const transcript = document.getElementById("voice-transcript").value.trim();
    const voiceFile = document.getElementById("voice-file").files?.[0];
    if (!voiceFile && !transcript) {
      throw new Error("Выбери аудиофайл или добавь текст транскрипта.");
    }
    const formData = new FormData();
    if (voiceFile) {
      formData.append("file", voiceFile);
    } else {
      formData.append("file", new Blob([transcript], { type: "text/plain" }), "voice.txt");
    }
    if (transcript) {
      formData.append("transcript_override", transcript);
    }
    const snapshot = await api(`/properties/${state.currentPropertyId}/voice`, { method: "POST", body: formData });
    renderSnapshot(snapshot);
    renderMedia(snapshot);
    log("Голосовая заметка сохранена", snapshot.voice_notes?.[0] || {});
  } catch (error) {
    log(`Ошибка голосовой заметки: ${error.message}`);
  }
});

document.getElementById("extract-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/api/v1/properties/${state.currentPropertyId}/extract`, { method: "POST" });
    snapshotOutput.textContent = JSON.stringify(response, null, 2);
    renderEvidence(response.evidence || []);
    await loadCurrentProperty();
    log("AI извлек поля из транскрипта", response.fields);
  } catch (error) {
    log(`Ошибка AI extraction: ${error.message}`);
  }
});

document.getElementById("generate-copy-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/api/v1/properties/${state.currentPropertyId}/generate-copy`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    snapshotOutput.textContent = JSON.stringify(response, null, 2);
    publicationOutput.innerHTML = Object.entries(response.platform_copy || {})
      .map(
        ([platform, value]) => `
          <div class="card">
            <strong>${platform}</strong>
            <p>${value.title}</p>
            <p>${value.description}</p>
          </div>
        `
      )
      .join("");
    log("AI сгенерировал тексты", { titles: response.titles });
  } catch (error) {
    log(`Ошибка copy generation: ${error.message}`);
  }
});

document.getElementById("validate-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/api/v1/properties/${state.currentPropertyId}/validate`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    renderPublication({ validations: response, jobs: [], export_url: null });
    log("Платформенная валидация выполнена", response);
  } catch (error) {
    log(`Ошибка валидации: ${error.message}`);
  }
});

document.getElementById("publish-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/api/v1/properties/${state.currentPropertyId}/publications`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    renderPublication(response);
    await loadCurrentProperty();
    log("Фиды и assisted export собраны", response);
  } catch (error) {
    log(`Ошибка публикации: ${error.message}`);
  }
});

async function bootstrap() {
  if (!state.token) {
    log("Сессия не найдена. Зарегистрируйся или войди.");
    return;
  }
  try {
    const user = await api("/api/v1/auth/me");
    setSession(user.email || state.currentUserEmail || "Авторизован");
    await loadProfile();
    await refreshProperties();
    log("Сессия восстановлена");
  } catch (error) {
    localStorage.removeItem("postit_token");
    state.token = "";
    log(`Не удалось восстановить сессию: ${error.message}`);
  }
}

bootstrap();
