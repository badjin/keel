"use strict";

/* Keel — install screen logic.
   No frameworks. Every /api/* call carries X-Kit-Token from the page URL
   and X-Kit-Lang from the current UI language. State (selected targets,
   wiki path/language) lives in memory only and is shared across tabs for
   the life of this page. */

let TOKEN = new URLSearchParams(location.search).get("t") || "";
if (TOKEN) {
  try {
    sessionStorage.setItem("kitToken", TOKEN);
  } catch (e) {
    /* private mode or blocked storage; token still works for this load */
  }
  const url = new URL(location.href);
  url.searchParams.delete("t");
  history.replaceState(null, "", url.pathname + url.search + url.hash);
} else {
  try {
    TOKEN = sessionStorage.getItem("kitToken") || "";
  } catch (e) {
    TOKEN = "";
  }
}

/* ---------------- i18n ---------------- */

function loadStoredLang() {
  try {
    const v = localStorage.getItem("kitLang");
    return v === "en" || v === "ko" ? v : null;
  } catch (e) {
    return null;
  }
}

let LANG = loadStoredLang() || "en";

function storeLang(lang) {
  try {
    localStorage.setItem("kitLang", lang);
  } catch (e) {
    /* private mode or blocked storage; the choice just won't persist */
  }
}

function t(key, vars) {
  const table = (window.KIT_I18N && window.KIT_I18N[LANG]) || {};
  const fallback = (window.KIT_I18N && window.KIT_I18N.en) || {};
  let text = table[key] !== undefined ? table[key] : fallback[key] !== undefined ? fallback[key] : key;
  if (vars) {
    Object.keys(vars).forEach((k) => {
      text = text.split("{" + k + "}").join(String(vars[k]));
    });
  }
  return text;
}

function applyI18n() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.innerHTML = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
    let map;
    try {
      map = JSON.parse(el.getAttribute("data-i18n-attr"));
    } catch (e) {
      map = null;
    }
    if (!map) return;
    Object.keys(map).forEach((attr) => {
      el.setAttribute(attr, t(map[attr]));
    });
  });
  $all(".lang-btn").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.lang === LANG));
  });
}

function setLang(lang) {
  if (lang !== "en" && lang !== "ko") return;
  LANG = lang;
  storeLang(lang);
  applyI18n();
  rerenderForLanguage();
}

document.getElementById("lang-btn-en").addEventListener("click", () => setLang("en"));
document.getElementById("lang-btn-ko").addEventListener("click", () => setLang("ko"));

function rerenderForLanguage() {
  // Re-render every dynamically built piece of UI from `state`, so
  // switching language never loses the user's current selections/inputs.
  if (state.catalogue.length) relabelHookCards();
  populateLlmSelect();
  syncGithubLanguageDefault();
  renderChosenFolders();
  renderRepoList();
  renderLocalRepoList();
  relabelRail();
  renderGhWhoami();
  if (state.finishResult) renderFinishSummary(state.finishResult);
  if (state.wikiResultItems) renderList("wiki-result", state.wikiResultItems);
  relabelErrors();
  syncDue(dueRange.value);
  if (state.env) {
    const missing = [];
    if (!state.env.clis.claude) missing.push("Claude Code");
    if (!state.env.clis.codex) missing.push("Codex");
    document.getElementById("target-detect-note").textContent = missing.length
      ? t("hooks.targetNotFound", { list: missing.join(", ") })
      : "";
  }
}

function relabelRail() {
  $all(".rail-station").forEach((st) => {
    const label = st.querySelector(".rail-label");
    if (label) label.textContent = t("events." + st.dataset.event);
  });
}

function relabelErrors() {
  Object.keys(state.errors).forEach((id) => {
    const src = state.errors[id];
    if (!src) return;
    const el = document.getElementById(id);
    if (el) el.textContent = t(src.key, src.vars);
  });
}

const STATIONS = [
  { event: "SessionStart" },
  { event: "UserPromptSubmit" },
  { event: "PreToolUse" },
  { event: "PostToolUse" },
  { event: "Stop" },
  { event: "SessionEnd" },
];

const state = {
  env: null,
  catalogue: [],
  installed: { claude: [], codex: [] },
  targets: new Set(),
  selectedHooks: new Set(),
  handoffTargets: new Set(),
  handoffThreshold: 60,
  wikiPath: "",
  wikiLocation: "",
  wikiFolderName: "knowledge-base",
  wikiLanguage: LANG,
  localFolderStack: [],
  chosenFolders: [], // [{path, isGit}] — survives folder navigation
  gitSubfolders: [],
  ghLanguage: LANG,
  ghLlm: "",
  repos: [],
  selectedRepos: {}, // full_name/path -> {branch, kind}
  jobId: null,
  jobTimer: null,
  ghWhoami: null, // null = never connected; {} = connected without a login; {login} otherwise
  finishResult: null,
  wikiResultItems: null,
  errors: {}, // elId -> {key, vars} for a currently-shown error that came from t(), else null
};

