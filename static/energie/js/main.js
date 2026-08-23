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
    funding: {
      deliverable: "funding",
      projectType: "new_build",
      units: 8,
      costs: 1200000,
      income: 65000,
      children: 0,
      selfUse: false,
      isfp: true,
      wpb: false,
      serial: false,
      oldHeating: true,
    },
    system: {
      heatSource: "air_heat_pump",
      floorHeating: true,
      distribution: "floor",
      ventilation: "central_hrv",
      pv: 14.8,
      storage: 10,
      hotWater: "integrated",
    },
    balanceSettings: {
      method: "din18599",
      climate: "reference",
      temperature: 20,
      airChange: 0.5,
      thermalBridge: 0.05,
      internalGains: 5,
      hotWater: 12.5,
      pvSelfUse: 55,
      airtightness: false,
      cooling: false,
      renewables: true,
    },
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

  const heatSourceProfiles = {
    air_heat_pump: { label: "Luft/Wasser-Wärmepumpe", short: "Wärmepumpe", icon: "WP", efficiency: 3.6, primaryFactor: 1.15, co2Factor: 0.21, price: 0.31, renewable: 65 },
    ground_heat_pump: { label: "Sole/Wasser-Wärmepumpe", short: "Erdwärmepumpe", icon: "SW", efficiency: 4.7, primaryFactor: 1.05, co2Factor: 0.18, price: 0.31, renewable: 76 },
    district_heating: { label: "Fernwärme", short: "Fernwärme", icon: "FW", efficiency: 0.96, primaryFactor: 0.70, co2Factor: 0.16, price: 0.16, renewable: 35 },
    pellet_boiler: { label: "Pelletkessel", short: "Pelletkessel", icon: "PK", efficiency: 0.88, primaryFactor: 0.20, co2Factor: 0.04, price: 0.10, renewable: 88 },
    gas_boiler: { label: "Gas-Brennwertkessel", short: "Gasheizung", icon: "GB", efficiency: 0.92, primaryFactor: 1.10, co2Factor: 0.24, price: 0.13, renewable: 0 },
  };

  const distributionProfiles = {
    floor: { label: "Fußbodenheizung", flowTemperature: 35, factor: 1 },
    radiator_low: { label: "Niedertemperatur-Heizkörper", flowTemperature: 45, factor: 1.07 },
    radiator_high: { label: "Bestandsheizkörper", flowTemperature: 65, factor: 1.24 },
  };

  const ventilationProfiles = {
    central_hrv: { label: "Zentral mit WRG", recovery: 84, factor: 0.72 },
    decentral_hrv: { label: "Dezentral mit WRG", recovery: 70, factor: 0.80 },
    exhaust: { label: "Abluftanlage", recovery: 0, factor: 1.05 },
    window: { label: "Fensterlüftung", recovery: 0, factor: 1.16 },
  };

  const hotWaterProfiles = {
    integrated: { label: "über Wärmeerzeuger", factor: 1, renewableBonus: 0 },
    heat_pump: { label: "separate Warmwasser-WP", factor: 0.72, renewableBonus: 8 },
    solar: { label: "solarthermisch unterstützt", factor: 0.58, renewableBonus: 18 },
    electric: { label: "elektrischer Speicher", factor: 1.35, renewableBonus: 0 },
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const de = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
  const de2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const deInteger = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });

  document.body.classList.toggle("is-embedded", window.self !== window.top);

  function clear(element) { while (element?.firstChild) element.removeChild(element.firstChild); }
  function text(element, value) { if (element) element.textContent = value == null ? "–" : String(value); }
  function parseGermanMoney(value) {
    const normalized = String(value ?? "").trim().replace(/[\s€]/g, "").replaceAll(".", "").replace(",", ".");
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function formatMoneyInput() {
    const input = $("#funding-costs");
    if (input) input.value = de2.format(state.funding.costs);
  }

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
    const advancedButton = $("#apply-goals-advanced-button");
    if (advancedButton) advancedButton.disabled = isBusy;
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
    state.system.heatSource = heating.type === "heat_pump" ? "air_heat_pump" : heatSourceProfiles[heating.type] ? heating.type : "air_heat_pump";
    state.system.pv = state.energyInputs.pv;
    state.system.ventilation = state.energyInputs.recovery >= 75 ? "central_hrv" : state.energyInputs.recovery > 0 ? "decentral_hrv" : "window";
    syncSystemInputs();
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
    if (!container) return;
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
    if (moduleId === "variants" && state.funding.deliverable !== "isfp") return;
    if (!state.openTabs.includes(moduleId)) state.openTabs.push(moduleId);
    state.activeModule = moduleId;
    $$('[data-workspace-view]').forEach((view) => {
      const active = view.dataset.workspaceView === moduleId;
      view.classList.toggle("is-active", active);
      view.hidden = !active;
    });
    $$('[data-module]').forEach((button) => button.classList.toggle("is-active", button.dataset.module === moduleId && button.classList.contains("rail-button")));
    renderTabs();
    const frameKey = moduleId === "envelope" ? "editor-envelope" : moduleId === "geometry" ? "cad-geometry" : "";
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
    if ($("#certificate-area")) $("#certificate-area").value = Number(project.geometry.heated_floor_area_m2 || zoneArea || 0).toFixed(1);
    if ($("#certificate-units")) $("#certificate-units").value = project.building.zones || state.funding.units;
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
      const statusCell = document.createElement("td"); const status = document.createElement("span"); status.className = `component-status${component.status === "assumption" ? " assumption" : ""}`; status.textContent = component.status === "verified" ? "Bestätigt" : "Prüfen"; statusCell.appendChild(status); row.appendChild(statusCell); tbody?.appendChild(row);

      const item = document.createElement("div"); item.className = `envelope-component${component.kind === "exterior_wall" ? " is-selected" : ""}`;
      const code = document.createElement("span"); code.textContent = kindLabels[component.kind] || "BT";
      const copy = document.createElement("div"); const itemName = document.createElement("strong"); itemName.textContent = component.name; const itemMeta = document.createElement("small"); itemMeta.textContent = `${de.format(component.area_m2)} m² · ${component.source === "library" ? "Library" : "Annahme"}`; copy.append(itemName, itemMeta);
      const u = document.createElement("b"); u.textContent = displayedU; item.append(code, copy, u); list?.appendChild(item);
    }
  }

  function renderBalance(items) {
    const list = $("#balance-list"); clear(list);
    if (!list) return;
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
    if (!container) return;
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
    if ($("#quality-progress")) $("#quality-progress").value = metrics.data_quality_percent;
    if ($("#energy-meter")) $("#energy-meter").value = Math.min(250, metrics.final_energy_kwh_m2a);
    text($("#callout-wall-u"), `${de2.format(metrics.weighted_u_value)} W/(m²K)`); text($("#envelope-average-u"), `${de2.format(metrics.weighted_u_value)} W/(m²K)`); text($("#envelope-selected-u"), `${de2.format(state.energyInputs.wallU)} W/(m²K)`);
    text($("#detail-primary"), de.format(primary)); text($("#detail-final"), de.format(metrics.final_energy_kwh_m2a)); text($("#detail-heating"), deInteger.format(metrics.useful_space_heat_kwh_a)); text($("#detail-co2"), de.format(metrics.co2_kg_m2a));
    text($("#formula-transmission"), `${deInteger.format(metrics.transmission_heat_loss_kwh_a)} kWh/a`); text($("#formula-ventilation"), `${deInteger.format(metrics.ventilation_heat_loss_kwh_a)} kWh/a`); text($("#formula-useful"), `${deInteger.format(metrics.useful_space_heat_kwh_a)} kWh/a`); text($("#formula-primary"), `${de.format(primary)} kWh/(m²·a)`);
    text($("#system-heating-load"), `${de.format(metrics.design_heat_load_kw)} kW`); text($("#system-efficiency"), de.format(state.energyInputs.efficiency)); text($("#system-pv-selfuse"), `${deInteger.format(metrics.pv_self_use_kwh_a)} kWh/a`); text($("#system-pv-label"), `${de.format(state.energyInputs.pv)} kWp`);
    text($("#variant-current-primary"), de.format(primary)); text($("#settings-current-primary"), `${de.format(primary)} kWh/(m²·a)`);
    text($("#co2-assessment"), metrics.co2_kg_m2a < 8 ? "niedrig" : metrics.co2_kg_m2a < 15 ? "mittel" : "hoch");
    renderBalance(result.energy_balance);
    renderCalculationFlow(result.energy_balance);
    renderSystemPlanner();
    renderBalancePreview();
    renderIsfpRoadmap();
    renderCertificatePreview();
    renderGoalStatus();
  }

  function renderPipeline() {
    const container = $("#pipeline-strip");
    clear(container);
    if (!container) return;
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

  function systemScenario() {
    const metrics = state.calculation?.metrics || {};
    const source = heatSourceProfiles[state.system.heatSource] || heatSourceProfiles.air_heat_pump;
    const distributionKey = state.system.floorHeating ? "floor" : state.system.distribution;
    const distribution = distributionProfiles[distributionKey] || distributionProfiles.radiator_low;
    const ventilation = ventilationProfiles[state.system.ventilation] || ventilationProfiles.central_hrv;
    const hotWater = hotWaterProfiles[state.system.hotWater] || hotWaterProfiles.integrated;
    const area = Math.max(1, Number(state.project?.geometry?.heated_floor_area_m2 || 1));
    const baseUseful = Math.max(1, Number(metrics.useful_space_heat_kwh_a || area * 45));
    const spaceHeat = baseUseful * (ventilation.factor / ventilationProfiles.central_hrv.factor);
    const hotWaterUseful = area * Number(state.balanceSettings.hotWater || 12.5) * hotWater.factor;
    const effectiveEfficiency = Math.max(0.5, source.efficiency / distribution.factor);
    const sourceEnergy = (spaceHeat + hotWaterUseful) / effectiveEfficiency;
    const auxiliaryEnergy = Math.max(180, (spaceHeat + hotWaterUseful) * 0.025);
    const pvYield = Math.max(0, state.system.pv) * 950;
    const electricHeating = ["air_heat_pump", "ground_heat_pump"].includes(state.system.heatSource);
    const electricityDemand = auxiliaryEnergy + (electricHeating ? sourceEnergy : 0);
    const requestedSelfUse = Math.max(0.20, Number(state.balanceSettings.pvSelfUse || 55) / 100);
    const storageBonus = Math.min(0.22, Math.max(0, state.system.storage) * 0.012);
    const selfUseRate = Math.min(0.80, requestedSelfUse + storageBonus);
    const pvSelfUse = state.balanceSettings.renewables ? Math.min(electricityDemand, pvYield * selfUseRate) : 0;
    const finalEnergy = Math.max(0, sourceEnergy + auxiliaryEnergy - pvSelfUse);
    const primaryEnergy = Math.max(0, ((sourceEnergy * source.primaryFactor) + (auxiliaryEnergy - pvSelfUse) * 1.8) / area);
    const co2 = Math.max(0, ((sourceEnergy * source.co2Factor) + (auxiliaryEnergy - pvSelfUse) * 0.38) / area);
    const cost = Math.max(0, sourceEnergy * source.price + (auxiliaryEnergy - pvSelfUse) * 0.31);
    const environmentalHeat = electricHeating ? Math.max(0, spaceHeat + hotWaterUseful - sourceEnergy) : (spaceHeat + hotWaterUseful) * source.renewable / 100;
    const renewableEnergy = state.balanceSettings.renewables ? environmentalHeat + pvSelfUse : 0;
    const renewableShare = Math.min(100, Math.max(0, (source.renewable + hotWater.renewableBonus + Math.min(20, pvSelfUse / Math.max(1, finalEnergy) * 20))));
    return { source, distribution, ventilation, hotWater, area, spaceHeat, hotWaterUseful, effectiveEfficiency, sourceEnergy, auxiliaryEnergy, pvYield, pvSelfUse, selfUseRate, finalEnergy, primaryEnergy, co2, cost, renewableEnergy, renewableShare };
  }

  function syncSystemInputs() {
    const values = {
      "system-heat-source": state.system.heatSource,
      "system-floor-heating": state.system.floorHeating ? "yes" : "no",
      "system-distribution": state.system.floorHeating ? "radiator_low" : state.system.distribution,
      "system-ventilation": state.system.ventilation,
      "system-pv": String(state.system.pv),
      "system-storage": String(state.system.storage),
      "system-hot-water": state.system.hotWater,
    };
    for (const [id, value] of Object.entries(values)) if ($(`#${id}`)) $(`#${id}`).value = value;
  }

  function flowDuration(value, reference, slow = 2.6, fast = 1.05) {
    const intensity = Math.max(0, Math.min(1, Number(value || 0) / Math.max(1, reference)));
    return slow - (slow - fast) * intensity;
  }

  function setFlowActivity(lineId, particleClass, active, duration) {
    const line = $(`#${lineId}`);
    line?.classList.toggle("is-inactive", !active);
    for (const particle of $$(`.flow-particle.${particleClass}`)) {
      particle.classList.toggle("is-inactive", !active);
      particle.querySelector("animateMotion")?.setAttribute("dur", `${duration.toFixed(2)}s`);
    }
  }

  function renderSystemPlanner() {
    if (!state.calculation || !$("#system-flow-stage")) return;
    const scenario = systemScenario();
    text($("#system-source-icon"), scenario.source.icon);
    text($("#system-source-title"), scenario.source.short);
    text($("#system-source-flow"), `${deInteger.format(scenario.sourceEnergy)} kWh/a`);
    text($("#system-source-note"), ["air_heat_pump", "ground_heat_pump"].includes(state.system.heatSource) ? `JAZ ${de2.format(scenario.effectiveEfficiency)}` : `η ${deInteger.format(scenario.effectiveEfficiency * 100)} %`);
    text($("#system-demand-value"), `${deInteger.format(scenario.spaceHeat + scenario.hotWaterUseful)} kWh/a`);
    text($("#system-demand-caption"), `${scenario.distribution.label} · ${scenario.distribution.flowTemperature} °C`);
    text($("#system-pv-flow"), `${deInteger.format(scenario.pvYield)} kWh/a`);
    text($("#system-storage-flow"), state.system.storage > 0 ? `${de2.format(state.system.storage)} kWh` : "ohne Speicher");
    text($("#system-grid-flow"), `${deInteger.format(Math.max(0, scenario.finalEnergy))} kWh/a`);
    text($("#system-preview-final"), `${de.format(scenario.finalEnergy / scenario.area)} kWh/(m²·a)`);
    text($("#system-preview-primary"), `${de.format(scenario.primaryEnergy)} kWh/(m²·a)`);
    text($("#system-preview-co2"), `${de.format(scenario.co2)} kg/(m²·a)`);
    text($("#system-preview-cost"), `${euro.format(scenario.cost)} /a`);
    text($("#system-preview-efficiency"), de2.format(scenario.effectiveEfficiency));
    text($("#system-preview-floor-heating"), state.system.floorHeating ? "Ja" : "Nein");
    text($("#system-preview-renewables"), `${deInteger.format(scenario.renewableShare)} %`);
    text($("#system-preview-self-use"), `${deInteger.format(scenario.pvSelfUse)} kWh/a`);
    text($("#system-preview-flow-temp"), `${scenario.distribution.flowTemperature} °C`);
    text($("#system-scenario-title"), `${scenario.source.label} · ${scenario.distribution.label}`);
    const baseline = Number(state.calculation.metrics.primary_energy_kwh_m2a || 0);
    const delta = scenario.primaryEnergy - baseline;
    text($("#system-change-copy"), Math.abs(delta) < 0.1 ? "entspricht dem berechneten Projektstand." : `${de.format(Math.abs(delta))} kWh/(m²·a) ${delta < 0 ? "weniger" : "mehr"} Primärenergie als der Projektstand.`);
    $("#system-change-note")?.classList.toggle("is-worse", delta > 0);
    for (const [id, visible] of [["planner-pv-visual", state.system.pv > 0], ["planner-storage-visual", state.system.storage > 0]]) {
      const element = $(`#${id}`); if (element) element.style.opacity = visible ? "1" : "0.12";
    }
    const gridEnergy = Math.max(0, scenario.finalEnergy);
    setFlowActivity("flow-pv-house", "particle-pv", state.system.pv > 0, flowDuration(scenario.pvYield, 30000));
    setFlowActivity("flow-grid-house", "particle-grid", gridEnergy > 1, flowDuration(gridEnergy, 12000));
    setFlowActivity("flow-source-house", "particle-source", scenario.sourceEnergy > 0, flowDuration(scenario.sourceEnergy, 12000));
    setFlowActivity("flow-storage-house", "particle-storage", state.system.storage > 0, flowDuration(state.system.storage, 20));
    setFlowActivity("flow-ventilation-house", "particle-ventilation", true, flowDuration(scenario.ventilation.recovery, 90, 3.1, 1.45));
    setFlowActivity("flow-distribution-house", "particle-distribution", true, flowDuration(scenario.spaceHeat, 16000));
    setFlowActivity("flow-hot-water-house", "particle-hot-water", scenario.hotWaterUseful > 0, flowDuration(scenario.hotWaterUseful, 8000));
    const stage = $("#system-flow-stage");
    stage.dataset.source = state.system.heatSource;
    stage.dataset.floorHeating = state.system.floorHeating ? "yes" : "no";
  }

  function balanceItem(id) {
    return Math.abs(Number(state.calculation?.energy_balance?.find((item) => item.id === id)?.value_kwh_a || 0));
  }

  function setMapValue(id, barId, value, maximum) {
    text($(`#${id}`), `${deInteger.format(value)} kWh/a`);
    const bar = $(`#${barId}`);
    if (bar) bar.style.setProperty("--value", `${Math.max(5, Math.min(100, value / Math.max(1, maximum) * 100))}%`);
  }

  function renderBalancePreview() {
    if (!state.calculation || !$("#map-transmission")) return;
    const settings = state.balanceSettings;
    const temperatureFactor = 1 + (settings.temperature - 20) * 0.065;
    const climateFactor = settings.climate === "location" ? 0.94 : 1;
    const bridgeFactor = 1 + (settings.thermalBridge - 0.05) * 1.8;
    const transmission = balanceItem("transmission") * temperatureFactor * climateFactor * bridgeFactor;
    const ventilation = balanceItem("ventilation") * (settings.airChange / 0.5) * temperatureFactor * (settings.airtightness ? 0.88 : 1);
    const internal = balanceItem("internal") * settings.internalGains / 5;
    const solar = balanceItem("solar") * climateFactor;
    const scenario = systemScenario();
    const systemLosses = scenario.hotWaterUseful * 0.12 + scenario.spaceHeat * 0.04;
    const useful = Math.max(0, transmission + ventilation + systemLosses - internal - solar);
    const renewable = settings.renewables ? scenario.renewableEnergy : 0;
    const values = [transmission, ventilation, systemLosses, internal, solar, renewable];
    const maximum = Math.max(1, ...values);
    setMapValue("map-transmission", "map-transmission-bar", transmission, maximum);
    setMapValue("map-ventilation", "map-ventilation-bar", ventilation, maximum);
    setMapValue("map-system-losses", "map-system-losses-bar", systemLosses, maximum);
    setMapValue("map-internal", "map-internal-bar", internal, maximum);
    setMapValue("map-solar", "map-solar-bar", solar, maximum);
    setMapValue("map-renewable", "map-renewable-bar", renewable, maximum);
    text($("#map-useful-energy"), deInteger.format(useful));
    text($("#map-final-energy"), deInteger.format(scenario.finalEnergy));
    const target = goalProfiles[state.goal.standard].target;
    const referenceShare = scenario.primaryEnergy / Math.max(1, target / (state.goal.standard === "geg" ? 1 : { eh55: 0.55, eh40: 0.40, eh40_qng: 0.40 }[state.goal.standard])) * 100;
    const compliant = scenario.primaryEnergy <= target;
    text($("#balance-compliance-status"), compliant ? `${goalProfiles[state.goal.standard].label} eingehalten` : "Ziel noch nicht eingehalten");
    $("#balance-compliance-status")?.classList.toggle("is-missed", !compliant);
    text($("#balance-reference-share"), `${deInteger.format(referenceShare)} % vom Referenzwert`);
    text($("#balance-renewable-share"), `${deInteger.format(scenario.renewableShare)} %`);
    text($("#detail-primary"), de.format(scenario.primaryEnergy));
    text($("#detail-final"), de.format(scenario.finalEnergy / scenario.area));
    text($("#detail-heating"), deInteger.format(useful));
    text($("#detail-co2"), de.format(scenario.co2));
    text($("#detail-ht"), de2.format(Number(state.calculation.metrics.weighted_u_value || 0) + settings.thermalBridge));
    text($("#detail-pv-credit"), deInteger.format(scenario.pvSelfUse));
    text($("#formula-transmission"), `${deInteger.format(transmission)} kWh/a`);
    text($("#formula-ventilation"), `${deInteger.format(ventilation)} kWh/a`);
    text($("#formula-useful"), `${deInteger.format(useful)} kWh/a`);
    text($("#formula-primary"), `${de.format(scenario.primaryEnergy)} kWh/(m²·a)`);
    renderCalculationFlow([
      { label: "Transmission", value_kwh_a: transmission },
      { label: "Lüftung", value_kwh_a: ventilation },
      { label: "Warmwasser / Verteilung", value_kwh_a: systemLosses },
      { label: "Interne Gewinne", value_kwh_a: -internal },
      { label: "Solare Gewinne", value_kwh_a: -solar },
      { label: "Erneuerbare Anrechnung", value_kwh_a: -renewable },
    ]);
  }

  function renderIsfpRoadmap() {
    if (!state.calculation || !$("#isfp-current-primary")) return;
    const area = Math.max(1, Number(state.project?.geometry?.heated_floor_area_m2 || 1));
    const current = Number(state.calculation.metrics.primary_energy_kwh_m2a || 0);
    const target = goalProfiles[state.goal.standard].target;
    const costs = [area * 205, area * 285, Math.max(42000, area * 105)];
    const grants = [costs[0] * 0.20, costs[1] * 0.20, costs[2] * 0.35];
    const totalCost = costs.reduce((sum, value) => sum + value, 0);
    const totalGrant = grants.reduce((sum, value) => sum + value, 0);
    text($("#isfp-current-primary"), `${de.format(current)} kWh/(m²·a)`);
    text($("#isfp-target-primary"), `${de.format(target)} kWh/(m²·a)`);
    text($("#isfp-total-saving"), `${deInteger.format(Math.max(0, (current - target) / Math.max(1, current) * 100))} %`);
    costs.forEach((value, index) => text($(`#isfp-cost-${index + 1}`), euro.format(value)));
    grants.forEach((value, index) => text($(`#isfp-grant-${index + 1}`), euro.format(value)));
    text($("#isfp-total-cost"), euro.format(totalCost));
    text($("#isfp-total-grant"), euro.format(totalGrant));
    text($("#isfp-net-cost"), euro.format(totalCost - totalGrant));
  }

  function renderCertificatePreview() {
    if (!$("#certificate-preview-final")) return;
    const metrics = state.calculation?.metrics || {};
    const scenario = state.calculation ? systemScenario() : null;
    const finalEnergy = Number(metrics.final_energy_kwh_m2a || 0);
    const primaryEnergy = Number(metrics.primary_energy_kwh_m2a || 0);
    const rating = state.calculation?.rating?.class || "–";
    const address = $("#certificate-address")?.value.trim() || "Adresse noch offen";
    const city = $("#certificate-city")?.value.trim() || "Ort noch offen";
    const reasonComplete = Boolean($("#certificate-reason")?.value);
    const certificateType = $("#certificate-type")?.value || "Bedarfsausweis";
    const reason = $("#certificate-reason")?.value || "noch auszuwählen";
    const issuer = $("#certificate-issuer")?.value.trim() || "";
    const registration = $("#certificate-registration")?.value.trim() || "noch nicht vergeben";
    const issuerComplete = Boolean(issuer);
    const completed = Number(reasonComplete) + Number(issuerComplete);
    const readiness = Math.round(75 + completed * 12.5);
    text($("#certificate-readiness-value"), readiness);
    text($("#certificate-missing-copy"), completed === 2 ? "Alle Pflichtangaben sind erfasst." : `${2 - completed} Pflichtangabe${completed === 1 ? "" : "n"} ergänzen.`);
    text($("#certificate-preview-address"), `${address} · ${city}`);
    text($("#certificate-preview-type"), certificateType);
    text($("#certificate-preview-building-type"), $("#certificate-building-type")?.value || "Mehrfamilienhaus");
    text($("#certificate-preview-year"), $("#certificate-year")?.value || "–");
    text($("#certificate-preview-heating-year"), $("#certificate-heating-year")?.value || "–");
    text($("#certificate-preview-units"), $("#certificate-units")?.value || state.funding.units);
    text($("#certificate-preview-area"), de.format(Number($("#certificate-area")?.value || 0)));
    text($("#certificate-preview-carrier"), scenario?.source?.label || "–");
    text($("#certificate-preview-reason"), reason);
    text($("#certificate-preview-registration"), registration);
    text($("#certificate-preview-registration-page2"), registration);
    text($("#certificate-preview-final"), `${de.format(finalEnergy)} kWh/(m²·a)`);
    text($("#certificate-preview-final-ad"), `${de.format(finalEnergy)} kWh/(m²·a)`);
    text($("#certificate-preview-primary"), `${de.format(primaryEnergy)} kWh/(m²·a)`);
    text($("#certificate-preview-primary-requirement"), `${de.format(primaryEnergy)} kWh/(m²·a)`);
    text($("#certificate-preview-envelope-quality"), `${de2.format(Number(metrics.weighted_u_value || 0))} W/(m²·K)`);
    text($("#certificate-preview-class"), rating);
    text($("#certificate-preview-co2"), `${de.format(metrics.co2_kg_m2a || 0)} kg CO₂-Äquivalent/(m²·a)`);
    text($("#certificate-preview-renewables"), scenario ? `${deInteger.format(scenario.renewableShare)} % Deckungsanteil` : "–");
    text($("#certificate-preview-renewable-share"), scenario ? `${deInteger.format(scenario.renewableShare)} %` : "– %");
    text($("#certificate-preview-renewables-page1"), scenario && scenario.renewableShare > 0 ? "Umweltwärme und Photovoltaik" : "keine Angabe");
    text($("#certificate-preview-issuer"), issuer || "noch offen");
    text($("#certificate-preview-demand-check"), certificateType === "Bedarfsausweis" ? "☑" : "☐");
    text($("#certificate-preview-consumption-check"), certificateType === "Verbrauchsausweis" ? "☑" : "☐");
    const marker = $("#certificate-scale-marker");
    if (marker) marker.style.setProperty("--marker", `${Math.max(1, Math.min(98, finalEnergy / 250 * 100))}%`);
    for (const [id, complete] of [["certificate-reason-check", reasonComplete], ["certificate-issuer-check", issuerComplete]]) {
      const row = $(`#${id}`);
      row?.classList.toggle("done", complete);
      text($("span", row), complete ? "✓" : "!");
      const detail = $("small", row);
      if (id === "certificate-reason-check") text(detail, complete ? $("#certificate-reason").value : "noch auswählen");
      if (id === "certificate-issuer-check") text(detail, complete ? "eingetragen · fachliche Bestätigung folgt" : "noch eintragen und fachlich bestätigen");
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

  const fundingSources = {
    kfn: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Klimafreundlicher-Neubau-Wohngeb%C3%A4ude-%28297-298%29/",
    knn: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Klimafreundlicher-Neubau-im-Niedrigpreissegment-%28296%29/",
    wef: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentum-f%C3%BCr-Familien-%28300%29/",
    home: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Neubau/F%C3%B6rderprodukte/Wohneigentumsprogramm-%28124%29/",
    renovation: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestehende-Immobilie/F%C3%B6rderprodukte/Bundesf%C3%B6rderung-f%C3%BCr-effiziente-Geb%C3%A4ude-Wohngeb%C3%A4ude-Kredit-%28261-262%29/",
    heating: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestehende-Immobilie/F%C3%B6rderprodukte/Heizungsf%C3%B6rderung-f%C3%BCr-Privatpersonen-Wohngeb%C3%A4ude-%28458%29/",
    supplement: "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestehende-Immobilie/F%C3%B6rderprodukte/Einzelma%C3%9Fnahmen-Erg%C3%A4nzungskredit-Wohngeb%C3%A4ude-%28358-359%29/",
    bafa: "https://www.energiewechsel.de/KAENEF/Redaktion/DE/Foerderprogramme/beg-em-privat.html",
  };

  function clampNumber(value, minimum, maximum) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return minimum;
    return Math.max(minimum, Math.min(maximum, parsed));
  }

  function familyCredit(children, qng) {
    if (children >= 5) return qng ? 270000 : 220000;
    if (children >= 3) return qng ? 250000 : 200000;
    return qng ? 220000 : 170000;
  }

  function familyIncomeLimit(children) {
    return children <= 1 ? 90000 : 90000 + ((children - 1) * 10000);
  }

  function heatingEligibleCosts(units) {
    if (units <= 1) return 28000;
    return 28000 + (Math.min(units, 6) - 1) * 15000 + Math.max(0, units - 6) * 8000;
  }

  function efficiencyEligibleCosts(units, hasIsfp) {
    if (hasIsfp) return 60000 + (Math.min(units, 6) - 1) * 30000 + Math.max(0, units - 6) * 15000;
    return 30000 + (Math.min(units, 6) - 1) * 15000 + Math.max(0, units - 6) * 8000;
  }

  function fundingGoalLabel() {
    if (state.funding.projectType === "renovation" && state.goal.standard === "eh40_qng") return "Effizienzhaus 40 NH-Klasse";
    return goalProfiles[state.goal.standard].label;
  }

  function fundingPrograms() {
    const data = state.funding;
    const units = Math.max(1, data.units);
    const costs = Math.max(0, data.costs);
    const qng = state.goal.standard === "eh40_qng";
    const reachesEfficiencyTarget = state.goal.standard !== "geg";

    if (data.projectType === "new_build") {
      const kfnCredit = Math.min(costs, units * (qng ? 150000 : 100000));
      const childrenEligible = data.selfUse && data.children > 0 && data.income <= familyIncomeLimit(data.children);
      const wefCredit = Math.min(costs, familyCredit(data.children, qng));
      return [
        {
          number: units === 1 && data.selfUse ? "KfW 297" : "KfW 298",
          name: "Klimafreundlicher Neubau",
          description: qng ? "EH 40 mit QNG-Zertifikat und erhöhtem Kreditrahmen." : "Förderkredit für EH 55 oder klimafreundliche Wohngebäude.",
          credit: reachesEfficiencyTarget ? kfnCredit : 0,
          grant: 0,
          status: reachesEfficiencyTarget ? "Passend" : "Ziel zu niedrig",
          statusKind: reachesEfficiencyTarget ? "ok" : "open",
          eligible: reachesEfficiencyTarget,
          recommended: reachesEfficiencyTarget,
          requirements: [
            { text: qng ? "EH 40 und QNG-Nachhaltigkeitszertifikat" : "EH 55 oder klimafreundliches Wohngebäude", open: !reachesEfficiencyTarget },
            { text: "Lebenszyklus-CO₂ nachweisen", open: true },
            { text: "keine Heizung mit Öl, Gas oder Biomasse", open: true },
          ],
          source: fundingSources.kfn,
        },
        {
          number: "KfW 296",
          name: "Klimafreundlicher Neubau im Niedrigpreissegment",
          description: "Kredit für flächen- und kosteneffizienten Neubau ab EH 55.",
          credit: reachesEfficiencyTarget ? Math.min(costs, units * 100000) : 0,
          grant: 0,
          status: reachesEfficiencyTarget ? "Zusatzprüfung" : "Ziel zu niedrig",
          statusKind: "check",
          eligible: reachesEfficiencyTarget,
          requirements: [
            { text: "mindestens EH 55", open: !reachesEfficiencyTarget },
            { text: "Flächenoptimierung nachweisen", open: true },
            { text: "Lebenszykluskosten-Grenzwert einhalten", open: true },
          ],
          source: fundingSources.knn,
        },
        {
          number: "KfW 300",
          name: "Wohneigentum für Familien",
          description: "Besonders günstiger Kredit für selbstnutzende Familien mit Kindern.",
          credit: childrenEligible && reachesEfficiencyTarget ? wefCredit : 0,
          grant: 0,
          status: childrenEligible && reachesEfficiencyTarget ? "Passend" : "Voraussetzungen offen",
          statusKind: childrenEligible && reachesEfficiencyTarget ? "ok" : "open",
          eligible: childrenEligible && reachesEfficiencyTarget,
          requirements: [
            { text: "mindestens ein Kind unter 18", open: data.children < 1 },
            { text: `Einkommensgrenze ${euro.format(familyIncomeLimit(Math.max(1, data.children)))}/a`, open: data.income > familyIncomeLimit(Math.max(1, data.children)) },
            { text: "einzige Immobilie und selbst genutzt", open: !data.selfUse },
          ],
          source: fundingSources.wef,
        },
        {
          number: "KfW 124",
          name: "Wohneigentumsprogramm",
          description: "Ergänzender Förderkredit für selbst genutztes Wohneigentum.",
          credit: data.selfUse ? Math.min(costs, 100000) : 0,
          grant: 0,
          status: data.selfUse ? "Kombinierbar" : "Nicht selbst genutzt",
          statusKind: data.selfUse ? "ok" : "open",
          eligible: data.selfUse,
          requirements: [
            { text: "Selbstnutzung", open: !data.selfUse },
            { text: "mit KfW 297/298 oder 300 kombinierbar", open: false },
          ],
          source: fundingSources.home,
        },
      ];
    }

    if (data.projectType === "renovation") {
      const baseRates = { eh55: 5, eh40: 10, eh40_qng: 15 };
      const baseRate = baseRates[state.goal.standard] || 0;
      const serialAllowed = ["eh55", "eh40", "eh40_qng"].includes(state.goal.standard);
      const rate = Math.min(40, baseRate + (data.wpb ? 10 : 0) + (data.serial && serialAllowed ? 15 : 0));
      const credit = Math.min(costs, units * 150000);
      return [
        {
          number: "KfW 261",
          name: "Wohngebäude · Effizienzhaus-Sanierung",
          description: "Förderkredit mit Tilgungszuschuss für die Komplettsanierung.",
          credit: baseRate ? credit : 0,
          grant: baseRate ? credit * rate / 100 : 0,
          status: baseRate ? `${rate} % Tilgungszuschuss` : "EH-Ziel wählen",
          statusKind: baseRate ? "ok" : "open",
          eligible: Boolean(baseRate),
          recommended: Boolean(baseRate),
          requirements: [
            { text: `${fundingGoalLabel()} als Zielstandard`, open: !baseRate },
            { text: data.wpb ? "10 %-Punkte WPB-Bonus berücksichtigt" : "WPB-Bonus optional", open: !data.wpb },
            { text: data.serial ? "15 %-Punkte serielles Sanieren berücksichtigt" : "Serielles Sanieren optional", open: !data.serial },
          ],
          source: fundingSources.renovation,
        },
        {
          number: "KfW 261 · Baubegleitung",
          name: "Fachplanung und Baubegleitung",
          description: "Zusätzlicher Kreditbetrag mit 50 % Tilgungszuschuss für die Fachbegleitung.",
          credit: units <= 2 ? 10000 : Math.min(40000, units * 4000),
          grant: units <= 2 ? 5000 : Math.min(20000, units * 2000),
          status: "Ergänzend",
          statusKind: "check",
          eligible: Boolean(baseRate),
          requirements: [{ text: "Energieeffizienz-Expert:in einbinden", open: true }],
          source: fundingSources.renovation,
        },
      ];
    }

    const efficiencyCap = efficiencyEligibleCosts(units, data.isfp);
    const bafaRate = data.isfp ? 20 : 15;
    const adjustedIncome = data.income - (data.children > 0 ? 10000 : 0);
    const incomeBonus = !data.selfUse ? 0 : adjustedIncome <= 30000 ? 40 : adjustedIncome <= 40000 ? 30 : adjustedIncome <= 50000 ? 10 : 0;
    const heatingRateCap = adjustedIncome <= 30000 && data.selfUse ? 80 : 70;
    const heatingRate = Math.min(heatingRateCap, 30 + (data.selfUse && data.oldHeating ? 16 : 0) + incomeBonus);
    const heatingCosts = Math.min(costs, heatingEligibleCosts(units));
    return [
      {
        number: "BAFA · BEG EM",
        name: "Gebäudehülle & Anlagentechnik",
        description: "Investitionszuschuss für Dämmung, Fenster, Lüftung und Heizungsoptimierung.",
        credit: 0,
        grant: Math.min(costs, efficiencyCap) * bafaRate / 100,
        status: `${bafaRate} % Zuschuss`,
        statusKind: "ok",
        eligible: true,
        recommended: true,
        requirements: [
          { text: "15 % Grundförderung", open: false },
          { text: data.isfp ? "5 %-Punkte iSFP-Bonus berücksichtigt" : "iSFP-Bonus nicht berücksichtigt", open: !data.isfp },
          { text: `Ausgabenlimit ${euro.format(efficiencyCap)}`, open: false },
        ],
        source: fundingSources.bafa,
      },
      {
        number: "KfW 458",
        name: "Heizungsförderung für Privatpersonen",
        description: "Zuschuss für eine neue klimafreundliche Heizung oder einen Netzanschluss.",
        credit: 0,
        grant: heatingCosts * heatingRate / 100,
        status: `${heatingRate} % Zuschuss`,
        statusKind: data.selfUse ? "ok" : "check",
        eligible: true,
        requirements: [
          { text: "30 % Grundförderung", open: false },
          { text: data.selfUse && data.oldHeating ? "16 %-Punkte Klimageschwindigkeitsbonus" : "Klimageschwindigkeitsbonus offen", open: !(data.selfUse && data.oldHeating) },
          { text: incomeBonus ? `${incomeBonus} %-Punkte Einkommensbonus` : "kein Einkommensbonus", open: !incomeBonus },
        ],
        source: fundingSources.heating,
      },
      {
        number: data.selfUse && data.income <= 90000 ? "KfW 358" : "KfW 359",
        name: "Ergänzungskredit",
        description: "Finanziert bereits bezuschusste Einzelmaßnahmen nach der Zuschusszusage.",
        credit: Math.min(costs, units * 120000),
        grant: 0,
        status: "Nach Zuschusszusage",
        statusKind: "check",
        eligible: true,
        requirements: [
          { text: "KfW- oder BAFA-Zuschusszusage liegt vor", open: true },
          { text: "Zusage höchstens 12 Monate alt", open: true },
        ],
        source: fundingSources.supplement,
      },
    ];
  }

  function fundingProcessSteps() {
    if (state.funding.projectType === "new_build") return [
      ["Expert:in beauftragen", "Energieeffizienz und ggf. Nachhaltigkeit"],
      ["Förderstufe planen", "EH-Ziel, LCA und ggf. QNG festlegen"],
      ["BzA erstellen", "Bestätigung zum Antrag vorbereiten"],
      ["Antrag stellen", "über Finanzierungspartner vor Baubeginn"],
      ["Vorhaben umsetzen", "erst nach Antrag bzw. mit Förderklausel"],
      ["Nachweise einreichen", "BnD, Zertifikate und Kreditabruf"],
    ];
    if (state.funding.projectType === "renovation") return [
      ["Bestand erfassen", "Bilanz, WPB und Zielstufe prüfen"],
      ["Expert:in beauftragen", "Sanierungskonzept und BzA"],
      ["Finanzierung klären", "KfW 261 und Boni festlegen"],
      ["Antrag stellen", "vor Lieferungs- und Leistungsbeginn"],
      ["Sanierung umsetzen", "Fachplanung dokumentiert Qualität"],
      ["BnD einreichen", "Tilgungszuschuss wird gutgeschrieben"],
    ];
    return [
      ["Maßnahme wählen", "Hülle, Technik oder Heizung abgrenzen"],
      ["Fachprüfung", "EEE oder Fachunternehmen einbinden"],
      ["Vertrag vorbereiten", "Förderklausel und Angebot sichern"],
      ["Zuschuss beantragen", "BAFA bzw. KfW vor Beginn"],
      ["Umsetzung", "Rechnungen und technische Nachweise sammeln"],
      ["BnD & Auszahlung", "danach ggf. KfW 358/359 abrechnen"],
    ];
  }

  function renderFundingProgram(program) {
    const card = document.createElement("article");
    card.className = `funding-program-card${program.recommended ? " is-recommended" : ""}${program.eligible ? "" : " is-ineligible"}`;
    const top = document.createElement("div"); top.className = "program-card-top";
    const number = document.createElement("span"); number.className = "program-number"; number.textContent = program.number;
    const status = document.createElement("span"); status.className = `program-status${program.statusKind === "check" ? " is-check" : program.statusKind === "open" ? " is-open" : ""}`; status.textContent = program.status;
    top.append(number, status);
    const title = document.createElement("h3"); title.textContent = program.name;
    const copy = document.createElement("p"); copy.textContent = program.description;
    const money = document.createElement("div"); money.className = "program-money";
    for (const [label, value] of [["Förderkredit", program.credit], ["Direkter Vorteil", program.grant]]) {
      const item = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = euro.format(value); item.append(label, strong); money.appendChild(item);
    }
    const requirements = document.createElement("ul"); requirements.className = "program-requirements";
    for (const requirement of program.requirements) {
      const item = document.createElement("li"); item.className = requirement.open ? "is-open" : ""; item.textContent = requirement.text; requirements.appendChild(item);
    }
    const source = document.createElement("a"); source.className = "program-source"; source.href = program.source; source.target = "_blank"; source.rel = "noreferrer"; source.textContent = "Offizielle Programminformation ↗";
    card.append(top, title, copy, money, requirements, source);
    return card;
  }

  function updateWorkflowNavigation() {
    const isIsfp = state.funding.deliverable === "isfp";
    const isfpStep = $("[data-isfp-step]");
    if (isfpStep) isfpStep.hidden = !isIsfp;
    const visibleSteps = $$(".module-rail [data-step]").filter((button) => !button.hidden);
    visibleSteps.forEach((button, index) => {
      button.dataset.step = String(index + 1);
      text($(".step-index", button), index + 1);
    });
    const total = visibleSteps.length;
    text($("#overview-step-label"), `Schritt 1 von ${total} · Projektstart`);
    const fundingIndex = visibleSteps.findIndex((button) => button.dataset.module === "funding") + 1;
    text($("#funding-step-label"), `Schritt ${fundingIndex} von ${total} · Förderprozess`);
  }

  function renderFunding() {
    const programs = fundingPrograms();
    const primary = programs.find((program) => program.recommended && program.eligible) || programs.find((program) => program.eligible) || programs[0];
    const deliverableLabels = { funding: "KfW-/BEG-Förderung", certificate: "Energieausweis", balance: "Wärmebilanz / GEG", isfp: "Individueller Sanierungsfahrplan" };
    const projectTypeLabels = { new_build: "Neubau", renovation: "Komplettsanierung", individual: "Einzelmaßnahmen" };
    const goalLabel = fundingGoalLabel();

    $$('[data-standard="eh40_qng"]').forEach((button) => {
      text($("strong", button), state.funding.projectType === "renovation" ? "EH 40 + NH" : "EH 40 + QNG");
      text($("small", button), state.funding.projectType === "renovation" ? "Nachhaltigkeits-Klasse" : "mit Zertifizierung");
    });

    $$('.deliverable-selector [data-deliverable]').forEach((button) => button.classList.toggle("is-active", button.dataset.deliverable === state.funding.deliverable));
    $$('[data-project-type]').forEach((button) => button.classList.toggle("is-active", button.dataset.projectType === state.funding.projectType));
    const outputModule = state.funding.deliverable === "isfp" ? "variants" : state.funding.deliverable;
    $$('[data-step]').forEach((button) => button.classList.toggle("is-goal-output", button.dataset.module === outputModule));
    text($("#goal-context-label"), `${deliverableLabels[state.funding.deliverable]} · ${projectTypeLabels[state.funding.projectType]} · ${goalLabel}`);
    document.body.dataset.deliverable = state.funding.deliverable;
    updateWorkflowNavigation();

    const fundingVisible = state.funding.deliverable === "funding";
    if ($("#funding-config")) $("#funding-config").hidden = !fundingVisible;
    if ($("#funding-impact-summary")) $("#funding-impact-summary").hidden = !fundingVisible;
    text($("#funding-primary-program"), `${primary.number} · ${primary.name}`);
    text($("#funding-primary-status"), primary.status);
    text($("#funding-credit-total"), euro.format(primary.credit));
    text($("#funding-grant-total"), euro.format(primary.grant));
    text($("#funding-grant-caption"), primary.grant > 0 ? "Tilgungs- bzw. Investitionszuschuss" : "kein Zuschuss; Zinsvorteil variabel");
    text($("#goal-funding-summary"), primary.number);

    text($("#funding-result-title"), `${projectTypeLabels[state.funding.projectType]} · ${goalLabel}`);
    text($("#funding-result-copy"), `${programs.filter((program) => program.eligible).length} von ${programs.length} geprüften Programmen passen grundsätzlich zu den aktuellen Angaben.`);
    text($("#funding-detail-credit"), euro.format(primary.credit));
    text($("#funding-detail-grant"), euro.format(primary.grant));
    text($("#funding-program-count"), `${programs.length} Programme · ${programs.filter((program) => program.eligible).length} passend`);

    const grid = $("#funding-program-grid"); clear(grid);
    programs.forEach((program) => grid?.appendChild(renderFundingProgram(program)));
    const process = $("#funding-process-list"); clear(process);
    for (const [title, copy] of fundingProcessSteps()) {
      const item = document.createElement("li"); const strong = document.createElement("strong"); strong.textContent = title; const detail = document.createElement("span"); detail.textContent = copy; item.append(strong, detail); process?.appendChild(item);
    }

    const renovationOnly = state.funding.projectType === "renovation";
    const individualOnly = state.funding.projectType === "individual";
    $("#funding-wpb")?.closest("label")?.classList.toggle("is-irrelevant", !renovationOnly);
    $("#funding-serial")?.closest("label")?.classList.toggle("is-irrelevant", !renovationOnly);
    $("#funding-isfp")?.closest("label")?.classList.toggle("is-irrelevant", !individualOnly);
    $("#funding-old-heating")?.closest("label")?.classList.toggle("is-irrelevant", !individualOnly);
  }

  function renderGoalControls() {
    const profile = goalProfiles[state.goal.standard];
    const displayLabel = fundingGoalLabel();
    $$('[data-standard]').forEach((button) => button.classList.toggle("is-active", button.dataset.standard === state.goal.standard));
    $$('[data-automation]').forEach((button) => button.classList.toggle("is-active", button.dataset.automation === state.goal.automation));
    if ($("#priority-slider")) $("#priority-slider").value = state.goal.priority;
    if ($("#renewable-slider")) $("#renewable-slider").value = state.goal.renewables;
    text($("#priority-output"), priorityLabel(state.goal.priority)); text($("#renewable-output"), `${state.goal.renewables} %`);
    text($("#project-target-badge"), `Ziel: ${displayLabel.replace("Effizienzhaus ", "EH ")}`); text($("#overview-goal-title"), displayLabel); text($("#settings-goal-title"), displayLabel); text($("#settings-target-primary"), de.format(profile.target));
    text($("#settings-wall-u"), `≤ ${de2.format(profile.wallU)} W/(m²K)`); text($("#settings-window-u"), `≤ ${de2.format(profile.windowU)} W/(m²K)`); text($("#settings-efficiency"), `JAZ ≥ ${de.format(profile.efficiency)}`); text($("#settings-recovery"), `≥ ${profile.recovery} %`);
    const pv = state.project ? state.project.systems.renewables.roof_potential_kwp * profile.pvFactor * state.goal.renewables / 75 : 0;
    text($("#settings-pv"), `${de.format(Math.min(state.project?.systems?.renewables?.roof_potential_kwp || pv, pv))} kWp`); text($("#goal-renewables-summary"), `${state.goal.renewables} %`); text($("#goal-priority-summary"), priorityLabel(state.goal.priority));
    renderFunding();
    if (state.calculation) renderGoalStatus();
  }

  function renderGoalStatus() {
    const profile = goalProfiles[state.goal.standard];
    const primary = state.calculation.metrics.primary_energy_kwh_m2a;
    const achieved = primary <= profile.target;
    const distance = primary - profile.target;
    text($("#goal-current-value"), de.format(primary)); text($("#goal-target-value"), de.format(profile.target));
    if ($("#goal-progress")) $("#goal-progress").value = Math.max(0, Math.min(100, profile.target / Math.max(primary, profile.target) * 100));
    const goalStatus = $("#goal-status"); goalStatus?.classList.toggle("is-missed", !achieved); text(goalStatus, achieved ? "Ziel erreicht" : `${de.format(distance)} über Ziel`);
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

  function syncFundingControls() {
    state.funding.units = Math.round(clampNumber($("#funding-units")?.value, 1, 500));
    state.funding.costs = Math.max(0, Math.min(1000000000, parseGermanMoney($("#funding-costs")?.value)));
    state.funding.income = clampNumber($("#funding-income")?.value, 0, 10000000);
    state.funding.children = Math.round(clampNumber($("#funding-children")?.value, 0, 10));
    state.funding.selfUse = Boolean($("#funding-self-use")?.checked);
    state.funding.isfp = Boolean($("#funding-isfp")?.checked);
    state.funding.wpb = Boolean($("#funding-wpb")?.checked);
    state.funding.serial = Boolean($("#funding-serial")?.checked);
    state.funding.oldHeating = Boolean($("#funding-old-heating")?.checked);
    renderFunding();
  }

  let calculationTimer = null;

  function scheduleCalculation() {
    window.clearTimeout(calculationTimer);
    calculationTimer = window.setTimeout(() => calculate({ silent: true }), 280);
  }

  function syncSystemControls() {
    const stage = $("#system-flow-stage");
    stage?.classList.remove("is-reconfiguring");
    if (stage) void stage.offsetWidth;
    stage?.classList.add("is-reconfiguring");
    window.setTimeout(() => stage?.classList.remove("is-reconfiguring"), 460);
    state.system.heatSource = $("#system-heat-source")?.value || state.system.heatSource;
    state.system.floorHeating = $("#system-floor-heating")?.value !== "no";
    state.system.distribution = state.system.floorHeating ? "floor" : ($("#system-distribution")?.value || "radiator_low");
    state.system.ventilation = $("#system-ventilation")?.value || state.system.ventilation;
    state.system.pv = clampNumber($("#system-pv")?.value, 0, 1000);
    state.system.storage = clampNumber($("#system-storage")?.value, 0, 1000);
    state.system.hotWater = $("#system-hot-water")?.value || state.system.hotWater;
    const source = heatSourceProfiles[state.system.heatSource];
    const distribution = distributionProfiles[state.system.distribution];
    const ventilation = ventilationProfiles[state.system.ventilation];
    state.energyInputs.heatingType = ["air_heat_pump", "ground_heat_pump"].includes(state.system.heatSource) ? "heat_pump" : state.system.heatSource;
    state.energyInputs.efficiency = source.efficiency / distribution.factor;
    state.energyInputs.recovery = ventilation.recovery;
    state.energyInputs.pv = state.system.pv;
    renderSystemPlanner();
    renderBalancePreview();
    scheduleCalculation();
  }

  function syncBalanceControls() {
    state.balanceSettings.method = $("#balance-method")?.value || "din18599";
    state.balanceSettings.climate = $("#balance-climate")?.value || "reference";
    state.balanceSettings.temperature = clampNumber($("#balance-temperature")?.value, 15, 30);
    state.balanceSettings.airChange = clampNumber($("#balance-air-change")?.value, 0.1, 2);
    state.balanceSettings.thermalBridge = clampNumber($("#balance-thermal-bridge")?.value, 0, 0.3);
    state.balanceSettings.internalGains = clampNumber($("#balance-internal-gains")?.value, 0, 20);
    const hotWaterValue = $("#balance-hot-water")?.value;
    state.balanceSettings.hotWater = hotWaterValue === "project" ? 12.5 : clampNumber(hotWaterValue, 0, 100);
    state.balanceSettings.pvSelfUse = clampNumber($("#balance-pv-self-use")?.value, 0, 100);
    state.balanceSettings.airtightness = Boolean($("#balance-airtightness")?.checked);
    state.balanceSettings.cooling = Boolean($("#balance-cooling")?.checked);
    state.balanceSettings.renewables = Boolean($("#balance-renewables")?.checked);
    renderSystemPlanner();
    renderBalancePreview();
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
      const deliverable = event.target.closest("[data-deliverable]");
      if (deliverable) {
        if (state.activeModule === "variants" && deliverable.dataset.deliverable !== "isfp") activateModule("overview");
        state.funding.deliverable = deliverable.dataset.deliverable;
        if (state.funding.deliverable === "isfp") {
          state.funding.projectType = "individual";
          state.funding.isfp = true;
          if ($("#funding-isfp")) $("#funding-isfp").checked = true;
        }
        renderGoalControls();
      }
      const projectType = event.target.closest("[data-project-type]");
      if (projectType) { state.funding.projectType = projectType.dataset.projectType; renderGoalControls(); }
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
      const certificatePage = event.target.closest("[data-certificate-page]");
      if (certificatePage) {
        const page = certificatePage.dataset.certificatePage;
        $$('[data-certificate-page]').forEach((button) => button.classList.toggle("is-active", button === certificatePage));
        $$('[data-certificate-preview-page]').forEach((preview) => {
          const active = preview.dataset.certificatePreviewPage === page;
          preview.classList.toggle("is-active", active);
          preview.hidden = !active;
        });
      }
    });
    $("#priority-slider")?.addEventListener("input", (event) => { state.goal.priority = Number(event.target.value); renderGoalControls(); });
    $("#renewable-slider")?.addEventListener("input", (event) => { state.goal.renewables = Number(event.target.value); renderGoalControls(); });
    $("#apply-goals-button")?.addEventListener("click", applyGoals);
    $("#apply-goals-advanced-button")?.addEventListener("click", applyGoals);
    for (const control of $$("#funding-config input, #funding-config select")) control.addEventListener(control.tagName === "SELECT" || control.type === "checkbox" ? "change" : "input", syncFundingControls);
    $("#funding-costs")?.addEventListener("blur", formatMoneyInput);
    for (const control of $$(".system-flow-stage select")) control.addEventListener("change", syncSystemControls);
    for (const control of $$(".balance-input-panel select, .balance-input-panel input")) control.addEventListener("change", syncBalanceControls);
    for (const control of $$(".certificate-input-panel input, .certificate-input-panel select")) control.addEventListener(control.tagName === "INPUT" ? "input" : "change", renderCertificatePreview);
    $("#recalculate-button")?.addEventListener("click", () => calculate());
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
