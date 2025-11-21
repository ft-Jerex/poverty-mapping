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
const refreshBtn = document.getElementById("refresh-btn");
const downloadMapBtn = document.getElementById("download-map-btn");

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

  geospatialPanel.classList.toggle("hidden", !isGeo);
  statisticsPanel.classList.toggle("hidden", isGeo);

  if (isGeo) {
    geospatialTabBtn.classList.add("text-slate-100", "border-emerald-500");
    geospatialTabBtn.classList.remove("text-slate-400", "border-transparent");
    statisticsTabBtn.classList.add("text-slate-400", "border-transparent");
    statisticsTabBtn.classList.remove("text-slate-100", "border-emerald-500");
  } else {
    statisticsTabBtn.classList.add("text-slate-100", "border-emerald-500");
    statisticsTabBtn.classList.remove("text-slate-400", "border-transparent");
    geospatialTabBtn.classList.add("text-slate-400", "border-transparent");
    geospatialTabBtn.classList.remove("text-slate-100", "border-emerald-500");
  }
}

function setupTabs() {
  if (!geospatialTabBtn || !statisticsTabBtn || !geospatialPanel || !statisticsPanel) return;

  setActiveTab("geospatial");

  geospatialTabBtn.addEventListener("click", () => setActiveTab("geospatial"));
  statisticsTabBtn.addEventListener("click", () => setActiveTab("statistics"));
}