function $(sel, root) {
  return (root || document).querySelector(sel);
}
function $all(sel, root) {
  return Array.from((root || document).querySelectorAll(sel));
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {}, { "X-Kit-Token": TOKEN, "X-Kit-Lang": LANG });
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const message = (data && data.error) || t("common.requestFailed", { status: res.status });
    throw new Error(message);
  }
  return data;
}

function showError(elId, message, i18nKey, i18nVars) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  state.errors[elId] = i18nKey ? { key: i18nKey, vars: i18nVars } : null;
}
function clearError(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = "";
  el.classList.remove("show");
  state.errors[elId] = null;
}

function uiError(key, vars) {
  // An Error whose text is re-derivable in another language: withBusy's
  // catch reads .i18nKey/.i18nVars back off it so a later language switch
  // can re-render the still-visible message instead of leaving it stale.
  const e = new Error(t(key, vars));
  e.i18nKey = key;
  e.i18nVars = vars;
  return e;
}

function setBusy(btn, busy) {
  if (!btn) return;
  btn.disabled = busy;
  btn.classList.toggle("busy", busy);
}

async function withBusy(btn, errId, fn) {
  clearError(errId);
  setBusy(btn, true);
  try {
    await fn();
  } catch (e) {
    showError(errId, e.message || String(e), e.i18nKey, e.i18nVars);
  } finally {
    setBusy(btn, false);
  }
}

/* ---------------- tabs ---------------- */

function showTab(name) {
  $all(".tab").forEach((tb) => tb.setAttribute("aria-selected", String(tb.dataset.tab === name)));
  $all(".panel").forEach((p) => {
    p.hidden = p.id !== `panel-${name}`;
  });
  if (name === "github") {
    syncGithubLanguageDefault();
  }
  $all(".toc-panel").forEach((panel) => {
    panel.hidden = panel.dataset.toc !== name;
  });
}

$all(".tab").forEach((tb) => {
  tb.addEventListener("click", () => showTab(tb.dataset.tab));
});

$all("[data-next], [data-prev]").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.next || btn.dataset.prev));
});

function markStepComplete(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.classList.add("is-complete");
}

/* ---------------- lifecycle rail ---------------- */

function renderRail() {
  const rail = document.getElementById("lifecycle-rail");
  rail.innerHTML = "";
  STATIONS.forEach((st, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rail-station";
    btn.dataset.event = st.event;

    const index = document.createElement("span");
    index.className = "rail-index";
    index.textContent = String(i + 1);
    btn.appendChild(index);

    const label = document.createElement("span");
    label.className = "rail-label";
    label.textContent = t("events." + st.event);
    btn.appendChild(label);

    const count = document.createElement("span");
    count.className = "rail-count";
    count.dataset.count = "";
    count.textContent = "0";
    btn.appendChild(count);

    btn.addEventListener("click", () => {
      const group = document.getElementById(`event-${st.event}`);
      if (!group) return;
      const reduceMotion =
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      group.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    });

    rail.appendChild(btn);
  });
}

function litRailEvents() {
  const counts = {};
  state.catalogue.forEach((hook) => {
    if (state.selectedHooks.has(hook.id)) {
      counts[hook.event] = (counts[hook.event] || 0) + 1;
    }
  });
  $all(".rail-station").forEach((st) => {
    const n = counts[st.dataset.event] || 0;
    st.classList.toggle("lit", n > 0);
    const badge = st.querySelector("[data-count]");
    if (badge) badge.textContent = String(n);
  });
}

/* ---------------- env + targets ---------------- */

async function loadEnv() {
  state.env = await api("/api/env");
  const version = state.env.version;
  document.getElementById("app-version").textContent = version ? `v${version}` : "";
  const claudeBox = document.getElementById("target-claude");
  const codexBox = document.getElementById("target-codex");
  const grokBox = document.getElementById("target-grok");
  claudeBox.disabled = !state.env.clis.claude;
  codexBox.disabled = !state.env.clis.codex;
  grokBox.disabled = !state.env.clis.grok;
  if (state.env.clis.claude) {
    claudeBox.checked = true;
    state.targets.add("claude");
  }
  if (state.env.clis.codex) {
    codexBox.checked = true;
    state.targets.add("codex");
  }
  if (state.env.clis.grok) {
    grokBox.checked = true;
    state.targets.add("grok");
  }
  const missing = [];
  if (!state.env.clis.claude) missing.push("Claude Code");
  if (!state.env.clis.codex) missing.push("Codex");
  document.getElementById("target-detect-note").textContent = missing.length
    ? t("hooks.targetNotFound", { list: missing.join(", ") })
    : "";

  state.wikiLocation = state.env.home;

  document.getElementById("btn-gh-login").hidden = !state.env.gh_cli;

  updateCodexNote();
  populateLlmSelect();
}

