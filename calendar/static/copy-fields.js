/** Поля описаний: общий текст + личные теги связки с цветом + уникальные параметры из профиля + опциональные переопределения по сетям. */
const CopyFields = {
  /** Только теги, созданные самим текущим пользователем (по умолчанию пусто). */
  seriesTagsList: [],

  keys: [
    "base",
    "series_tag",
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
    "tags",
    "instagram_collaborators",
    "instagram_reel_tags",
    "tiktok_mentions",
  ],

  labels: {
    base: "Универсальный текст",
    series_tag: "Тег связки карточек (до 10 карточек одного цвета)",
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
    base: "Идёт во все публикации, если для сети не включено своё описание ниже.",
    series_tag:
      "У каждого автора свой список тегов. Создайте тег (одно слово + цвет) — после создания он появится в выпадушке.",
    tags: "По умолчанию подставлены ваши теги из профиля. Можно дописать свои или убрать лишние (# или без, через запятую).",
    instagram_collaborators:
      "По умолчанию подставлены ваши ники из профиля. Можно дописать или изменить (логины через запятую, без @).",
    instagram_reel_tags:
      "По умолчанию подставлено облако отметок Instagram из вашего профиля. Можно дописать или изменить.",
    tiktok_mentions:
      "По умолчанию подставлено облако отметок TikTok из вашего профиля (@ или через запятую). Можно дописать или изменить.",
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

  setSeriesTags(payload) {
    const mine = Array.isArray(payload) ? payload : (payload && payload.mine) || [];
    const all = Array.isArray(payload) ? payload : (payload && payload.all) || mine;
    CopyFields.seriesTagsList = mine;
    if (typeof WeekPlanner !== "undefined" && WeekPlanner.setSeriesPalette) {
      WeekPlanner.setSeriesPalette(all);
    }
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

    CopyFields.populateSeriesSelect(root, d.series_tag || "");
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
    CopyFields.populateSeriesSelect(root);
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

    const addTagBtn = root.querySelector("[data-add-series-tag]");
    if (addTagBtn) {
      addTagBtn.addEventListener("click", async () => {
        const nameInp = root.querySelector("[data-new-tag-name]");
        const colorInp = root.querySelector("[data-new-tag-color]");
        const raw = (nameInp ? nameInp.value : "").trim();
        if (!raw) {
          alert("Введите название тега (одно слово без пробелов).");
          return;
        }
        if (/\s/.test(raw)) {
          alert("Название тега должно состоять из одного слова без пробелов.");
          return;
        }
        const color = colorInp ? colorInp.value : "#2dd4bf";
        try {
          const res = await api("/api/series-tags", {
            method: "POST",
            body: JSON.stringify({ name: raw, color }),
          });
          CopyFields.setSeriesTags(res);
          CopyFields.populateSeriesSelect(root, raw);
          if (nameInp) nameInp.value = "";
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
