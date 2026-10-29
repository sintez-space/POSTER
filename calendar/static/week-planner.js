/**
 * Недельная сетка Пн–Пт, слоты 09–21 с делением каждого слота по вертикали на 2 полуслота:
 * - полуслот 1 (основной ролик) + полуслот 2 (карусель до 15 фото).
 * Если 2-й полуслот пуст — карточка в 1-м занимает слот, а справа остаётся узкий плюсик (1/4) под карусель.
 * Когда в слоте 2 карточки (half-slot) или узкий режим — превью заставки/1-го слайда карусели идёт на всю ширину карточки,
 * а внизу поверх картинки выводится имя автора и над ним цветной тег.
 */
const WeekPlanner = (() => {
  const SLOT_HOURS = [9, 11, 13, 15, 17, 19];
  const SLOT_LABELS = ["09–11", "11–13", "13–15", "15–17", "17–19", "19–21"];
  const DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт"];
  const DEFAULT_STATUS = {
    draft: "черновик",
    submitted: "в слоте (ждёт админа)",
    approved: "зафиксировано",
    exported: "в POSTER",
    rejected: "отклонено",
  };

  function hexToPalette(hex) {
    const clean = String(hex || "#2dd4bf").replace("#", "");
    const num = parseInt(clean.length === 6 ? clean : "2dd4bf", 16);
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return {
      accent: `#${clean}`,
      bg: `rgba(${r}, ${g}, ${b}, 0.16)`,
      border: `rgba(${r}, ${g}, ${b}, 0.58)`,
    };
  }

  const SERIES_PALETTE = {
    "Видео-1": hexToPalette("#2dd4bf"),
    "Видео-2": hexToPalette("#60a5fa"),
    "Видео-3": hexToPalette("#f59e0b"),
    "Видео-4": hexToPalette("#c084fc"),
    "Видео-5": hexToPalette("#fb7185"),
    "Видео-6": hexToPalette("#4ade80"),
    "Видео-7": hexToPalette("#38bdf8"),
    "Видео-8": hexToPalette("#fb923c"),
    "Видео-9": hexToPalette("#a3e635"),
    "Видео-10": hexToPalette("#f472b6"),
  };

  function setSeriesPalette(tagList) {
    if (!Array.isArray(tagList)) return;
    for (const t of tagList) {
      if (t && t.name) {
        SERIES_PALETTE[t.name] = hexToPalette(t.color || "#2dd4bf");
      }
    }
  }

  const CATALOG_PALETTE = {};

  function setCatalogPalette(tagList) {
    if (!Array.isArray(tagList)) return;
    for (const key of Object.keys(CATALOG_PALETTE)) delete CATALOG_PALETTE[key];
    for (const t of tagList) {
      if (t && t.name) CATALOG_PALETTE[t.name] = hexToPalette(t.color || "#60a5fa");
    }
  }

  let dragId = null;
  let selectedCardId = null;

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function isoDate(d) {
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }

  function todayIso() {
    return isoDate(new Date());
  }

  function isPastDateIso(dateIso) {
    return String(dateIso || "").slice(0, 10) < todayIso();
  }

  function mondayOf(date) {
    const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const day = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - day);
    return d;
  }

  function addDays(d, n) {
    const x = new Date(d.getTime());
    x.setDate(x.getDate() + n);
    return x;
  }

  function isWeekend(d) {
    const wd = d.getDay();
    return wd === 0 || wd === 6;
  }

  function shiftWorkdays(d, n) {
    let x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const step = n >= 0 ? 1 : -1;
    let left = Math.abs(n);
    while (isWeekend(x)) x = addDays(x, step);
    while (left > 0) {
      x = addDays(x, step);
      if (!isWeekend(x)) left -= 1;
    }
    return x;
  }

  function visibleWorkdays(start, count) {
    const days = [];
    let x = new Date(start.getFullYear(), start.getMonth(), start.getDate());
    while (isWeekend(x)) x = addDays(x, 1);
    for (let i = 0; i < count; i++) {
      days.push(x);
      x = shiftWorkdays(x, 1);
    }
    return days;
  }

  function weekBreakBefore(days, index) {
    if (index <= 0) return false;
    return isoDate(mondayOf(days[index])) !== isoDate(mondayOf(days[index - 1]));
  }

  function parseScheduled(iso) {
    const [datePart, timePart] = (iso || "").split("T");
    if (!datePart) return null;
    const [y, m, day] = datePart.split("-").map(Number);
    const [hh, mm] = (timePart || "00:00").split(":").map(Number);
    if (!y || !m || !day) return null;
    return new Date(y, m - 1, day, hh || 0, mm || 0, 0, 0);
  }

  function cellKey(dateIso, hour) {
    return `${dateIso}T${pad2(hour)}:00`;
  }

  function slotForItem(item) {
    if (!item || !item.scheduled_at) return null;
    const d = parseScheduled(item.scheduled_at);
    if (!d) return null;
    const wd = (d.getDay() + 6) % 7;
    if (wd > 4) return null;
    if (!SLOT_HOURS.includes(d.getHours()) || d.getMinutes() !== 0) return null;
    return cellKey(isoDate(d), d.getHours());
  }

  function slotPartOf(item) {
    return item && Number(item.slot_part) === 2 ? 2 : 1;
  }

  function isLocked(item) {
    return item && (item.status === "approved" || item.status === "exported");
  }

  function defaultCanDrag(item, me) {
    if (!item || !me) return false;
    if (isLocked(item)) return false;
    if (me.role === "admin") return true;
    return !!item.is_mine;
  }

  function canClearSlot(item, me) {
    if (!item || !me || !slotForItem(item)) return false;
    if (isLocked(item)) return false;
    if (me.role === "admin") return true;
    return !!item.is_mine;
  }

  function canDeleteItem(item, me) {
    if (!item || !me) return false;
    if (isLocked(item)) return false;
    if (me.role === "admin") return true;
    return !!item.is_mine;
  }

  function weekLabel(from, to) {
    const opts = { day: "2-digit", month: "short" };
    return `${from.toLocaleDateString("ru-RU", opts)} — ${to.toLocaleDateString("ru-RU", { ...opts, year: "numeric" })}`;
  }

  function dayArrowIcon(dir) {
    const d = dir < 0 ? "M15 5 L8 12 L15 19" : "M9 5 L16 12 L9 19";
    return `<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }

  function titleOf(item) {
    const d = item.descriptions || {};
    return (d.base || d.tiktok || d.instagram || "").trim() || (item.media_kind === "carousel" ? "Карусель" : "Без названия");
  }

  function seriesTagOf(item) {
    const d = (item && item.descriptions) || {};
    return (d.series_tag || "").trim();
  }

  function catalogTagOf(item) {
    const d = (item && item.descriptions) || {};
    return (d.catalog_tag || "").trim();
  }

  function tagsOfItem(item) {
    const d = (item && item.descriptions) || {};
    const names = [];
    const raw = Array.isArray(d.card_tags) ? d.card_tags : [];
    for (const entry of raw) {
      const name = String(typeof entry === "string" ? entry : (entry && entry.name) || "").trim();
      if (name && !names.includes(name)) names.push(name);
    }
    if (!names.length) {
      const own = seriesTagOf(item);
      const shared = catalogTagOf(item);
      if (own) names.push(own);
      if (shared && shared !== own) names.push(shared);
    }
    return names;
  }

  function paletteForTag(name) {
    return SERIES_PALETTE[name] || CATALOG_PALETTE[name] || hexToPalette("#94a3b8");
  }

  function pillStyle(palette) {
    if (!palette) return "";
    return ` style="--series-accent:${palette.accent};--series-bg:${palette.bg};--series-border:${palette.border}"`;
  }

  function escHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function confirmDeleteModal(item, onConfirm) {
    let wrap = document.getElementById("confirmDeleteBackdrop");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "confirmDeleteBackdrop";
      wrap.className = "modal-backdrop";
      wrap.style.zIndex = "80";
      wrap.innerHTML = `
        <div class="modal" style="max-width:360px" role="dialog" aria-modal="true">
          <div class="modal-header">
            <div class="modal-header-text">
              <h2>Удалить карточку?</h2>
              <p class="modal-desc" id="confirmDeleteDesc"></p>
            </div>
          </div>
          <div class="modal-footer" style="margin-top:14px">
            <button type="button" class="primary" id="confirmDeleteYesBtn" style="background:var(--destructive);color:#fff">Удалить навсегда</button>
            <span class="footer-spacer"></span>
            <button type="button" class="outline" id="confirmDeleteNoBtn">Отмена</button>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      wrap.addEventListener("click", (e) => {
        if (e.target === wrap) wrap.classList.remove("open");
      });
      wrap.querySelector("#confirmDeleteNoBtn").onclick = () => wrap.classList.remove("open");
    }
    const desc = wrap.querySelector("#confirmDeleteDesc");
    if (desc) {
      desc.textContent = `«${titleOf(item).slice(0, 60)}» будет удалена без возможности восстановления.`;
    }
    const yesBtn = wrap.querySelector("#confirmDeleteYesBtn");
    yesBtn.onclick = async () => {
      wrap.classList.remove("open");
      if (onConfirm) await onConfirm(item);
    };
    wrap.classList.add("open");
  }

  /**
   * Модальное окно удаления юзера админом с двумя вариантами:
   * 1) Удалить только юзера (работы остаются)
   * 2) Удалить юзера и все его работы
   */
  function confirmDeleteUserModal(userObj, onDeleteWithMode) {
    let wrap = document.getElementById("confirmDeleteUserBackdrop");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "confirmDeleteUserBackdrop";
      wrap.className = "modal-backdrop";
      wrap.style.zIndex = "85";
      wrap.innerHTML = `
        <div class="modal" style="max-width:440px" role="dialog" aria-modal="true">
          <div class="modal-header">
            <div class="modal-header-text">
              <h2>Удаление пользователя</h2>
              <p class="modal-desc" id="confirmDeleteUserDesc"></p>
            </div>
            <button type="button" class="modal-close ghost" id="confirmDeleteUserClose">×</button>
          </div>
          <div class="modal-body" style="gap:10px;padding-top:14px">
            <button type="button" class="outline" id="delUserKeepWorksBtn" style="width:100%;text-align:left;padding:12px 14px">
              <strong style="display:block;color:var(--primary);margin-bottom:2px">1. Удалить только юзера (оставить работы)</strong>
              <span class="text-muted" style="font-size:11.5px">Аккаунт и доступ удаляются, но все его карточки и ролики остаются в календаре и материалах.</span>
            </button>
            <button type="button" class="outline" id="delUserAndWorksBtn" style="width:100%;text-align:left;padding:12px 14px;border-color:var(--destructive)">
              <strong style="display:block;color:var(--destructive);margin-bottom:2px">2. Удалить юзера И все его работы</strong>
              <span class="text-muted" style="font-size:11.5px">Полное удаление аккаунта вместе со всеми его карточками, видео и каруселями без возврата.</span>
            </button>
          </div>
          <div class="modal-footer">
            <span class="footer-spacer"></span>
            <button type="button" class="ghost" id="confirmDeleteUserCancel">Отмена</button>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      const close = () => wrap.classList.remove("open");
      wrap.addEventListener("click", (e) => {
        if (e.target === wrap) close();
      });
      wrap.querySelector("#confirmDeleteUserClose").onclick = close;
      wrap.querySelector("#confirmDeleteUserCancel").onclick = close;
    }
    const nameStr = userObj.display_name || userObj.login || "Пользователь";
    wrap.querySelector("#confirmDeleteUserDesc").textContent = `Выберите вариант удаления для «${nameStr}» (@${userObj.login}):`;

    wrap.querySelector("#delUserKeepWorksBtn").onclick = async () => {
      wrap.classList.remove("open");
      if (onDeleteWithMode) await onDeleteWithMode(userObj, "keep_works");
    };
    wrap.querySelector("#delUserAndWorksBtn").onclick = async () => {
      if (!confirm(`Точно удалить «${nameStr}» И ВСЕ его карточки/файлы навсегда?`)) return;
      wrap.classList.remove("open");
      if (onDeleteWithMode) await onDeleteWithMode(userObj, "delete_works");
    };
    wrap.classList.add("open");
  }

  /**
   * Модальное окно смены пароля (для себя или админом для любого юзера).
   */
  function openPasswordModal({ userId = null, userName = "", isSelf = true }) {
    let wrap = document.getElementById("changePasswordBackdrop");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "changePasswordBackdrop";
      wrap.className = "modal-backdrop";
      wrap.style.zIndex = "85";
      wrap.innerHTML = `
        <div class="modal" style="max-width:380px" role="dialog" aria-modal="true">
          <div class="modal-header">
            <div class="modal-header-text">
              <h2 id="pwdModalTitle">Смена пароля</h2>
              <p class="modal-desc" id="pwdModalDesc"></p>
            </div>
            <button type="button" class="modal-close ghost" id="pwdModalClose">×</button>
          </div>
          <div class="modal-body" style="padding-top:14px">
            <div class="field" id="pwdCurrentWrap">
              <label for="pwdCurrentInput">Текущий пароль</label>
              <input type="password" id="pwdCurrentInput" autocomplete="current-password" placeholder="Текущий пароль">
            </div>
            <div class="field">
              <label for="pwdNewInput">Новый пароль</label>
              <input type="password" id="pwdNewInput" autocomplete="new-password" placeholder="Минимум 4 символа">
            </div>
            <div class="field">
              <label for="pwdConfirmInput">Повторите новый пароль</label>
              <input type="password" id="pwdConfirmInput" autocomplete="new-password" placeholder="Повторите новый пароль">
            </div>
            <p class="error" id="pwdModalErr"></p>
          </div>
          <div class="modal-footer">
            <button type="button" class="ghost" id="pwdModalCancel">Отмена</button>
            <span class="footer-spacer"></span>
            <button type="button" class="primary" id="pwdModalSave">Сохранить пароль</button>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      const close = () => wrap.classList.remove("open");
      wrap.addEventListener("click", (e) => {
        if (e.target === wrap) close();
      });
      wrap.querySelector("#pwdModalClose").onclick = close;
      wrap.querySelector("#pwdModalCancel").onclick = close;
    }

    wrap.querySelector("#pwdModalTitle").textContent = isSelf ? "Сменить мой пароль" : "Сменить пароль пользователя";
    wrap.querySelector("#pwdModalDesc").textContent = isSelf
      ? "Введите текущий и новый пароль"
      : `Новый пароль для «${userName}»`;
    wrap.querySelector("#pwdCurrentWrap").hidden = !isSelf;
    wrap.querySelector("#pwdCurrentInput").value = "";
    wrap.querySelector("#pwdNewInput").value = "";
    wrap.querySelector("#pwdConfirmInput").value = "";
    const errEl = wrap.querySelector("#pwdModalErr");
    errEl.textContent = "";

    wrap.querySelector("#pwdModalSave").onclick = async () => {
      errEl.textContent = "";
      const cur = wrap.querySelector("#pwdCurrentInput").value;
      const nw = wrap.querySelector("#pwdNewInput").value;
      const cf = wrap.querySelector("#pwdConfirmInput").value;
      if (nw.length < 4) {
        errEl.textContent = "Новый пароль должен быть не короче 4 символов";
        return;
      }
      if (nw !== cf) {
        errEl.textContent = "Пароли не совпадают";
        return;
      }
      try {
        if (isSelf) {
          await api("/api/me/password", {
            method: "POST",
            body: JSON.stringify({ current_password: cur, new_password: nw }),
          });
        } else {
          await api(`/api/admin/users/${userId}/password`, {
            method: "POST",
            body: JSON.stringify({ new_password: nw }),
          });
        }
        wrap.classList.remove("open");
        alert("Пароль успешно обновлён.");
      } catch (e) {
        errEl.textContent = e.message || String(e);
      }
    };

    wrap.classList.add("open");
  }

  /**
   * Модальное окно выбора доступного материала при клике на «+» в слоте календаря.
   * Показывает свободные материалы (сгруппированные по авторам, как во вкладке «Материалы»):
   * клик по карточке сразу ставит её в выбранный слот календаря!
   */
  function openSlotPickerModal({ key, slotPart = 1, mediaKind = "video", poolItems = [], me, onPicked, onCreateNew }) {
    let wrap = document.getElementById("slotMaterialPickerBackdrop");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "slotMaterialPickerBackdrop";
      wrap.className = "modal-backdrop";
      wrap.style.zIndex = "75";
      wrap.innerHTML = `
        <div class="modal modal-wide" style="max-width:860px" role="dialog" aria-modal="true">
          <div class="modal-header">
            <div class="modal-header-text">
              <h2 id="slotPickerTitle">Выбрать материал для размещения в слот</h2>
              <p class="modal-desc" id="slotPickerDesc"></p>
            </div>
            <button type="button" class="modal-close ghost" id="slotPickerClose">×</button>
          </div>
          <div class="modal-body" style="overflow-y:auto;max-height:66vh" id="slotPickerBody"></div>
          <div class="modal-footer">
            <button type="button" class="ghost" id="slotPickerCancel">Отмена</button>
            <span class="footer-spacer"></span>
            <button type="button" class="primary" id="slotPickerCreateNewBtn">+ Создать новый материал в этот слот</button>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      const close = () => wrap.classList.remove("open");
      wrap.addEventListener("click", (e) => {
        if (e.target === wrap) close();
      });
      wrap.querySelector("#slotPickerClose").onclick = close;
      wrap.querySelector("#slotPickerCancel").onclick = close;
    }

    const kindRu = mediaKind === "carousel" ? "Карусель" : "Рилс (видео)";
    wrap.querySelector("#slotPickerTitle").textContent = `Разместить материал в слот · ${fmtLocal(key)} (слот ${slotPart})`;
    wrap.querySelector("#slotPickerDesc").textContent =
      `Нажмите на любой свободный материал ниже, чтобы сразу поставить его в расписание (${kindRu}), или создайте новый.`;

    const createBtn = wrap.querySelector("#slotPickerCreateNewBtn");
    createBtn.textContent = mediaKind === "carousel" ? "+ Создать новую карусель в слот" : "+ Создать новый рилс в слот";
    createBtn.onclick = () => {
      wrap.classList.remove("open");
      if (onCreateNew) onCreateNew(key, slotPart, mediaKind);
    };

    const body = wrap.querySelector("#slotPickerBody");
    body.innerHTML = "";

    const isAdmin = me && me.role === "admin";
    const freeItems = (poolItems || []).filter((it) => {
      if (slotForItem(it)) return false;
      if (!isAdmin && !it.is_mine) return false;
      return true;
    });

    if (!freeItems.length) {
      body.innerHTML = `
        <div class="admin-user-group" style="text-align:center;padding:28px 16px">
          <p class="text-muted" style="margin:0 0 12px">Свободных материалов вне расписания сейчас нет.</p>
        </div>`;
      wrap.classList.add("open");
      return;
    }

    const byAuthor = new Map();
    for (const it of freeItems) {
      const aName = it.author_name || "Автор";
      if (!byAuthor.has(aName)) byAuthor.set(aName, []);
      byAuthor.get(aName).push(it);
    }

    for (const [authorName, list] of byAuthor.entries()) {
      const group = document.createElement("div");
      group.className = "admin-user-group";
      group.style.marginBottom = "12px";
      group.innerHTML = `
        <div class="admin-subcol-title" style="margin-bottom:8px">
          <span>${escHtml(authorName)} — свободные материалы</span>
          <span class="badge primary">${list.length}</span>
        </div>
        <div class="admin-cards-grid"></div>`;
      const grid = group.querySelector(".admin-cards-grid");

      for (const item of list) {
        const card = buildCard(item, {
          me,
          canDrag: () => false,
          inSlot: false,
          compact: false,
        });
        card.style.cursor = "pointer";
        card.title = "Нажмите, чтобы поставить этот материал в выбранный слот";
        const PickHandler = async (ev) => {
          ev.stopPropagation();
          try {
            await api(`/api/submissions/${item.id}`, {
              method: "PUT",
              body: JSON.stringify({ scheduled_at: key, slot_part: slotPart }),
            });
            wrap.classList.remove("open");
            if (onPicked) await onPicked(item);
          } catch (err) {
            alert(err.message || String(err));
          }
        };
        card.addEventListener("click", PickHandler);
        card.addEventListener("dblclick", PickHandler);
        grid.appendChild(card);
      }
      body.appendChild(group);
    }

    wrap.classList.add("open");
  }

  function buildCard(item, opts) {
    const {
      me,
      canDrag = (it) => defaultCanDrag(it, me),
      onOpen,
      onClearSlot,
      onDelete,
      onToggleApprove,
      compact = false,
      inSlot = false,
      halfSlot = false,
    } = opts;

    const el = document.createElement("div");
    const foreign = item.is_mine === false && me.role !== "admin";
    const locked = isLocked(item);
    const cardTags = tagsOfItem(item);
    const sTag = cardTags[0] || "";
    const palette = sTag ? paletteForTag(sTag) : null;
    const isCarousel = item.media_kind === "carousel";

    el.className =
      "week-card" +
      (foreign ? " foreign" : "") +
      (locked ? " locked" : "") +
      (cardTags.length ? " has-series" : "") +
      (isCarousel ? " is-carousel" : "") +
      (halfSlot ? " half-slot" : "") +
      (compact ? " compact" : "") +
      (selectedCardId === item.id ? " selected" : "");

    if (palette) {
      el.style.setProperty("--series-accent", palette.accent);
      el.style.setProperty("--series-bg", palette.bg);
      el.style.setProperty("--series-border", palette.border);
    }

    const draggable = canDrag(item);
    el.draggable = draggable;
    el.dataset.id = item.id;

    if (draggable) {
      el.addEventListener("dragstart", (e) => {
        dragId = item.id;
        e.dataTransfer.setData("application/x-poster-id", item.id);
        e.dataTransfer.setData("text/plain", item.id);
        e.dataTransfer.effectAllowed = "move";
        el.classList.add("dragging");
      });
      el.addEventListener("dragend", () => {
        dragId = null;
        el.classList.remove("dragging");
      });
    } else {
      el.addEventListener("dragstart", (e) => e.preventDefault());
    }

    const slidesCount = Array.isArray(item.carousel_files) ? item.carousel_files.length : 0;
    const hasCover = !!(item.cover_name || slidesCount > 0);
    const coverSrc = `/api/submissions/${item.id}/cover?thumb=1&t=${encodeURIComponent(item.updated_at || "")}`;
    const thumb = hasCover
      ? `<div class="week-card-cover"><img class="week-card-thumb" src="${coverSrc}" alt="Заставка" loading="lazy" decoding="async" draggable="false">${
          isCarousel ? `<span class="carousel-count-badge">▤ ${slidesCount || 0}</span>` : ""
        }</div>`
      : `<div class="week-card-cover week-card-ph"><span class="week-card-ph-label">${
          isCarousel ? `▤ ${slidesCount}/15` : "9:16"
        }</span></div>`;

    const author = escHtml(item.author_name || "—");
    const title = escHtml(titleOf(item));
    const tagBadges = cardTags
      .map((name) => `<span class="series-pill"${pillStyle(paletteForTag(name))}>${escHtml(name)}</span>`)
      .join("");

    const showClear = inSlot && onClearSlot && canClearSlot(item, me);
    const showApprove = inSlot && me.role === "admin" && onToggleApprove;
    const showPoolDelete = !inSlot && onDelete && canDeleteItem(item, me);

    const avatarHtml = item.author_avatar_url
      ? `<img class="card-author-avatar" src="${item.author_avatar_url}" alt="${author}" title="${author}" draggable="false">`
      : `<div class="card-author-avatar card-author-avatar-fallback" title="${author}">${escHtml(
          (item.author_name || "А").slice(0, 1).toUpperCase()
        )}</div>`;

    el.innerHTML = `
      ${thumb}
      <div class="card-top-right-avatar">
        ${avatarHtml}
      </div>
      <div class="card-bottom-right-actions">
        ${
          showApprove
            ? `<button type="button" class="card-act-btn ${locked ? "act-approved" : ""}" data-act="approve" title="${
                locked ? "Снять фиксацию" : "Подтвердить и зафиксировать слот"
              }">✓</button>`
            : ""
        }
        ${
          showClear
            ? `<button type="button" class="card-act-btn act-clear" data-act="clear" title="Убрать из расписания в пул">×</button>`
            : ""
        }
        ${
          showPoolDelete
            ? `<button type="button" class="card-act-btn act-delete" data-act="delete" title="Удалить карточку"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button>`
            : ""
        }
      </div>
      <div class="week-card-body">
        <div class="week-card-top">
          <div class="week-card-badges">${tagBadges}</div>
        </div>
        <div class="week-card-title">${title}</div>
        <div class="week-card-meta">${author}</div>
      </div>
      <div class="week-card-overlay-bottom">
        ${tagBadges}
        <div class="overlay-author-name">${author}</div>
      </div>`;

    const approveBtn = el.querySelector('[data-act="approve"]');
    if (approveBtn) {
      approveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        onToggleApprove(item);
      });
    }

    const clearBtn = el.querySelector('[data-act="clear"]');
    if (clearBtn) {
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        onClearSlot(item);
      });
    }

    const deleteBtn = el.querySelector('[data-act="delete"]');
    if (deleteBtn) {
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        confirmDeleteModal(item, onDelete);
      });
    }

    el.addEventListener("click", (e) => {
      if (e.defaultPrevented) return;
      selectedCardId = item.id;
      document.querySelectorAll(".week-card.selected").forEach((node) => {
        node.classList.remove("selected");
      });
      el.classList.add("selected");
    });

    el.addEventListener("dblclick", (e) => {
      if (e.defaultPrevented) return;
      selectedCardId = item.id;
      document.querySelectorAll(".week-card.selected").forEach((node) => {
        node.classList.remove("selected");
      });
      el.classList.add("selected");
      if (onOpen) onOpen(item.id, item);
    });
    return el;
  }

  /**
   * Рендерит пул нераспределённых карточек в 3 строки на всю ширину (в шапке или блоке).
   */
  function renderAuthorPool(mount, allItems, opts) {
    if (!mount) return;
    mount.innerHTML = "";
    const onlyMine = opts.onlyMineInPool !== false && opts.me && opts.me.role !== "admin";
    const unscheduled = (allItems || []).filter((it) => {
      if (slotForItem(it)) return false;
      if (onlyMine && !it.is_mine) return false;
      return true;
    });

    if (opts.onDropToPool && !mount.dataset.poolDropBound) {
      mount.dataset.poolDropBound = "1";
      mount.addEventListener("dragover", (e) => {
        e.preventDefault();
        mount.classList.add("drop-target");
      });
      mount.addEventListener("dragleave", () => mount.classList.remove("drop-target"));
      mount.addEventListener("drop", async (e) => {
        e.preventDefault();
        mount.classList.remove("drop-target");
        const id = dragId || e.dataTransfer.getData("application/x-poster-id") || e.dataTransfer.getData("text/plain");
        if (id && !id.startsWith("http")) await opts.onDropToPool(id);
      });
    }

    if (!unscheduled.length) {
      return;
    }

    const sorted = [...unscheduled].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    const cnt = sorted.length;
    const sizeClass = cnt <= 4 ? "pool-few" : cnt <= 10 ? "pool-medium" : "pool-many";
    const grid = document.createElement("div");
    grid.className = `pool-three-rows ${sizeClass}`;
    grid.style.setProperty("--pool-count", String(cnt));
    for (const item of sorted) {
      const wrap = document.createElement("div");
      wrap.className = "pool-card-slot";
      wrap.appendChild(buildCard(item, { ...opts, compact: true, inSlot: false }));
      grid.appendChild(wrap);
    }
    mount.appendChild(grid);
  }

  /**
   * Мини-календарь на месяц (слева в шапке): дни с постами закрашиваются,
   * непрозрачность пропорциональна числу постов в этот день.
   */
  function renderMiniMonth(mount, year, month, countsMap, activeMonday, onSelectDay, activeEnd) {
    if (!mount) return;
    mount.innerHTML = "";
    const monthNames = [
      "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
      "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ];
    const head = document.createElement("div");
    head.className = "mini-cal-head";
    head.textContent = `${monthNames[month - 1] || ""} ${year}`;
    mount.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "mini-cal-grid";
    for (const wd of ["П", "В", "С", "Ч", "П", "С", "В"]) {
      const w = document.createElement("div");
      w.className = "mini-cal-wd";
      w.textContent = wd;
      grid.appendChild(w);
    }

    const firstDay = new Date(year, month - 1, 1);
    const startPad = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(year, month, 0).getDate();
    const maxCnt = Math.max(1, ...Object.values(countsMap || {}));

    const activeStart = activeMonday ? isoDate(activeMonday) : "";
    const activeEndIso = activeEnd
      ? isoDate(activeEnd)
      : activeMonday
        ? isoDate(addDays(activeMonday, 4))
        : "";

    for (let i = 0; i < startPad; i++) {
      const empty = document.createElement("div");
      empty.className = "mini-cal-day empty";
      grid.appendChild(empty);
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const dObj = new Date(year, month - 1, day);
      const dIso = isoDate(dObj);
      const cnt = (countsMap && countsMap[dIso]) || 0;
      const past = isPastDateIso(dIso);
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className =
        "mini-cal-day" +
        (cnt > 0 ? " has-posts" : "") +
        (past ? " is-past-day" : "") +
        (dIso >= activeStart && dIso <= activeEndIso ? " in-active-week" : "");
      cell.textContent = String(day);
      if (cnt > 0) {
        const alpha = Math.min(0.95, 0.25 + (cnt / Math.max(3, maxCnt)) * 0.7);
        cell.style.setProperty("--post-alpha", alpha.toFixed(2));
        cell.title = `${dIso}: постов — ${cnt}`;
      }
      cell.onclick = () => {
        if (onSelectDay) onSelectDay(dObj, past);
      };
      grid.appendChild(cell);
    }

    mount.appendChild(grid);
  }

  function init(options) {
    const {
      mount,
      labelEl,
      prevBtn,
      nextBtn,
      miniMonthMount,
      filterBar,
      getFilterIndex,
      me,
      onOpen,
      onCreate,
      canDrag = (item) => defaultCanDrag(item, me),
      statusLabels = DEFAULT_STATUS,
      resolveItem,
    } = options;

    const FEED_ORIGIN = "2026-10-30";
    let viewStart = parseScheduled(`${FEED_ORIGIN}T00:00`) || mondayOf(new Date());
    let items = [];
    let loadSeq = 0;
    let filterChips = [];
    let calendarView = "board";
    let feedItems = [];
    let feedNet = "ig";
    let feedScale = 100;
    try {
      calendarView = sessionStorage.getItem("posterCalView") === "feed" ? "feed" : "board";
      const savedNet = sessionStorage.getItem("posterFeedNet");
      if (savedNet === "ig" || savedNet === "tt" || savedNet === "yt" || savedNet === "vk") feedNet = savedNet;
      const savedScale = Number(sessionStorage.getItem("posterFeedScale"));
      if (savedScale >= 50 && savedScale <= 100) feedScale = savedScale;
    } catch (_) {}

    function findItem(id) {
      return items.find((x) => x.id === id) || (resolveItem ? resolveItem(id) : null);
    }

    function currentDays() {
      return visibleWorkdays(viewStart, 5);
    }

    function itemMatchesFilter(item) {
      if (!filterChips.length) return true;
      const tagNames = tagsOfItem(item).map((s) => s.toLowerCase());
      const author = String((item && item.author_name) || "").trim().toLowerCase();
      return filterChips.some((chip) => {
        const needle = String(chip.name || "").trim().toLowerCase();
        if (!needle) return false;
        if (chip.kind === "name") return author === needle;
        return tagNames.includes(needle);
      });
    }

    function setupFilterBar() {
      if (!filterBar) return;
      filterBar.innerHTML = "";
      const chipsEl = document.createElement("div");
      chipsEl.className = "cal-filter-chips";
      const wrap = document.createElement("div");
      wrap.className = "cal-filter-input-wrap";
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "Тег или имя участника";
      input.autocomplete = "off";
      input.setAttribute("aria-label", "Фильтр по тегу или имени");
      const suggest = document.createElement("div");
      suggest.className = "cal-filter-suggest";
      suggest.hidden = true;
      const resetBtn = document.createElement("button");
      resetBtn.type = "button";
      resetBtn.className = "outline";
      resetBtn.textContent = "Сбросить";
      wrap.appendChild(input);
      wrap.appendChild(suggest);
      const viewToggle = document.createElement("div");
      viewToggle.className = "cal-view-toggle";
      const boardBtn = document.createElement("button");
      boardBtn.type = "button";
      boardBtn.textContent = "Расписание";
      const feedBtn = document.createElement("button");
      feedBtn.type = "button";
      feedBtn.textContent = "Лента";
      function paintViewToggle() {
        boardBtn.className = calendarView === "board" ? "active" : "";
        feedBtn.className = calendarView === "feed" ? "active" : "";
      }
      function setCalendarView(next) {
        calendarView = next === "feed" ? "feed" : "board";
        try {
          sessionStorage.setItem("posterCalView", calendarView);
        } catch (_) {}
        paintViewToggle();
        if (calendarView === "feed") load();
        else render();
      }
      boardBtn.onclick = () => setCalendarView("board");
      feedBtn.onclick = () => setCalendarView("feed");
      viewToggle.appendChild(boardBtn);
      viewToggle.appendChild(feedBtn);
      paintViewToggle();

      filterBar.appendChild(chipsEl);
      filterBar.appendChild(wrap);
      filterBar.appendChild(viewToggle);
      filterBar.appendChild(resetBtn);

      let suggestItems = [];
      let activeIdx = 0;

      function paintChips() {
        chipsEl.innerHTML = "";
        for (const chip of filterChips) {
          const el = document.createElement("button");
          el.type = "button";
          el.className = "cal-filter-chip" + (chip.kind === "name" ? " is-name" : " is-tag");
          el.title = chip.kind === "name" ? "Убрать имя" : "Убрать тег";
          el.innerHTML = `<span>${escHtml(chip.kind === "name" ? chip.name : "#" + chip.name)}</span><span class="cal-filter-x" aria-hidden="true">×</span>`;
          el.onclick = () => {
            filterChips = filterChips.filter((c) => !(c.kind === chip.kind && c.name === chip.name));
            paintChips();
            render();
          };
          chipsEl.appendChild(el);
        }
      }

      function indexOfFilter() {
        const src = typeof getFilterIndex === "function" ? getFilterIndex() || {} : {};
        const tags = [];
        const seenTags = new Set();
        for (const t of src.tags || []) {
          const name = String(t && t.name ? t.name : t || "").trim();
          const key = name.toLowerCase();
          if (!name || seenTags.has(key)) continue;
          seenTags.add(key);
          tags.push({ kind: "tag", name });
        }
        const names = [];
        const seenNames = new Set();
        for (const raw of src.names || []) {
          const name = String(raw && raw.display_name ? raw.display_name : raw || "").trim();
          const key = name.toLowerCase();
          if (!name || seenNames.has(key)) continue;
          seenNames.add(key);
          names.push({ kind: "name", name });
        }
        return { tags, names };
      }

      function hideSuggest() {
        suggest.hidden = true;
        suggest.innerHTML = "";
        suggestItems = [];
      }

      function paintSuggest() {
        suggest.innerHTML = "";
        suggestItems.forEach((item, idx) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "cal-filter-opt" + (idx === activeIdx ? " active" : "");
          btn.innerHTML = `<span>${escHtml(item.kind === "name" ? item.name : "#" + item.name)}</span><em>${
            item.kind === "name" ? "участник" : "тег"
          }</em>`;
          btn.onmousedown = (e) => {
            e.preventDefault();
            pinChip(item);
          };
          suggest.appendChild(btn);
        });
        suggest.hidden = suggestItems.length === 0;
      }

      function refreshSuggest() {
        const q = input.value.trim().replace(/^#+/, "").toLowerCase();
        if (!q) {
          hideSuggest();
          return;
        }
        const src = indexOfFilter();
        const pool = src.tags.concat(src.names);
        suggestItems = pool
          .filter((item) => item.name.toLowerCase().startsWith(q))
          .filter((item) => !filterChips.some((c) => c.kind === item.kind && c.name.toLowerCase() === item.name.toLowerCase()))
          .slice(0, 8);
        activeIdx = 0;
        paintSuggest();
      }

      function pinChip(item) {
        if (!item || !item.name) return;
        if (filterChips.some((c) => c.kind === item.kind && c.name.toLowerCase() === item.name.toLowerCase())) {
          input.value = "";
          hideSuggest();
          return;
        }
        filterChips = filterChips.concat([{ kind: item.kind, name: item.name }]);
        input.value = "";
        hideSuggest();
        paintChips();
        render();
      }

      input.addEventListener("input", refreshSuggest);
      input.addEventListener("focus", refreshSuggest);
      input.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" && suggestItems.length) {
          e.preventDefault();
          activeIdx = (activeIdx + 1) % suggestItems.length;
          paintSuggest();
          return;
        }
        if (e.key === "ArrowUp" && suggestItems.length) {
          e.preventDefault();
          activeIdx = (activeIdx - 1 + suggestItems.length) % suggestItems.length;
          paintSuggest();
          return;
        }
        if (e.key === "Escape") {
          hideSuggest();
          return;
        }
        if (e.key === "Enter") {
          e.preventDefault();
          if (suggestItems.length) pinChip(suggestItems[activeIdx] || suggestItems[0]);
          return;
        }
        if (e.key === "Backspace" && !input.value && filterChips.length) {
          filterChips = filterChips.slice(0, -1);
          paintChips();
          render();
        }
      });
      resetBtn.onclick = () => {
        if (!filterChips.length && !input.value) return;
        filterChips = [];
        input.value = "";
        hideSuggest();
        paintChips();
        render();
      };
      document.addEventListener("click", (e) => {
        if (!filterBar.contains(e.target)) hideSuggest();
      });
      paintChips();
    }

    setupFilterBar();

    prevBtn.onclick = () => {
      viewStart = shiftWorkdays(viewStart, -5);
      load();
    };
    nextBtn.onclick = () => {
      viewStart = shiftWorkdays(viewStart, 5);
      load();
    };
    prevBtn.title = "На неделю назад";
    nextBtn.title = "На неделю вперёд";

    async function refreshMiniMonth(seq) {
      if (!miniMonthMount) return;
      const days = currentDays();
      const anchor = days[0];
      const y = anchor.getFullYear();
      const m = anchor.getMonth() + 1;
      try {
        const mData = await api(`/api/calendar/month?year=${y}&month=${m}`);
        if (seq !== loadSeq) return;
        renderMiniMonth(
          miniMonthMount,
          y,
          m,
          mData.counts || {},
          anchor,
          (clickedDate) => {
            const wd = (clickedDate.getDay() + 6) % 7;
            viewStart = wd > 4 ? mondayOf(clickedDate) : new Date(clickedDate.getFullYear(), clickedDate.getMonth(), clickedDate.getDate());
            load();
          },
          days[days.length - 1]
        );
      } catch (_) {}
    }

    async function load() {
      const seq = ++loadSeq;
      const days = currentDays();
      labelEl.textContent = weekLabel(days[0], days[days.length - 1]);
      const weekReq = api(
        `/api/calendar/week?date_from=${isoDate(days[0])}&date_to=${isoDate(days[days.length - 1])}`
      );
      const poolReq =
        calendarView === "feed" ? api("/api/submissions/pool").catch(() => []) : Promise.resolve(null);
      const [data, pool] = await Promise.all([weekReq, poolReq]);
      if (seq !== loadSeq) return;
      items = data.items || [];
      if (Array.isArray(pool)) feedItems = pool;
      render();
      await refreshMiniMonth(seq);
    }

    function itemByKeyAndPart(key, part) {
      return items.find((it) => slotForItem(it) === key && slotPartOf(it) === part);
    }

    async function clearSlot(item) {
      if (!canClearSlot(item, me)) {
        alert("Очистить этот слот может только админ.");
        return;
      }
      try {
        await api(`/api/submissions/${item.id}`, {
          method: "PUT",
          body: JSON.stringify({ scheduled_at: "" }),
        });
        await load();
        if (options.onMoved) await options.onMoved();
      } catch (err) {
        alert(err.message || String(err));
      }
    }

    async function toggleApprove(item) {
      if (me.role !== "admin") return;
      const nextStatus = isLocked(item) ? "submitted" : "approved";
      try {
        await api(`/api/submissions/${item.id}`, {
          method: "PUT",
          body: JSON.stringify({ status: nextStatus }),
        });
        await load();
        if (options.onMoved) await options.onMoved();
      } catch (err) {
        alert(err.message || String(err));
      }
    }

    async function moveItem(id, targetKey, targetPart) {
      if (isPastDateIso(targetKey)) {
        throw new Error("Нельзя ставить карточку на прошедшую дату.");
      }
      const moving = findItem(id);
      if (!moving) throw new Error("материал не найден");
      if (!canDrag(moving)) {
        throw new Error(
          isLocked(moving)
            ? "Слот подтверждён админом и зафиксирован."
            : "Нельзя перемещать чужую карточку."
        );
      }
      const part = targetPart || (moving.media_kind === "carousel" ? 2 : 1);
      const oldKey = slotForItem(moving);
      const oldPart = slotPartOf(moving);
      if (targetKey === oldKey && part === oldPart) return;

      const occupant = itemByKeyAndPart(targetKey, part);
      if (occupant && occupant.id !== id) {
        if (!canDrag(occupant)) {
          throw new Error("Полуслот занят другой карточкой — очистить чужой или зафиксированный слот может только админ.");
        }
        if (oldKey) {
          /* Обмен местами (в т.ч. видео <-> карусель внутри одного таймслота) */
          await api(`/api/submissions/${occupant.id}`, {
            method: "PUT",
            body: JSON.stringify({ scheduled_at: "" }),
          });
          await api(`/api/submissions/${id}`, {
            method: "PUT",
            body: JSON.stringify({ scheduled_at: targetKey, slot_part: part }),
          });
          await api(`/api/submissions/${occupant.id}`, {
            method: "PUT",
            body: JSON.stringify({ scheduled_at: oldKey, slot_part: oldPart }),
          });
        } else {
          await api(`/api/submissions/${occupant.id}`, {
            method: "PUT",
            body: JSON.stringify({ scheduled_at: "" }),
          });
          await api(`/api/submissions/${id}`, {
            method: "PUT",
            body: JSON.stringify({ scheduled_at: targetKey, slot_part: part }),
          });
        }
      } else {
        await api(`/api/submissions/${id}`, {
          method: "PUT",
          body: JSON.stringify({ scheduled_at: targetKey, slot_part: part }),
        });
      }
      await load();
      if (options.onMoved) await options.onMoved();
    }

    function setupSubslotDrop(el, key, part) {
      el.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.classList.add("drop-target");
      });
      el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
      el.addEventListener("drop", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        el.classList.remove("drop-target");
        const id = dragId || e.dataTransfer.getData("application/x-poster-id") || e.dataTransfer.getData("text/plain");
        if (!id || id.startsWith("http")) return;
        try {
          await moveItem(id, key, part);
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    }

    function entriesForHour(a, b, when) {
      const filled = [a, b].filter(Boolean);
      const carousels = filled.filter((it) => it.media_kind === "carousel");
      const rest = filled.filter((it) => it.media_kind !== "carousel");
      const asItem = (item) => ({ item, hole: false, caption: when });
      if (!filled.length) {
        return [{ item: null, hole: true, title: `Свободный слот · ${when}`, caption: when }];
      }
      const out = [];
      if (rest.length) out.push(asItem(rest[0]));
      else out.push(asItem(carousels[0]));
      for (const c of carousels) {
        if (!out.some((entry) => entry.item === c)) out.push(asItem(c));
      }
      for (const extra of rest.slice(1)) out.push(asItem(extra));
      return out;
    }

    function feedSequence() {
      const placed = new Map();
      const loose = [];
      let minDay = "";
      let maxDay = "";
      for (const it of feedItems) {
        const key = slotForItem(it);
        if (!key) {
          loose.push(it);
          continue;
        }
        const mapKey = `${key}|${slotPartOf(it)}`;
        if (placed.has(mapKey)) loose.push(it);
        else placed.set(mapKey, it);
        const day = key.slice(0, 10);
        if (day < FEED_ORIGIN) continue;
        if (!minDay || day < minDay) minDay = day;
        if (!maxDay || day > maxDay) maxDay = day;
      }
      minDay = FEED_ORIGIN;
      if (!maxDay || maxDay < FEED_ORIGIN) maxDay = FEED_ORIGIN;
      const seq = [];
      let cursor = parseScheduled(`${minDay}T00:00`);
      let guard = 0;
      while (cursor && isoDate(cursor) <= maxDay && guard < 400) {
        guard += 1;
        if (!isWeekend(cursor)) {
          const dateIso = isoDate(cursor);
          const dayName = DAY_NAMES[(cursor.getDay() + 6) % 7] || "";
          const shortDate = `${pad2(cursor.getDate())}.${pad2(cursor.getMonth() + 1)}`;
          for (let hi = 0; hi < SLOT_HOURS.length; hi++) {
            const hour = SLOT_HOURS[hi];
            const key = cellKey(dateIso, hour);
            const when = `${dayName} ${shortDate} ${SLOT_LABELS[hi]}`;
            const a = placed.get(`${key}|1`) || null;
            const b = placed.get(`${key}|2`) || null;
            seq.push(...entriesForHour(a, b, when));
          }
        }
        cursor = addDays(cursor, 1);
      }
      loose.sort(feedSort);
      for (const it of loose) seq.push({ item: it, hole: false, caption: "" });
      const firstReal = seq.findIndex((entry) => entry && !entry.hole);
      if (firstReal < 0) return seq.slice(0, 3);
      if (firstReal > 3) return seq.slice(firstReal - 3);
      return seq;
    }

    function feedSort(a, b) {
      const sa = String((a && a.scheduled_at) || "");
      const sb = String((b && b.scheduled_at) || "");
      if (sa && sb && sa !== sb) return sa < sb ? -1 : 1;
      if (sa && !sb) return -1;
      if (!sa && sb) return 1;
      const ca = String((a && a.created_at) || "");
      const cb = String((b && b.created_at) || "");
      if (ca !== cb) return ca < cb ? -1 : 1;
      return String((a && a.id) || "").localeCompare(String((b && b.id) || ""));
    }

    function feedSlotNote(cell) {
      const note = document.createElement("div");
      note.className = "feed-slot-note";
      const when = document.createElement("div");
      when.className = "feed-slot-when";
      when.textContent = cell.caption || (cell.hole ? "Свободно" : "не в расписании");
      note.appendChild(when);
      if (cell.hole || !cell.item) return note;
      const item = cell.item;
      const who = document.createElement("div");
      who.className = "feed-slot-who";
      const author = item.author_name || "—";
      if (item.author_avatar_url) {
        const img = document.createElement("img");
        img.className = "feed-slot-avatar";
        img.src = item.author_avatar_url;
        img.alt = author;
        img.draggable = false;
        who.appendChild(img);
      } else {
        const fallback = document.createElement("div");
        fallback.className = "feed-slot-avatar is-fallback";
        fallback.textContent = author.slice(0, 1).toUpperCase();
        who.appendChild(fallback);
      }
      const name = document.createElement("span");
      name.textContent = author;
      who.appendChild(name);
      note.appendChild(who);
      const tags = tagsOfItem(item);
      if (tags.length) {
        const row = document.createElement("div");
        row.className = "feed-slot-tags";
        for (const tagName of tags) {
          const pill = document.createElement("span");
          pill.className = "series-pill";
          const palette = paletteForTag(tagName);
          if (palette) {
            pill.style.setProperty("--series-accent", palette.accent);
            pill.style.setProperty("--series-bg", palette.bg);
            pill.style.setProperty("--series-border", palette.border);
          }
          pill.textContent = tagName;
          row.appendChild(pill);
        }
        note.appendChild(row);
      }
      const title = document.createElement("div");
      title.className = "feed-slot-title";
      title.textContent = titleOf(item);
      note.appendChild(title);
      return note;
    }

    function renderFeed() {
      const board = document.createElement("div");
      board.className = "feed-board";
      const nets = document.createElement("div");
      nets.className = "feed-nets";
      const netList = [
        { id: "ig", label: "Инстаграм" },
        { id: "tt", label: "TikTok" },
        { id: "yt", label: "YouTube" },
        { id: "vk", label: "ВК" },
      ];
      for (const net of netList) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = net.label;
        if (feedNet === net.id) btn.className = "active";
        btn.onclick = () => {
          feedNet = net.id;
          try {
            sessionStorage.setItem("posterFeedNet", feedNet);
          } catch (_) {}
          render();
        };
        nets.appendChild(btn);
      }
      const scaleLabel = document.createElement("label");
      scaleLabel.className = "feed-scale";
      const scaleText = document.createElement("span");
      scaleText.textContent = `${feedScale}%`;
      const scaleInput = document.createElement("input");
      scaleInput.type = "range";
      scaleInput.min = "50";
      scaleInput.max = "100";
      scaleInput.step = "5";
      scaleInput.value = String(feedScale);
      scaleInput.setAttribute("aria-label", "Масштаб ленты");
      scaleInput.oninput = () => {
        feedScale = Number(scaleInput.value) || 100;
        scaleText.textContent = `${feedScale}%`;
        const sheet = board.querySelector(".feed-sheet");
        if (sheet) sheet.style.setProperty("--feed-scale", String(feedScale / 100));
        try {
          sessionStorage.setItem("posterFeedScale", String(feedScale));
        } catch (_) {}
      };
      scaleLabel.append("Масштаб", scaleInput, scaleText);
      const tools = document.createElement("div");
      tools.className = "feed-tools";
      tools.append(nets, scaleLabel);
      board.appendChild(tools);

      if (feedNet !== "ig") {
        const empty = document.createElement("p");
        empty.className = "feed-empty";
        const label = netList.find((n) => n.id === feedNet);
        empty.textContent = `Превью ленты ${label ? label.label : ""} пока пустое.`;
        board.appendChild(empty);
        mount.appendChild(board);
        return;
      }

      const sequence = feedSequence();
      if (!sequence.length) {
        const empty = document.createElement("p");
        empty.className = "feed-empty";
        empty.textContent = "Нет материалов для ленты.";
        board.appendChild(empty);
        mount.appendChild(board);
        return;
      }

      const cols = 3;
      const rows = Math.ceil(sequence.length / cols);
      const cells = Array.from({ length: rows * cols }, () => null);
      for (let i = 0; i < sequence.length; i++) {
        const rowFromBottom = Math.floor(i / cols);
        const colFromRight = i % cols;
        const row = rows - 1 - rowFromBottom;
        const col = cols - 1 - colFromRight;
        cells[row * cols + col] = sequence[i];
      }

      const sheet = document.createElement("div");
      sheet.className = "feed-sheet";
      sheet.style.setProperty("--feed-scale", String(feedScale / 100));
      for (let r = 0; r < rows; r++) {
        const line = document.createElement("div");
        line.className = "feed-line";
        const lineCells = document.createElement("div");
        lineCells.className = "feed-line-cells";
        for (let c = 0; c < cols; c++) {
          const cell = cells[r * cols + c];
          if (!cell) {
            const gap = document.createElement("div");
            gap.className = "feed-cell is-empty";
            lineCells.appendChild(gap);
            continue;
          }
          if (cell.hole) {
            const hole = document.createElement("div");
            hole.className = "feed-cell is-hole";
            hole.title = cell.title || cell.caption || "";
            lineCells.appendChild(hole);
            continue;
          }
          const item = cell.item;
          const tile = document.createElement("button");
          tile.type = "button";
          tile.className = "feed-cell";
          if (!itemMatchesFilter(item)) tile.classList.add("is-filter-dim");
          const slidesCount = Array.isArray(item.carousel_files) ? item.carousel_files.length : 0;
          const hasCover = !!(item.cover_name || slidesCount > 0);
          if (hasCover) {
            const img = document.createElement("img");
            img.src = `/api/submissions/${item.id}/cover?thumb=1&t=${encodeURIComponent(item.updated_at || "")}`;
            img.alt = titleOf(item);
            img.draggable = false;
            tile.appendChild(img);
          } else {
            tile.classList.add("is-blank");
            tile.textContent = "нет заставки";
          }
          tile.title = `${titleOf(item)}${item.author_name ? " · " + item.author_name : ""}${cell.caption ? " · " + cell.caption : ""}`;
          tile.onclick = () => {
            if (onOpen) onOpen(item.id, item);
          };
          lineCells.appendChild(tile);
        }
        const times = document.createElement("div");
        times.className = "feed-line-times";
        for (let c = 0; c < cols; c++) {
          const cell = cells[r * cols + c];
          if (!cell) continue;
          times.appendChild(feedSlotNote(cell));
        }
        line.append(lineCells, times);
        sheet.appendChild(line);
      }
      board.appendChild(sheet);
      mount.appendChild(board);
    }

    function render() {
      mount.innerHTML = "";
      if (calendarView === "feed") {
        renderFeed();
        return;
      }
      const isAdmin = me && me.role === "admin";
      const days = currentDays();
      const stage = document.createElement("div");
      stage.className = "week-stage";

      const prevDay = document.createElement("button");
      prevDay.type = "button";
      prevDay.className = "week-day-arrow";
      prevDay.setAttribute("aria-label", "На день назад");
      prevDay.title = "На день назад";
      prevDay.innerHTML = dayArrowIcon(-1);
      prevDay.onclick = () => {
        viewStart = shiftWorkdays(viewStart, -1);
        load();
      };

      const nextDay = document.createElement("button");
      nextDay.type = "button";
      nextDay.className = "week-day-arrow";
      nextDay.setAttribute("aria-label", "На день вперёд");
      nextDay.title = "На день вперёд";
      nextDay.innerHTML = dayArrowIcon(1);
      nextDay.onclick = () => {
        viewStart = shiftWorkdays(viewStart, 1);
        load();
      };

      const grid = document.createElement("div");
      grid.className = "week-grid" + (isAdmin ? " has-day-footers" : "");
      const colParts = ["70px"];
      for (let i = 0; i < days.length; i++) {
        if (weekBreakBefore(days, i)) colParts.push("100px");
        colParts.push("minmax(0, 1fr)");
      }
      grid.style.gridTemplateColumns = colParts.join(" ");

      function appendGap() {
        const gap = document.createElement("div");
        gap.className = "week-week-gap";
        gap.setAttribute("aria-hidden", "true");
        grid.appendChild(gap);
      }

      grid.appendChild(document.createElement("div")).className = "week-corner";

      for (let i = 0; i < days.length; i++) {
        if (weekBreakBefore(days, i)) appendGap();
        const dayDate = days[i];
        const head = document.createElement("div");
        head.className = "week-day-head";
        const name = DAY_NAMES[(dayDate.getDay() + 6) % 7] || "";
        head.innerHTML = `<strong>${name}</strong><span>${dayDate.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</span>`;
        grid.appendChild(head);
      }

      for (let si = 0; si < SLOT_HOURS.length; si++) {
        const hour = SLOT_HOURS[si];
        const timeLabel = document.createElement("div");
        timeLabel.className = "week-time-label";
        timeLabel.textContent = SLOT_LABELS[si];
        grid.appendChild(timeLabel);

        for (let i = 0; i < days.length; i++) {
          if (weekBreakBefore(days, i)) appendGap();
          const dayDate = days[i];
          const dateIso = isoDate(dayDate);
          const pastDay = isPastDateIso(dateIso);
          const key = cellKey(dateIso, hour);
          const cell = document.createElement("div");
          cell.dataset.key = key;

          const item1 = itemByKeyAndPart(key, 1);
          const item2 = itemByKeyAndPart(key, 2);
          const bothUsed = !!(item1 && item2);
          const leftIsCarousel = !!(item1 && item1.media_kind === "carousel");
          const splitHalf = bothUsed || leftIsCarousel || !!(item2 && !item1);

          cell.className =
            "week-cell " +
            (splitHalf ? "cell-split-half" : "cell-primary-with-carousel") +
            (pastDay ? " is-past-slot" : "");

          const sub1 = document.createElement("div");
          sub1.className = "week-subslot subslot-primary";
          if (item1) {
            sub1.appendChild(
              buildCard(item1, {
                me,
                canDrag,
                onOpen,
                onClearSlot: clearSlot,
                onToggleApprove: toggleApprove,
                statusLabels,
                inSlot: true,
                halfSlot: splitHalf || item1.media_kind === "carousel",
              })
            );
            if (!itemMatchesFilter(item1)) sub1.firstChild.classList.add("is-filter-dim");
          } else {
            const add1 = document.createElement("button");
            add1.type = "button";
            add1.className = "week-add";
            add1.disabled = pastDay;
            add1.innerHTML = `<span class="plus-sign">+</span><span class="plus-hint">рилс</span>`;
            add1.title = pastDay ? "Прошедшая дата недоступна" : "Новый рилс (видео) в этот слот";
            add1.onclick = async () => {
              if (pastDay) return;
              if (onCreate) {
                await onCreate(key, 1, "video");
                await load();
              }
            };
            sub1.appendChild(add1);
          }
          if (!pastDay) setupSubslotDrop(sub1, key, 1);

          const sub2 = document.createElement("div");
          sub2.className = "week-subslot subslot-carousel" + (item2 || leftIsCarousel ? " is-filled" : " is-mini");
          if (item2) {
            sub2.appendChild(
              buildCard(item2, {
                me,
                canDrag,
                onOpen,
                onClearSlot: clearSlot,
                onToggleApprove: toggleApprove,
                statusLabels,
                inSlot: true,
                halfSlot: true,
              })
            );
            if (!itemMatchesFilter(item2)) sub2.firstChild.classList.add("is-filter-dim");
          } else {
            const add2 = document.createElement("button");
            add2.type = "button";
            add2.className = "week-add-carousel";
            add2.disabled = pastDay;
            const wantVideoRight = leftIsCarousel;
            add2.innerHTML = `<span class="plus-sign">+</span><span class="plus-hint">${
              wantVideoRight ? "рилс" : "карусель"
            }</span>`;
            add2.title = pastDay
              ? "Прошедшая дата недоступна"
              : wantVideoRight
                ? "Добавить рилс (видео) во второй полуслот"
                : "Создать карусель фото (до 15 шт.) во втором полуслоте";
            add2.onclick = async () => {
              if (pastDay) return;
              if (onCreate) {
                await onCreate(key, 2, wantVideoRight ? "video" : "carousel");
                await load();
              }
            };
            sub2.appendChild(add2);
          }
          if (!pastDay) setupSubslotDrop(sub2, key, 2);

          cell.appendChild(sub1);
          cell.appendChild(sub2);
          grid.appendChild(cell);
        }
      }

      if (isAdmin) {
        const footCorner = document.createElement("div");
        footCorner.className = "week-corner-foot";
        grid.appendChild(footCorner);

        for (let i = 0; i < days.length; i++) {
          if (weekBreakBefore(days, i)) appendGap();
          const dayDate = days[i];
          const dateIso = isoDate(dayDate);
          const dayItemsCount = items.filter((it) => (it.scheduled_at || "").slice(0, 10) === dateIso).length;
          const foot = document.createElement("div");
          foot.className = "week-day-footer";
          foot.innerHTML = `
            <a class="btn day-download-btn ${dayItemsCount ? "has-items" : ""}" href="/api/admin/export-day-zip?date=${dateIso}" download="poster_day_${dateIso}.zip" title="Скачать архив материалов, текстов и расписания на ${dateIso} для POSTER">
              ⬇ Скачать материалы (${dayItemsCount})
            </a>`;
          grid.appendChild(foot);
        }
      }

      stage.appendChild(prevDay);
      stage.appendChild(grid);
      stage.appendChild(nextDay);
      mount.appendChild(stage);
    }

    return {
      load,
      getItems: () => items,
      moveItem,
      clearSlot,
      toggleApprove,
      findItem,
    };
  }

  return {
    init,
    renderAuthorPool,
    renderMiniMonth,
    buildCard,
    confirmDeleteModal,
    confirmDeleteUserModal,
    openPasswordModal,
    openSlotPickerModal,
    mondayOf,
    isoDate,
    todayIso,
    isPastDateIso,
    tagsOfItem,
    slotForItem,
    slotPartOf,
    isLocked,
    defaultCanDrag,
    canClearSlot,
    canDeleteItem,
    setSeriesPalette,
    setCatalogPalette,
    SLOT_HOURS,
    DEFAULT_STATUS,
    SERIES_PALETTE,
  };
})();