function updateCodexNote() {
  const isCodex = state.targets.has("codex");
  document.getElementById("codex-hooks-note").hidden = !isCodex;
  document.getElementById("codex-trust-guide").hidden = !isCodex;
  document.getElementById("grok-via-claude-note").hidden =
    !(state.targets.has("grok") && state.targets.has("claude"));
}

document.getElementById("target-claude").addEventListener("change", (e) => {
  if (e.target.checked) state.targets.add("claude");
  else state.targets.delete("claude");
  updateCodexNote();
  if (state.catalogue.length) renderHookCards();
});
document.getElementById("target-codex").addEventListener("change", (e) => {
  if (e.target.checked) state.targets.add("codex");
  else state.targets.delete("codex");
  updateCodexNote();
  if (state.catalogue.length) renderHookCards();
});
document.getElementById("target-grok").addEventListener("change", (e) => {
  if (e.target.checked) state.targets.add("grok");
  else state.targets.delete("grok");
  updateCodexNote();
});

function renderSegmented(container, options, selectedValue, onChange) {
  container.innerHTML = "";
  options.forEach((opt) => {
    const seg = document.createElement("label");
    seg.className = "segment" + (opt.value === selectedValue ? " selected" : "");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = container.id;
    input.value = opt.value;
    input.checked = opt.value === selectedValue;
    input.addEventListener("change", () => {
      $all(".segment", container).forEach((s) => s.classList.remove("selected"));
      seg.classList.add("selected");
      onChange(opt.value);
    });
    seg.appendChild(input);
    const span = document.createElement("span");
    span.textContent = opt.label;
    seg.appendChild(span);
    container.appendChild(seg);
  });
}

function populateLlmSelect() {
  const container = document.getElementById("gh-llm");
  const options = [{ value: "", label: t("github.llmNotUsed") }];
  if (state.env) {
    if (state.env.clis.claude) options.push({ value: "claude", label: "Claude Code" });
    if (state.env.clis.codex) options.push({ value: "codex", label: "Codex" });
  }
  if (!options.some((o) => o.value === state.ghLlm)) state.ghLlm = "";
  renderSegmented(container, options, state.ghLlm, (v) => {
    state.ghLlm = v;
  });
}

/* ---------------- catalogue + hook cards ---------------- */

async function loadCatalogue() {
  const data = await api("/api/catalogue");
  state.catalogue = data.catalogue || [];
  state.installed = data.installed || { claude: [], codex: [] };
  state.handoffTargets = new Set(data.handoff_targets || []);
  state.handoffThreshold = data.handoff_threshold || 60;
  renderHookCards();
}

function computeSelectedHooks() {
  const targets = state.targets.size ? Array.from(state.targets) : ["claude", "codex"];
  const installedForTargets = new Set();
  targets.forEach((tg) => {
    (state.installed[tg] || []).forEach((id) => installedForTargets.add(id));
  });
  if (installedForTargets.size) return installedForTargets;
  return new Set(state.catalogue.filter((hook) => hook.default).map((hook) => hook.id));
}

function hookField(hook, field) {
  return LANG === "en" ? hook[field + "_en"] : hook[field + "_ko"];
}

function buildHookCards() {
  STATIONS.forEach((st) => {
    const list = document.querySelector(`[data-event-list="${st.event}"]`);
    if (list) list.innerHTML = "";
  });

  state.catalogue.forEach((hook) => {
    if (hook.id === "automatic-handoff-restore") return;
    const list = document.querySelector(`[data-event-list="${hook.event}"]`);
    if (!list) return;

    const checked = state.selectedHooks.has(hook.id);

    const card = document.createElement("div");
    card.className = "card" + (checked ? " selected" : "");
    card.dataset.hookId = hook.id;

    const head = document.createElement("div");
    head.className = "card-head";

    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = checked;
    box.addEventListener("change", () => {
      if (box.checked) {
        state.selectedHooks.add(hook.id);
        if (hook.id === "automatic-handoff" && !state.handoffTargets.size) {
          state.targets.forEach((target) => state.handoffTargets.add(target));
          buildHookCards();
          return;
        }
      }
      else state.selectedHooks.delete(hook.id);
      card.classList.toggle("selected", box.checked);
      litRailEvents();
    });
    label.appendChild(box);
    label.appendChild(document.createTextNode(hookField(hook, "title") || hook.id));
    head.appendChild(label);

    card.appendChild(head);

    const desc = document.createElement("p");
    desc.className = "desc";
    desc.textContent = hookField(hook, "desc") || "";
    card.appendChild(desc);

    if (hook.id === "automatic-handoff") {
      const controls = document.createElement("div");
      controls.className = "handoff-controls";
      for (const target of ["claude", "codex", "grok"]) {
        const choice = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = state.handoffTargets.has(target);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) state.handoffTargets.add(target);
          else state.handoffTargets.delete(target);
        });
        choice.append(checkbox, document.createTextNode({claude: "Claude Code", codex: "Codex", grok: "Grok"}[target]));
        controls.appendChild(choice);
      }
      const sliderLabel = document.createElement("label");
      sliderLabel.textContent = t("hooks.handoffThreshold") + " ";
      const value = document.createElement("span");
      value.textContent = state.handoffThreshold + "%";
      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = "40";
      slider.max = "80";
      slider.value = String(state.handoffThreshold);
      slider.addEventListener("input", () => {
        state.handoffThreshold = Number(slider.value);
        value.textContent = slider.value + "%";
      });
      sliderLabel.append(slider, value);
      controls.append(sliderLabel);
      const note = document.createElement("p");
      note.textContent = t("hooks.handoffGrokNote");
      controls.append(note);
      card.appendChild(controls);
    }

    const example = hookField(hook, "example");
    if (example) {
      const ex = document.createElement("p");
      ex.className = "example";
      ex.textContent = example;
      card.appendChild(ex);
    }

    list.appendChild(card);
  });

  litRailEvents();
}

