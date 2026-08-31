"use strict";

const state = {
  payload: null,
  polling: null,
  selectedQuery: null,
  selectedMetric: "vacancy_active_now",
  historyRange: "all",
  pulseRange: "all",
  pulseScope: "market",
  recruiterCapacity: 25,
  capacityInitialized: false,
  correlationMethod: "spearman",
  correlationLagDays: 0,
  pulseSeries: new Set([
    "vacancy_published_30d",
    "resume_active_published_30d",
    "competition_stock",
    "competition_flow",
    "hr_vacancy_density",
    "observed_support_score",
    "proxy_support_index",
  ]),
  firstRunPrompted: false,
};

const metricNames = {
  vacancy_active_now: "Активные вакансии",
  vacancy_published_30d: "Новые вакансии за 30 дней",
  resume_active_now: "Активные резюме",
  resume_active_published_30d: "Новые резюме за 30 дней",
  competition_stock: "STOCK: активные резюме / активные вакансии",
  competition_flow: "FLOW: новые резюме / новые вакансии за 30 дней",
  hr_vacancy_density: "Плотность HR-вакансий на 1 000 IT-вакансий",
  observed_support_score: "Наблюдаемый индекс поддержки поиска",
  proxy_support_index: "Proxy-индекс поддержки поиска",
  proxy_deficit_index: "Proxy-индекс потребности в усилении",
};

const pulseSeriesMeta = [
  { key: "vacancy_active_now", label: "Активные вакансии", short: "Вакансии сейчас", color: "#25d9d0", digits: 0 },
  { key: "resume_active_now", label: "Активные резюме", short: "Резюме сейчас", color: "#9b6cff", digits: 0 },
  { key: "vacancy_published_30d", label: "Новые вакансии за 30 дней", short: "Вакансии 30д", color: "#398cff", digits: 0 },
  { key: "resume_active_published_30d", label: "Новые резюме за 30 дней", short: "Резюме 30д", color: "#ffbd59", digits: 0 },
  { key: "competition_stock", label: "Конкуренция STOCK", short: "STOCK", color: "#53e3a6", digits: 2 },
  { key: "competition_flow", label: "Конкуренция FLOW", short: "FLOW", color: "#ff7c8a", digits: 2 },
  { key: "hr_vacancy_density", label: "HR-вакансий на 1 000 IT", short: "HR‰", color: "#64d7ff", digits: 2 },
  { key: "observed_support_score", label: "Наблюдаемая поддержка (OBS)", short: "OBS", color: "#d3f5ff", digits: 1 },
  { key: "proxy_support_index", label: "Proxy-поддержка поиска", short: "PROXY", color: "#b8f27c", digits: 1 },
  { key: "proxy_deficit_index", label: "Proxy-потребность в усилении", short: "ДЕФ", color: "#ff8794", digits: 1 },
];

const $ = (selector) => document.querySelector(selector);
const formatNumber = (value, digits = 0) => {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
};
const formatDateTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
};
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3600);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
  return data;
}

async function loadDashboard() {
  try {
    const payload = await fetchJson("/api/dashboard");
    state.payload = payload;
    if (!state.capacityInitialized) {
      state.recruiterCapacity = Number(payload.search_force_capacity) || 25;
      state.capacityInitialized = true;
    }
    render(payload);
    setServerState(true, payload.status);
    if (payload.status.running) startPolling();
    if (!payload.browser_profile_configured && !state.firstRunPrompted) {
      state.firstRunPrompted = true;
      window.setTimeout(() => {
        openSettings();
        showToast("Откройте Chrome, войдите в HH и после входа полностью закройте браузер");
      }, 250);
    }
  } catch (error) {
    setServerState(false);
    showToast(`Не удалось загрузить панель: ${error.message}`);
  }
}

function setServerState(ok, status = null) {
  const dot = $("#statusDot");
  const text = $("#statusText");
  dot.classList.toggle("ok", ok);
  dot.classList.toggle("error", !ok);
  text.textContent = ok ? (status?.message || "Локальный сервер работает") : "Локальный сервер недоступен";
}

function render(payload) {
  $("#lastUpdated").textContent = `Последний замер: ${formatDateTime(payload.latest_at)}`;
  $("#browserStatus").textContent = payload.login_browser_running
    ? "Chrome: открыт для входа"
    : payload.browser_profile_configured
      ? "Chrome-профиль: создан"
      : "Chrome-профиль: не создан";
  $("#configWarning").classList.toggle("hidden", payload.browser_profile_configured);

  const capacitySelect = $("#capacitySelect");
  if (capacitySelect) capacitySelect.value = String(state.recruiterCapacity);
  renderKpis(payload.summary);
  renderSearchForce(payload.search_force);
  renderQueryOptions(payload.queries);
  renderPositions(payload.latest);
  renderInsights(payload.latest);
  renderErrors(payload.latest, payload.status);
  renderProgress(payload.status);
  drawHistoryChart();
  renderPulseAnalytics();
}

