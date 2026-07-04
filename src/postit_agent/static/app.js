const state = {
  token: localStorage.getItem("postit_token") || "",
  currentPropertyId: "",
  currentUserEmail: "",
};

const logOutput = document.getElementById("log-output");
const snapshotOutput = document.getElementById("snapshot-output");
const publicationOutput = document.getElementById("publication-output");
const propertyList = document.getElementById("property-list");
const currentPropertyLabel = document.getElementById("current-property-label");
const sessionBadge = document.getElementById("session-badge");

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
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || "Запрос завершился ошибкой");
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

function renderPublication(data) {
  if (!data) {
    publicationOutput.innerHTML = "";
    return;
  }
  const jobs = (data.jobs || [])
    .map((job) => {
      const errors = (job.errors || []).length ? `<p><strong>Ошибки:</strong> ${job.errors.join("; ")}</p>` : "";
      const notes = (job.notes || []).length ? `<p><strong>Заметки:</strong> ${job.notes.join("; ")}</p>` : "";
      const feedLink = job.feed_url ? `<a href="${job.feed_url}" target="_blank" rel="noreferrer">Открыть фид</a>` : "";
      const exportLink = job.export_url ? `<a href="${job.export_url}" target="_blank" rel="noreferrer">Скачать ZIP</a>` : "";
      return `
        <div class="card">
          <strong>${job.platform}</strong>
          <p>Статус: ${job.status}</p>
          <p>Канал: ${job.channel}</p>
          <div class="link-list">${feedLink} ${exportLink}</div>
          ${errors}
          ${notes}
        </div>
      `;
    })
    .join("");

  const validations = (data.validations || [])
    .map((item) => `<li>${item.platform}: ${item.ready ? "готово" : "нужны правки"}</li>`)
    .join("");

  publicationOutput.innerHTML = `
    <div class="card">
      <strong>Валидация</strong>
      <ul>${validations}</ul>
      ${data.export_url ? `<p><a href="${data.export_url}" target="_blank" rel="noreferrer">Общий экспортный архив</a></p>` : ""}
    </div>
    ${jobs}
  `;
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
  currentPropertyLabel.textContent = snapshot.property.title_base || snapshot.property.address?.street || snapshot.property.id;
  snapshotOutput.textContent = JSON.stringify(snapshot, null, 2);
}

document.getElementById("register-button").addEventListener("click", async () => {
  try {
    const payload = {
      email: document.getElementById("auth-email").value,
      password: document.getElementById("auth-password").value,
      phone: document.getElementById("auth-phone").value || null,
    };
    const result = await api("/auth/register", { method: "POST", body: JSON.stringify(payload) });
    state.token = result.access_token;
    localStorage.setItem("postit_token", state.token);
    setSession(result.user.email);
    await loadProfile();
    await refreshProperties();
    log("Аккаунт создан", result.user);
  } catch (error) {
    log(`Ошибка регистрации: ${error.message}`);
  }
});

document.getElementById("login-button").addEventListener("click", async () => {
  try {
    const payload = {
      email: document.getElementById("auth-email").value,
      password: document.getElementById("auth-password").value,
    };
    const result = await api("/auth/login", { method: "POST", body: JSON.stringify(payload) });
    state.token = result.access_token;
    localStorage.setItem("postit_token", state.token);
    setSession(result.user.email);
    await loadProfile();
    await refreshProperties();
    log("Вход выполнен", result.user);
  } catch (error) {
    log(`Ошибка логина: ${error.message}`);
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
    const payload = {
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
    const property = await api("/properties", { method: "POST", body: JSON.stringify(payload) });
    state.currentPropertyId = property.id;
    await refreshProperties();
    await loadCurrentProperty();
    log("Объект создан", property);
  } catch (error) {
    log(`Ошибка создания объекта: ${error.message}`);
  }
});

document.getElementById("refresh-properties-button").addEventListener("click", async () => {
  try {
    await refreshProperties();
  } catch (error) {
    log(`Ошибка обновления списка: ${error.message}`);
  }
});

document.getElementById("upload-photos-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const input = document.getElementById("photo-files");
    if (!input.files.length) {
      throw new Error("Выбери хотя бы одну фотографию.");
    }
    const formData = new FormData();
    [...input.files].forEach((file) => formData.append("files", file));
    const snapshot = await api(`/properties/${state.currentPropertyId}/photos`, { method: "POST", body: formData });
    snapshotOutput.textContent = JSON.stringify(snapshot, null, 2);
    log("Фотографии загружены", { count: snapshot.photos.length });
  } catch (error) {
    log(`Ошибка загрузки фото: ${error.message}`);
  }
});

document.getElementById("upload-voice-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const transcript = document.getElementById("voice-transcript").value.trim();
    if (!transcript) {
      throw new Error("Добавь текст транскрипта для dev-сценария.");
    }
    const formData = new FormData();
    formData.append("file", new Blob([transcript], { type: "text/plain" }), "voice.txt");
    formData.append("transcript_override", transcript);
    const snapshot = await api(`/properties/${state.currentPropertyId}/voice`, { method: "POST", body: formData });
    snapshotOutput.textContent = JSON.stringify(snapshot, null, 2);
    log("Голосовая заметка сохранена", snapshot.voice_notes?.[0] || {});
  } catch (error) {
    log(`Ошибка голосовой заметки: ${error.message}`);
  }
});

document.getElementById("extract-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/properties/${state.currentPropertyId}/ai/extract`, { method: "POST" });
    snapshotOutput.textContent = JSON.stringify(response, null, 2);
    log("AI извлек поля из транскрипта", response.fields);
  } catch (error) {
    log(`Ошибка AI extraction: ${error.message}`);
  }
});

document.getElementById("generate-copy-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/properties/${state.currentPropertyId}/ai/generate-copy`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    snapshotOutput.textContent = JSON.stringify(response, null, 2);
    log("AI сгенерировал тексты", { titles: response.titles });
  } catch (error) {
    log(`Ошибка copy generation: ${error.message}`);
  }
});

document.getElementById("validate-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/properties/${state.currentPropertyId}/validate`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    publicationOutput.innerHTML = `
      <div class="card">
        <strong>Результаты валидации</strong>
        <ul>${response.map((item) => `<li>${item.platform}: ${item.ready ? "готово" : item.errors.join("; ")}</li>`).join("")}</ul>
      </div>
    `;
    log("Платформенная валидация выполнена", response);
  } catch (error) {
    log(`Ошибка валидации: ${error.message}`);
  }
});

document.getElementById("publish-button").addEventListener("click", async () => {
  try {
    requireProperty();
    const response = await api(`/properties/${state.currentPropertyId}/publish`, {
      method: "POST",
      body: JSON.stringify({ platforms: selectedPlatforms() }),
    });
    renderPublication(response);
    await loadCurrentProperty();
    log("Фиды и экспорт собраны", response);
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
    const profile = await api("/me/profile");
    setSession(profile.contact_email || "Авторизован");
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