function renderHookCards() {
  state.selectedHooks = computeSelectedHooks();
  buildHookCards();
}

function relabelHookCards() {
  // Re-render the same cards from the current `state.selectedHooks` without
  // recomputing it, so a language switch never resets the user's choices.
  buildHookCards();
}

document.getElementById("btn-install-hooks").addEventListener("click", () => {
  const btn = document.getElementById("btn-install-hooks");
  withBusy(btn, "hooks-error", async () => {
    const targets = Array.from(state.targets);
    if (!targets.length) throw uiError("hooks.chooseCliFirst");
    if (state.selectedHooks.has("automatic-handoff") &&
        !Array.from(state.handoffTargets).some((target) => targets.includes(target))) {
      throw uiError("hooks.chooseHandoffCli");
    }
    const result = await api("/api/hooks/install", {
      method: "POST",
      body: { ids: Array.from(state.selectedHooks), targets,
        handoff_targets: Array.from(state.handoffTargets).filter((target) => targets.includes(target)),
        handoff_threshold: state.handoffThreshold },
    });
    await loadCatalogue();
    markStepComplete("hooks");
    document.getElementById("grok-statusline-manual-note").hidden = !result.grok_statusline_manual;
  });
});

document.getElementById("btn-codex-check-approval").addEventListener("click", () => {
  const btn = document.getElementById("btn-codex-check-approval");
  withBusy(btn, "hooks-error", async () => {
    const status = await api("/api/hooks/codex-trust");
    const resultEl = document.getElementById("codex-trust-result");
    resultEl.textContent = status.remaining === 0 && status.total_count > 0
      ? t("hooks.codexTrustAllApproved", { total: status.total_count })
      : t("hooks.codexTrustRemaining", {
          trusted: status.trusted_count,
          total: status.total_count,
          remaining: status.remaining,
        });
  });
});

document.getElementById("btn-uninstall-hooks").addEventListener("click", () => {
  const btn = document.getElementById("btn-uninstall-hooks");
  withBusy(btn, "hooks-error", async () => {
    const targets = Array.from(state.targets);
    if (!targets.length) throw uiError("hooks.chooseCliFirst");
    const removeSkills = document.getElementById("uninstall-remove-skills").checked;
    await api("/api/hooks/uninstall", { method: "POST", body: { targets, remove_skills: removeSkills } });
    await loadCatalogue();
    document.getElementById("grok-statusline-manual-note").hidden = true;
  });
});

/* ---------------- wiki tab ---------------- */

function validateWikiFolderName(name) {
  if (!name || !name.trim()) return t("wiki.folderNameRequired");
  if (name.includes("/") || name.includes("..")) return t("wiki.folderNameInvalidChars");
  return null;
}

function updateWikiPathPreview() {
  const input = document.getElementById("wiki-path");
  const name = state.wikiFolderName || "";
  input.value = state.wikiLocation ? `${state.wikiLocation}/${name}` : "";
  state.wikiPath = input.value;
}

document.getElementById("wiki-folder-name").addEventListener("input", (e) => {
  state.wikiFolderName = e.target.value;
  updateWikiPathPreview();
  const err = validateWikiFolderName(state.wikiFolderName);
  if (err) showError("wiki-error", err);
  else clearError("wiki-error");
});

async function loadWikiLocationList(path) {
  const data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
  state.wikiLocation = data.path;
  document.getElementById("wiki-location-current-path").textContent = data.path;
  const list = document.getElementById("wiki-location-list");
  list.innerHTML = "";
  (data.dirs || []).forEach((dir) => {
    const row = document.createElement("div");
    row.className = "folder-row";
    const nm = document.createElement("span");
    nm.className = "name";
    nm.textContent = dir.name + (dir.is_git ? "  (git)" : "");
    nm.addEventListener("click", () => loadWikiLocationListSafe(dir.path));
    row.appendChild(nm);
    list.appendChild(row);
  });
  document.getElementById("btn-wiki-location-up").onclick = () => {
    if (data.parent) loadWikiLocationListSafe(data.parent);
  };
  updateWikiPathPreview();
}

