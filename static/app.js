const map = L.map("map", {
  zoomControl: false,
});

L.control.zoom({ position: "topright" }).addTo(map);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const statusEl = document.getElementById("status");
const toggleButtons = Array.from(document.querySelectorAll(".model-toggle"));
const opacitySlider = document.getElementById("opacity-slider");
const opacityValue = document.getElementById("opacity-value");
const legendModelNameEl = document.getElementById("legend-model-name");
const pieCanvas = document.getElementById("category-pie");
const barCanvas = document.getElementById("top5-bar");
const brgySearchInput = document.getElementById("brgy-search-input");
const brgySearchBtn = document.getElementById("brgy-search-btn");
const brgySearchStatus = document.getElementById("brgy-search-status");
const brgyTilesCanvas = document.getElementById("brgy-tiles-chart");

const modelLayers = {
  catboost: null,
  rf: null,
  cnn: null,
};

const modelGeojson = {
  catboost: null,
  rf: null,
  cnn: null,
};

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
    : "poverty_quartile_cnn";
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

function getTop5Barangays(modelKey) {
  const geo = modelGeojson[modelKey];
  if (!geo || !Array.isArray(geo.features)) {
    return { labels: [], values: [] };
  }

  const stats = new Map();

  geo.features.forEach((f) => {
    const props = f.properties || {};
    const rawPct = props.poverty_pct;
    const val = Number(rawPct);
    if (!Number.isFinite(val)) return;
    const name = props.barangay || "Unknown";

    const rec = stats.get(name) || { sum: 0, count: 0 };
    rec.sum += val;
    rec.count += 1;
    stats.set(name, rec);
  });

  const arr = Array.from(stats.entries()).map(([name, rec]) => ({
    name,
    value: rec.count ? rec.sum / rec.count : 0,
  }));

  arr.sort((a, b) => b.value - a.value);
  const top5 = arr.slice(0, 5);

  return {
    labels: top5.map((d) => d.name),
    values: top5.map((d) => Number(d.value.toFixed(1))),
  };
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

function updateCharts(modelKey) {
  if (!pieCanvas || !barCanvas || typeof Chart === "undefined") return;

  const catStats = getCategoryStats(modelKey);
  const top5 = getTop5Barangays(modelKey);

  if (pieChart) pieChart.destroy();
  pieChart = new Chart(pieCanvas.getContext("2d"), {
    type: "pie",
    data: {
      labels: catStats.labels,
      datasets: [
        {
          data: catStats.counts,
          backgroundColor: catStats.labels.map((label) => getQuartileColor(label)),
          borderWidth: 0,
        },
      ],
    },
    options: {
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const dataArr = ctx.dataset.data || [];
              const total = dataArr.reduce((a, b) => a + b, 0) || 1;
              const count = ctx.parsed || 0;
              const pct = (count / total) * 100;
              return `${ctx.label}: ${count} cells (${pct.toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });

  if (barChart) barChart.destroy();
  barChart = new Chart(barCanvas.getContext("2d"), {
    type: "bar",
    data: {
      labels: top5.labels,
      datasets: [
        {
          data: top5.values,
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
            label: (ctx) => `${ctx.parsed.y.toFixed(1)}%`,
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
          : "CNN";

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
      modelKey === "catboost" ? "CatBoost" : modelKey === "rf" ? "Random Forest" : "CNN";
  }

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
}

async function loadPredictions() {
  try {
    setStatus("Loading predictions • Zamboanga City");
    const res = await fetch("/api/predictions");
    if (!res.ok) {
      throw new Error(`Request failed with status ${res.status}`);
    }
    const data = await res.json();

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

loadPredictions();