/** Поля описаний: общий текст + личные теги связки с цветом + уникальные параметры из профиля + опциональные переопределения по сетям. */
const CopyFields = {
  /** Свои теги автора открытой карточки. */
  seriesTagsList: [],
  /** Все личные теги всех авторов — для фильтра и цвета карточек. */
  allSeriesTags: [],
  /** Общие теги, которые создаёт администратор. */
  catalogTagsList: [],
  viewer: null,
  tagOwnerId: "",
  submissionId: "",
  cardTags: [],
  tagRoot: null,
  formLocked: false,

  keys: [
    "base",
    "series_tag",
    "catalog_tag",
    "instagram",
    "tiktok",
    "youtube_title",
    "youtube",
    "vk",
    "tags",
    "instagram_collaborators",
    "instagram_reel_tags",
    "tiktok_mentions",
  ],

  overrideGroups: {
    instagram: ["instagram"],
    tiktok: ["tiktok"],
    youtube: ["youtube_title", "youtube"],
    vk: ["vk"],
  },

  uniqueParamKeys: [
    "series_tag",
    "catalog_tag",
    "tags",
    "instagram_collaborators",
    "instagram_reel_tags",
    "tiktok_mentions",
  ],

  labels: {
    base: "Описание для соцсети",
    series_tag: "Свой тег",
    catalog_tag: "Общий тег",
    instagram: "Instagram — подпись",
    tiktok: "TikTok — подпись",
    youtube_title: "YouTube — название",
    youtube: "YouTube — описание",
    vk: "VK Клипы — описание",
    tags: "Хэштеги (.tags.txt)",
    instagram_collaborators: "Instagram — коллабораторы (.collaborators.txt)",
    instagram_reel_tags: "Instagram — отметки на рилсе (.reel_tags.txt)",
    tiktok_mentions: "TikTok — отметки (@ через запятую или без @)",
  },

  hints: {
    base: "",
    series_tag:
      "Цветной бабл на карточке. Крестик предлагает открепить только её или удалить тег отовсюду.",
    catalog_tag:
      "Общий тег администратора тоже вешается баблом. Его можно открепить от карточки.",
    tags: "",
    instagram_collaborators: "",
    instagram_reel_tags: "",
    tiktok_mentions: "",
  },

  limitKey(field) {
    const map = {
      base: "tiktok",
      instagram: "instagram",
      tiktok: "tiktok",
      youtube: "youtube",
      vk: "vk",
      youtube_title: "youtube_title",
    };
    return map[field] || null;
  },

  setViewer(me) {
    CopyFields.viewer = me || null;
  },

  setSeriesTags(payload) {
    const mine = Array.isArray(payload) ? payload : (payload && payload.mine) || [];
    const all = Array.isArray(payload) ? payload : (payload && payload.all) || mine;
    CopyFields.seriesTagsList = mine;
    CopyFields.allSeriesTags = all;
    if (typeof WeekPlanner !== "undefined" && WeekPlanner.setSeriesPalette) {
      WeekPlanner.setSeriesPalette(all);
    }
  },

  setCatalogTags(payload) {
    const items = Array.isArray(payload) ? payload : (payload && payload.items) || [];
    CopyFields.catalogTagsList = items;
    if (typeof WeekPlanner !== "undefined" && WeekPlanner.setCatalogPalette) {
      WeekPlanner.setCatalogPalette(items);
    }
    if (typeof CopyFields.onCatalogChanged === "function") CopyFields.onCatalogChanged(items);
  },

  async ensureOwnerTags(ownerId) {
    const id = ownerId || (CopyFields.viewer && CopyFields.viewer.id) || "";
    CopyFields.tagOwnerId = id;
    const q = id ? `?owner_id=${encodeURIComponent(id)}` : "";
    const tags = await api(`/api/series-tags${q}`);
    CopyFields.setSeriesTags(tags);
    const catalog = await api("/api/catalog-tags");
    CopyFields.setCatalogTags(catalog);
    return tags;
  },

  applyTagRole(root) {
    const admin = !!(CopyFields.viewer && CopyFields.viewer.role === "admin");
    const creator = root.querySelector("[data-catalog-creator]");
    const del = root.querySelector("[data-delete-catalog-tag]");
    if (creator) creator.hidden = !admin;
    if (del) del.hidden = !admin;
  },

  populateSeriesSelect(root, selectedVal) {
    const sel = root.querySelector('[data-copy-field="series_tag"]');
    if (!sel || sel.tagName !== "SELECT") return;
    const current = selectedVal !== undefined ? selectedVal : sel.value;
    sel.innerHTML = "";
    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "— без тега связки —";
    sel.appendChild(emptyOpt);

    const seen = new Set();
    for (const item of CopyFields.seriesTagsList) {
      seen.add(item.name);
      const opt = document.createElement("option");
      opt.value = item.name;
      opt.textContent = item.name;
      opt.dataset.color = item.color;
      sel.appendChild(opt);
    }
    if (current && !seen.has(current)) {
      const opt = document.createElement("option");
      opt.value = current;
      opt.textContent = current;
      sel.appendChild(opt);
    }
    sel.value = current || "";
  },

  populateCatalogSelect(root, selectedVal) {
    const sel = root.querySelector('[data-copy-field="catalog_tag"]');
    if (!sel || sel.tagName !== "SELECT") return;
    const current = selectedVal !== undefined ? selectedVal : sel.value;
    sel.innerHTML = "";
    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "— без общего тега —";
    sel.appendChild(emptyOpt);
    const seen = new Set();
    for (const item of CopyFields.catalogTagsList) {
      seen.add(item.name);
      const opt = document.createElement("option");
      opt.value = item.name;
      opt.textContent = item.name;
      opt.dataset.color = item.color || "";
      sel.appendChild(opt);
    }
    if (current && !seen.has(current)) {
      const opt = document.createElement("option");
      opt.value = current;
      opt.textContent = current;
      sel.appendChild(opt);
    }
    sel.value = current || "";
  },

  defaultsFromUserProfile(me) {
    const p = (me && me.profile) || {};
    const handles = [
      p.instagram_handle,
      p.tiktok_handle,
      p.youtube_handle,
      p.vk_handle,
      p.telegram_handle,
    ]
      .map((x) => String(x || "").trim().replace(/^@+/, ""))
      .filter(Boolean);
    const uniqueHandles = [...new Set(handles)].join(", ");
    return {
      tags: p.tags_cloud || "",
      instagram_collaborators: p.default_collaborators || uniqueHandles || "",
      instagram_reel_tags: p.ig_mentions_cloud || "",
      tiktok_mentions: p.tt_mentions_cloud || "",
    };
  },

  _overrideFlags(descriptions) {
    const d = descriptions || {};
    const raw = d.overrides;
    if (raw && typeof raw === "object") return raw;
    return {};
  },

  _overrideOn(root, network) {
    const cb = root.querySelector(`[data-copy-override="${network}"]`);
    return !!(cb && cb.checked);
  },

  setFormLocked(locked) {
    CopyFields.formLocked = !!locked;
    if (CopyFields.tagRoot) CopyFields.renderTagBubbles(CopyFields.tagRoot);
    document.querySelectorAll("[data-open-tag-create]").forEach((btn) => {
      btn.hidden = !!locked;
    });
  },

  _normalizeTag(entry) {
    if (!entry) return null;
    const name = String(typeof entry === "string" ? entry : entry.name || "").trim();
    if (!name) return null;
    let source = typeof entry === "object" && entry.source ? entry.source : "";
    if (source !== "own" && source !== "catalog") {
      const inCatalog = CopyFields.catalogTagsList.some((t) => t.name === name);
      const inOwn = CopyFields.seriesTagsList.some((t) => t.name === name);
      source = inCatalog && !inOwn ? "catalog" : "own";
    }
    return { name, source };
  },

  setCardTagsFromDescriptions(descriptions) {
    const d = descriptions || {};
    const raw = Array.isArray(d.card_tags) ? d.card_tags : [];
    let tags = raw.map((entry) => CopyFields._normalizeTag(entry)).filter(Boolean);
    if (!tags.length) {
      if (String(d.series_tag || "").trim()) tags.push(CopyFields._normalizeTag({ name: d.series_tag, source: "own" }));
      if (String(d.catalog_tag || "").trim()) tags.push(CopyFields._normalizeTag({ name: d.catalog_tag, source: "catalog" }));
    }
    const seen = new Set();
    CopyFields.cardTags = tags.filter((t) => {
      const key = t.name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  },

  packCardTags() {
    const card_tags = CopyFields.cardTags.map((t) => ({ name: t.name, source: t.source === "catalog" ? "catalog" : "own" }));
    const own = card_tags.find((t) => t.source !== "catalog");
    const shared = card_tags.find((t) => t.source === "catalog");
    return {
      card_tags,
      series_tag: own ? own.name : "",
      catalog_tag: shared ? shared.name : "",
    };
  },

  _tagColor(name, source) {
    const list = source === "catalog" ? CopyFields.catalogTagsList : CopyFields.seriesTagsList;
    const found = list.find((t) => t.name === name) || CopyFields.catalogTagsList.find((t) => t.name === name) || CopyFields.seriesTagsList.find((t) => t.name === name);
    return (found && found.color) || (source === "catalog" ? "#60a5fa" : "#2dd4bf");
  },

  renderTagBubbles(root) {
    if (!root) return;
    CopyFields.tagRoot = root;
    const assigned = root.querySelector("[data-card-tag-bubbles]");
    const available = root.querySelector("[data-available-tag-bubbles]");
    if (!assigned) return;
    assigned.innerHTML = "";
    if (available) available.innerHTML = "";
    const locked = CopyFields.formLocked;
    for (const tag of CopyFields.cardTags) {
      assigned.appendChild(CopyFields._bubble(tag, { assigned: true, locked }));
    }
    if (!CopyFields.cardTags.length) assigned.innerHTML = "";
    if (!available) return;
    const have = new Set(CopyFields.cardTags.map((t) => t.name.toLowerCase()));
    const pool = [];
    for (const t of CopyFields.seriesTagsList) pool.push({ name: t.name, source: "own", color: t.color });
    for (const t of CopyFields.catalogTagsList) {
      if (!pool.some((x) => x.name.toLowerCase() === t.name.toLowerCase())) {
        pool.push({ name: t.name, source: "catalog", color: t.color });
      }
    }
    for (const tag of pool) {
      if (have.has(tag.name.toLowerCase())) continue;
      available.appendChild(CopyFields._bubble(tag, { assigned: false, locked }));
    }
  },

  canEditTag(tag) {
    if (CopyFields.formLocked) return false;
    const me = CopyFields.viewer;
    if (!me || me.readonly || me.role === "observer") return false;
    if (tag.source === "catalog") return me.role === "admin";
    return true;
  },

  _bubble(tag, opts) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tag-bubble" + (opts.assigned ? " is-on" : " is-available");
    btn.style.setProperty("--bubble", tag.color || CopyFields._tagColor(tag.name, tag.source));
    btn.disabled = !!opts.locked;
    const label = document.createElement("span");
    label.textContent = tag.name;
    btn.appendChild(label);
    if (!opts.locked && CopyFields.canEditTag(tag)) {
      const edit = document.createElement("span");
      edit.className = "tag-bubble-edit";
      edit.textContent = "✎";
      edit.title = "Изменить тег";
      edit.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        CopyFields.openTagEditModal(tag);
      });
      btn.appendChild(edit);
    }
    if (opts.assigned) {
      const x = document.createElement("span");
      x.className = "tag-bubble-x";
      x.textContent = "×";
      x.title = "Снять с карточки";
      x.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        CopyFields.unpinTag(tag);
      });
      btn.appendChild(x);
      btn.title = tag.name;
    } else {
      btn.title = "Добавить тег на карточку";
      btn.onclick = () => CopyFields.attachTag(tag);
    }
    return btn;
  },

  unpinTag(tag) {
    if (CopyFields.formLocked) return;
    CopyFields.cardTags = CopyFields.cardTags.filter((t) => t.name.toLowerCase() !== tag.name.toLowerCase());
    CopyFields.renderTagBubbles(CopyFields.tagRoot);
    CopyFields.persistCardTags();
  },

  canCreateTag() {
    const me = CopyFields.viewer;
    if (CopyFields.formLocked) return false;
    if (!me || me.readonly || me.role === "observer") return false;
    return true;
  },

  attachTag(tag) {
    if (CopyFields.formLocked) return;
    if (CopyFields.cardTags.some((t) => t.name.toLowerCase() === tag.name.toLowerCase())) return;
    CopyFields.cardTags = CopyFields.cardTags.concat([{ name: tag.name, source: tag.source === "catalog" ? "catalog" : "own" }]);
    CopyFields.renderTagBubbles(CopyFields.tagRoot);
    CopyFields.persistCardTags();
  },

  async persistCardTags() {
    const id = CopyFields.submissionId;
    if (!id || !CopyFields.tagRoot) return;
    try {
      await api(`/api/submissions/${id}`, {
        method: "PUT",
        body: JSON.stringify({ descriptions: CopyFields.readForm(CopyFields.tagRoot) }),
      });
      if (typeof CopyFields.onTagsChanged === "function") CopyFields.onTagsChanged();
    } catch (err) {
      alert(err.message || String(err));
    }
  },

  openTagEditModal(tag) {
    const creating = !tag;
    if (creating) {
      if (!CopyFields.canCreateTag()) return;
    } else if (!CopyFields.canEditTag(tag)) return;
    let wrap = document.getElementById("tagEditBackdrop");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "tagEditBackdrop";
      wrap.className = "modal-backdrop";
      wrap.style.zIndex = "80";
      wrap.innerHTML = `
        <div class="modal settings-modal" role="dialog" aria-modal="true">
          <div class="modal-header">
            <div class="modal-header-text">
              <h2 id="tagEditTitle">Изменить тег</h2>
              <p class="modal-desc" id="tagEditDesc"></p>
            </div>
          </div>
          <div class="modal-body settings-body">
            <div class="settings-tag-form">
              <label class="field-label" for="tagEditName">Название</label>
              <input type="text" id="tagEditName" class="settings-tag-name" maxlength="24" placeholder="Одно слово, без пробелов">
              <div class="settings-tag-color-row">
                <span>Цвет</span>
                <input type="color" id="tagEditColor" value="#2dd4bf">
                <button type="button" class="primary" id="tagEditSave">Сохранить</button>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="danger-text" id="tagEditDelete">Удалить тег</button>
            <span class="footer-spacer"></span>
            <button type="button" class="outline" id="tagEditCancel">Отмена</button>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      wrap.addEventListener("click", (e) => {
        if (e.target === wrap) wrap.classList.remove("open");
      });
      wrap.querySelector("#tagEditCancel").onclick = () => wrap.classList.remove("open");
    }
    const nameInp = wrap.querySelector("#tagEditName");
    const colorInp = wrap.querySelector("#tagEditColor");
    const saveBtn = wrap.querySelector("#tagEditSave");
    const deleteBtn = wrap.querySelector("#tagEditDelete");
    if (creating) {
      wrap.querySelector("#tagEditTitle").textContent = "Новый тег";
      wrap.querySelector("#tagEditDesc").textContent = "Одно слово без пробелов. Тег сразу встанет на эту карточку.";
      nameInp.value = "";
      colorInp.value = "#2dd4bf";
      saveBtn.textContent = "Создать";
      deleteBtn.hidden = true;
    } else {
      const color = tag.color || CopyFields._tagColor(tag.name, tag.source);
      wrap.querySelector("#tagEditTitle").textContent = `Тег «${tag.name}»`;
      wrap.querySelector("#tagEditDesc").textContent = "Новое имя попадёт на все карточки, где тег уже стоит. Удаление снимет его отовсюду.";
      nameInp.value = tag.name;
      colorInp.value = /^#[0-9a-fA-F]{6}$/.test(color) ? color : tag.source === "catalog" ? "#60a5fa" : "#2dd4bf";
      saveBtn.textContent = "Сохранить";
      deleteBtn.hidden = false;
    }
    saveBtn.onclick = async () => {
      const raw = nameInp.value.trim();
      if (!raw) {
        alert("Введите название тега (одно слово без пробелов).");
        return;
      }
      if (/\s/.test(raw)) {
        alert("Название тега должно состоять из одного слова без пробелов.");
        return;
      }
      try {
        if (creating) await CopyFields.createOwnTag(raw, colorInp.value);
        else await CopyFields.saveTagEdit(tag, raw, colorInp.value);
        wrap.classList.remove("open");
      } catch (err) {
        alert(err.message || String(err));
      }
    };
    deleteBtn.onclick = async () => {
      if (creating) return;
      if (!confirm(`Удалить тег «${tag.name}»? Он пропадёт из списка и со всех карточек.`)) return;
      try {
        await CopyFields.deleteTagEverywhere(tag);
        wrap.classList.remove("open");
      } catch (err) {
        alert(err.message || String(err));
      }
    };
    wrap.classList.add("open");
    nameInp.focus();
  },

  async createOwnTag(name, color) {
    const body = { name, color };
    if (
      CopyFields.viewer &&
      CopyFields.viewer.role === "admin" &&
      CopyFields.tagOwnerId &&
      CopyFields.tagOwnerId !== CopyFields.viewer.id
    ) {
      body.user_id = CopyFields.tagOwnerId;
    }
    const res = await api("/api/series-tags", {
      method: "POST",
      body: JSON.stringify(body),
    });
    CopyFields.setSeriesTags(res);
    if (!CopyFields.cardTags.some((t) => t.name.toLowerCase() === name.toLowerCase())) {
      CopyFields.cardTags = CopyFields.cardTags.concat([{ name, source: "own" }]);
    }
    CopyFields.renderTagBubbles(CopyFields.tagRoot);
    CopyFields.persistCardTags();
    if (typeof CopyFields.onTagsChanged === "function") CopyFields.onTagsChanged(res);
    return res;
  },

  async deleteTagEverywhere(tag) {
    let res;
    if (tag.source === "catalog") {
      res = await api(`/api/catalog-tags/${encodeURIComponent(tag.name)}`, { method: "DELETE" });
      CopyFields.setCatalogTags(res);
    } else {
      const q = CopyFields.tagOwnerId ? `?owner_id=${encodeURIComponent(CopyFields.tagOwnerId)}` : "";
      res = await api(`/api/series-tags/${encodeURIComponent(tag.name)}${q}`, { method: "DELETE" });
      CopyFields.setSeriesTags(res);
    }
    CopyFields.cardTags = CopyFields.cardTags.filter((t) => t.name.toLowerCase() !== tag.name.toLowerCase());
    CopyFields.renderTagBubbles(CopyFields.tagRoot);
    if (typeof CopyFields.onTagsChanged === "function") CopyFields.onTagsChanged(res);
    return res;
  },

  async saveTagEdit(tag, name, color) {
    const old = tag.name;
    let res;
    if (tag.source === "catalog") {
      res = await api(`/api/catalog-tags/${encodeURIComponent(old)}`, {
        method: "PATCH",
        body: JSON.stringify({ name, color }),
      });
      CopyFields.setCatalogTags(res);
    } else {
      const body = { name, color };
      if (
        CopyFields.viewer &&
        CopyFields.viewer.role === "admin" &&
        CopyFields.tagOwnerId &&
        CopyFields.tagOwnerId !== CopyFields.viewer.id
      ) {
        body.user_id = CopyFields.tagOwnerId;
      }
      res = await api(`/api/series-tags/${encodeURIComponent(old)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      CopyFields.setSeriesTags(res);
    }
    CopyFields.cardTags = CopyFields.cardTags.map((t) =>
      t.source === tag.source && t.name.toLowerCase() === old.toLowerCase() ? { ...t, name } : t
    );
    CopyFields.renderTagBubbles(CopyFields.tagRoot);
    if (typeof CopyFields.onTagsChanged === "function") CopyFields.onTagsChanged(res);
    return res;
  },

  readForm(root) {
    const out = {};
    const elBase = root.querySelector('[data-copy-field="base"]');
    if (elBase) out.base = elBase.value;
    for (const key of CopyFields.uniqueParamKeys) {
      const el = root.querySelector(`[data-copy-field="${key}"]`);
      if (el) out[key] = el.value;
    }
    out.overrides = {};
    for (const network of Object.keys(CopyFields.overrideGroups)) {
      const on = CopyFields._overrideOn(root, network);
      out.overrides[network] = on;
      for (const key of CopyFields.overrideGroups[network]) {
        const el = root.querySelector(`[data-copy-field="${key}"]`);
        out[key] = on && el ? el.value : "";
      }
    }
    out._profile_prefilled = true;
    const packed = CopyFields.packCardTags();
    out.card_tags = packed.card_tags;
    out.series_tag = packed.series_tag;
    out.catalog_tag = packed.catalog_tag;
    return out;
  },

  writeForm(root, descriptions, me) {
    const raw = descriptions || {};
    const profDef = CopyFields.defaultsFromUserProfile(me);
    const alreadyPrefilled = !!raw._profile_prefilled;
    const d = { ...raw };

    for (const k of ["tags", "instagram_collaborators", "instagram_reel_tags", "tiktok_mentions"]) {
      if (d[k] === undefined || (!alreadyPrefilled && !String(d[k] || "").trim())) {
        d[k] = profDef[k] || "";
      }
    }

    CopyFields.setCardTagsFromDescriptions(d);
    CopyFields.renderTagBubbles(root);
    CopyFields.applyTagRole(root);
    for (const key of CopyFields.keys) {
      const el = root.querySelector(`[data-copy-field="${key}"]`);
      if (el) el.value = d[key] || "";
    }
    CopyFields.refreshOverrideUI(root, d);
  },

  refreshOverrideUI(root, descriptions) {
    const flags = CopyFields._overrideFlags(descriptions);
    for (const network of Object.keys(CopyFields.overrideGroups)) {
      const on = !!flags[network];
      const cb = root.querySelector(`[data-copy-override="${network}"]`);
      if (cb) cb.checked = on;
      CopyFields._setOverridePanel(root, network, on);
      const acc = root.querySelector(`[data-accordion="${network}"]`);
      if (acc) acc.open = false;
      CopyFields._updateAccordionSummary(root, network);
    }
  },

  _setOverridePanel(root, network, on) {
    const panel = root.querySelector(`[data-copy-override-panel="${network}"]`);
    if (!panel) return;
    panel.hidden = !on;
    panel.querySelectorAll("input, textarea").forEach((el) => {
      el.disabled = !on;
    });
  },

  _updateAccordionSummary(root, network) {
    const el = root.querySelector(`[data-acc-summary="${network}"]`);
    if (!el) return;
    const on = CopyFields._overrideOn(root, network);
    if (on) {
      el.textContent = "своё описание";
      el.classList.add("acc-summary-custom");
    } else {
      el.textContent = "универсальный текст";
      el.classList.remove("acc-summary-custom");
    }
  },

  bindOverrides(root, onTagsChanged) {
    CopyFields.onTagsChanged = onTagsChanged;
    CopyFields.renderTagBubbles(root);
    CopyFields.applyTagRole(root);
    if (root.dataset.copyOverridesBound) return;
    root.dataset.copyOverridesBound = "1";

    for (const network of Object.keys(CopyFields.overrideGroups)) {
      const cb = root.querySelector(`[data-copy-override="${network}"]`);
      if (!cb) continue;
      cb.addEventListener("change", () => {
        CopyFields._setOverridePanel(root, network, cb.checked);
        CopyFields._updateAccordionSummary(root, network);
      });
    }

    root.querySelectorAll("[data-copy-field]").forEach((el) => {
      el.addEventListener("input", () => {
        const acc = el.closest("[data-accordion]");
        if (acc) CopyFields._updateAccordionSummary(root, acc.getAttribute("data-accordion"));
      });
    });

    const addTagBtn = root.querySelector("[data-open-tag-create]");
    if (addTagBtn) {
      addTagBtn.addEventListener("click", () => CopyFields.openTagEditModal(null));
    }

    const deleteSeriesBtn = root.querySelector("[data-delete-series-tag]");
    if (deleteSeriesBtn) {
      deleteSeriesBtn.addEventListener("click", async () => {
        const sel = root.querySelector('[data-copy-field="series_tag"]');
        const name = sel ? sel.value.trim() : "";
        if (!name) {
          alert("Сначала выберите свой тег, который нужно удалить.");
          return;
        }
        if (!confirm(`Удалить тег «${name}»? Он пропадёт из списка и снимется со всех карточек этого автора.`)) return;
        try {
          const q = CopyFields.tagOwnerId ? `?owner_id=${encodeURIComponent(CopyFields.tagOwnerId)}` : "";
          const res = await api(`/api/series-tags/${encodeURIComponent(name)}${q}`, { method: "DELETE" });
          CopyFields.setSeriesTags(res);
          CopyFields.populateSeriesSelect(root, "");
          if (onTagsChanged) onTagsChanged(res);
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    }

    const clearCatalogBtn = root.querySelector("[data-clear-catalog-tag]");
    if (clearCatalogBtn) {
      clearCatalogBtn.addEventListener("click", () => {
        const sel = root.querySelector('[data-copy-field="catalog_tag"]');
        if (sel) sel.value = "";
      });
    }

    const addCatalogBtn = root.querySelector("[data-add-catalog-tag]");
    if (addCatalogBtn) {
      addCatalogBtn.addEventListener("click", async () => {
        const nameInp = root.querySelector("[data-new-catalog-name]");
        const colorInp = root.querySelector("[data-new-catalog-color]");
        const raw = (nameInp ? nameInp.value : "").trim();
        if (!raw) {
          alert("Введите название общего тега (одно слово без пробелов).");
          return;
        }
        if (/\s/.test(raw)) {
          alert("Название тега должно состоять из одного слова без пробелов.");
          return;
        }
        const color = colorInp ? colorInp.value : "#60a5fa";
        try {
          const res = await api("/api/catalog-tags", {
            method: "POST",
            body: JSON.stringify({ name: raw, color }),
          });
          CopyFields.setCatalogTags(res);
          if (!CopyFields.cardTags.some((t) => t.name.toLowerCase() === raw.toLowerCase())) {
            CopyFields.cardTags = CopyFields.cardTags.concat([{ name: raw, source: "catalog" }]);
          }
          CopyFields.renderTagBubbles(root);
          if (nameInp) nameInp.value = "";
          CopyFields.persistCardTags();
          if (onTagsChanged) onTagsChanged(res);
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    }

    const deleteCatalogBtn = root.querySelector("[data-delete-catalog-tag]");
    if (deleteCatalogBtn) {
      deleteCatalogBtn.addEventListener("click", async () => {
        const sel = root.querySelector('[data-copy-field="catalog_tag"]');
        const name = sel ? sel.value.trim() : "";
        if (!name) {
          alert("Сначала выберите общий тег, который нужно удалить из списка.");
          return;
        }
        if (!confirm(`Удалить общий тег «${name}»? Он пропадёт у всех и снимется со всех карточек.`)) return;
        try {
          const res = await api(`/api/catalog-tags/${encodeURIComponent(name)}`, { method: "DELETE" });
          CopyFields.setCatalogTags(res);
          CopyFields.populateCatalogSelect(root, "");
          if (onTagsChanged) onTagsChanged(res);
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    }
  },

  bindCounters(root, limits) {
    if (typeof counter !== "function") return;
    for (const key of CopyFields.keys) {
      const lk = CopyFields.limitKey(key);
      const el = root.querySelector(`[data-copy-field="${key}"]`);
      const cnt = root.querySelector(`[data-copy-counter="${key}"]`);
      if (!el || !cnt || !lk || !limits[lk]) continue;
      const max = limits[lk].char_limit;
      const tick = () => counter(el, max, cnt);
      el.addEventListener("input", tick);
      tick();
    }
  },
};
