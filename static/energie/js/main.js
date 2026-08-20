(() => {
  "use strict";

  const apiBase = document.body.dataset.apiBase || "/api/v1/energie";
  const displayOnly = document.body.dataset.displayOnly === "true";
  const displayProjectUrl = document.body.dataset.projectUrl || "";
  const displayPipelineUrl = document.body.dataset.pipelineUrl || "";
  const state = {
    project: null,
    calculation: null,
    pipeline: null,
    modelSources: null,
    datasets: [],
    selections: { "vectoplan-editor": null, "vectoplan-cad": null },
    activeModule: "overview",
    openTabs: ["overview"],
    energyInputs: null,
    goal: { standard: "eh40", priority: 60, renewables: 75, automation: "prepare" },
  };

  const modules = {
    overview: { label: "Projektübersicht", icon: "dashboard" },
    geometry: { label: "Gebäude & Zonen", icon: "building" },
    envelope: { label: "Hülle & U-Werte", icon: "layers" },
    heating: { label: "Anlagentechnik", icon: "heat" },
    balance: { label: "Energiebilanz", icon: "chart" },
    variants: { label: "Varianten & Sanierung", icon: "compare" },
    certificate: { label: "Energieausweis", icon: "certificate" },
    funding: { label: "Fördercheck", icon: "funding" },
    reports: { label: "Berichte", icon: "document" },
    settings: { label: "Projektziele", icon: "settings" },
  };

  const goalProfiles = {
    geg: { label: "GEG", target: 75, wallU: 0.24, windowU: 1.1, efficiency: 3.4, recovery: 68, pvFactor: 0.55 },
    eh55: { label: "Effizienzhaus 55", target: 42, wallU: 0.20, windowU: 0.95, efficiency: 3.8, recovery: 80, pvFactor: 0.78 },
    eh40: { label: "Effizienzhaus 40", target: 28, wallU: 0.16, windowU: 0.80, efficiency: 4.1, recovery: 84, pvFactor: 1.0 },
    eh40_qng: { label: "EH 40 + QNG", target: 24, wallU: 0.14, windowU: 0.75, efficiency: 4.3, recovery: 88, pvFactor: 1.0 },
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const de = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
  const de2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const deInteger = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

  function clear(element) { while (element?.firstChild) element.removeChild(element.firstChild); }
  function text(element, value) { if (element) element.textContent = value == null ? "–" : String(value); }

  async function fetchJson(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(payload.errors) ? payload.errors.join(", ") : payload.error;
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function fetchDisplayJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Vorschaudaten nicht verfügbar (HTTP ${response.status})`);
    return response.json();
  }

  function showToast(message, isError = false) {
    const region = $("#toast-region");
    if (!region) return;
    const toast = document.createElement("div");
    toast.className = `toast${isError ? " is-error" : ""}`;
    toast.textContent = message;
    region.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  function setBusy(isBusy, message = "Energieberechnung wird aktualisiert …") {
    const feedback = $(".calculation-feedback");
    feedback?.classList.toggle("is-calculating", isBusy);
    feedback?.classList.remove("is-error");
    const button = $("#apply-goals-button");
    if (button) button.disabled = isBusy;
    text($("#calculation-message"), message);
  }

  function componentByKind(kind) {
    return state.project?.envelope?.components?.find((component) => component.kind === kind) || {};
  }

  function componentU(kind, fallback) {
    const value = Number(componentByKind(kind).u_value);
    return Number.isFinite(value) && value > 0 ? value : fallback;
  }

  function initializeEnergyInputs() {
    const heating = state.project.systems.heating;
    state.energyInputs = {
      wallU: componentU("exterior_wall", 0.24),
      roofU: componentU("roof", 0.18),
      floorU: componentU("floor", 0.28),
      windowU: componentU("window", 1.1),
      heatingType: heating.type,
      efficiency: Number(heating.seasonal_performance_factor ?? heating.efficiency ?? 1),
      recovery: Number(state.project.systems.ventilation.heat_recovery_rate ?? 0) * 100,
      pv: Number(state.project.systems.renewables.pv_peak_kwp ?? 0),
    };
  }

  function pipelineProject() {
    const project = JSON.parse(JSON.stringify(state.project));
    const inputs = state.energyInputs;
    const values = { exterior_wall: inputs.wallU, roof: inputs.roofU, floor: inputs.floorU, window: inputs.windowU };
    for (const component of project.envelope.components || []) {
      if (Number.isFinite(values[component.kind])) {
        component.u_value = values[component.kind];
        component.layers = [];
      }
    }
    project.systems.heating.type = inputs.heatingType;
    project.systems.heating.seasonal_performance_factor = inputs.efficiency;
    project.systems.ventilation.heat_recovery_rate = inputs.recovery / 100;
    project.systems.renewables.pv_peak_kwp = inputs.pv;
    const targetRatios = { geg: [1, 1], eh55: [0.55, 0.70], eh40: [0.40, 0.55], eh40_qng: [0.40, 0.55] };
    const ratios = targetRatios[state.goal.standard] || targetRatios.geg;
    project.targets = {
      ...(project.targets || {}),
      standard: state.goal.standard.toUpperCase(),
      primary_energy_ratio: ratios[0],
      transmission_ratio: ratios[1],
      renewable_target_percent: state.goal.renewables,
      automation: state.goal.automation,
      optimization_priority: state.goal.priority,
    };
    return project;
  }

  function stageOutput(id) {
    return state.pipeline?.stages?.find((stage) => stage.id === id)?.output || {};
  }

  function projectPipelineResult(result) {
    const summary = result.summary || {};
    const annual = result.stages?.find((stage) => stage.id === "annual-balance")?.output || {};
    const systems = result.stages?.find((stage) => stage.id === "systems")?.output || {};
    const ratingClass = summary.energy_class || "–";
    return {
      calculated_at: result.calculated_at,
      metrics: {
        weighted_u_value: summary.mean_u_value_w_m2k,
        transmission_heat_loss_kwh_a: annual.transmission_heat_loss_kwh_a || 0,
        ventilation_heat_loss_kwh_a: annual.ventilation_heat_loss_kwh_a || 0,
        useful_space_heat_kwh_a: summary.useful_space_heating_kwh_a || 0,
        final_energy_kwh_a: systems.final_energy_kwh_a || 0,
        final_energy_kwh_m2a: summary.final_energy_kwh_m2a || 0,
        primary_energy_kwh_m2a: summary.primary_energy_kwh_m2a || 0,
        co2_kg_m2a: summary.co2_kg_m2a || 0,
        pv_self_use_kwh_a: systems.pv_self_use_kwh_a || 0,
        data_quality_percent: summary.data_quality_percent || 0,
        design_heat_load_kw: summary.design_heat_load_kw || 0,
      },
      rating: { class: ratingClass },
      energy_balance: [
        { id: "transmission", label: "Transmission", value_kwh_a: Math.round(annual.transmission_heat_loss_kwh_a || 0) },
        { id: "ventilation", label: "Lüftung", value_kwh_a: Math.round(annual.ventilation_heat_loss_kwh_a || 0) },
        { id: "internal", label: "Interne Gewinne", value_kwh_a: -Math.round(annual.internal_gains_kwh_a || 0) },
        { id: "solar", label: "Solare Gewinne", value_kwh_a: -Math.round(annual.solar_gains_kwh_a || 0) },
        { id: "pv", label: "PV-Eigennutzung", value_kwh_a: -Math.round(systems.pv_self_use_kwh_a || 0) },
      ],
    };
  }

  async function calculate(options = {}) {
    if (!state.project) return;
    setBusy(true);
    try {
      const result = displayOnly
        ? await fetchDisplayJson(displayPipelineUrl)
        : await fetchJson("/pipeline/run", { method: "POST", body: JSON.stringify({ project: pipelineProject() }) });
      state.pipeline = result;
      state.calculation = projectPipelineResult(result);
      renderCalculation();
      renderPipeline();
      renderVariants();
      const time = new Date(result.calculated_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
      setBusy(false, `Berechnung aktuell · ${time}`);
      text($("#calculation-timestamp"), `Berechnet ${time}`);
      if (!options.silent) showToast("Energieberechnung wurde aktualisiert.");
    } catch (error) {
      $(".calculation-feedback")?.classList.add("is-error");
      setBusy(false, "Berechnung nicht verfügbar");
      showToast(`Berechnung fehlgeschlagen: ${error.message}`, true);
    }
  }

  function renderTabs() {
    const container = $("#project-tabs");
    clear(container);
    for (const moduleId of state.openTabs) {
      const meta = modules[moduleId];
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `project-tab${moduleId === state.activeModule ? " is-active" : ""}`;
      tab.dataset.tabModule = moduleId;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(moduleId === state.activeModule));

      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", `#i-${meta.icon}`);
      icon.appendChild(use);
      const label = document.createElement("span");
      label.textContent = moduleId === "overview" ? `${state.project?.project?.name || "Projekt"} · Übersicht` : meta.label;
      tab.append(icon, label);

      if (moduleId !== "overview") {
        const close = document.createElement("span");
        close.className = "tab-close";
        close.dataset.closeTab = moduleId;
        close.setAttribute("role", "button");
        close.setAttribute("aria-label", `${meta.label} schließen`);
        const closeIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        const closeUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
        closeUse.setAttribute("href", "#i-close");
        closeIcon.appendChild(closeUse);
        close.appendChild(closeIcon);
        tab.appendChild(close);
      }
      container.appendChild(tab);
    }
  }

  function activateModule(moduleId) {
    if (!modules[moduleId]) return;
    if (!state.openTabs.includes(moduleId)) state.openTabs.push(moduleId);
    state.activeModule = moduleId;
    $$('[data-workspace-view]').forEach((view) => {
      const active = view.dataset.workspaceView === moduleId;
      view.classList.toggle("is-active", active);
      view.hidden = !active;
    });
    $$('[data-module]').forEach((button) => button.classList.toggle("is-active", button.dataset.module === moduleId && button.classList.contains("rail-button")));
    renderTabs();
    const frameKey = moduleId === "envelope" ? "editor-envelope" : moduleId === "geometry" ? "cad-geometry" : moduleId === "heating" ? "cad-systems" : "";
    if (frameKey) requestModelSelection($(`[data-model-source-frame="${frameKey}"]`));
  }

  function closeTab(moduleId) {
    const index = state.openTabs.indexOf(moduleId);
    if (index < 0 || moduleId === "overview") return;
    state.openTabs.splice(index, 1);
    if (state.activeModule === moduleId) activateModule(state.openTabs[Math.max(0, index - 1)] || "overview");
    else renderTabs();
  }

  function renderProject() {
    const project = state.project;
    const zoneArea = (project.zones || []).reduce((sum, zone) => sum + Number(zone.floor_area_m2 || 0), 0);
    const envelopeArea = (project.envelope.components || []).reduce((sum, component) => sum + Number(component.area_m2 || 0), 0);
    text($("#project-status-name"), project.project.name);
    text($("#stat-area"), `${de.format(project.geometry.heated_floor_area_m2 || zoneArea)} m²`);
    text($("#stat-envelope"), `${de.format(project.geometry.envelope_area_m2 || envelopeArea)} m²`);
    text($("#stat-floors"), project.building.floors || "–");
    text($("#stat-zones"), project.building.zones || project.zones?.length || "–");
    text($("#callout-pv"), `${de.format(project.systems.renewables.pv_peak_kwp)} kWp`);
    text($("#overview-revision"), `Revision ${project.provenance?.geometry_revision || project.revision}`);
    text($("#envelope-revision"), project.provenance?.geometry_revision || project.revision);
    renderComponents();
    renderGoalControls();
    renderTabs();
  }

  function renderComponents() {
    const tbody = $("#component-table-body");
    const list = $("#envelope-component-list");
    clear(tbody); clear(list);
    const kindLabels = { exterior_wall: "AW", roof: "DA", floor: "BP", window: "FE" };

    for (const component of state.project.envelope.components) {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      const name = document.createElement("span"); name.className = "component-name";
      const icon = document.createElement("span"); icon.className = "component-icon"; icon.textContent = kindLabels[component.kind] || "BT";
      const label = document.createElement("span"); label.textContent = component.name;
      name.append(icon, label); nameCell.appendChild(name); row.appendChild(nameCell);
      const displayedU = Number.isFinite(Number(component.u_value)) ? de2.format(component.u_value) : "aus Schichten";
      for (const value of [`${de.format(component.area_m2)} m²`, component.insulation_cm ? `${de.format(component.insulation_cm)} cm` : "–", `${displayedU}${displayedU === "aus Schichten" ? "" : " W/(m²K)"}`]) {
        const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
      }
      const sourceCell = document.createElement("td"); const source = document.createElement("span"); source.className = `source-tag${component.source !== "library" ? " assumption" : ""}`; source.textContent = component.source === "library" ? "Library" : "Annahme"; sourceCell.appendChild(source); row.appendChild(sourceCell);
      const statusCell = document.createElement("td"); const status = document.createElement("span"); status.className = `component-status${component.status === "assumption" ? " assumption" : ""}`; status.textContent = component.status === "verified" ? "Bestätigt" : "Prüfen"; statusCell.appendChild(status); row.appendChild(statusCell); tbody.appendChild(row);

      const item = document.createElement("div"); item.className = `envelope-component${component.kind === "exterior_wall" ? " is-selected" : ""}`;
      const code = document.createElement("span"); code.textContent = kindLabels[component.kind] || "BT";
      const copy = document.createElement("div"); const itemName = document.createElement("strong"); itemName.textContent = component.name; const itemMeta = document.createElement("small"); itemMeta.textContent = `${de.format(component.area_m2)} m² · ${component.source === "library" ? "Library" : "Annahme"}`; copy.append(itemName, itemMeta);
      const u = document.createElement("b"); u.textContent = displayedU; item.append(code, copy, u); list.appendChild(item);
    }
  }

  function renderBalance(items) {
    const list = $("#balance-list"); clear(list);
    const max = Math.max(1, ...items.map((item) => Math.abs(item.value_kwh_a)));
    for (const item of items) {
      const row = document.createElement("div"); row.className = `balance-row${item.value_kwh_a < 0 ? " is-gain" : ""}`;
      const label = document.createElement("span"); label.textContent = item.label;
      const progress = document.createElement("progress"); progress.max = max; progress.value = Math.abs(item.value_kwh_a);
      const value = document.createElement("strong"); value.textContent = `${item.value_kwh_a < 0 ? "−" : ""}${deInteger.format(Math.abs(item.value_kwh_a))}`;
      row.append(label, progress, value); list.appendChild(row);
    }
  }

  function renderCalculationFlow(items) {
    const container = $("#calculation-flow"); clear(container);
    const max = Math.max(1, ...items.map((item) => Math.abs(item.value_kwh_a)));
    for (const item of items) {
      const row = document.createElement("div"); row.className = `flow-row${item.value_kwh_a < 0 ? " is-gain" : ""}`;
      const label = document.createElement("span"); label.textContent = item.label;
      const track = document.createElement("div"); track.className = "flow-bar-track";
      const progress = document.createElement("progress"); progress.max = max; progress.value = Math.abs(item.value_kwh_a); track.appendChild(progress);
      const value = document.createElement("strong"); value.textContent = `${item.value_kwh_a < 0 ? "−" : ""}${deInteger.format(Math.abs(item.value_kwh_a))} kWh`;
      row.append(label, track, value); container.appendChild(row);
    }
  }

  function renderCalculation() {
    const result = state.calculation;
    const metrics = result.metrics;
    const primary = metrics.primary_energy_kwh_m2a;
    text($("#metric-primary"), de.format(primary)); text($("#metric-final"), de.format(metrics.final_energy_kwh_m2a)); text($("#metric-co2"), de.format(metrics.co2_kg_m2a)); text($("#metric-quality"), metrics.data_quality_percent);
    text($("#metric-final-total"), `${deInteger.format(metrics.final_energy_kwh_a)} kWh/a`); text($("#rating-class"), `Klasse ${result.rating.class}`); text($("#rating-letter"), result.rating.class); text($("#rating-primary"), de.format(metrics.final_energy_kwh_m2a));
    if ($("#rating-primary")?.nextElementSibling) $("#rating-primary").nextElementSibling.textContent = "kWh/(m²·a) Endenergie";
    $("#quality-progress").value = metrics.data_quality_percent; $("#energy-meter").value = Math.min(250, metrics.final_energy_kwh_m2a);
    text($("#callout-wall-u"), `${de2.format(metrics.weighted_u_value)} W/(m²K)`); text($("#envelope-average-u"), `${de2.format(metrics.weighted_u_value)} W/(m²K)`); text($("#envelope-selected-u"), `${de2.format(state.energyInputs.wallU)} W/(m²K)`);
    text($("#detail-primary"), de.format(primary)); text($("#detail-final"), de.format(metrics.final_energy_kwh_m2a)); text($("#detail-heating"), deInteger.format(metrics.useful_space_heat_kwh_a)); text($("#detail-co2"), de.format(metrics.co2_kg_m2a));
    text($("#formula-transmission"), `${deInteger.format(metrics.transmission_heat_loss_kwh_a)} kWh/a`); text($("#formula-ventilation"), `${deInteger.format(metrics.ventilation_heat_loss_kwh_a)} kWh/a`); text($("#formula-useful"), `${deInteger.format(metrics.useful_space_heat_kwh_a)} kWh/a`); text($("#formula-primary"), `${de.format(primary)} kWh/(m²·a)`);
    text($("#system-heating-load"), `${de.format(metrics.design_heat_load_kw)} kW`); text($("#system-efficiency"), de.format(state.energyInputs.efficiency)); text($("#system-pv-selfuse"), `${deInteger.format(metrics.pv_self_use_kwh_a)} kWh/a`); text($("#system-pv-label"), `${de.format(state.energyInputs.pv)} kWp`);
    text($("#variant-current-primary"), de.format(primary)); text($("#settings-current-primary"), `${de.format(primary)} kWh/(m²·a)`);
    text($("#co2-assessment"), metrics.co2_kg_m2a < 8 ? "niedrig" : metrics.co2_kg_m2a < 15 ? "mittel" : "hoch");
    renderBalance(result.energy_balance); renderCalculationFlow(result.energy_balance); renderGoalStatus();
  }

  function renderPipeline() {
    const container = $("#pipeline-strip");
    clear(container);
    for (const stage of state.pipeline?.stages || []) {
      const step = document.createElement("div");
      const stageStatus = stage.output?.status;
      step.className = `pipeline-step${stageStatus === "insufficient-data" ? " is-warning" : ""}`;
      const marker = document.createElement("span"); marker.textContent = stageStatus === "insufficient-data" ? "!" : "✓";
      const label = document.createElement("strong"); label.textContent = stage.label;
      step.append(marker, label); container.appendChild(step);
    }
  }

  function renderVariants() {
    const container = $(".variant-grid");
    const variants = state.pipeline?.variants || [];
    if (!container || !variants.length) return;
    clear(container);
    const current = document.createElement("article"); current.className = "variant-card";
    const currentBadge = document.createElement("span"); currentBadge.className = "variant-badge current"; currentBadge.textContent = "Aktuell";
    const currentTitle = document.createElement("h2"); currentTitle.textContent = "Projektmodell";
    const currentValue = document.createElement("strong"); currentValue.textContent = de.format(state.pipeline.summary.primary_energy_kwh_m2a);
    const currentUnit = document.createElement("small"); currentUnit.textContent = "kWh/(m²·a)";
    const currentFacts = document.createElement("ul");
    for (const fact of [`Revision ${state.pipeline.project_revision}`, `${state.pipeline.quality.score_percent} % Datenqualität`, "reproduzierbarer Arbeitsstand"]) {
      const item = document.createElement("li"); item.textContent = fact; currentFacts.appendChild(item);
    }
    current.append(currentBadge, currentTitle, currentValue, currentUnit, currentFacts);
    container.appendChild(current);
    for (const variant of variants) {
      const card = document.createElement("article");
      card.className = `variant-card${variant.id === "complete" ? " recommended" : ""}`;
      const badge = document.createElement("span"); badge.className = "variant-badge"; badge.textContent = variant.id === "complete" ? "Empfohlen" : "Variante";
      const title = document.createElement("h2"); title.textContent = variant.label;
      const value = document.createElement("strong"); value.textContent = de.format(variant.summary.primary_energy_kwh_m2a);
      const unit = document.createElement("small"); unit.textContent = "kWh/(m²·a) Primärenergie";
      const list = document.createElement("ul");
      const saving = document.createElement("li"); saving.textContent = `${de.format(variant.primary_energy_saving_percent)} % Einsparung`;
      const measures = document.createElement("li"); measures.textContent = `${variant.changes.length} Maßnahmenpakete`;
      const heatingLoad = document.createElement("li"); heatingLoad.textContent = `Heizlast ${de.format(variant.summary.design_heat_load_kw)} kW`;
      list.append(saving, measures, heatingLoad); card.append(badge, title, value, unit, list); container.appendChild(card);
    }
  }

  function populateDatasets() {
    const select = $("#dataset-selector");
    if (!select) return;
    clear(select);
    const current = document.createElement("option"); current.value = ""; current.textContent = "Projektmodell"; select.appendChild(current);
    for (const dataset of state.datasets) {
      const option = document.createElement("option"); option.value = dataset.id; option.textContent = dataset.label; select.appendChild(option);
    }
  }

  async function loadDataset(datasetId) {
    if (!datasetId) return;
    setBusy(true, "Testfall wird geladen …");
    try {
      state.project = await fetchJson(`/datasets/${encodeURIComponent(datasetId)}`);
      state.pipeline = null; state.calculation = null;
      initializeEnergyInputs(); renderProject(); configureModelSources();
      await calculate({ silent: true });
      showToast(`${state.project.dataset_label || state.project.project.name} wurde geladen.`);
    } catch (error) {
      setBusy(false, "Testfall konnte nicht geladen werden"); showToast(error.message, true);
    }
  }

  function priorityLabel(value) {
    if (value <= 30) return "Kosten";
    if (value >= 70) return "Energie & CO₂";
    return "Ausgewogen";
  }

  function renderGoalControls() {
    const profile = goalProfiles[state.goal.standard];
    $$('[data-standard]').forEach((button) => button.classList.toggle("is-active", button.dataset.standard === state.goal.standard));
    $$('[data-automation]').forEach((button) => button.classList.toggle("is-active", button.dataset.automation === state.goal.automation));
    $("#priority-slider").value = state.goal.priority; $("#renewable-slider").value = state.goal.renewables;
    text($("#priority-output"), priorityLabel(state.goal.priority)); text($("#renewable-output"), `${state.goal.renewables} %`);
    text($("#project-target-badge"), `Ziel: ${profile.label.replace("Effizienzhaus ", "EH ")}`); text($("#overview-goal-title"), profile.label); text($("#settings-goal-title"), profile.label); text($("#settings-target-primary"), de.format(profile.target));
    text($("#settings-wall-u"), `≤ ${de2.format(profile.wallU)} W/(m²K)`); text($("#settings-window-u"), `≤ ${de2.format(profile.windowU)} W/(m²K)`); text($("#settings-efficiency"), `JAZ ≥ ${de.format(profile.efficiency)}`); text($("#settings-recovery"), `≥ ${profile.recovery} %`);
    const pv = state.project ? state.project.systems.renewables.roof_potential_kwp * profile.pvFactor * state.goal.renewables / 75 : 0;
    text($("#settings-pv"), `${de.format(Math.min(state.project?.systems?.renewables?.roof_potential_kwp || pv, pv))} kWp`); text($("#goal-renewables-summary"), `${state.goal.renewables} %`); text($("#goal-priority-summary"), priorityLabel(state.goal.priority));
    if (state.calculation) renderGoalStatus();
  }

  function renderGoalStatus() {
    const profile = goalProfiles[state.goal.standard];
    const primary = state.calculation.metrics.primary_energy_kwh_m2a;
    const achieved = primary <= profile.target;
    const distance = primary - profile.target;
    text($("#goal-current-value"), de.format(primary)); text($("#goal-target-value"), de.format(profile.target));
    $("#goal-progress").value = Math.max(0, Math.min(100, profile.target / Math.max(primary, profile.target) * 100));
    const goalStatus = $("#goal-status"); goalStatus.classList.toggle("is-missed", !achieved); text(goalStatus, achieved ? "Ziel erreicht" : `${de.format(distance)} über Ziel`);
    text($("#target-distance"), achieved ? `${profile.label} erreicht` : `${de.format(distance)} kWh/(m²·a) bis ${profile.label}`);
    text($("#settings-goal-delta"), achieved ? "Ziel im Arbeitsmodell erreicht" : `noch ${de.format(distance)} kWh/(m²·a) über Ziel`);
  }

  async function applyGoals() {
    const profile = goalProfiles[state.goal.standard];
    const roofPotential = Number(state.project.systems.renewables.roof_potential_kwp ?? state.project.systems.renewables.pv_peak_kwp ?? 0);
    state.energyInputs.wallU = profile.wallU; state.energyInputs.windowU = profile.windowU; state.energyInputs.efficiency = profile.efficiency; state.energyInputs.recovery = profile.recovery;
    if (state.goal.standard !== "geg") state.energyInputs.heatingType = "heat_pump";
    state.energyInputs.pv = Math.min(roofPotential, roofPotential * profile.pvFactor * state.goal.renewables / 75);
    await calculate({ silent: true });
    showToast(`${profile.label} wurde auf das Projektmodell angewendet.`);
  }

  async function createReportDraft(type) {
    if (displayOnly) {
      showToast("Berichtsvorschau · die Service-Anbindung folgt später.");
      return;
    }
    const reportMap = { calculation: "thermal-report-draft", quality: "thermal-report-draft", variants: "renovation-roadmap-draft" };
    try {
      const documentType = reportMap[type] || "thermal-report-draft";
      const result = await fetchJson(`/documents/${documentType}`, { method: "POST", body: JSON.stringify({ project: pipelineProject() }) });
      showToast(`${result.document_type} wurde als prüfbarer Arbeitsentwurf erstellt.`);
    } catch (error) { showToast(`Bericht konnte nicht vorbereitet werden: ${error.message}`, true); }
  }

  function frameSourceForWindow(sourceWindow) {
    return $$('[data-model-source-frame]').find((frame) => frame.contentWindow === sourceWindow) || null;
  }

  function sourceConfigForFrame(frame) {
    if (!frame || !state.modelSources) return null;
    return frame.dataset.modelSourceFrame === "editor-envelope" ? state.modelSources.editor : state.modelSources.cad;
  }

  function requestModelSelection(frame) {
    const source = sourceConfigForFrame(frame);
    if (!source || !frame.contentWindow) return;
    frame.contentWindow.postMessage({
      contract: state.modelSources.contract,
      type: state.modelSources.messages.request,
      source: "vectoplan-energie",
      projectId: state.project.project.id,
      acceptedKinds: ["zone", "room", "exterior_wall", "roof", "floor", "window", "system", "equipment"],
    }, source.origin);
  }

  function publishEditorPreview(frame) {
    if (!frame?.contentWindow || !state.modelSources?.editor) return;
    const floors = Number(state.project.building.floors || 3);
    frame.contentWindow.postMessage({
      contract: "vectoplan-generator-preview.v1",
      type: "vectoplan.generator-preview.update",
      sequence: Date.now(),
      reason: "energy-project-model",
      payload: {
        familyName: state.project.project.name,
        objectKind: "building",
        variantId: state.project.revision || "current",
        materialClass: "thermal-envelope",
        geometry: { shape: "block", width: 18.4, height: Math.max(3, floors * 3), depth: 12.2, unit: "m", cellsX: 6, cellsY: Math.max(1, floors), cellsZ: 4 },
        raw: { projectId: state.project.project.id, purpose: "energy-envelope-selection" },
      },
      assets: [],
    }, state.modelSources.editor.origin);
  }

  async function acceptSelection(message) {
    try {
      const normalized = displayOnly
        ? message
        : (await fetchJson("/model-selections/normalize", { method: "POST", body: JSON.stringify(message) })).selection;
      state.selections[normalized.source] = normalized;
      renderSelection(normalized);
    } catch (error) {
      showToast(`Modellauswahl verworfen: ${error.message}`, true);
    }
  }

  function renderSelection(message) {
    const drawer = $("#selection-drawer");
    const object = message.selection?.objects?.[0];
    if (!drawer || !object) return;
    drawer.hidden = false;
    text($("#selection-title"), object.name);
    text($("#selection-source"), `${message.source === "vectoplan-editor" ? "3D · Editor" : "2D · CAD"} · Revision ${message.revision}`);
    const properties = $("#selection-properties"); clear(properties);
    const entries = [["Objekttyp", object.kind], ["Modell-ID", object.id], ...Object.entries(object.properties || {})];
    for (const [key, value] of entries) {
      const row = document.createElement("div"); const term = document.createElement("dt"); const detail = document.createElement("dd");
      term.textContent = String(key).replaceAll("_", " "); detail.textContent = value == null ? "–" : String(value); row.append(term, detail); properties.appendChild(row);
    }
  }

  function onModelMessage(event) {
    const frame = frameSourceForWindow(event.source);
    const source = sourceConfigForFrame(frame);
    if (!frame || !source || event.origin !== source.origin || !event.data) return;
    const layer = frame.closest("[data-live-source]");
    if (event.data.contract === "vectoplan-generator-preview.v1") {
      layer.hidden = false;
      layer?.classList.add("is-connected");
      if (event.data.type === "vectoplan.generator-preview.ready") publishEditorPreview(frame);
      return;
    }
    if (event.data.contract !== state.modelSources.contract) return;
    layer?.classList.add("is-connected");
    if (event.data.type === state.modelSources.messages.ready) requestModelSelection(frame);
    if (event.data.type === state.modelSources.messages.changed) acceptSelection(event.data);
  }

  async function probeModelSource(frame, source) {
    const layer = frame.closest("[data-live-source]");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 1800);
    try {
      await fetch(source.url, { mode: "no-cors", cache: "no-store", signal: controller.signal });
      frame.addEventListener("load", () => {
        if (source.source === "vectoplan-cad") layer.hidden = false;
        window.setTimeout(() => { requestModelSelection(frame); if (source.source === "vectoplan-editor") publishEditorPreview(frame); }, 250);
      }, { once: true });
      if (source.source === "vectoplan-cad") layer.hidden = false;
      frame.src = source.url;
    } catch (error) {
      layer.hidden = true;
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function configureModelSources() {
    if (displayOnly) return;
    try {
      state.modelSources = await fetchJson(`/model-sources?project_id=${encodeURIComponent(state.project.project.id)}`);
      for (const frame of $$('[data-model-source-frame]')) {
        const source = sourceConfigForFrame(frame);
        if (source) probeModelSource(frame, source);
      }
    } catch (error) {
      showToast("Editor/CAD-Routen konnten nicht vorbereitet werden.", true);
    }
  }

  function demoSelection(source, object) {
    acceptSelection({
      contract: "vectoplan.energy-selection.v1",
      type: "vectoplan.energy-selection.changed",
      source,
      projectId: state.project.project.id,
      revision: source === "vectoplan-editor" ? state.project.provenance?.geometry_revision || state.project.revision : state.project.provenance?.cad_revision || state.project.revision,
      selection: { objects: [object] },
    });
  }

  function bindEvents() {
    document.addEventListener("click", (event) => {
      const moduleButton = event.target.closest("[data-module]");
      if (moduleButton) activateModule(moduleButton.dataset.module);
      const tab = event.target.closest("[data-tab-module]");
      if (tab && !event.target.closest("[data-close-tab]")) activateModule(tab.dataset.tabModule);
      const close = event.target.closest("[data-close-tab]");
      if (close) { event.stopPropagation(); closeTab(close.dataset.closeTab); }
      const standard = event.target.closest("[data-standard]");
      if (standard) { state.goal.standard = standard.dataset.standard; renderGoalControls(); }
      const automation = event.target.closest("[data-automation]");
      if (automation) { state.goal.automation = automation.dataset.automation; renderGoalControls(); }
      const preset = event.target.closest("[data-goal-preset]");
      if (preset) { state.goal.standard = preset.dataset.goalPreset; renderGoalControls(); activateModule("settings"); }
      const report = event.target.closest("[data-report-type]");
      if (report) createReportDraft(report.dataset.reportType);
      const floor = event.target.closest("[data-floor]");
      if (floor) { $$('[data-floor]').forEach((row) => row.classList.toggle("is-selected", row === floor)); showToast(`${floor.textContent.trim().replace(/\d+$/, "").trim()} geöffnet.`); }
      const validate = event.target.closest('[data-action="validate-zones"]');
      if (validate) showToast("7 Zonen vollständig, 1 Nutzungsprofil muss geprüft werden.");
      const envelopeSurface = event.target.closest(".env-selected");
      if (envelopeSurface) {
        const component = componentByKind("exterior_wall");
        demoSelection("vectoplan-editor", { id: component.id || "wall-1", kind: "exterior_wall", name: component.name || "Außenwand", properties: { area_m2: component.area_m2, u_value_w_m2k: component.u_value || state.energyInputs.wallU, source: component.source || "project-model" }, geometryRef: component.id || "wall-1", libraryRef: component.source === "library" ? component.id : "" });
      }
      const zoneSurface = event.target.closest(".zone-surfaces path");
      if (zoneSurface) demoSelection("vectoplan-cad", { id: "zone-eg-07", kind: "zone", name: "Zone 07 · Wohnen", properties: { floor_area_m2: 39.8, volume_m3: 109.5, target_temperature_c: 20, usage_profile: "Wohnen" }, geometryRef: "cad:zone-eg-07" });
      const systemModule = event.target.closest(".system-module");
      if (systemModule) demoSelection("vectoplan-cad", { id: `system-${systemModule.textContent.trim().split(/\s+/)[0].toLowerCase()}`, kind: "system", name: systemModule.querySelector("strong")?.textContent || "Anlagensystem", properties: { description: systemModule.querySelector("small")?.textContent || "", source: "cad-system-schema" }, geometryRef: "cad:system-schema" });
    });
    $("#priority-slider").addEventListener("input", (event) => { state.goal.priority = Number(event.target.value); renderGoalControls(); });
    $("#renewable-slider").addEventListener("input", (event) => { state.goal.renewables = Number(event.target.value); renderGoalControls(); });
    $("#apply-goals-button").addEventListener("click", applyGoals);
    $("#recalculate-button").addEventListener("click", () => calculate());
    $("#dataset-selector")?.addEventListener("change", (event) => loadDataset(event.target.value));
    $("#selection-close")?.addEventListener("click", () => { $("#selection-drawer").hidden = true; });
    window.addEventListener("message", onModelMessage);
  }

  async function boot() {
    bindEvents(); setBusy(true, "Projektmodell wird geladen …");
    try {
      if (displayOnly) {
        state.project = await fetchDisplayJson(displayProjectUrl);
        state.datasets = [];
        state.modelSources = null;
      } else {
        const bootstrap = await fetchJson("/bootstrap");
        state.project = bootstrap.project;
        state.datasets = bootstrap.datasets || [];
        state.modelSources = bootstrap.model_sources || null;
      }
      initializeEnergyInputs(); renderProject(); populateDatasets(); configureModelSources(); activateModule("overview");
      await calculate({ silent: true });
    } catch (error) {
      setBusy(false, "Service nicht bereit"); $(".calculation-feedback")?.classList.add("is-error"); showToast(`Arbeitsbereich konnte nicht geladen werden: ${error.message}`, true);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