function loadWikiLocationListSafe(path) {
  return loadWikiLocationList(path)
    .then(() => clearError("wiki-error"))
    .catch((e) => showError("wiki-error", e.message || String(e)));
}

$all('input[name="wiki-lang"]').forEach((r) =>
  r.addEventListener("change", (e) => {
    if (e.target.checked) state.wikiLanguage = e.target.value;
    syncGithubLanguageDefault();
  })
);

$all('input[name="wiki-mode"]').forEach((r) =>
  r.addEventListener("change", (e) => {
    const isFolder = e.target.value === "folder";
    document.getElementById("folder-browser").hidden = !isFolder;
    if (isFolder && !state.localFolderStack.length) {
      loadFolderListSafe(state.env ? state.env.home : "");
    }
  })
);

function renderChosenFolders() {
  const wrap = document.getElementById("chosen-folders");
  if (!wrap) return;
  wrap.innerHTML = "";
  if (!state.chosenFolders.length) return;
  const label = document.createElement("p");
  label.className = "field-note";
  label.textContent = t("wiki.chosenFolders");
  wrap.appendChild(label);
  state.chosenFolders.forEach((f) => {
    const row = document.createElement("div");
    row.className = "folder-row";
    const nm = document.createElement("span");
    nm.className = "name";
    nm.textContent = f.path + (f.isGit ? "  (git)" : "");
    row.appendChild(nm);
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "btn secondary";
    rm.innerHTML = `<span class="label">${t("github.remove")}</span>`;
    rm.addEventListener("click", () => {
      state.chosenFolders = state.chosenFolders.filter((c) => c.path !== f.path);
      renderChosenFolders();
      const box = document.querySelector(`#folder-list input[data-path="${CSS.escape(f.path)}"]`);
      if (box) box.checked = false;
    });
    row.appendChild(rm);
    wrap.appendChild(row);
  });
}

async function loadFolderList(path) {
  const data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
  state.localFolderStack = [data.path, data.parent];
  document.getElementById("folder-current-path").textContent = data.path;
  const list = document.getElementById("folder-list");
  list.innerHTML = "";
  (data.dirs || []).forEach((dir, idx) => {
    const name = dir.name;
    const dirPath = dir.path;
    const isGit = dir.is_git;
    const chosen = state.chosenFolders.some((f) => f.path === dirPath);

    const row = document.createElement("div");
    row.className = "folder-row";

    const box = document.createElement("input");
    box.type = "checkbox";
    box.id = `folder-check-${idx}`;
    box.dataset.path = dirPath;
    box.checked = chosen;
    box.addEventListener("change", () => {
      if (box.checked) {
        if (!state.chosenFolders.some((f) => f.path === dirPath)) {
          state.chosenFolders.push({ path: dirPath, isGit });
        }
      } else {
        state.chosenFolders = state.chosenFolders.filter((f) => f.path !== dirPath);
      }
      renderChosenFolders();
    });
    row.appendChild(box);

    const nm = document.createElement("label");
    nm.className = "name";
    nm.htmlFor = box.id;
    nm.textContent = name + (isGit ? "  (git)" : "");
    nm.addEventListener("click", (e) => {
      e.preventDefault();
      loadFolderListSafe(dirPath);
    });
    row.appendChild(nm);

    list.appendChild(row);
  });
  document.getElementById("btn-folder-up").onclick = () => {
    if (data.parent) loadFolderListSafe(data.parent);
  };
}

function loadFolderListSafe(path) {
  return loadFolderList(path)
    .then(() => clearError("wiki-error"))
    .catch((e) => showError("wiki-error", e.message || String(e)));
}

document.getElementById("btn-init-wiki").addEventListener("click", () => {
  const btn = document.getElementById("btn-init-wiki");
  withBusy(btn, "wiki-error", async () => {
    const nameError = validateWikiFolderName(state.wikiFolderName);
    if (nameError) throw new Error(nameError);
    updateWikiPathPreview();
    if (!state.wikiPath) throw uiError("wiki.chooseLocation");
    const targets = Array.from(state.targets);
    const result = await api("/api/wiki/init", {
      method: "POST",
      body: { path: state.wikiPath, language: state.wikiLanguage, targets },
    });
    renderList("wiki-result", result.created || []);
    markStepComplete("wiki");

    const mode = $('input[name="wiki-mode"]:checked').value;
    if (mode === "folder") {
      const selected = state.chosenFolders.map((f) => f.path);
      if (selected.length) {
        const localResult = await api("/api/wiki/local", { method: "POST", body: { folders: selected } });
        renderList("wiki-result", (result.created || []).concat(localResult.written || []));
        state.gitSubfolders = localResult.git_repos || [];
        renderLocalRepoList();
      }
    }
  });
});