function renderStatisticsCharts(stats) {
  if (!stats || typeof Chart === "undefined") return;

  latestStatistics = stats;

  // Top barangays by census poverty (bar chart)
  if (statsTopHhCanvas && stats.top_poverty_households) {
    const s = stats.top_poverty_households;
    if (statsTopHhChart) statsTopHhChart.destroy();
    statsTopHhChart = new Chart(statsTopHhCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: s.barangays,
        datasets: [
          {
            label: "% of households that are poor",
            data: s.poverty_magnitude.map((v) => Number((v * 100).toFixed(1))),
            backgroundColor: "#ef4444",
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
              maxRotation: 45,
              minRotation: 0,
              font: { size: 10 },
            },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const i = ctx.dataIndex;
                const hh = s.total_households?.[i];
                const poor = s.poor_households?.[i];
                const pct = ctx.parsed.y;
                if (hh != null && poor != null) {
                  return `${pct.toFixed(1)}% of ${hh} households poor (${poor} households)`;
                }
                return `${pct.toFixed(1)}% of households poor`;
              },
            },
          },
        },
      },
    });
  }

  // Poor children attending vs not attending (doughnut)
  if (statsChildrenCanvas && stats.poor_children_attendance) {
    const c = stats.poor_children_attendance;
    if (statsChildrenChart) statsChildrenChart.destroy();
    statsChildrenChart = new Chart(statsChildrenCanvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels: ["Attending school", "Not attending"],
        datasets: [
          {
            data: [c.attending, c.not_attending],
            backgroundColor: ["#22c55e", "#ef4444"],
          },
        ],
      },
      options: {
        plugins: {
          legend: {
            position: "bottom",
            labels: { boxWidth: 10, font: { size: 10 } },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const total = (c.attending || 0) + (c.not_attending || 0);
                const val = ctx.parsed;
                const pct = total ? (val / total) * 100 : 0;
                return `${ctx.label}: ${val.toLocaleString()} children (${pct.toFixed(1)}%)`;
              },
            },
          },
        },
      },
    });
  }

  // Top barangays contributing to poor children not attending school (bar)
  if (
    statsChildrenTopNonAttendCanvas &&
    stats.top_poor_children_not_attending &&
    stats.poor_children_attendance
  ) {
    const t = stats.top_poor_children_not_attending;
    const totalCityNotAttending =
      stats.poor_children_attendance.not_attending || 0;

    if (statsChildrenTopNonAttendChart)
      statsChildrenTopNonAttendChart.destroy();

    statsChildrenTopNonAttendChart = new Chart(
      statsChildrenTopNonAttendCanvas.getContext("2d"),
      {
        type: "bar",
        data: {
          labels: t.barangays,
          datasets: [
            {
              data: t.not_attending,
              backgroundColor: "#ef4444",
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
                maxRotation: 45,
                minRotation: 0,
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
                  const i = ctx.dataIndex;
                  const val = ctx.parsed.y;
                  const brgyTotal = t.total_children?.[i] || 0;
                  const shareCity = totalCityNotAttending
                    ? (val / totalCityNotAttending) * 100
                    : 0;
                  const shareBrgy = brgyTotal
                    ? (val / brgyTotal) * 100
                    : 0;
                  const base = `${val.toLocaleString()} children not attending`;
                  if (!brgyTotal && !totalCityNotAttending) return base;
                  if (brgyTotal && totalCityNotAttending) {
                    return `${base} (${shareBrgy.toFixed(
                      1,
                    )}% of poor children in barangay, ${shareCity.toFixed(
                      1,
                    )}% of city non-attending)`;
                  }
                  if (brgyTotal) {
                    return `${base} (${shareBrgy.toFixed(
                      1,
                    )}% of poor children in barangay)`;
                  }
                  return `${base} (${shareCity.toFixed(
                    1,
                  )}% of city non-attending)`;
                },
              },
            },
          },
        },
      },
    );
  }

  // Water source composition (stacked bar or simple bar)
  if (statsWaterCanvas && stats.water_source) {
    const w = stats.water_source;
    if (statsWaterChart) statsWaterChart.destroy();
    statsWaterChart = new Chart(statsWaterCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: w.labels,
        datasets: [
          {
            data: w.counts,
            backgroundColor: [
              "#22c55e",
              "#f97316",
              "#0ea5e9",
              "#a855f7",
              "#64748b",
            ],
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
            ticks: { color: "#cbd5f5", font: { size: 9 } },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.parsed.y.toLocaleString()} households`,
            },
          },
        },
      },
    });
  }

  // Employment profile of poor workers (horizontal bar)
  if (statsEmploymentCanvas && stats.poor_employment_occupation) {
    const e = stats.poor_employment_occupation;
    if (statsEmploymentChart) statsEmploymentChart.destroy();
    statsEmploymentChart = new Chart(statsEmploymentCanvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: e.labels,
        datasets: [
          {
            data: e.counts,
            backgroundColor: "#0ea5e9",
            borderRadius: 4,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: "#1e293b" },
          },
          y: {
            ticks: { color: "#cbd5f5", font: { size: 9 } },
            grid: { display: false },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.parsed.x.toLocaleString()} workers`,
            },
          },
        },
      },
    });
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

async function refreshPredictions() {
  if (!refreshBtn) {
    return;
  }

  try {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Refreshing…";
    setStatus("Refreshing predictions from latest data…");

    const res = await fetch("/api/refresh", { method: "POST" });
    if (!res.ok) {
      throw new Error(`Refresh failed with status ${res.status}`);
    }
    const data = await res.json();

    if (!data || data.success === false) {
      const msg = data && data.error ? data.error : "Unknown refresh error";
      setStatus(`Refresh failed: ${msg}`, "error");
      return;
    }

    // Clear existing layers and reload predictions
    clearAllLayers();
    await loadPredictions();

    const note = data.message || "Refresh completed.";
    setStatus(`${note} Showing latest predictions.`);
  } catch (err) {
    console.error("Error during refresh:", err);
    setStatus("Failed to refresh predictions. Check backend logs.", "error");
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
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
          color: "#e5e7eb",
          weight: 1,
          fillOpacity: 0,
        },
      }).addTo(map);

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

    console.log("All setup complete!");
  } catch (err) {
    console.error("Error loading predictions:", err);
    setStatus("Failed to load predictions. Check the backend logs.", "error");
  }
}

setupTabs();
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