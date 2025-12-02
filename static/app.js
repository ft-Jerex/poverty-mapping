const map = L.map("map", {
  zoomControl: false,
});

L.control.zoom({ position: "topright" }).addTo(map);

let osmBase = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
});

let satelliteBase = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 19,
    attribution:
      'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
  },
);

osmBase.addTo(map);

const statusEl = document.getElementById("status");
const toggleButtons = Array.from(document.querySelectorAll(".model-toggle"));
const opacitySlider = document.getElementById("opacity-slider");
const opacityValue = document.getElementById("opacity-value");
const legendModelNameEl = document.getElementById("legend-model-name");

const legendContainer = document.querySelector(
  '.backdrop-blur.bg-slate-900\\/75.border.border-slate-700.rounded-lg.shadow-lg.px-4.py-3.w-52.text-xs.text-slate-200.space-y-2'
);
let quartileRanges = {};
const pieCanvas = document.getElementById("category-pie");
const pieSummaryEl = document.getElementById("category-pie-summary");
const barCanvas = document.getElementById("top5-bar");
const top5CategoryFilter = document.getElementById("top5-category-filter");
const brgySearchInput = document.getElementById("brgy-search-input");
const brgySearchBtn = document.getElementById("brgy-search-btn");
const brgySearchStatus = document.getElementById("brgy-search-status");
const brgyTilesCanvas = document.getElementById("brgy-tiles-chart");
const brgyDropdown = document.getElementById("brgy-dropdown");
const geospatialTabBtn = document.getElementById("tab-geospatial");
const statisticsTabBtn = document.getElementById("tab-statistics");
const geospatialPanel = document.getElementById("panel-geospatial");
const statisticsPanel = document.getElementById("panel-statistics");
const peopleTabBtn = document.getElementById("tab-people");
const peoplePanel = document.getElementById("panel-people");
const peopleListContainer = document.getElementById("people-list");
const censusToggle = document.getElementById("toggle-census-poverty");
const statsTopHhCanvas = document.getElementById("stats-top-hh");
const statsChildrenCanvas = document.getElementById("stats-children");
const statsChildrenTopNonAttendCanvas = document.getElementById(
  "stats-children-top-nonattend",
);
const statsWaterCanvas = document.getElementById("stats-water");
const statsEmploymentCanvas = document.getElementById("stats-employment");
const barangayFactorsSelect = document.getElementById("barangay-factors-select");
const statsBarangayFactorsCanvas = document.getElementById("stats-barangay-factors");
const statsCustomSection = document.getElementById("stats-custom-section");
const statsCustomContainer = document.getElementById("stats-custom-sheets");

const createUserBtn = document.getElementById("create-user-btn");
const logoutBtn = document.getElementById("logout-btn");
const refreshBtn = document.getElementById("refresh-btn");
const downloadMapBtn = document.getElementById("download-map-btn");
const resetViewBtn = document.getElementById("reset-view-btn");
const logoutModal = document.getElementById("logout-modal");
const logoutConfirmBtn = document.getElementById("logout-confirm-btn");
const logoutCancelBtn = document.getElementById("logout-cancel-btn");
const createUserModal = document.getElementById("create-user-modal");
const createUserUsernameInput = document.getElementById("create-user-username");
const createUserPasswordInput = document.getElementById("create-user-password");
const createUserErrorEl = document.getElementById("create-user-error");
const createUserCancelBtn = document.getElementById("create-user-cancel-btn");
const createUserSubmitBtn = document.getElementById("create-user-submit-btn");
const feedbackEmailInput = document.getElementById("feedback-email");
const feedbackBarangayInput = document.getElementById("feedback-barangay");
const feedbackMessageInput = document.getElementById("feedback-message");
const feedbackStatusEl = document.getElementById("feedback-status");
const feedbackSubmitBtn = document.getElementById("feedback-submit-btn");

// Refresh modal elements
const refreshModal = document.getElementById("refresh-modal");
const refreshStartDateInput = document.getElementById("refresh-start-date");
const refreshEndDateInput = document.getElementById("refresh-end-date");
const refreshErrorEl = document.getElementById("refresh-error");
const refreshWarningEl = document.getElementById("refresh-warning");
const refreshCancelBtn = document.getElementById("refresh-cancel-btn");
const refreshStartBtn = document.getElementById("refresh-start-btn");

const refreshProgressModal = document.getElementById("refresh-progress-modal");
const refreshProgressPhase = document.getElementById("refresh-progress-phase");
const refreshProgressPct = document.getElementById("refresh-progress-pct");
const refreshProgressBar = document.getElementById("refresh-progress-bar");
const refreshProgressMessage = document.getElementById("refresh-progress-message");
const refreshProgressError = document.getElementById("refresh-progress-error");
const refreshProgressCloseBtn = document.getElementById("refresh-progress-close-btn");

const refreshWarningModal = document.getElementById("refresh-warning-modal");
const refreshWarningText = document.getElementById("refresh-warning-text");
const suppressWarningCheckbox = document.getElementById("suppress-warning-checkbox");
const refreshWarningCancelBtn = document.getElementById("refresh-warning-cancel-btn");
const refreshWarningProceedBtn = document.getElementById("refresh-warning-proceed-btn");

// Refresh state
let refreshPollingInterval = null;
let pendingRefreshParams = null;

const modelLayers = {
  catboost: null,
  rf: null,
  cnn: null,
  census: null,
};

const modelGeojson = {
  catboost: null,
  rf: null,
  cnn: null,
};

let censusPovertyGeojson = null;

let boundaryLayer = null;
let barangayBoundaryLayer = null;
let barangayLabelLayer = null;
let barangayHighlightLayer = null;
let boundaryGeojson = null;

let activeModel = "catboost";
let currentOpacity = 0.8;

let pieChart = null;
let barChart = null;
let brgyTilesChart = null;
let statsTopHhChart = null;
let statsChildrenChart = null;
let statsChildrenTopNonAttendChart = null;
let statsWaterChart = null;
let statsEmploymentChart = null;
let statsBarangayFactorsChart = null;
let statsCustomCharts = [];
let latestStatistics = null;

function formatRange(min, max) {
  // Show as percent with 1 decimal
  return `${min.toFixed(1)}% – ${max.toFixed(1)}%`;
}

function updateLegend(modelKey) {
  if (!legendContainer || !quartileRanges[modelKey]) return;
  const ranges = quartileRanges[modelKey];
  // Find the legend color blocks
  const legendBlocks = legendContainer.querySelectorAll(".flex.items-center.gap-2");
  if (legendBlocks.length !== 4) return;
  for (let i = 0; i < 4; ++i) {
    const range = ranges[i];
    const labelSpan = legendBlocks[i].querySelector("span:nth-child(2)");
    if (labelSpan && range) {
      // Update label to include value range
      let labelText = "";
      if (range.label === "Not poor") {
        labelText = `Not poor (${formatRange(range.min * 100, range.max * 100)})`;
      } else if (range.label === "Lower-middle") {
        labelText = `Lower-middle (${formatRange(range.min * 100, range.max * 100)})`;
      } else if (range.label === "Upper-middle") {
        labelText = `Upper-middle (${formatRange(range.min * 100, range.max * 100)})`;
      } else if (range.label === "Poorest") {
        labelText = `Poorest (${formatRange(range.min * 100, range.max * 100)})`;
      }
      labelSpan.textContent = labelText;
    }
  }
}

function setStatus(text, tone = "info") {
  if (!statusEl) return;
  statusEl.textContent = text;
  if (tone === "error") {
    statusEl.classList.remove("border-slate-700", "text-slate-200");
    statusEl.classList.add("border-red-500/70", "text-red-200");
  }
}

function getQuartileColor(label) {
  switch (label) {
    case "Not poor":
      return "#22c55e";
    case "Lower-middle":
      return "#eab308";
    case "Upper-middle":
      return "#f97316";
    case "Poorest":
      return "#ef4444";
    default:
      return "#64748b";
  }
}