function renderList(id, items) {
  const el = document.getElementById(id);
  el.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : JSON.stringify(item);
    el.appendChild(li);
  });
  if (id === "wiki-result") state.wikiResultItems = items;
}

/* ---------------- github tab ---------------- */

function syncGithubLanguageDefault() {
  state.ghLanguage = state.wikiLanguage;
  const container = document.getElementById("gh-lang-row");
  renderSegmented(
    container,
    [
      { value: "ko", label: "한국어" },
      { value: "en", label: "English" },
    ],
    state.ghLanguage,
    (v) => {
      state.ghLanguage = v;
    }
  );
}

document.getElementById("btn-gh-login").addEventListener("click", () => {
  const btn = document.getElementById("btn-gh-login");
  withBusy(btn, "gh-connect-error", async () => {
    const res = await api("/api/github/token", { method: "POST", body: { use_gh: true } });
    await onGithubConnected(res);
  });
});

document.getElementById("btn-gh-connect").addEventListener("click", () => {
  const btn = document.getElementById("btn-gh-connect");
  withBusy(btn, "gh-connect-error", async () => {
    const token = document.getElementById("gh-token").value.trim();
    if (!token) throw uiError("github.tokenRequired");
    const res = await api("/api/github/token", { method: "POST", body: { token } });
    await onGithubConnected(res);
  });
});

function renderGhWhoami() {
  const el = document.getElementById("gh-whoami");
  if (!el || state.ghWhoami === null) return;
  el.textContent = state.ghWhoami.login
    ? t("github.connected", { login: state.ghWhoami.login })
    : t("github.connectedNoLogin");
}

async function onGithubConnected(res) {
  state.ghWhoami = res && res.login ? { login: res.login } : {};
  renderGhWhoami();
  const repos = await api("/api/github/repos");
  state.repos = repos.repos || repos || [];
  renderRepoList();
}

document.getElementById("repo-search").addEventListener("input", () => renderRepoList());

function populateBranchSelect(branchSel, list, selectedBranch, defaultBranch) {
  branchSel.innerHTML = "";
  list.forEach((b) => {
    const bname = typeof b === "string" ? b : b.name;
    const opt = document.createElement("option");
    opt.value = bname;
    opt.textContent = bname;
    if (bname === (selectedBranch || defaultBranch)) opt.selected = true;
    branchSel.appendChild(opt);
  });
}

function watchedConfigEntry(repo) {
  // Keyed by repo only: pages are rendered per repo (one page set per
  // repo, regardless of branch), so `config.watch_repos` never carries
  // more than one entry per repo — see server `_record_watch_repo`.
  const list = (state.env && state.env.config && state.env.config.watch_repos) || [];
  return list.find((w) => w && w.repo === repo) || null;
}

function applyWatchResultsLocally(items) {
  // After a successful run, `config.json` on disk now reflects `items`'
  // watch flags, but `state.env.config` (loaded once at boot) does not —
  // update it here so a repo unticked-then-reticked later in the same
  // page session sees the just-written state instead of a stale one.
  if (!state.env || !state.env.config) return;
  const watchRepos = Array.isArray(state.env.config.watch_repos) ? state.env.config.watch_repos.slice() : [];
  items.forEach((item) => {
    if (item.kind !== "github") return;
    const idx = watchRepos.findIndex((w) => w && w.repo === item.repo_or_path);
    if (idx !== -1) watchRepos.splice(idx, 1);
    if (item.watch) {
      watchRepos.push({
        repo: item.repo_or_path,
        branch: item.branch,
        days: item.days,
        language: item.language,
        llm: item.llm,
      });
    }
  });
  state.env.config.watch_repos = watchRepos;
}