function renderKpis(summary) {
  const digits = { competition_stock_average: 2, competition_flow_average: 2, hr_vacancy_density: 2, observed_support_score: 1, proxy_support_index: 1, proxy_deficit_index: 1 };
  document.querySelectorAll("[data-kpi]").forEach((node) => {
    const key = node.dataset.kpi;
    node.textContent = formatNumber(summary[key], digits[key] ?? 0);
    node.classList.remove("skeleton");
  });
}

function forceLevel(value) {
  if (value === null || value === undefined) return ["нет данных", "force-na"];
  if (value >= 85) return ["низкий видимый HR-demand", "force-strong"];
  if (value >= 70) return ["умеренный видимый HR-demand", "force-normal"];
  if (value >= 50) return ["повышенный видимый HR-demand", "force-weak"];
  return ["высокий видимый HR-demand", "force-critical"];
}

function renderSearchForce(force) {
  const status = $("#searchForceStatus");
  const recruiters = $("#searchForceRecruiters");
  const stage = document.querySelector(".radar-stage");
  const observed = finiteValue(force?.observed_support_score);
  const proxy = finiteValue(force?.proxy_support_index ?? force?.search_force_index);
  const value = observed ?? proxy;
  const [label, cssClass] = forceLevel(value);
  const hud = $("#searchSupportHud");
  if (hud) hud.textContent = formatNumber(value, 1);
  status.textContent = force
    ? observed !== null
      ? `${label} · OBS по 365-дневной истории`
      : `${label} · proxy K=${formatNumber(force.vacancies_per_recruiter)} (истории для OBS пока мало)`
    : "Фактический W неизвестен";
  recruiters.textContent = force
    ? `HR-вакансии: ${formatNumber(force.recruiter_vacancy_active_now)} · HR‰: ${formatNumber(force.hr_vacancy_density, 2)} · true ISP: н/д`
    : "HR-аналитика: —";
  stage?.classList.remove("force-na", "force-strong", "force-normal", "force-weak", "force-critical");
  stage?.classList.add(cssClass);

  const setText = (selector, valueText) => { const node = $(selector); if (node) node.textContent = valueText; };
  setText("#modelTargetVacancies", formatNumber(force?.target_vacancy_active_total));
  setText("#modelHrDensity", formatNumber(force?.hr_vacancy_density, 2));
  setText("#modelRequiredRecruiters", formatNumber(force?.required_recruiters_estimate, 1));
  const requiredMin = finiteValue(force?.required_recruiters_min);
  const requiredMax = finiteValue(force?.required_recruiters_max);
  setText("#modelRequiredRange", requiredMin === null || requiredMax === null ? "—" : `${formatNumber(requiredMin, 1)}–${formatNumber(requiredMax, 1)}`);
  setText("#modelOpenRecruiterVacancies", formatNumber(force?.recruiter_vacancy_active_now));
  setText("#modelRecruitingPressure", force?.recruiting_pressure == null ? "—" : `${formatNumber(force.recruiting_pressure, 2)}×`);
  setText("#modelForceIndex", formatNumber(proxy, 1));
  setText("#modelDeficitIndex", formatNumber(force?.proxy_deficit_index ?? force?.search_resource_deficit_index, 1));
  setText("#modelTrueIndex", "н/д");

  const low = finiteValue(force?.proxy_support_index_low);
  const base = finiteValue(force?.proxy_support_index_base);
  const high = finiteValue(force?.proxy_support_index_high);
  setText("#modelProxyRange", low === null || high === null ? "—" : `${formatNumber(low, 1)}–${formatNumber(high, 1)} · база ${formatNumber(base, 1)}`);
  const range = $("#scenarioRange");
  const marker = $("#scenarioBaseMarker");
  if (range && low !== null && high !== null) {
    range.style.left = `${Math.max(0, Math.min(100, low))}%`;
    range.style.width = `${Math.max(0, Math.min(100, high) - Math.max(0, low))}%`;
  }
  if (marker && base !== null) marker.style.left = `${Math.max(0, Math.min(100, base))}%`;
}