function formatPovertyPct(feature) {
  const raw = feature.properties?.poverty_pct;
  if (raw === null || raw === undefined || Number.isNaN(raw)) {
    return "N/A";
  }
  const val = Number(raw);
  if (!Number.isFinite(val)) return "N/A";
  return `${val.toFixed(1)}%`;
}

function updateLayerOpacity(opacity) {
  currentOpacity = opacity;

  // Update all layers
  Object.entries(modelLayers).forEach(([key, layer]) => {
    if (layer) {
      layer.eachLayer(function (featureLayer) {
        // Get current style
        const currentStyle = featureLayer.options.style || {};

        // Update the style with new opacity
        featureLayer.setStyle({
          fillOpacity: opacity,
        });
      });
    }
  });
}

function getQuartileField(modelKey) {
  return modelKey === "catboost"
    ? "poverty_quartile_catboost"
    : modelKey === "rf"
    ? "poverty_quartile_rf"
    : modelKey === "cnn"
    ? "poverty_quartile_cnn"
    : "poverty_quartile_census";
}

const CATEGORY_ORDER = ["Not poor", "Lower-middle", "Upper-middle", "Poorest"];

function getCategoryStats(modelKey) {
  const geo = modelGeojson[modelKey];
  const counts = {
    "Not poor": 0,
    "Lower-middle": 0,
    "Upper-middle": 0,
    Poorest: 0,
  };

  if (!geo || !Array.isArray(geo.features)) {
    return { labels: CATEGORY_ORDER, counts: CATEGORY_ORDER.map((l) => counts[l]) };
  }

  const qField = getQuartileField(modelKey);
  geo.features.forEach((f) => {
    const label = f.properties?.[qField];
    if (label && Object.prototype.hasOwnProperty.call(counts, label)) {
      counts[label] += 1;
    }
  });

  return {
    labels: CATEGORY_ORDER,
    counts: CATEGORY_ORDER.map((l) => counts[l] || 0),
  };
}

function getTop5Barangays(modelKey, categoryFilter = "all") {
  const geo = modelGeojson[modelKey];
  if (!geo || !Array.isArray(geo.features)) {
    return { labels: [], values: [], mode: categoryFilter === "all" ? "overall" : "category", category: categoryFilter };
  }

  const stats = new Map();
  const qField = getQuartileField(modelKey);

  geo.features.forEach((f) => {
    const props = f.properties || {};
    const rawPct = props.poverty_pct;
    const val = Number(rawPct);
    const name = props.barangay || "Unknown";
    const label = props[qField];

    let rec = stats.get(name);
    if (!rec) {
      rec = {
        sumPoverty: 0,
        count: 0,
        catCounts: {
          "Not poor": 0,
          "Lower-middle": 0,
          "Upper-middle": 0,
          Poorest: 0,
        },
      };
    }

    if (Number.isFinite(val)) {
      rec.sumPoverty += val;
    }
    rec.count += 1;
    if (label && Object.prototype.hasOwnProperty.call(rec.catCounts, label)) {
      rec.catCounts[label] += 1;
    }

    stats.set(name, rec);
  });

  let arr;

  if (categoryFilter === "all") {
    arr = Array.from(stats.entries()).map(([name, rec]) => ({
      name,
      value: rec.count ? rec.sumPoverty / rec.count : 0,
    }));
  } else {
    arr = Array.from(stats.entries()).map(([name, rec]) => {
      const total = rec.count || 1;
      const catCount = rec.catCounts[categoryFilter] || 0;
      return {
        name,
        value: (catCount / total) * 100,
      };
    });
  }

  arr.sort((a, b) => b.value - a.value);
  const top5 = arr.slice(0, 5);

  return {
    labels: top5.map((d) => d.name),
    values: top5.map((d) => Number(d.value.toFixed(1))),
    mode: categoryFilter === "all" ? "overall" : "category",
    category: categoryFilter,
  };
}

function updateCharts(modelKey) {
  if (typeof Chart === "undefined") return;

  // Category distribution pie chart
  if (pieCanvas) {
    const stats = getCategoryStats(modelKey);
    const total = stats.counts.reduce((sum, v) => sum + v, 0);

    if (pieChart) {
      pieChart.destroy();
      pieChart = null;
    }

    const ctxPie = pieCanvas.getContext("2d");
    const colors = stats.labels.map((l) => getQuartileColor(l));

    pieChart = new Chart(ctxPie, {
      type: "doughnut",
      data: {
        labels: stats.labels,
        datasets: [
          {
            data: stats.counts,
            backgroundColor: colors,
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              boxWidth: 10,
              font: { size: 10 },
            },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const label = ctx.label || "";
                const val = ctx.parsed;
                const pct = total ? (val / total) * 100 : 0;
                return `${label}: ${val.toLocaleString()} tiles (${pct.toFixed(1)}%)`;
              },
            },
          },
        },
      },
    });

    // Text summary with per-category tiles and percentages
    if (pieSummaryEl) {
      if (!total) {
        pieSummaryEl.textContent = "No tiles available for this model.";
      } else {
        const lines = stats.labels.map((label, idx) => {
          const count = stats.counts[idx] || 0;
          const pct = total ? (count / total) * 100 : 0;
          return `<div>${label}: <span class="font-semibold">${count.toLocaleString()}</span> tiles (${pct.toFixed(1)}%)</div>`;
        });
        pieSummaryEl.innerHTML = [
          `<div class="font-semibold mb-1">${total.toLocaleString()} tiles total</div>`,
          ...lines,
        ].join("");
      }
    }
  }

  // Top 5 barangays bar chart
  if (barCanvas) {
    const categoryFilter = top5CategoryFilter ? top5CategoryFilter.value || "all" : "all";
    const top5 = getTop5Barangays(modelKey, categoryFilter);

    if (barChart) {
      barChart.destroy();
      barChart = null;
    }

    const ctxBar = barCanvas.getContext("2d");
    const barColor = top5.mode === "overall" ? "#0ea5e9" : getQuartileColor(top5.category);

    barChart = new Chart(ctxBar, {
      type: "bar",
      data: {
        labels: top5.labels,
        datasets: [
          {
            data: top5.values,
            backgroundColor: barColor,
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: "#1e293b" },
          },
          x: {
            ticks: {
              color: "#cbd5f5",
              font: { size: 9 },
            },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const val = ctx.parsed.y;
                if (top5.mode === "overall") {
                  return `${val.toFixed(1)}% average predicted poverty`;
                }
                return `${val.toFixed(1)}% of tiles in ${top5.category} category`;
              },
            },
          },
        },
      },
    });
  }
}

function getBarangayCategoryStats(modelKey, barangayName) {
  const geo = modelGeojson[modelKey];
  if (!geo || !Array.isArray(geo.features)) {
    return { labels: CATEGORY_ORDER, counts: CATEGORY_ORDER.map(() => 0) };
  }

  const target = (barangayName || "").toString().toLowerCase();
  const counts = {
    "Not poor": 0,
    "Lower-middle": 0,
    "Upper-middle": 0,
    Poorest: 0,
  };
  let total = 0;

  const qField = getQuartileField(modelKey);
  geo.features.forEach((f) => {
    const props = f.properties || {};
    const brgy = (props.barangay || "").toString().toLowerCase();
    if (!target || brgy !== target) return;
    const label = props[qField];
    if (label && Object.prototype.hasOwnProperty.call(counts, label)) {
      counts[label] += 1;
      total += 1;
    }
  });

  return {
    labels: CATEGORY_ORDER,
    counts: CATEGORY_ORDER.map((l) => counts[l] || 0),
    total,
  };
}

function highlightBarangayOnMap(barangayName) {
  if (!boundaryGeojson || !Array.isArray(boundaryGeojson.features)) return false;

  const term = (barangayName || "").toString().toLowerCase();
  if (!term) return false;

  const matches = boundaryGeojson.features.filter((f) => {
    const props = f.properties || {};
    const name = (props.barangay || props.adm4_en || "").toString().toLowerCase();
    return name === term;
  });

  if (!matches.length) {
    return false;
  }

  if (barangayHighlightLayer && map.hasLayer(barangayHighlightLayer)) {
    map.removeLayer(barangayHighlightLayer);
  }

  const feature = matches[0];
  barangayHighlightLayer = L.geoJSON(feature, {
    style: {
      color: "#38bdf8",
      weight: 3,
      fillOpacity: 0,
    },
  }).addTo(map);

  try {
    map.fitBounds(barangayHighlightLayer.getBounds(), { padding: [30, 30] });
  } catch (e) {
    console.error("Error fitting bounds to barangay highlight:", e);
  }

  return true;
}