function renderRepoList() {
  const q = document.getElementById("repo-search").value.trim().toLowerCase();
  const wrap = document.getElementById("repo-list");
  wrap.innerHTML = "";
  state.repos
    .filter((r) => {
      const name = typeof r === "string" ? r : r.full_name || r.name || "";
      return name.toLowerCase().includes(q);
    })
    .forEach((r) => {
      const name = typeof r === "string" ? r : r.full_name || r.name;
      const defaultBranch = typeof r === "object" ? r.default_branch : null;
      const existing = state.selectedRepos[name];

      const row = document.createElement("div");
      row.className = "repo-row";

      const head = document.createElement("div");
      head.className = "repo-head";
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !!existing;
      label.appendChild(box);
      label.appendChild(document.createTextNode(name));
      head.appendChild(label);
      row.appendChild(head);

      const branchSel = document.createElement("select");
      branchSel.hidden = !existing;
      row.appendChild(branchSel);

      const watchLabel = document.createElement("label");
      watchLabel.className = "watch-checkbox";
      watchLabel.hidden = !existing;
      const watchBox = document.createElement("input");
      watchBox.type = "checkbox";
      watchLabel.appendChild(watchBox);
      watchLabel.appendChild(document.createTextNode(" " + t("github.watchLabel")));
      row.appendChild(watchLabel);

      // `loadBranches` fires on every redraw of this row (search typing
      // re-renders the whole list, a branch-list fetch resolves later) —
      // it must only ever restore the checkbox from the in-memory
      // selection (`sel.watch`), never recompute it from config, or a
      // user's still-unsaved tick/untick would be lost on the next redraw.
      const loadBranches = async () => {
        try {
          const branches = await api(`/api/github/branches?repo=${encodeURIComponent(name)}`);
          const list = branches.branches || branches || [];
          const sel = state.selectedRepos[name];
          const selectedBranch = sel ? sel.branch : "";
          populateBranchSelect(branchSel, list, selectedBranch, defaultBranch);
          if (sel) {
            sel.branch = branchSel.value;
            watchBox.checked = !!sel.watch;
          }
        } catch (e) {
          showError("gh-run-error", e.message);
        }
      };

      box.addEventListener("change", () => {
        if (box.checked) {
          // The config default (repo-only match, per `_record_watch_repo`)
          // is applied exactly once, right here at first selection — not
          // on any later redraw or branch-list load.
          const configured = watchedConfigEntry(name);
          state.selectedRepos[name] = {
            kind: "github",
            branch: (existing && existing.branch) || (configured && configured.branch) || defaultBranch || "",
            watch: !!configured,
          };
          watchBox.checked = state.selectedRepos[name].watch;
          branchSel.hidden = false;
          watchLabel.hidden = false;
          loadBranches();
        } else {
          delete state.selectedRepos[name];
          branchSel.hidden = true;
          watchLabel.hidden = true;
        }
      });
      branchSel.addEventListener("change", () => {
        if (state.selectedRepos[name]) state.selectedRepos[name].branch = branchSel.value;
      });
      watchBox.addEventListener("change", () => {
        if (state.selectedRepos[name]) state.selectedRepos[name].watch = watchBox.checked;
      });

      if (existing) {
        watchBox.checked = !!existing.watch;
        loadBranches();
      }

      wrap.appendChild(row);
    });
}

function renderLocalRepoList() {
  const wrap = document.getElementById("local-repo-list");
  if (!wrap) return;
  wrap.innerHTML = "";
  state.gitSubfolders.forEach((path) => {
    const existing = state.selectedRepos[path];
    const row = document.createElement("div");
    row.className = "repo-row";
    const head = document.createElement("div");
    head.className = "repo-head";
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!existing;
    label.appendChild(box);
    label.appendChild(document.createTextNode(t("github.localRepoLabel", { path })));
    head.appendChild(label);
    row.appendChild(head);

    const branchSel = document.createElement("select");
    branchSel.hidden = !existing;
    row.appendChild(branchSel);

    const loadBranches = async () => {
      try {
        const data = await api(`/api/local/branches?path=${encodeURIComponent(path)}`);
        const selectedBranch = state.selectedRepos[path] ? state.selectedRepos[path].branch : "";
        populateBranchSelect(branchSel, data.branches || [], selectedBranch, data.default);
        if (state.selectedRepos[path]) state.selectedRepos[path].branch = branchSel.value;
      } catch (e) {
        showError("gh-run-error", e.message);
      }
    };

    box.addEventListener("change", () => {
      if (box.checked) {
        state.selectedRepos[path] = { kind: "local", branch: (existing && existing.branch) || "" };
        branchSel.hidden = false;
        loadBranches();
      } else {
        delete state.selectedRepos[path];
        branchSel.hidden = true;
      }
    });
    branchSel.addEventListener("change", () => {
      if (state.selectedRepos[path]) state.selectedRepos[path].branch = branchSel.value;
    });

    if (existing) loadBranches();

    wrap.appendChild(row);
  });
}

const dueRange = document.getElementById("due-range");
const dueNumber = document.getElementById("due-number");
const dueDisplay = document.getElementById("due-display");
function syncDue(value) {
  const v = Math.min(180, Math.max(30, Number(value) || 90));
  dueRange.value = String(v);
  dueNumber.value = String(v);
  dueDisplay.textContent = t("github.dueDisplay", { n: v });
}
function previewDue(value) {
  const n = Number(value);
  if (Number.isFinite(n) && n >= 30 && n <= 180) {
    dueRange.value = String(n);
    dueDisplay.textContent = t("github.dueDisplay", { n });
  }
}
dueRange.addEventListener("input", () => syncDue(dueRange.value));
dueNumber.addEventListener("input", () => previewDue(dueNumber.value));
dueNumber.addEventListener("change", () => syncDue(dueNumber.value));

