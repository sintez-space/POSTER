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

  function weekLabel(monday) {
    const fri = addDays(monday, 4);
    const opts = { day: "2-digit", month: "short" };
    return `${monday.toLocaleDateString("ru-RU", opts)} — ${fri.toLocaleDateString("ru-RU", { ...opts, year: "numeric" })}`;
  }

  function titleOf(item) {
    const d = item.descriptions || {};
    return (d.base || d.tiktok || d.instagram || "").trim() || (item.media_kind === "carousel" ? "Карусель" : "Без названия");
  }

  function seriesTagOf(item) {
    const d = (item && item.descriptions) || {};
    return (d.series_tag || "").trim();
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
    const sTag = seriesTagOf(item);
    const palette = sTag ? SERIES_PALETTE[sTag] || hexToPalette("#2dd4bf") : null;
    const isCarousel = item.media_kind === "carousel";

    el.className =
      "week-card" +
      (foreign ? " foreign" : "") +
      (locked ? " locked" : "") +
      (sTag ? " has-series" : "") +
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
    const kindBadge = isCarousel ? `<span class="carousel-kind-pill" title="Карусель фото">Карусель</span>` : "";
    const seriesBadge = sTag ? `<span class="series-pill">${escHtml(sTag)}</span>` : "";
    const foreignBadge = foreign
      ? `<span class="slot-foreign-pill" title="Чужая карточка — редактировать или снять со слота нельзя">Чужой</span>`
      : "";
    const lockBadge = locked
      ? `<span class="slot-lock-pill" title="Подтверждено админом — слот зафиксирован">${
          me.role === "admin" ? "Фикс" : "Фикс админа"
        }</span>`
      : inSlot && !foreign
        ? `<span class="slot-pending-pill" title="Ожидает подтверждения админа">План</span>`
        : "";

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
          <div class="week-card-badges">${kindBadge}${seriesBadge}${lockBadge}${foreignBadge}</div>
        </div>
        <div class="week-card-title">${title}</div>
        <div class="week-card-meta">${author}</div>
      </div>
      <div class="week-card-overlay-bottom">
        ${seriesBadge}
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
  function renderMiniMonth(mount, year, month, countsMap, activeMonday, onSelectDay) {
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
    const activeEnd = activeMonday ? isoDate(addDays(activeMonday, 4)) : "";

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
        (dIso >= activeStart && dIso <= activeEnd ? " in-active-week" : "");
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
      me,
      onOpen,
      onCreate,
      canDrag = (item) => defaultCanDrag(item, me),
      statusLabels = DEFAULT_STATUS,
      resolveItem,
    } = options;

    let weekMonday = mondayOf(new Date());
    let items = [];

    function findItem(id) {
      return items.find((x) => x.id === id) || (resolveItem ? resolveItem(id) : null);
    }

    prevBtn.onclick = () => {
      weekMonday = addDays(weekMonday, -7);
      load();
    };
    nextBtn.onclick = () => {
      weekMonday = addDays(weekMonday, 7);
      load();
    };

    async function refreshMiniMonth() {
      if (!miniMonthMount) return;
      const y = weekMonday.getFullYear();
      const m = weekMonday.getMonth() + 1;
      try {
        const mData = await api(`/api/calendar/month?year=${y}&month=${m}`);
        renderMiniMonth(miniMonthMount, y, m, mData.counts || {}, weekMonday, (clickedDate) => {
          weekMonday = mondayOf(clickedDate);
          load();
        });
      } catch (_) {}
    }

    async function load() {
      labelEl.textContent = weekLabel(weekMonday);
      const data = await api(`/api/calendar/week?week_start=${isoDate(weekMonday)}`);
      items = data.items || [];
      render();
      await refreshMiniMonth();
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

    function render() {
      mount.innerHTML = "";
      const isAdmin = me && me.role === "admin";
      const grid = document.createElement("div");
      grid.className = "week-grid" + (isAdmin ? " has-day-footers" : "");

      grid.appendChild(document.createElement("div")).className = "week-corner";

      for (let d = 0; d < 5; d++) {
        const dayDate = addDays(weekMonday, d);
        const head = document.createElement("div");
        head.className = "week-day-head";
        head.innerHTML = `<strong>${DAY_NAMES[d]}</strong><span>${dayDate.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}</span>`;
        grid.appendChild(head);
      }

      for (let si = 0; si < SLOT_HOURS.length; si++) {
        const hour = SLOT_HOURS[si];
        const timeLabel = document.createElement("div");
        timeLabel.className = "week-time-label";
        timeLabel.textContent = SLOT_LABELS[si];
        grid.appendChild(timeLabel);

        for (let d = 0; d < 5; d++) {
          const dayDate = addDays(weekMonday, d);
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

        for (let d = 0; d < 5; d++) {
          const dayDate = addDays(weekMonday, d);
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

      mount.appendChild(grid);
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
    mondayOf,
    isoDate,
    todayIso,
    isPastDateIso,
    slotForItem,
    slotPartOf,
    isLocked,
    defaultCanDrag,
    canClearSlot,
    canDeleteItem,
    setSeriesPalette,
    SLOT_HOURS,
    DEFAULT_STATUS,
    SERIES_PALETTE,
  };
})();