function updateBarangayTilesChart(modelKey, barangayName) {
  if (!brgyTilesCanvas || typeof Chart === "undefined") return;

  const stats = getBarangayCategoryStats(modelKey, barangayName);

  if (!stats.total) {
    if (brgyTilesChart) {
      brgyTilesChart.destroy();
      brgyTilesChart = null;
    }
    if (brgySearchStatus) {
      brgySearchStatus.textContent = "No tiles found for this barangay in the selected model.";
    }
    return;
  }

  const percents = stats.counts.map((c) => (stats.total ? (c / stats.total) * 100 : 0));

  if (brgyTilesChart) brgyTilesChart.destroy();
  brgyTilesChart = new Chart(brgyTilesCanvas.getContext("2d"), {
    type: "bar",
    data: {
      labels: stats.labels,
      datasets: [
        {
          data: percents.map((v) => Number(v.toFixed(1))),
          backgroundColor: stats.labels.map((l) => getQuartileColor(l)),
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: {
            callback: (val) => `${val}%`,
          },
          grid: {
            color: "#1e293b",
          },
        },
        x: {
          ticks: {
            color: "#cbd5f5",
            font: {
              size: 10,
            },
          },
          grid: {
            display: false,
          },
        },
      },
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.parsed.y.toFixed(1)}% of tiles`,
          },
        },
      },
    },
  });

  if (brgySearchStatus) {
    const summary = stats.labels
      .map((label, i) => `${label}: ${percents[i].toFixed(1)}%`)
      .join(" • ");
    brgySearchStatus.textContent = `${barangayName}  ${stats.total} tiles  ${summary}`;
  }
}

function updateBarangayFactorsChart(selectedName) {
  if (!statsBarangayFactorsCanvas || typeof Chart === "undefined") return;
  if (!latestStatistics || !latestStatistics.barangay_factors) return;

  const name = (selectedName || "").toString().trim();
  if (!name) {
    if (statsBarangayFactorsChart) {
      statsBarangayFactorsChart.destroy();
      statsBarangayFactorsChart = null;
    }
    return;
  }

  const rec = latestStatistics.barangay_factors[name];
  if (!rec) {
    if (statsBarangayFactorsChart) {
      statsBarangayFactorsChart.destroy();
      statsBarangayFactorsChart = null;
    }
    return;
  }

  const labels = [
    "Poor households",
    "Poor children not attending",
    "Households using unsafe water",
    "Poor workers in vulnerable jobs",
  ];

  const rawValues = [
    rec.poverty_households_pct,
    rec.children_not_attending_pct,
    rec.unsafe_water_households_pct,
    rec.vulnerable_jobs_pct,
  ];

  const dataValues = rawValues.map((v) =>
    v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v),
  );

  if (statsBarangayFactorsChart) statsBarangayFactorsChart.destroy();

  const ctx = statsBarangayFactorsCanvas.getContext("2d");
  statsBarangayFactorsChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          data: dataValues,
          backgroundColor: ["#ef4444", "#eab308", "#0ea5e9", "#a855f7"],
          borderRadius: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          grid: { color: "#1e293b" },
          ticks: {
            callback: (val) => `${val}%`,
          },
        },
        x: {
          ticks: {
            color: "#cbd5f5",
            font: { size: 9 },
          },
          grid: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const idx = ctx.dataIndex;
              const val = ctx.parsed.y;
              if (val === null || val === undefined || Number.isNaN(val)) {
                return `${labels[idx]}: no data`;
              }

              const base = `${val.toFixed(1)}%`;

              if (idx === 0) {
                const hh = rec.poor_households;
                const totalHh = rec.total_households;
                if (hh != null && totalHh != null) {
                  return `${labels[idx]}: ${base} of households (${hh.toLocaleString()} of ${totalHh.toLocaleString()})`;
                }
                return `${labels[idx]}: ${base} of households`;
              }

              if (idx === 1) {
                const notAtt = rec.children_not_attending;
                const totalChildren = rec.total_poor_children;
                if (notAtt != null && totalChildren != null) {
                  return `${labels[idx]}: ${base} of poor children (${notAtt.toLocaleString()} of ${totalChildren.toLocaleString()})`;
                }
                return `${labels[idx]}: ${base} of poor children`;
              }

              if (idx === 2) {
                const unsafe = rec.unsafe_water_households;
                const totalWs = rec.total_water_households;
                if (unsafe != null && totalWs != null) {
                  return `${labels[idx]}: ${base} of water-using households (${unsafe.toLocaleString()} of ${totalWs.toLocaleString()})`;
                }
                return `${labels[idx]}: ${base} of water-using households`;
              }

              if (idx === 3) {
                const vuln = rec.vulnerable_jobs_employed;
                const totalEmp = rec.total_poor_employed;
                if (vuln != null && totalEmp != null) {
                  return `${labels[idx]}: ${base} of poor workers (${vuln.toLocaleString()} of ${totalEmp.toLocaleString()})`;
                }
                return `${labels[idx]}: ${base} of poor workers`;
              }

              return `${labels[idx]}: ${base}`;
            },
          },
        },
      },
    },
  });
}

function createModelLayer(modelKey, geojson) {
  const layer = L.geoJSON(geojson, {
    style: function (feature) {
      const qField = getQuartileField(modelKey);
      const label = feature.properties?.[qField];
      const color = getQuartileColor(label);
      return {
        color: "#020617",
        weight: 0.2,
        opacity: 0.4,
        fillColor: color,
        fillOpacity: currentOpacity,
      };
    },
    onEachFeature: function (feature, featureLayer) {
      const brgy = feature.properties?.barangay || "Unknown";
      const pct = formatPovertyPct(feature);
      const qField = getQuartileField(modelKey);
      const cat = feature.properties?.[qField] || "N/A";

      const title =
        modelKey === "catboost"
          ? "CatBoost"
          : modelKey === "rf"
          ? "Random Forest"
          : modelKey === "cnn"
          ? "CNN"
          : "Census Poverty";

      const html = `
        <div class="text-xs" style="color: #ffffff;">
          <div class="font-semibold mb-1" style="color: #ffffff;">${title} grid cell</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Barangay:</span> ${brgy}</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Predicted poverty:</span> ${pct}</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Category:</span> ${cat}</div>
        </div>
      `;

      featureLayer.bindTooltip(html, {
        sticky: true,
        opacity: 0.95,
        direction: "top",
        className: "bg-slate-900/90 border border-slate-600 rounded-md px-3 py-2 shadow-md",
      });

      // Store the original opacity for this feature
      featureLayer._baseOpacity = currentOpacity;

      featureLayer.on({
        mouseover: function (e) {
          const l = e.target;
          l._baseOpacity = currentOpacity;
          l.setStyle({
            weight: 1.2,
            opacity: 0.9,
            fillOpacity: Math.min(currentOpacity + 0.15, 1),
          });
        },
        mouseout: function (e) {
          const l = e.target;
          l.setStyle({
            weight: 0.2,
            opacity: 0.4,
            fillOpacity: l._baseOpacity || currentOpacity,
          });
        },
      });
    },
  });

  return layer;
}

function createCensusPovertyLayer(geojson) {
  const layer = L.geoJSON(geojson, {
    style: function (feature) {
      const label = feature.properties?.poverty_quartile_census;
      const color = getQuartileColor(label);
      return {
        color: "#020617",
        weight: 1,
        opacity: 0.7,
        fillColor: color,
        fillOpacity: 0.35,
      };
    },
    onEachFeature: function (feature, featureLayer) {
      const props = feature.properties || {};
      const brgy = props.barangay || "Unknown";
      const totalHh = props.total_households ?? "N/A";
      const poorHh = props.poor_households ?? "N/A";
      const mag = props.poverty_magnitude;
      const magPct =
        mag === null || mag === undefined || Number.isNaN(Number(mag))
          ? "N/A"
          : `${(Number(mag) * 100).toFixed(1)}%`;

      const html = `
        <div class="text-xs" style="color: #ffffff;">
          <div class="font-semibold mb-1" style="color: #ffffff;">Census poverty (households)</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Barangay:</span> ${brgy}</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Total households:</span> ${totalHh}</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Poor households:</span> ${poorHh}</div>
          <div style="color: #ffffff;"><span style="color: #cbd5e1;">Poverty magnitude:</span> ${magPct}</div>
        </div>
      `;

      featureLayer.bindTooltip(html, {
        sticky: true,
        opacity: 0.95,
        direction: "top",
        className: "bg-slate-900/90 border border-slate-600 rounded-md px-3 py-2 shadow-md",
      });
    },
  });

  return layer;
}

function clearAllLayers() {
  // Remove all model layers from map
  Object.values(modelLayers).forEach(layer => {
    if (layer && map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
  });
  
  // Remove boundary layers
  if (boundaryLayer && map.hasLayer(boundaryLayer)) {
    map.removeLayer(boundaryLayer);
  }
  if (barangayBoundaryLayer && map.hasLayer(barangayBoundaryLayer)) {
    map.removeLayer(barangayBoundaryLayer);
  }
  if (barangayLabelLayer && map.hasLayer(barangayLabelLayer)) {
    map.removeLayer(barangayLabelLayer);
  }
  if (barangayHighlightLayer && map.hasLayer(barangayHighlightLayer)) {
    map.removeLayer(barangayHighlightLayer);
  }
  
  // Reset layer references
  modelLayers.catboost = null;
  modelLayers.rf = null;
  modelLayers.cnn = null;
  modelLayers.census = null;
  boundaryLayer = null;
  barangayBoundaryLayer = null;
  barangayLabelLayer = null;
  barangayHighlightLayer = null;
}

function activateModel(modelKey) {
  Object.entries(modelLayers).forEach(([key, layer]) => {
    if (!layer) return;
    if (map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
    if (key === modelKey) {
      layer.addTo(map);
    }
  });

  activeModel = modelKey;

  if (boundaryLayer && map.hasLayer(boundaryLayer)) {
    boundaryLayer.bringToFront();
  }
  if (barangayHighlightLayer && map.hasLayer(barangayHighlightLayer)) {
    barangayHighlightLayer.bringToFront();
  }

  if (legendModelNameEl) {
    legendModelNameEl.textContent =
      modelKey === "catboost"
        ? "CatBoost"
        : modelKey === "rf"
        ? "Random Forest"
        : modelKey === "cnn"
        ? "CNN"
        : "Census Poverty";
  }
  updateLegend(modelKey);

  toggleButtons.forEach((btn) => {
    const key = btn.getAttribute("data-model");
    if (key === modelKey) {
      btn.classList.remove("bg-slate-800", "text-slate-100");
      btn.classList.add("bg-emerald-500", "text-slate-900", "font-semibold");
    } else {
      btn.classList.remove("bg-emerald-500", "text-slate-900", "font-semibold");
      btn.classList.add("bg-slate-800", "text-slate-100");
    }
  });

  setStatus(
    `Showing ${
      modelKey === "catboost" ? "CatBoost" : modelKey === "rf" ? "Random Forest" : "CNN"
    } predictions • Hover a grid to see barangay and poverty details`,
  );

  updateCharts(modelKey);

  if (brgySearchInput && brgySearchInput.value.trim()) {
    updateBarangayTilesChart(modelKey, brgySearchInput.value.trim());
  }
}

function setupToggleButtons() {
  toggleButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const modelKey = btn.getAttribute("data-model");
      if (!modelKey || !modelLayers[modelKey]) return;
      activateModel(modelKey);
    });
  });
}

function setupOpacitySlider() {
  if (!opacitySlider || !opacityValue) {
    console.error("Opacity slider elements not found!");
    return;
  }

  console.log("Setting up opacity slider...");

  opacitySlider.addEventListener("input", (e) => {
    const value = parseInt(e.target.value);
    const opacity = value / 100;

    console.log(`Opacity changed to: ${value}% (${opacity})`);

    currentOpacity = opacity;
    opacityValue.textContent = `${value}%`;
    updateLayerOpacity(opacity);
  });

  // Also listen for change event
  opacitySlider.addEventListener("change", (e) => {
    const value = parseInt(e.target.value);
    const opacity = value / 100;

    console.log(`Opacity finalized at: ${value}% (${opacity})`);

    currentOpacity = opacity;
    opacityValue.textContent = `${value}%`;
    updateLayerOpacity(opacity);
  });
}

function setupBarangayLayerToggles() {
  const borderToggle = document.getElementById("toggle-brgy-borders");
  const labelToggle = document.getElementById("toggle-brgy-labels");
  const satelliteToggle = document.getElementById("toggle-satellite");

  if (borderToggle) {
    borderToggle.addEventListener("change", (e) => {
      if (!boundaryLayer) return;
      if (e.target.checked) {
        boundaryLayer.addTo(map);
        boundaryLayer.bringToFront();
      } else if (map.hasLayer(boundaryLayer)) {
        map.removeLayer(boundaryLayer);
      }
    });
  }

  if (satelliteToggle) {
    // Apply initial state on load
    if (satelliteToggle.checked) {
      if (osmBase && map.hasLayer(osmBase)) {
        map.removeLayer(osmBase);
      }
      if (satelliteBase && !map.hasLayer(satelliteBase)) {
        satelliteBase.addTo(map);
      }
    }

    satelliteToggle.addEventListener("change", (e) => {
      const useSatellite = e.target.checked;
      if (useSatellite) {
        if (osmBase && map.hasLayer(osmBase)) {
          map.removeLayer(osmBase);
        }
        if (satelliteBase && !map.hasLayer(satelliteBase)) {
          satelliteBase.addTo(map);
        }
      } else {
        if (satelliteBase && map.hasLayer(satelliteBase)) {
          map.removeLayer(satelliteBase);
        }
        if (osmBase && !map.hasLayer(osmBase)) {
          osmBase.addTo(map);
        }
      }
    });
  }

  if (labelToggle) {
    labelToggle.addEventListener("change", (e) => {
      if (!barangayLabelLayer) return;
      if (e.target.checked) {
        barangayLabelLayer.addTo(map);
      } else if (map.hasLayer(barangayLabelLayer)) {
        map.removeLayer(barangayLabelLayer);
      }
    });
  }

  map.on("zoomend", () => {
    if (!barangayLabelLayer || !labelToggle || !labelToggle.checked) return;
    const z = map.getZoom();
    if (z < 12) {
      if (map.hasLayer(barangayLabelLayer)) {
        map.removeLayer(barangayLabelLayer);
      }
    } else if (!map.hasLayer(barangayLabelLayer)) {
      barangayLabelLayer.addTo(map);
    }
  });
}

function setupBarangaySearch() {
  if (!brgySearchInput || !brgySearchBtn) return;

  const runSearch = () => {
    const term = brgySearchInput.value.trim();

    if (!term) {
      if (brgySearchStatus) {
        brgySearchStatus.textContent = "Enter a barangay name to search.";
      }
      return;
    }

    const normalized = term;

    const highlighted = highlightBarangayOnMap(normalized);
    if (!highlighted) {
      if (brgySearchStatus) {
        brgySearchStatus.textContent = "No matching barangay found in the boundary layer.";
      }
      if (brgyTilesChart) {
        brgyTilesChart.destroy();
        brgyTilesChart = null;
      }
      return;
    }

    updateBarangayTilesChart(activeModel, normalized);
  };

  brgySearchBtn.addEventListener("click", runSearch);
  brgySearchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runSearch();
    }
  });

  if (brgyDropdown) {
    brgyDropdown.addEventListener("change", () => {
      const value = brgyDropdown.value;
      if (value) {
        brgySearchInput.value = value;
        runSearch();
      }
    });
  }
}

function setupTop5CategoryFilter() {
  if (!top5CategoryFilter) return;

  top5CategoryFilter.addEventListener("change", () => {
    updateCharts(activeModel);
  });
}

function getAllBarangayNamesFromBoundary() {
  if (!boundaryGeojson || !Array.isArray(boundaryGeojson.features)) return [];

  const seen = new Set();
  const names = [];

  boundaryGeojson.features.forEach((f) => {
    const props = f.properties || {};
    const rawName = props.barangay || props.adm4_en;
    if (!rawName) return;
    const name = rawName.toString().trim();
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    names.push(name);
  });

  names.sort((a, b) => a.localeCompare(b));
  return names;
}

function populateBarangayDropdown() {
  if (!brgyDropdown) return;

  const names = getAllBarangayNamesFromBoundary();
  brgyDropdown.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select barangay...";
  brgyDropdown.appendChild(placeholder);

  names.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    brgyDropdown.appendChild(opt);
  });
}

function setActiveTab(tab) {
  if (!geospatialPanel || !statisticsPanel || !geospatialTabBtn || !statisticsTabBtn) return;

  const isGeo = tab === "geospatial";
  const isStats = tab === "statistics";
  const isPeople = tab === "people";

  geospatialPanel.classList.toggle("hidden", !isGeo);
  statisticsPanel.classList.toggle("hidden", !isStats);
  if (peoplePanel) {
    peoplePanel.classList.toggle("hidden", !isPeople);
  }

  if (geospatialTabBtn) {
    if (isGeo) {
      geospatialTabBtn.classList.add("text-slate-100", "border-emerald-500");
      geospatialTabBtn.classList.remove("text-slate-400", "border-transparent");
    } else {
      geospatialTabBtn.classList.add("text-slate-400", "border-transparent");
      geospatialTabBtn.classList.remove("text-slate-100", "border-emerald-500");
    }
  }

  if (statisticsTabBtn) {
    if (isStats) {
      statisticsTabBtn.classList.add("text-slate-100", "border-emerald-500");
      statisticsTabBtn.classList.remove("text-slate-400", "border-transparent");
    } else {
      statisticsTabBtn.classList.add("text-slate-400", "border-transparent");
      statisticsTabBtn.classList.remove("text-slate-100", "border-emerald-500");
    }
  }

  if (peopleTabBtn) {
    if (isPeople) {
      peopleTabBtn.classList.add("text-slate-100", "border-emerald-500");
      peopleTabBtn.classList.remove("text-slate-400", "border-transparent");
    } else {
      peopleTabBtn.classList.add("text-slate-400", "border-transparent");
      peopleTabBtn.classList.remove("text-slate-100", "border-emerald-500");
    }
  }

  if (isPeople) {
    fetchPeopleMessages();
  }
}

function setupTabs() {
  if (!geospatialTabBtn || !statisticsTabBtn || !geospatialPanel || !statisticsPanel) return;

  setActiveTab("geospatial");

  geospatialTabBtn.addEventListener("click", () => setActiveTab("geospatial"));
  statisticsTabBtn.addEventListener("click", () => setActiveTab("statistics"));
  if (peopleTabBtn) {
    peopleTabBtn.addEventListener("click", () => setActiveTab("people"));
  }
}

function setupAdminControls() {
  if (logoutBtn && logoutModal) {
    logoutBtn.addEventListener("click", (e) => {
      e.preventDefault();
      logoutModal.classList.remove("hidden");
    });
  }

  if (logoutCancelBtn && logoutModal) {
    logoutCancelBtn.addEventListener("click", (e) => {
      e.preventDefault();
      logoutModal.classList.add("hidden");
    });
  }

  if (logoutConfirmBtn && logoutModal) {
    logoutConfirmBtn.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.href = "/logout";
    });
  }

  if (createUserBtn && createUserModal) {
    createUserBtn.addEventListener("click", (e) => {
      e.preventDefault();

      if (createUserUsernameInput) {
        createUserUsernameInput.value = "";
      }
      if (createUserPasswordInput) {
        createUserPasswordInput.value = "";
      }
      if (createUserErrorEl) {
        createUserErrorEl.textContent = "";
        createUserErrorEl.classList.add("hidden");
      }

      createUserModal.classList.remove("hidden");

      if (createUserUsernameInput) {
        createUserUsernameInput.focus();
      }
    });
  }

  if (createUserCancelBtn && createUserModal) {
    createUserCancelBtn.addEventListener("click", (e) => {
      e.preventDefault();
      createUserModal.classList.add("hidden");
    });
  }

  if (createUserSubmitBtn && createUserModal) {
    createUserSubmitBtn.addEventListener("click", async (e) => {
      e.preventDefault();

      const username = createUserUsernameInput
        ? createUserUsernameInput.value.trim()
        : "";
      const password = createUserPasswordInput ? createUserPasswordInput.value : "";

      if (!username || !password) {
        if (createUserErrorEl) {
          createUserErrorEl.textContent = "Username and password are required.";
          createUserErrorEl.classList.remove("hidden");
        }
        return;
      }

      if (createUserErrorEl) {
        createUserErrorEl.textContent = "";
        createUserErrorEl.classList.add("hidden");
      }

      try {
        const res = await fetch("/auth/register", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ username, password }),
        });

        let data = null;
        try {
          data = await res.json();
        } catch (err) {}

        if (!res.ok || !data || data.success === false) {
          const msg = data && data.error ? data.error : `Request failed with status ${res.status}`;
          if (createUserErrorEl) {
            createUserErrorEl.textContent = msg;
            createUserErrorEl.classList.remove("hidden");
          }
          return;
        }

        createUserModal.classList.add("hidden");
      } catch (err) {
        console.error("Error creating user:", err);
        if (createUserErrorEl) {
          createUserErrorEl.textContent = "Failed to create user. Check console for details.";
          createUserErrorEl.classList.remove("hidden");
        }
      }
    });
  }

  if (downloadMapBtn) {
    downloadMapBtn.addEventListener("click", (e) => {
      e.preventDefault();
      downloadMapImage();
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", (e) => {
      e.preventDefault();
      openRefreshModal();
    });
  }
}

function renderStatisticsCharts(stats) {
  if (!stats || typeof Chart === "undefined") return;

  latestStatistics = stats;

  // Admin-defined custom sheet visualizations (driven by Admin Data sheets)
  if (statsCustomSection && statsCustomContainer) {
    // Destroy any existing custom charts
    if (Array.isArray(statsCustomCharts)) {
      statsCustomCharts.forEach((ch) => {
        if (ch && typeof ch.destroy === "function") {
          ch.destroy();
        }
      });
    }
    statsCustomCharts = [];

    statsCustomContainer.innerHTML = "";

    const custom = Array.isArray(stats.custom_sheets) ? stats.custom_sheets : [];
    if (!custom.length) {
      statsCustomSection.classList.add("hidden");
    } else {
      statsCustomSection.classList.remove("hidden");

      custom.forEach((cfg, index) => {
        const title = cfg.sheet_name || cfg.safe_name || `Sheet ${index + 1}`;
        const chartId = `stats-custom-sheet-${index}`;

        const wrapper = document.createElement("div");
        wrapper.className = "mb-4 border border-slate-800 rounded-lg p-3 bg-slate-950/60";

        const h = document.createElement("h4");
        h.className = "text-xs font-semibold text-slate-200 mb-1";
        h.textContent = title;
        wrapper.appendChild(h);

        const p = document.createElement("p");
        p.className = "text-[11px] text-slate-400 mb-2";
        p.textContent =
          "Driven by the admin Data sheets quick visualization settings for this sheet.";
        wrapper.appendChild(p);

        const canvasWrapper = document.createElement("div");
        canvasWrapper.className = "h-40";
        const canvas = document.createElement("canvas");
        canvas.id = chartId;
        canvasWrapper.appendChild(canvas);
        wrapper.appendChild(canvasWrapper);

        statsCustomContainer.appendChild(wrapper);

        if (typeof Chart === "undefined") return;

        const labels = Array.isArray(cfg.x_labels) ? cfg.x_labels : [];
        const values = Array.isArray(cfg.y_values) ? cfg.y_values : [];
        const type = (cfg.chart_type || "bar").toLowerCase();

        const palette = [
          "#22c55e",
          "#0ea5e9",
          "#eab308",
          "#f97316",
          "#a855f7",
          "#ef4444",
          "#6366f1",
          "#ec4899",
        ];
        const colors = labels.map((_, i) => palette[i % palette.length]);

        const ctx = canvas.getContext("2d");
        const chart = new Chart(ctx, {
          type: type === "pie" ? "pie" : type,
          data: {
            labels,
            datasets: [
              {
                label: cfg.y_column || "Value",
                data: values,
                backgroundColor: type === "pie" ? colors : colors,
                borderColor: type === "pie" ? colors : colors,
                borderWidth: type === "line" ? 2 : 0,
                tension: 0.25,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            scales:
              type === "pie"
                ? {}
                : {
                    x: {
                      ticks: { color: "#cbd5f5", font: { size: 10 } },
                      grid: { display: false },
                    },
                    y: {
                      beginAtZero: true,
                      ticks: { color: "#cbd5f5", font: { size: 10 } },
                      grid: { color: "#1e293b" },
                    },
                  },
            plugins: {
              legend: { labels: { font: { size: 10 } } },
              tooltip: {
                callbacks: {
                  label: (ctx) => {
                    const val = ctx.parsed.y ?? ctx.parsed;
                    if (val === null || val === undefined || Number.isNaN(val)) {
                      return `${ctx.dataset.label}: no data`;
                    }
                    return `${ctx.dataset.label}: ${val}`;
                  },
                },
              },
            },
          },
        });

        statsCustomCharts.push(chart);
      });
    }
  }

  // Barangay factors (per-barangay profile)
  if (
    barangayFactorsSelect &&
    statsBarangayFactorsCanvas &&
    latestStatistics &&
    latestStatistics.barangay_list &&
    latestStatistics.barangay_factors
  ) {
    // Populate dropdown
    barangayFactorsSelect.innerHTML = "";

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select barangay...";
    barangayFactorsSelect.appendChild(placeholder);

    latestStatistics.barangay_list.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      barangayFactorsSelect.appendChild(opt);
    });

    barangayFactorsSelect.addEventListener("change", () => {
      const value = barangayFactorsSelect.value;
      updateBarangayFactorsChart(value);
    });
  }
}

async function downloadMapImage() {
  if (!downloadMapBtn || typeof html2canvas === "undefined") {
    return;
  }

  try {
    downloadMapBtn.disabled = true;
    setStatus("Preparing map image for download…");

    const mapEl = document.getElementById("map");
    if (!mapEl) {
      setStatus("Map element not found.", "error");
      return;
    }

    const canvas = await html2canvas(mapEl, {
      useCORS: true,
      logging: false,
      scale: 2,
      backgroundColor: "#020617",
    });

    const dataUrl = canvas.toDataURL("image/png");
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = "zamboanga_poverty_map.png";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    setStatus("Map image downloaded.");
  } catch (err) {
    console.error("Error downloading map image:", err);
    setStatus("Failed to download map image.", "error");
  } finally {
    if (downloadMapBtn) {
      downloadMapBtn.disabled = false;
    }
  }
}

// ============================================================================
// REFRESH WORKFLOW
// ============================================================================

function getDefaultDateRange() {
  const today = new Date();
  const endDate = today.toISOString().split('T')[0];
  const startDate = new Date(today);
  startDate.setFullYear(startDate.getFullYear() - 1);
  return {
    startDate: startDate.toISOString().split('T')[0],
    endDate: endDate
  };
}

function openRefreshModal() {
  if (!refreshModal) return;
  
  // Set default date range (1 year leading up to today)
  const { startDate, endDate } = getDefaultDateRange();
  if (refreshStartDateInput) refreshStartDateInput.value = startDate;
  if (refreshEndDateInput) refreshEndDateInput.value = endDate;
  
  // Set max date to today
  const today = new Date().toISOString().split('T')[0];
  if (refreshEndDateInput) refreshEndDateInput.max = today;
  if (refreshStartDateInput) refreshStartDateInput.max = today;
  
  // Clear any previous errors/warnings
  if (refreshErrorEl) {
    refreshErrorEl.textContent = '';
    refreshErrorEl.classList.add('hidden');
  }
  if (refreshWarningEl) {
    refreshWarningEl.textContent = '';
    refreshWarningEl.classList.add('hidden');
  }
  
  refreshModal.classList.remove('hidden');
  
  // Set up event listeners
  if (refreshCancelBtn) {
    refreshCancelBtn.onclick = () => refreshModal.classList.add('hidden');
  }
  if (refreshStartBtn) {
    refreshStartBtn.onclick = () => initiateRefresh();
  }
  
  // Validate date range on change
  if (refreshStartDateInput) {
    refreshStartDateInput.onchange = validateDateRange;
  }
  if (refreshEndDateInput) {
    refreshEndDateInput.onchange = validateDateRange;
  }
}

function validateDateRange() {
  if (!refreshStartDateInput || !refreshEndDateInput || !refreshErrorEl) return true;
  
  const startDate = new Date(refreshStartDateInput.value);
  const endDate = new Date(refreshEndDateInput.value);
  
  if (startDate >= endDate) {
    refreshErrorEl.textContent = 'Start date must be before end date.';
    refreshErrorEl.classList.remove('hidden');
    return false;
  }
  
  const daysDiff = Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24));
  if (daysDiff > 365) {
    refreshErrorEl.textContent = `Date range cannot exceed 365 days. Current: ${daysDiff} days.`;
    refreshErrorEl.classList.remove('hidden');
    return false;
  }
  
  refreshErrorEl.classList.add('hidden');
  return true;
}

async function initiateRefresh() {
  if (!validateDateRange()) return;
  
  const startDate = refreshStartDateInput?.value;
  const endDate = refreshEndDateInput?.value;
  
  // Store params for potential retry after warning
  pendingRefreshParams = { startDate, endDate, force: false };
  
  // First check for cooldown warning
  try {
    const checkRes = await fetch('/api/refresh/check');
    const checkData = await checkRes.json();
    
    if (checkData.should_warn) {
      // Close refresh modal, show warning modal
      if (refreshModal) refreshModal.classList.add('hidden');
      showRefreshWarningModal(checkData.days_since_refresh, checkData.cooldown_days);
      return;
    }
  } catch (err) {
    console.warn('Could not check refresh status:', err);
    // Continue with refresh anyway
  }
  
  // No warning needed, proceed with refresh
  startRefresh(startDate, endDate, false);
}

function showRefreshWarningModal(daysSinceRefresh, cooldownDays) {
  if (!refreshWarningModal) return;
  
  if (refreshWarningText) {
    refreshWarningText.textContent = 
      `A refresh was performed ${daysSinceRefresh} days ago (recommended: every ${cooldownDays} days). ` +
      `Are you sure you want to refresh again?`;
  }
  
  if (suppressWarningCheckbox) {
    suppressWarningCheckbox.checked = false;
  }
  
  refreshWarningModal.classList.remove('hidden');
  
  if (refreshWarningCancelBtn) {
    refreshWarningCancelBtn.onclick = () => {
      refreshWarningModal.classList.add('hidden');
      pendingRefreshParams = null;
    };
  }
  
  if (refreshWarningProceedBtn) {
    refreshWarningProceedBtn.onclick = async () => {
      // Save preference if checkbox is checked
      if (suppressWarningCheckbox?.checked) {
        try {
          await fetch('/api/refresh/suppress-warning', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ suppress: true })
          });
        } catch (err) {
          console.warn('Could not save preference:', err);
        }
      }
      
      refreshWarningModal.classList.add('hidden');
      
      if (pendingRefreshParams) {
        startRefresh(
          pendingRefreshParams.startDate,
          pendingRefreshParams.endDate,
          true // force=true to bypass cooldown
        );
      }
    };
  }
}

async function startRefresh(startDate, endDate, force) {
  // Hide other modals
  if (refreshModal) refreshModal.classList.add('hidden');
  if (refreshWarningModal) refreshWarningModal.classList.add('hidden');
  
  // Show progress modal
  if (refreshProgressModal) {
    refreshProgressModal.classList.remove('hidden');
    updateRefreshProgress('STARTING', 'Initiating refresh...', 0);
    if (refreshProgressCloseBtn) refreshProgressCloseBtn.classList.add('hidden');
    if (refreshProgressError) {
      refreshProgressError.textContent = '';
      refreshProgressError.classList.add('hidden');
    }
  }
  
  try {
    const res = await fetch('/api/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_date: startDate,
        end_date: endDate,
        force: force
      })
    });
    
    const data = await res.json();
    
    if (!res.ok) {
      if (data.error === 'cooldown_warning') {
        // Show warning modal
        if (refreshProgressModal) refreshProgressModal.classList.add('hidden');
        showRefreshWarningModal(data.days_since_refresh, data.cooldown_days);
        return;
      }
      
      throw new Error(data.error || `Refresh failed with status ${res.status}`);
    }
    
    if (!data.success) {
      throw new Error(data.error || 'Unknown refresh error');
    }
    
    // Start polling for progress
    startRefreshPolling();
    
  } catch (err) {
    console.error('Error starting refresh:', err);
    showRefreshError(err.message);
  }
}

function startRefreshPolling() {
  // Clear any existing polling
  if (refreshPollingInterval) {
    clearInterval(refreshPollingInterval);
  }
  
  // Poll every 2 seconds
  refreshPollingInterval = setInterval(async () => {
    try {
      const res = await fetch('/api/refresh/status');
      const data = await res.json();
      
      const phase = data.phase || 'UNKNOWN';
      const progress = data.progress || 0;
      const message = data.message || '';
      
      updateRefreshProgress(phase, message, progress);
      
      // Check if complete or errored
      if (phase === 'COMPLETED') {
        stopRefreshPolling();
        showRefreshComplete(message);
      } else if (phase === 'ERROR') {
        stopRefreshPolling();
        showRefreshError(data.error || message);
      }
      
    } catch (err) {
      console.error('Error polling refresh status:', err);
    }
  }, 2000);
}

function stopRefreshPolling() {
  if (refreshPollingInterval) {
    clearInterval(refreshPollingInterval);
    refreshPollingInterval = null;
  }
}

function updateRefreshProgress(phase, message, progress) {
  const phaseLabels = {
    'STARTED': 'Initializing',
    'STARTING': 'Initializing',
    'GEE_EXTRACTION': 'Extracting GEE Data',
    'GEE_EXTRACTION_DONE': 'GEE Data Complete',
    'GEE_SKIPPED': 'Using Cached Data',
    'PREPROCESSING': 'Preprocessing',
    'PREPROCESSING_DONE': 'Preprocessing Complete',
    'INFERENCE': 'Running Models',
    'INFERENCE_DONE': 'Models Complete',
    'MERGING': 'Merging Results',
    'MERGING_DONE': 'Merge Complete',
    'COPYING': 'Copying Files',
    'COPYING_DONE': 'Files Copied',
    'COMPLETED': 'Completed',
    'ERROR': 'Error'
  };
  
  const label = phaseLabels[phase] || phase;
  
  if (refreshProgressPhase) refreshProgressPhase.textContent = label;
  if (refreshProgressPct) refreshProgressPct.textContent = `${progress}%`;
  if (refreshProgressBar) refreshProgressBar.style.width = `${progress}%`;
  if (refreshProgressMessage) refreshProgressMessage.textContent = message;
}

function showRefreshError(errorMessage) {
  if (refreshProgressError) {
    refreshProgressError.textContent = errorMessage;
    refreshProgressError.classList.remove('hidden');
  }
  if (refreshProgressPhase) refreshProgressPhase.textContent = 'Error';
  if (refreshProgressBar) refreshProgressBar.classList.remove('bg-emerald-500');
  if (refreshProgressBar) refreshProgressBar.classList.add('bg-red-500');
  if (refreshProgressCloseBtn) refreshProgressCloseBtn.classList.remove('hidden');
  
  if (refreshProgressCloseBtn) {
    refreshProgressCloseBtn.onclick = () => {
      if (refreshProgressModal) refreshProgressModal.classList.add('hidden');
      // Reset bar color
      if (refreshProgressBar) {
        refreshProgressBar.classList.remove('bg-red-500');
        refreshProgressBar.classList.add('bg-emerald-500');
      }
    };
  }
}

async function showRefreshComplete(message) {
  updateRefreshProgress('COMPLETED', message, 100);
  
  if (refreshProgressCloseBtn) {
    refreshProgressCloseBtn.classList.remove('hidden');
    refreshProgressCloseBtn.textContent = 'Done';
    refreshProgressCloseBtn.onclick = async () => {
      if (refreshProgressModal) refreshProgressModal.classList.add('hidden');
      
      // Reload predictions
      setStatus('Reloading predictions...');
      clearAllLayers();
      await loadPredictions();
      setStatus('Predictions refreshed successfully!');
    };
  }
}

// Legacy function for backward compatibility
async function refreshPredictions() {
  openRefreshModal();
}

async function loadPredictions() {
  try {
    setStatus("Loading predictions • Zamboanga City");
    const res = await fetch("/api/predictions");
    if (!res.ok) {
      throw new Error(`Request failed with status ${res.status}`);
    }
    const data = await res.json();
    if (data.quartileRanges) {
      quartileRanges = data.quartileRanges;
    }

    if (data.boundary) {
      boundaryGeojson = data.boundary;
      boundaryLayer = L.geoJSON(data.boundary, {
        style: {
          color: "#facc15",
          weight: 1.5,
          opacity: 1,
          fillOpacity: 0,
        },
      }).addTo(map);

      boundaryLayer.bringToFront();

      try {
        map.fitBounds(boundaryLayer.getBounds(), { padding: [20, 20] });
      } catch (e) {
        console.error("Error fitting bounds:", e);
      }
      populateBarangayDropdown();
    }

    if (data.barangayLabels) {
      barangayLabelLayer = L.geoJSON(data.barangayLabels, {
        pointToLayer: function (feature, latlng) {
          const name = feature.properties?.barangay ?? "";
          return L.marker(latlng, {
            icon: L.divIcon({
              className: "brgy-label",
              html: `<span>${name}</span>`,
              iconSize: [100, 20],
            }),
          });
        },
      });

      const labelToggle = document.getElementById("toggle-brgy-labels");
      const z = map.getZoom();
      if (labelToggle && labelToggle.checked && z >= 12) {
        barangayLabelLayer.addTo(map);
      }
    }

    if (data.models) {
      if (data.models.catboost) {
        modelGeojson.catboost = data.models.catboost;
        modelLayers.catboost = createModelLayer("catboost", data.models.catboost);
        console.log("CatBoost layer created");
      }
      if (data.models.rf) {
        modelGeojson.rf = data.models.rf;
        modelLayers.rf = createModelLayer("rf", data.models.rf);
        console.log("RF layer created");
      }
      if (data.models.cnn) {
        modelGeojson.cnn = data.models.cnn;
        modelLayers.cnn = createModelLayer("cnn", data.models.cnn);
        console.log("CNN layer created");
      }
    }

    if (data.censusPoverty) {
      censusPovertyGeojson = data.censusPoverty;
      // Layer will be created lazily when the toggle is used
    }

    const initialModel = modelLayers.catboost
      ? "catboost"
      : modelLayers.rf
      ? "rf"
      : modelLayers.cnn
      ? "cnn"
      : null;

    if (initialModel) {
      activateModel(initialModel);
    } else {
      setStatus("No prediction layers available", "error");
    }

    setupToggleButtons();
    setupOpacitySlider();
    setupBarangayLayerToggles();
    setupBarangaySearch();
    setupTop5CategoryFilter();

    console.log("All setup complete!");
  } catch (err) {
    console.error("Error loading predictions:", err);
    setStatus("Failed to load predictions. Check the backend logs.", "error");
  }
}

function smoothScrollToSelector(selector) {
  const target = document.querySelector(selector);
  if (!target) return;

  const rect = target.getBoundingClientRect();
  const headerOffset = 64; // approximate height of sticky nav
  const top = window.pageYOffset + rect.top - headerOffset;

  window.scrollTo({
    top: top < 0 ? 0 : top,
    behavior: "smooth",
  });
}

function setupLandingPageNavigation() {
  // Only run on landing page where hero section exists
  const heroSection = document.getElementById("hero");
  if (!heroSection) return;

  const overviewLink = document.querySelector('a[href="#hero"]');
  const mapLinks = Array.from(document.querySelectorAll('a[href="#map-section"]'));
  const aboutLink = document.querySelector('a[href="#about-section"]');

  const attachSmooth = (elOrList, selector) => {
    if (!elOrList) return;
    const list = Array.isArray(elOrList) ? elOrList : [elOrList];
    list.forEach((el) => {
      if (!el) return;
      el.addEventListener("click", (e) => {
        e.preventDefault();
        smoothScrollToSelector(selector);
      });
    });
  };

  attachSmooth(overviewLink, "#hero");
  attachSmooth(mapLinks, "#map-section");
  attachSmooth(aboutLink, "#about-section");
}

function setupLandingPageAnimations() {
  const heroSection = document.getElementById("hero");
  if (!heroSection) return; // not on landing page
  const aboutSection = document.getElementById("about-section");

  const sections = [heroSection];
  if (aboutSection) sections.push(aboutSection);

  if (!("IntersectionObserver" in window)) {
    // Fallback: just make them visible
    sections.forEach((el) => {
      if (!el) return;
      el.classList.remove("opacity-0", "translate-y-3", "translate-y-4");
      el.classList.add("opacity-100", "translate-y-0");
    });
    return;
  }

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        el.classList.remove("opacity-0", "translate-y-3", "translate-y-4");
        el.classList.add("opacity-100", "translate-y-0");
        obs.unobserve(el);
      });
    },
    { threshold: 0.2 },
  );

  sections.forEach((el) => {
    if (!el) return;
    el.classList.add("opacity-0", "translate-y-3", "transition-all", "duration-700", "ease-out");
    observer.observe(el);
  });
}

function setupResetViewControl() {
  if (!resetViewBtn) return;
  resetViewBtn.addEventListener("click", (e) => {
    e.preventDefault();
    resetMapView();
  });
}

function resetMapView() {
  try {
    if (boundaryLayer && typeof boundaryLayer.getBounds === "function") {
      map.fitBounds(boundaryLayer.getBounds(), { padding: [20, 20] });
      return;
    }

    if (boundaryGeojson) {
      const tmp = L.geoJSON(boundaryGeojson);
      map.fitBounds(tmp.getBounds(), { padding: [20, 20] });
      map.removeLayer(tmp);
      return;
    }

    // Fallback: approximate center of Zamboanga City
    map.setView([6.9214, 122.079], 11);
  } catch (e) {
    console.error("Error resetting map view:", e);
  }
}

function setupFeedbackForm() {
  const heroSection = document.getElementById("hero");
  if (!heroSection) return; // only on landing page

  if (!feedbackEmailInput || !feedbackMessageInput || !feedbackSubmitBtn) return;

  const validateEmail = (value) => {
    if (!value) return false;
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value);
  };

  const setStatus = (text, tone = "info") => {
    if (!feedbackStatusEl) return;
    feedbackStatusEl.textContent = text;
    if (tone === "error") {
      feedbackStatusEl.classList.remove("text-slate-400", "text-emerald-400");
      feedbackStatusEl.classList.add("text-red-400");
    } else if (tone === "success") {
      feedbackStatusEl.classList.remove("text-slate-400", "text-red-400");
      feedbackStatusEl.classList.add("text-emerald-400");
    } else {
      feedbackStatusEl.classList.remove("text-emerald-400", "text-red-400");
      feedbackStatusEl.classList.add("text-slate-400");
    }
  };

  feedbackSubmitBtn.addEventListener("click", async (e) => {
    e.preventDefault();

    const email = feedbackEmailInput.value.trim();
    const barangay = feedbackBarangayInput ? feedbackBarangayInput.value.trim() : "";
    const message = feedbackMessageInput.value.trim();

    if (!validateEmail(email)) {
      setStatus("Please enter a valid email address.", "error");
      return;
    }
    if (!message) {
      setStatus("Please enter a message.", "error");
      return;
    }

    setStatus("Sending message…", "info");
    feedbackSubmitBtn.disabled = true;

    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, barangay, message }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok || data.success === false) {
        const msg = data && data.error ? data.error : `Failed to send message (status ${res.status}).`;
        setStatus(msg, "error");
        return;
      }

      if (feedbackEmailInput) feedbackEmailInput.value = "";
      if (feedbackBarangayInput) feedbackBarangayInput.value = "";
      if (feedbackMessageInput) feedbackMessageInput.value = "";

      setStatus("Thank you. Your message has been received.", "success");
    } catch (err) {
      console.error("Error submitting feedback", err);
      setStatus("Failed to send message. Please try again later.", "error");
    } finally {
      feedbackSubmitBtn.disabled = false;
    }
  });
}

async function fetchPeopleMessages() {
  if (!peopleListContainer) return;

  peopleListContainer.textContent = "Loading messages…";

  try {
    const res = await fetch("/api/feedback");
    if (!res.ok) {
      if (res.status === 403) {
        peopleListContainer.textContent = "Sign in as an admin to view messages.";
        return;
      }
      peopleListContainer.textContent = `Failed to load messages (status ${res.status}).`;
      return;
    }

    const data = await res.json();
    const messages = Array.isArray(data.messages) ? data.messages : [];

    if (!messages.length) {
      peopleListContainer.textContent = "No messages submitted yet.";
      return;
    }

    const items = messages.map((m) => {
      const email = m.email || "Unknown email";
      const barangay = m.barangay || "Unspecified barangay";
      const created = m.created_at || "";
      const body = m.message || "";
      const esc = (str) => String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `
        <div class="border border-slate-800 bg-slate-900/70 rounded-xl px-3 py-2.5 shadow-sm">
          <div class="flex items-center justify-between gap-2 mb-1">
            <div class="flex flex-col">
              <span class="text-[11px] font-semibold text-slate-100">${esc(email)}</span>
              <span class="text-[10px] text-slate-400">${esc(barangay)}</span>
            </div>
            <span class="text-[10px] text-slate-500 whitespace-nowrap">${esc(created)}</span>
          </div>
          <p class="text-[11px] text-slate-200 leading-snug whitespace-pre-wrap">${esc(body)}</p>
        </div>
      `;
    });

    peopleListContainer.innerHTML = items.join("");
  } catch (err) {
    console.error("Failed to load people messages", err);
    peopleListContainer.textContent = "Failed to load messages.";
  }
}

setupTabs();
setupAdminControls();
setupLandingPageNavigation();
setupLandingPageAnimations();
setupResetViewControl();
setupFeedbackForm();
loadPredictions();

// Load statistics for the Statistics tab
(async function loadStatistics() {
  try {
    const res = await fetch("/api/statistics");
    if (!res.ok) return;
    const stats = await res.json();
    renderStatisticsCharts(stats);
  } catch (e) {
    console.error("Failed to load statistics", e);
  }
})();

if (censusToggle) {
  censusToggle.addEventListener("change", (e) => {
    if (!censusPovertyGeojson) return;

    if (!modelLayers.census && censusPovertyGeojson) {
      modelLayers.census = createCensusPovertyLayer(censusPovertyGeojson);
    }

    const layer = modelLayers.census;
    if (!layer) return;

    if (e.target.checked) {
      layer.addTo(map);
    } else if (map.hasLayer(layer)) {
      map.removeLayer(layer);
    }
  });
}