document.getElementById("btn-gh-run").addEventListener("click", () => {
  const btn = document.getElementById("btn-gh-run");
  clearError("gh-run-error");
  setBusy(btn, true);
  (async () => {
    try {
      const days = Number(dueNumber.value) || 90;
      const items = Object.entries(state.selectedRepos).map(([key, v]) => {
        const item = { kind: v.kind, repo_or_path: key, branch: v.branch || null };
        if (v.kind === "github") {
          item.watch = !!v.watch;
          // Carried on the item (not read from outer scope later) so a
          // post-success local config update can rebuild exactly the
          // entry the server just wrote to config.json.
          item.days = days;
          item.language = state.ghLanguage;
          item.llm = state.ghLlm || null;
        }
        return item;
      });
      if (!items.length) throw uiError("github.chooseRepo");
      const res = await api("/api/history/run", {
        method: "POST",
        body: { items, days, language: state.ghLanguage, llm: state.ghLlm || null },
      });
      state.jobId = res.job;
      pollJob(
        () => setBusy(btn, false),
        () => applyWatchResultsLocally(items)
      );
    } catch (e) {
      showError("gh-run-error", e.message || String(e), e.i18nKey, e.i18nVars);
      setBusy(btn, false);
    }
  })();
});

function appendLog(line) {
  const log = document.getElementById("gh-log");
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  const row = document.createElement("div");
  row.className = "line";
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = `${hh}:${mm}:${ss}`;
  row.appendChild(time);
  row.appendChild(document.createTextNode(line));
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

function pollJob(onDone, onSuccess) {
  if (state.jobTimer) clearInterval(state.jobTimer);
  let seenLines = 0;
  const finish = () => {
    clearInterval(state.jobTimer);
    state.jobTimer = null;
    if (onDone) onDone();
  };
  state.jobTimer = setInterval(async () => {
    try {
      const data = await api(`/api/jobs/${state.jobId}`);
      const lines = data.log || [];
      for (let i = seenLines; i < lines.length; i++) appendLog(lines[i]);
      seenLines = lines.length;
      if (data.state === "done" || data.state === "error") {
        if (data.state === "error") {
          showError("gh-run-error", t("github.jobError"));
        } else {
          markStepComplete("github");
          if (onSuccess) onSuccess();
        }
        finish();
      }
    } catch (e) {
      showError("gh-run-error", e.message);
      finish();
    }
  }, 1500);
}

/* ---------------- finish tab ---------------- */

document.getElementById("btn-finish").addEventListener("click", () => {
  const btn = document.getElementById("btn-finish");
  withBusy(btn, "finish-error", async () => {
    const result = await api("/api/finish", { method: "POST" });
    state.finishResult = result;
    renderFinishSummary(result);
    checkObsidian();
    markStepComplete("finish");
  });
});

function renderFinishSummary(result) {
  const hookSummary = Object.entries(result.hooks || {})
    .filter(([, ids]) => Array.isArray(ids) && ids.length)
    .map(([cli, ids]) => `${cli}: ${ids.join(", ")}`)
    .join(" · ");
  const items = [
    t("finish.summary.wikiPath", { v: result.wiki_path || "-" }),
    t("finish.summary.language", { v: result.language || "-" }),
    t("finish.summary.hooksInstalled", { v: hookSummary || "-" }),
    t("finish.summary.skillsInstalled", { v: (result.skills || []).join(", ") || "-" }),
    t("finish.summary.reposConnected", { v: (result.repos || []).join(", ") || "-" }),
  ];
  renderList("finish-summary", items);
}

const OBSIDIAN_DOWNLOAD_URL = "https://obsidian.md/download";

function checkObsidian() {
  const found = state.env && state.env.obsidian;
  document.getElementById("obsidian-found").hidden = !found;
  document.getElementById("obsidian-missing").hidden = !!found;
  document.getElementById("obsidian-download-link").href = OBSIDIAN_DOWNLOAD_URL;
}

document.getElementById("btn-obsidian-open").addEventListener("click", () => {
  const btn = document.getElementById("btn-obsidian-open");
  withBusy(btn, "finish-error", async () => {
    await api("/api/obsidian/open", { method: "POST" });
  });
});

document.getElementById("btn-copy-brew").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.getElementById("obsidian-brew").textContent);
  } catch (e) {
    /* clipboard may be unavailable; the command is still visible to copy by hand */
  }
});

document.getElementById("btn-obsidian-recheck").addEventListener("click", () => {
  const btn = document.getElementById("btn-obsidian-recheck");
  withBusy(btn, "finish-error", async () => {
    state.env = await api("/api/env");
    checkObsidian();
  });
});

/* ---------------- boot ---------------- */

applyI18n();
$all('input[name="wiki-lang"]').forEach((r) => {
  r.checked = r.value === LANG;
});
renderRail();
syncDue(dueRange.value);
(async function init() {
  if (!TOKEN) {
    showError("hooks-error", t("common.reopenUrl"), "common.reopenUrl");
    return;
  }
  try {
    await loadEnv();
    await loadCatalogue();
    await loadWikiLocationListSafe(state.env.home);
    syncGithubLanguageDefault();
    checkObsidian();
  } catch (e) {
    showError("hooks-error", e.message);
  }
})();