function renderQueryOptions(queries) {
  const select = $("#querySelect");
  const current = state.selectedQuery || select.value || queries[0]?.id;
  select.innerHTML = queries.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`
  ).join("");
  state.selectedQuery = queries.some((item) => item.id === current) ? current : queries[0]?.id;
  select.value = state.selectedQuery || "";
}

function competitionLevel(value) {
  if (value === null || value === undefined) return ["level-na", "нет данных"];
  if (value < 3) return ["level-low", "низкая"];
  if (value < 8) return ["level-mid", "средняя"];
  return ["level-high", "высокая"];
}

function renderPositions(rows) {
  const grid = $("#positionGrid");
  const empty = $("#emptyState");
  if (!rows.length) {
    grid.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  const filter = $("#positionSearch").value.trim().toLocaleLowerCase("ru-RU");
  const filtered = rows.filter((row) => row.label.toLocaleLowerCase("ru-RU").includes(filter));
  grid.innerHTML = filtered.map((row) => {
    const [stockClass, stockText] = competitionLevel(row.competition_stock);
    const [flowClass, flowText] = competitionLevel(row.competition_flow);
    return `
      <article class="position-card">
        <div class="position-title">${escapeHtml(row.label)}</div>
        <div class="position-query">Запрос: ${escapeHtml(row.query_text)}</div>
        <div class="metric-pairs">
          <div class="metric-box"><span>Активные вакансии</span><strong>${formatNumber(row.vacancy_active_now)}</strong><small>текущий запас спроса</small></div>
          <div class="metric-box"><span>Новые вакансии за 30 дней</span><strong>${formatNumber(row.vacancy_published_30d)}</strong><small>поток спроса</small></div>
          <div class="metric-box"><span>Активные резюме</span><strong>${formatNumber(row.resume_active_now)}</strong><small>текущий запас кандидатов</small></div>
          <div class="metric-box"><span>Новые резюме за 30 дней</span><strong>${formatNumber(row.resume_active_published_30d)}</strong><small>поток кандидатов</small></div>
        </div>
        <div class="competition-list">
          <div class="competition-row">
            <div class="competition-copy"><span>STOCK — текущая конкуренция</span><small>активные резюме ÷ активные вакансии</small></div>
            <strong class="competition-badge ${stockClass}">${formatNumber(row.competition_stock, 2)} · ${stockText}</strong>
          </div>
          <div class="competition-row">
            <div class="competition-copy"><span>FLOW — конкуренция за 30 дней</span><small>новые резюме ÷ новые вакансии</small></div>
            <strong class="competition-badge ${flowClass}">${formatNumber(row.competition_flow, 2)} · ${flowText}</strong>
          </div>
        </div>
        ${row.resume_error ? `<div class="resume-warning">${escapeHtml(row.resume_error)}</div>` : ""}
      </article>`;
  }).join("");
}

function renderInsights(rows) {
  const container = $("#insights");
  const ranked = rows
    .filter((row) => row.competition_stock !== null)
    .sort((a, b) => a.competition_stock - b.competition_stock)
    .slice(0, 4);
  if (!ranked.length) {
    container.innerHTML = `<div class="insight-item"><div class="insight-rank">i</div><div><strong>Нужны данные резюме</strong><span>Откройте «Подключение HH», войдите в Chrome и запустите браузерный сбор.</span></div></div>`;
    return;
  }
  container.innerHTML = ranked.map((row, index) => `
    <div class="insight-item">
      <div class="insight-rank">${index + 1}</div>
      <div>
        <strong>${escapeHtml(row.label)}</strong>
        <span>STOCK ${formatNumber(row.competition_stock, 2)} · FLOW ${formatNumber(row.competition_flow, 2)}</span>
      </div>
    </div>`).join("");
}

function renderErrors(rows, status) {
  const messages = [];
  rows.forEach((row) => {
    if (row.resume_error) messages.push(`${row.label}: ${row.resume_error}`);
  });
  if (state.payload?.search_force?.force_error) messages.push(`Proxy recruiting-support: ${state.payload.search_force.force_error}`);
  if (status.last_error) messages.push(status.last_error);
  (status.errors || []).forEach((item) => messages.push(`${item.label || item.query_id}: ${item.error}`));
  const unique = [...new Set(messages)];
  const panel = $("#errorPanel");
  if (!unique.length) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#errorList").innerHTML = unique.map((message) => `<div class="error-row">${escapeHtml(message)}</div>`).join("");
}

function renderProgress(status) {
  const panel = $("#progressPanel");
  const button = $("#collectButton");
  const show = status.running || status.phase === "error";
  panel.classList.toggle("hidden", !show);
  button.disabled = Boolean(status.running);
  button.classList.toggle("loading", Boolean(status.running));
  $("#progressTitle").textContent = status.running ? "Собираю данные HH" : "Ошибка сбора";
  $("#progressMessage").textContent = status.message || "";
  $("#progressCount").textContent = `${status.current || 0} / ${status.total || 0}`;
  const percent = status.total ? Math.min(100, Math.round((status.current / status.total) * 100)) : 0;
  $("#progressBar").style.width = `${percent}%`;
}

async function startCollection() {
  try {
    const status = await fetchJson("/api/collect", { method: "POST" });
    renderProgress(status);
    setServerState(true, status);
    showToast("Сбор запущен. Панель обновится автоматически.");
    startPolling();
  } catch (error) {
    if (error.message.includes("Собираю") || error.message.includes("запущен")) startPolling();
    showToast(error.message);
  }
}

function startPolling() {
  if (state.polling) return;
  state.polling = window.setInterval(async () => {
    try {
      const status = await fetchJson("/api/status");
      renderProgress(status);
      setServerState(true, status);
      if (!status.running) {
        window.clearInterval(state.polling);
        state.polling = null;
        await loadDashboard();
        showToast(status.last_error ? `Ошибка: ${status.last_error}` : "Данные обновлены");
      }
    } catch (error) {
      window.clearInterval(state.polling);
      state.polling = null;
      showToast(error.message);
    }
  }, 850);
}

function rangeDurationMs(range) {
  if (range === "1d") return 24 * 60 * 60 * 1000;
  if (range === "7d") return 7 * 24 * 60 * 60 * 1000;
  return null;
}

function filterRowsByRange(rows, range) {
  if (!rows.length || range === "all") return rows;
  const latest = Math.max(...rows.map((row) => row.date.getTime()));
  const duration = rangeDurationMs(range);
  return rows.filter((row) => row.date.getTime() >= latest - duration);
}

function chartData() {
  const history = state.payload?.history || [];
  const rows = history
    .filter((row) => row.query_id === state.selectedQuery && row[state.selectedMetric] !== null)
    .map((row) => ({ date: new Date(row.collected_at), value: Number(row[state.selectedMetric]) }))
    .filter((point) => !Number.isNaN(point.date.getTime()) && Number.isFinite(point.value));
  return filterRowsByRange(rows, state.historyRange);
}

function svgEl(name, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function drawHistoryChart() {
  const svg = $("#historyChart");
  const empty = $("#chartEmpty");
  svg.innerHTML = "";
  $("#chartLegend").textContent = metricNames[state.selectedMetric] || state.selectedMetric;
  const points = chartData();
  if (!points.length) {
    empty.textContent = "Для выбранного показателя данных пока нет.";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  const W = 900, H = 340;
  const pad = { left: 66, right: 24, top: 24, bottom: 48 };
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const values = points.map((p) => p.value);
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min = Math.max(0, min * .85 - 1); max = max * 1.15 + 1; }
  const margin = (max - min) * .08;
  min = Math.max(0, min - margin); max += margin;

  const x = (index) => pad.left + (points.length === 1 ? innerW / 2 : (index / (points.length - 1)) * innerW);
  const y = (value) => pad.top + innerH - ((value - min) / (max - min)) * innerH;

  const defs = svgEl("defs");
  const gradient = svgEl("linearGradient", { id: "areaGradient", x1: "0", y1: "0", x2: "0", y2: "1" });
  gradient.append(svgEl("stop", { offset: "0%", "stop-color": "#25d9d0", "stop-opacity": ".28" }));
  gradient.append(svgEl("stop", { offset: "100%", "stop-color": "#25d9d0", "stop-opacity": "0" }));
  defs.append(gradient); svg.append(defs);

  for (let i = 0; i <= 4; i += 1) {
    const gy = pad.top + (innerH / 4) * i;
    svg.append(svgEl("line", { x1: pad.left, y1: gy, x2: W - pad.right, y2: gy, stroke: "rgba(148,177,206,.12)", "stroke-width": 1 }));
    const label = svgEl("text", { x: pad.left - 12, y: gy + 4, fill: "#71869a", "font-size": 11, "text-anchor": "end" });
    label.textContent = formatNumber(max - ((max - min) / 4) * i, state.selectedMetric.includes("competition") ? 1 : 0);
    svg.append(label);
  }

  const linePath = points.map((point, index) => `${index ? "L" : "M"} ${x(index)} ${y(point.value)}`).join(" ");
  const areaPath = `${linePath} L ${x(points.length - 1)} ${pad.top + innerH} L ${x(0)} ${pad.top + innerH} Z`;
  svg.append(svgEl("path", { d: areaPath, fill: "url(#areaGradient)" }));
  svg.append(svgEl("path", { d: linePath, fill: "none", stroke: "#25d9d0", "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round" }));

  const dateIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  dateIndexes.forEach((index) => {
    const text = svgEl("text", { x: x(index), y: H - 15, fill: "#71869a", "font-size": 11, "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle" });
    text.textContent = formatChartDate(points[index].date, state.historyRange);
    svg.append(text);
  });

  points.forEach((point, index) => {
    const group = svgEl("g", { tabindex: 0 });
    const hit = svgEl("circle", { cx: x(index), cy: y(point.value), r: 14, fill: "transparent" });
    const dot = svgEl("circle", { cx: x(index), cy: y(point.value), r: 5, fill: "#071019", stroke: "#25d9d0", "stroke-width": 3 });
    const title = svgEl("title");
    title.textContent = `${formatDateTime(point.date.toISOString())}: ${formatNumber(point.value, state.selectedMetric.includes("competition") ? 2 : 0)}`;
    group.append(hit, dot, title); svg.append(group);
  });
}


function formatChartDate(date, range) {
  if (range === "1d") {
    return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  if (range === "7d") {
    return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short" }).format(date);
  }
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "2-digit" }).format(date);
}

function addNullable(bucket, key, value) {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return;
  bucket[key] = (bucket[key] || 0) + number;
  bucket[`${key}Count`] = (bucket[`${key}Count`] || 0) + 1;
}

function totalOrNull(bucket, key) {
  return bucket[`${key}Count`] ? bucket[key] : null;
}

function buildMarketPulseRows() {
  const buckets = new Map();
  (state.payload?.history || []).forEach((row) => {
    const date = new Date(row.collected_at);
    if (Number.isNaN(date.getTime())) return;
    const key = row.collected_at;
    const bucket = buckets.get(key) || { collected_at: key, date };
    addNullable(bucket, "vacancy_active_now", row.vacancy_active_now);
    addNullable(bucket, "vacancy_published_30d", row.vacancy_published_30d);
    addNullable(bucket, "resume_active_now", row.resume_active_now);
    addNullable(bucket, "resume_active_published_30d", row.resume_active_published_30d);
    buckets.set(key, bucket);
  });

  const forceByDate = new Map(
    (state.payload?.search_force_history || []).map((row) => [row.collected_at, row])
  );

  return [...buckets.values()].map((bucket) => {
    const vacancyActive = totalOrNull(bucket, "vacancy_active_now");
    const vacancy30d = totalOrNull(bucket, "vacancy_published_30d");
    const resumeActive = totalOrNull(bucket, "resume_active_now");
    const resume30d = totalOrNull(bucket, "resume_active_published_30d");
    const force = forceByDate.get(bucket.collected_at) || {};
    return {
      date: bucket.date,
      collected_at: bucket.collected_at,
      vacancy_active_now: vacancyActive,
      vacancy_published_30d: vacancy30d,
      resume_active_now: resumeActive,
      resume_active_published_30d: resume30d,
      competition_stock: vacancyActive > 0 && resumeActive !== null ? resumeActive / vacancyActive : null,
      competition_flow: vacancy30d > 0 && resume30d !== null ? resume30d / vacancy30d : null,
      hr_vacancy_density: force.hr_vacancy_density,
      observed_support_score: force.observed_support_score,
      proxy_support_index: force.proxy_support_index ?? force.search_force_index,
      proxy_deficit_index: force.proxy_deficit_index ?? force.search_resource_deficit_index,
    };
  }).sort((a, b) => a.date - b.date);
}

function buildHrPulseRows() {
  return (state.payload?.search_force_history || []).map((row) => ({
    date: new Date(row.collected_at),
    collected_at: row.collected_at,
    vacancy_active_now: row.recruiter_vacancy_active_now,
    vacancy_published_30d: row.recruiter_vacancy_published_30d,
    resume_active_now: row.recruiter_resume_active_now,
    resume_active_published_30d: row.recruiter_resume_published_30d,
    competition_stock: row.recruiter_competition_stock,
    competition_flow: row.recruiter_competition_flow,
    hr_vacancy_density: row.hr_vacancy_density,
    observed_support_score: row.observed_support_score,
    proxy_support_index: row.proxy_support_index ?? row.search_force_index,
    proxy_deficit_index: row.proxy_deficit_index ?? row.search_resource_deficit_index,
  })).filter((row) => !Number.isNaN(row.date.getTime())).sort((a, b) => a.date - b.date);
}

function pulseRows() {
  const rows = state.pulseScope === "hr" ? buildHrPulseRows() : buildMarketPulseRows();
  return filterRowsByRange(rows, state.pulseRange);
}

function finiteValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pulseNormalizedSeries(rows, meta) {
  const first = rows.find((row) => {
    const value = finiteValue(row[meta.key]);
    return value !== null && value !== 0;
  });
  if (!first) return [];
  const base = finiteValue(first[meta.key]);
  return rows.map((row) => {
    const raw = finiteValue(row[meta.key]);
    return {
      date: row.date,
      raw,
      normalized: raw === null ? null : (raw / base) * 100,
    };
  });
}

function pulseValueText(meta, value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return formatNumber(Number(value), meta.digits);
}

function renderPulseToggles() {
  const box = $("#pulseSeriesToggles");
  box.innerHTML = pulseSeriesMeta.map((meta) => `
    <button type="button" class="series-toggle ${state.pulseSeries.has(meta.key) ? "active" : ""}"
      data-series="${meta.key}" style="--series-color:${meta.color}">${meta.label}</button>
  `).join("");
}

function renderPulseLatest(rows) {
  const box = $("#pulseLatest");
  const latest = [...rows].reverse().find((row) => pulseSeriesMeta.some((meta) => row[meta.key] !== null && row[meta.key] !== undefined));
  if (!latest) {
    box.innerHTML = "";
    return;
  }
  const priority = [
    "vacancy_published_30d",
    "resume_active_published_30d",
    "competition_stock",
    "competition_flow",
    "hr_vacancy_density",
    "observed_support_score",
    "proxy_support_index",
    "proxy_deficit_index",
  ];
  box.innerHTML = priority.map((key) => {
    const meta = pulseSeriesMeta.find((item) => item.key === key);
    return `<div class="pulse-stat"><span>${meta.label}</span><strong>${pulseValueText(meta, latest[key])}</strong><small>${formatDateTime(latest.collected_at)}</small></div>`;
  }).join("");
}

function pearsonFromPairs(pairs) {
  if (pairs.length < 3) return { value: null, count: pairs.length };
  const meanX = pairs.reduce((sum, pair) => sum + pair[0], 0) / pairs.length;
  const meanY = pairs.reduce((sum, pair) => sum + pair[1], 0) / pairs.length;
  let numerator = 0, sumX = 0, sumY = 0;
  pairs.forEach(([x, y]) => {
    const dx = x - meanX;
    const dy = y - meanY;
    numerator += dx * dy;
    sumX += dx * dx;
    sumY += dy * dy;
  });
  const denominator = Math.sqrt(sumX * sumY);
  return { value: denominator ? numerator / denominator : null, count: pairs.length };
}

function averageRanks(values) {
  const indexed = values.map((value, index) => ({ value, index })).sort((a, b) => a.value - b.value);
  const ranks = new Array(values.length);
  let cursor = 0;
  while (cursor < indexed.length) {
    let end = cursor + 1;
    while (end < indexed.length && indexed[end].value === indexed[cursor].value) end += 1;
    const average = ((cursor + 1) + end) / 2;
    for (let i = cursor; i < end; i += 1) ranks[indexed[i].index] = average;
    cursor = end;
  }
  return ranks;
}

function laggedPairs(rows, xKey, yKey, lagDays) {
  const validRows = rows.filter((row) => row.date instanceof Date && !Number.isNaN(row.date.getTime()));
  if (!lagDays) {
    return validRows.map((row) => [finiteValue(row[xKey]), finiteValue(row[yKey])])
      .filter(([x, y]) => x !== null && y !== null);
  }
  const lagMs = lagDays * 86400000;
  const toleranceMs = 2 * 86400000;
  const pairs = [];
  validRows.forEach((source) => {
    const x = finiteValue(source[xKey]);
    if (x === null) return;
    const targetTime = source.date.getTime() + lagMs;
    let best = null;
    let bestDistance = Infinity;
    validRows.forEach((candidate) => {
      const y = finiteValue(candidate[yKey]);
      if (y === null) return;
      const distance = Math.abs(candidate.date.getTime() - targetTime);
      if (distance < bestDistance) { best = y; bestDistance = distance; }
    });
    if (best !== null && bestDistance <= toleranceMs) pairs.push([x, best]);
  });
  return pairs;
}

function correlation(rows, xKey, yKey) {
  const pairs = laggedPairs(rows, xKey, yKey, state.correlationLagDays);
  if (state.correlationMethod === "pearson") return pearsonFromPairs(pairs);
  if (pairs.length < 3) return { value: null, count: pairs.length };
  const rankX = averageRanks(pairs.map((pair) => pair[0]));
  const rankY = averageRanks(pairs.map((pair) => pair[1]));
  return pearsonFromPairs(rankX.map((value, index) => [value, rankY[index]]));
}

function correlationMeaning(value) {
  if (value === null) return ["neutral", "недостаточно данных"];
  const abs = Math.abs(value);
  const strength = abs >= .7 ? "сильная" : abs >= .4 ? "умеренная" : abs >= .2 ? "слабая" : "почти отсутствует";
  if (abs < .2) return ["neutral", strength];
  return [value > 0 ? "positive" : "negative", `${strength} ${value > 0 ? "прямая" : "обратная"}`];
}

function correlationBaseKey(rows) {
  const observedCount = rows.filter((row) => finiteValue(row.observed_support_score) !== null).length;
  return observedCount >= 3 ? "observed_support_score" : "proxy_support_index";
}

function renderPulseCorrelations(rows) {
  const box = $("#pulseCorrelations");
  const baseKey = correlationBaseKey(rows);
  const baseLabel = baseKey === "observed_support_score" ? "OBS" : "PROXY";
  const candidates = pulseSeriesMeta.filter((meta) => !["observed_support_score", "proxy_support_index", "proxy_deficit_index"].includes(meta.key) && state.pulseSeries.has(meta.key));
  if (!candidates.length) {
    box.innerHTML = '<div class="correlation-card neutral"><span>Выберите хотя бы одну рыночную линию</span><strong>—</strong><small>для расчёта связи</small></div>';
    return;
  }
  box.innerHTML = candidates.map((meta) => {
    const result = correlation(rows, baseKey, meta.key);
    const [cssClass, meaning] = correlationMeaning(result.value);
    const value = result.value === null ? "—" : formatNumber(result.value, 2);
    const method = state.correlationMethod === "spearman" ? "ρ" : "r";
    const lag = state.correlationLagDays ? ` · лаг +${state.correlationLagDays}д` : "";
    return `<div class="correlation-card ${cssClass}"><span>${baseLabel} ↔ ${meta.short}</span><strong>${method} ${value}</strong><small>${meaning}${lag} · пар: ${result.count}</small></div>`;
  }).join("");
}


function drawPulseChart(rows) {
  const svg = $("#pulseChart");
  const empty = $("#pulseEmpty");
  svg.innerHTML = "";
  const selected = pulseSeriesMeta.filter((meta) => state.pulseSeries.has(meta.key));
  const series = selected.map((meta) => ({ meta, points: pulseNormalizedSeries(rows, meta) }))
    .filter((item) => item.points.some((point) => point.normalized !== null));

  if (!rows.length || !series.length) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  const W = 1100, H = 430;
  const pad = { left: 70, right: 26, top: 26, bottom: 55 };
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const allValues = series.flatMap((item) => item.points.map((point) => point.normalized).filter(Number.isFinite));
  allValues.push(100);
  let min = Math.min(...allValues), max = Math.max(...allValues);
  if (min === max) { min = Math.max(0, min - 10); max += 10; }
  const margin = Math.max(6, (max - min) * .1);
  min = Math.max(0, min - margin);
  max += margin;

  const x = (index) => pad.left + (rows.length === 1 ? innerW / 2 : (index / (rows.length - 1)) * innerW);
  const y = (value) => pad.top + innerH - ((value - min) / (max - min)) * innerH;

  for (let i = 0; i <= 4; i += 1) {
    const gy = pad.top + (innerH / 4) * i;
    svg.append(svgEl("line", { x1: pad.left, y1: gy, x2: W - pad.right, y2: gy, stroke: "rgba(148,177,206,.12)", "stroke-width": 1 }));
    const label = svgEl("text", { x: pad.left - 12, y: gy + 4, fill: "#71869a", "font-size": 11, "text-anchor": "end" });
    label.textContent = formatNumber(max - ((max - min) / 4) * i, 0);
    svg.append(label);
  }

  if (100 >= min && 100 <= max) {
    svg.append(svgEl("line", { x1: pad.left, y1: y(100), x2: W - pad.right, y2: y(100), stroke: "rgba(255,255,255,.28)", "stroke-width": 1.2, "stroke-dasharray": "7 7" }));
    const baseline = svgEl("text", { x: W - pad.right, y: y(100) - 7, fill: "#71869a", "font-size": 10, "text-anchor": "end" });
    baseline.textContent = "первый замер = 100";
    svg.append(baseline);
  }

  const tickCount = Math.min(5, rows.length);
  const dateIndexes = [...new Set(Array.from({ length: tickCount }, (_, index) => Math.round((rows.length - 1) * index / Math.max(1, tickCount - 1))))];
  dateIndexes.forEach((index) => {
    const text = svgEl("text", { x: x(index), y: H - 17, fill: "#71869a", "font-size": 11, "text-anchor": index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle" });
    text.textContent = formatChartDate(rows[index].date, state.pulseRange);
    svg.append(text);
  });

  series.forEach(({ meta, points }) => {
    let path = "";
    let drawing = false;
    points.forEach((point, index) => {
      if (point.normalized === null) { drawing = false; return; }
      path += `${drawing ? " L" : " M"} ${x(index)} ${y(point.normalized)}`;
      drawing = true;
    });
    svg.append(svgEl("path", { d: path, fill: "none", stroke: meta.color, "stroke-width": meta.key === "proxy_support_index" ? 3.6 : 2.3, "stroke-linecap": "round", "stroke-linejoin": "round", opacity: meta.key === "proxy_support_index" ? 1 : .86 }));

    const pointStep = Math.max(1, Math.ceil(rows.length / 80));
    points.forEach((point, index) => {
        if (point.normalized === null) return;
        if (index % pointStep !== 0 && index !== points.length - 1) return;
        const group = svgEl("g");
        const hit = svgEl("circle", { cx: x(index), cy: y(point.normalized), r: 11, fill: "transparent" });
        const dot = svgEl("circle", { cx: x(index), cy: y(point.normalized), r: meta.key === "proxy_support_index" ? 4.4 : 3.2, fill: "#071019", stroke: meta.color, "stroke-width": 2.2 });
        const title = svgEl("title");
        title.textContent = `${meta.label}\n${formatDateTime(point.date.toISOString())}\nРеальное значение: ${pulseValueText(meta, point.raw)}\nОтносительно первого замера: ${formatNumber(point.normalized, 1)}`;
        group.append(hit, dot, title);
        svg.append(group);
      });
  });
}

function renderPulseAnalytics() {
  renderPulseToggles();
  const rows = pulseRows();
  renderPulseLatest(rows);
  drawPulseChart(rows);
  renderPulseCorrelations(rows);
  const note = $("#pulseCoverageNote");
  note.textContent = state.pulseScope === "hr"
    ? "HR-вакансии — наблюдаемый posted demand, а не работающий штат. HR-резюме и HR STOCK/FLOW накапливаются с версии 1.7.0."
    : "OBS основан на историческом percentile HR-плотности и появляется после 5 замеров. PROXY зависит от K; истинная мощность неизвестна без работающего headcount W.";
}


async function openSettings() {
  const payload = state.payload || await fetchJson("/api/dashboard");
  $("#profilePathValue").textContent = payload.profile_dir || "data\\hh-browser-profile";
  $("#diagnosticsResults").classList.add("hidden");
  $("#clearProfileButton").disabled = !payload.browser_profile_configured;
  $("#openLoginButton").disabled = Boolean(payload.login_browser_running);
  $("#settingsModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeSettings() {
  $("#settingsModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
}

async function openLoginBrowser() {
  try {
    const result = await fetchJson("/api/browser/open-login", { method: "POST" });
    $("#openLoginButton").disabled = true;
    showToast(result.message || "Chrome открыт для входа в HH");
    await loadDashboard();
  } catch (error) {
    showToast(`Не удалось открыть Chrome: ${error.message}`);
  }
}

function diagnosticLabel(name) {
  return {
    vacancy_page: "Поиск вакансий через Chrome",
    resume_page: "Поиск резюме через Chrome",
  }[name] || name;
}

async function runDiagnostics() {
  const box = $("#diagnosticsResults");
  box.classList.remove("hidden");
  box.innerHTML = '<div class="diagnostic-loading">Открываю Chrome и проверяю страницы HH…</div>';
  try {
    const result = await fetchJson("/api/browser/check", { method: "POST" });
    const checks = result.checks || [];
    box.innerHTML = checks.map((check) => {
      const ok = check.ok;
      const message = ok
        ? `Найдено: ${formatNumber(check.details?.found)}`
        : escapeHtml(check.error?.message || "Неизвестная ошибка");
      return `<div class="diagnostic-row ${ok ? "diagnostic-ok" : "diagnostic-fail"}">
        <span>${ok ? "✓" : "!"}</span>
        <div><strong>${escapeHtml(diagnosticLabel(check.name))}</strong><small>${message}</small></div>
      </div>`;
    }).join("") || '<div class="diagnostic-row diagnostic-fail"><span>!</span><div><strong>Нет результата</strong><small>Проверка не вернула данных.</small></div></div>';
    await loadDashboard();
  } catch (error) {
    box.innerHTML = `<div class="diagnostic-row diagnostic-fail"><span>!</span><div><strong>Проверка не выполнена</strong><small>${escapeHtml(error.message)}</small></div></div>`;
  }
}

async function clearProfile() {
  if (!window.confirm("Удалить отдельный профиль Chrome и сохранённую сессию HH?")) return;
  try {
    await fetchJson("/api/browser/profile", { method: "DELETE" });
    showToast("Профиль Chrome удалён");
    await loadDashboard();
    await openSettings();
  } catch (error) {
    showToast(error.message);
  }
}


async function changeRecruiterCapacity(event) {
  const capacity = Number(event.target.value);
  try {
    await fetchJson("/api/search-force/capacity", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacancies_per_recruiter: capacity }),
    });
    state.recruiterCapacity = capacity;
    showToast(`Proxy-модель пересчитана: K=${capacity} одновременных вакансий`);
    await loadDashboard();
  } catch (error) {
    showToast(`Не удалось изменить модель: ${error.message}`);
  }
}

$("#collectButton").addEventListener("click", startCollection);
$("#emptyCollectButton").addEventListener("click", startCollection);
$("#settingsButton").addEventListener("click", openSettings);
$("#warningSettingsButton").addEventListener("click", openSettings);
$("#closeSettingsButton").addEventListener("click", closeSettings);
$("#settingsModal").addEventListener("click", (event) => { if (event.target === $("#settingsModal")) closeSettings(); });
$("#openLoginButton").addEventListener("click", openLoginBrowser);
$("#diagnosticsButton").addEventListener("click", runDiagnostics);
$("#clearProfileButton").addEventListener("click", clearProfile);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSettings(); });
$("#positionSearch").addEventListener("input", () => renderPositions(state.payload?.latest || []));
$("#querySelect").addEventListener("change", (event) => { state.selectedQuery = event.target.value; drawHistoryChart(); });
$("#metricSelect").addEventListener("change", (event) => { state.selectedMetric = event.target.value; drawHistoryChart(); });
$("#historyRangeSelect").addEventListener("change", (event) => { state.historyRange = event.target.value; drawHistoryChart(); });
$("#pulseScopeSelect").addEventListener("change", (event) => { state.pulseScope = event.target.value; renderPulseAnalytics(); });
$("#pulseRangeSelect").addEventListener("change", (event) => { state.pulseRange = event.target.value; renderPulseAnalytics(); });
$("#correlationMethodSelect").addEventListener("change", (event) => { state.correlationMethod = event.target.value; renderPulseAnalytics(); });
$("#correlationLagSelect").addEventListener("change", (event) => { state.correlationLagDays = Number(event.target.value) || 0; renderPulseAnalytics(); });
$("#capacitySelect").addEventListener("change", changeRecruiterCapacity);
$("#pulseSeriesToggles").addEventListener("click", (event) => {
  const button = event.target.closest("[data-series]");
  if (!button) return;
  const key = button.dataset.series;
  if (state.pulseSeries.has(key)) {
    if (state.pulseSeries.size === 1) {
      showToast("На графике должна остаться хотя бы одна линия");
      return;
    }
    state.pulseSeries.delete(key);
  } else {
    state.pulseSeries.add(key);
  }
  renderPulseAnalytics();
});
window.addEventListener("resize", () => { drawHistoryChart(); renderPulseAnalytics(); });

loadDashboard();
