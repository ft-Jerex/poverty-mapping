(function () {
  const sheetsListEl = document.getElementById("sheets-list");
  const sheetTitleEl = document.getElementById("sheet-title");
  const sheetMetaEl = document.getElementById("sheet-meta");
  const sheetHelpEl = document.getElementById("sheet-help");
  const sheetSaveNotice = document.getElementById("sheet-save-notice");
  const addColumnBtn = document.getElementById("add-column-btn");
  const addRowBtn = document.getElementById("add-row-btn");
  const saveSheetBtn = document.getElementById("save-sheet-btn");
  const exportSheetBtn = document.getElementById("export-sheet-btn");
  const deleteSheetBtn = document.getElementById("delete-sheet-btn");
  const sheetThead = document.getElementById("sheet-thead");
  const sheetTbody = document.getElementById("sheet-tbody");
  const uploadSheetBtn = document.getElementById("upload-sheet-btn");
  const uploadSheetInput = document.getElementById("upload-sheet-input");
  const newSheetBtn = document.getElementById("new-sheet-btn");
  const newSheetModal = document.getElementById("new-sheet-modal");
  const newSheetNameInput = document.getElementById("new-sheet-name");
  const newSheetColumnsInput = document.getElementById("new-sheet-columns");
  const newSheetErrorEl = document.getElementById("new-sheet-error");
  const newSheetCancelBtn = document.getElementById("new-sheet-cancel-btn");
  const newSheetCreateBtn = document.getElementById("new-sheet-create-btn");
  const chartTypeSelect = document.getElementById("chart-type-select");
  const chartXSelect = document.getElementById("chart-x-select");
  const chartYSelect = document.getElementById("chart-y-select");
  const chartSortSelect = document.getElementById("chart-sort-select");
  const chartRowFilterMode = document.getElementById("chart-row-filter-mode");
  const chartBarangayFilter = document.getElementById("chart-barangay-filter");
  const chartExposeToggle = document.getElementById("chart-expose-toggle");
  const updateChartBtn = document.getElementById("update-chart-btn");
  const chartStatusEl = document.getElementById("chart-status");
  const chartCanvas = document.getElementById("sheet-chart");

  let sheets = [];
  let activeSheetSafeName = null;
  let activeSheetMeta = null;
  let header = [];
  let rows = [];
  let chart = null;
  let pendingDeleteSheet = false;
  let pendingDeleteTimer = null;

  function refreshBarangayFilterOptions() {
    if (!chartBarangayFilter) return;

    chartBarangayFilter.innerHTML = '<option value="">Select barangay…</option>';

    const xCol = chartXSelect.value;
    if (!xCol || !rows.length) {
      chartBarangayFilter.disabled = true;
      return;
    }

    const seen = new Set();
    rows.forEach((r) => {
      const v = r[xCol];
      const raw = v === null || v === undefined ? "" : String(v);
      const upper = raw.trim().toUpperCase();
      if (!raw || upper === "TOTAL" || seen.has(raw)) return;
      const label = raw;
      seen.add(label);
      const opt = document.createElement("option");
      opt.value = label;
      opt.textContent = label;
      chartBarangayFilter.appendChild(opt);
    });

    if (chartRowFilterMode && chartRowFilterMode.value === "single") {
      chartBarangayFilter.disabled = false;
    } else {
      chartBarangayFilter.disabled = true;
    }
  }

  function setButtonsEnabled(enabled) {
    if (addColumnBtn) addColumnBtn.disabled = !enabled;
    addRowBtn.disabled = !enabled;
    saveSheetBtn.disabled = !enabled;
    exportSheetBtn.disabled = !enabled;
    updateChartBtn.disabled = !enabled;
    if (chartExposeToggle) chartExposeToggle.disabled = !enabled;
    if (deleteSheetBtn) deleteSheetBtn.disabled = !enabled;
  }

  async function ensureAuthenticated() {
    try {
      const res = await fetch("/api/me");
      if (!res.ok) return;
      const data = await res.json();
      if (!data.user) {
        window.location.href = "/login";
      }
    } catch (e) {
      console.error("Failed to check auth", e);
    }
  }

  function showSheetSaveNotice(message, isError) {
    if (!sheetSaveNotice) return;
    sheetSaveNotice.textContent = message;
    sheetSaveNotice.classList.remove("hidden");

    if (isError) {
      sheetSaveNotice.classList.remove(
        "border-emerald-500/40",
        "bg-emerald-500/10",
        "text-emerald-200",
      );
      sheetSaveNotice.classList.add("border-red-500/40", "bg-red-500/10", "text-red-200");
    } else {
      sheetSaveNotice.classList.remove(
        "border-red-500/40",
        "bg-red-500/10",
        "text-red-200",
      );
      sheetSaveNotice.classList.add(
        "border-emerald-500/40",
        "bg-emerald-500/10",
        "text-emerald-200",
      );
    }

    if (sheetSaveNotice._timeoutId) {
      clearTimeout(sheetSaveNotice._timeoutId);
    }
    sheetSaveNotice._timeoutId = setTimeout(() => {
      sheetSaveNotice.classList.add("hidden");
    }, 3500);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderSheetsList() {
    sheetsListEl.innerHTML = "";
    if (!sheets.length) {
      const empty = document.createElement("p");
      empty.className = "text-[11px] text-slate-500 px-2";
      empty.textContent = "No sheets defined yet.";
      sheetsListEl.appendChild(empty);
      return;
    }

    sheets.forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "w-full text-left px-3 py-1.5 rounded-md text-[11px] hover:bg-slate-800/80 flex items-center justify-between gap-2";
      if (s.safe_name === activeSheetSafeName) {
        btn.classList.add("bg-slate-800", "border", "border-emerald-500/60");
      }
      btn.dataset.safeName = s.safe_name;
      btn.innerHTML = `
        <span class="truncate">${escapeHtml(s.sheet_name || s.safe_name)}</span>
        <span class="text-slate-500 text-[10px] whitespace-nowrap">${
          s.rows ?? 0
        } rows · ${s.columns ?? 0} cols</span>
      `;
      btn.addEventListener("click", () => {
        if (s.safe_name !== activeSheetSafeName) {
          loadSheet(s.safe_name);
        }
      });
      sheetsListEl.appendChild(btn);
    });
  }

  async function loadSheets() {
    try {
      const res = await fetch("/api/sheets");
      if (!res.ok) {
        throw new Error("Failed to load sheets");
      }
      const data = await res.json();
      if (!data.success) {
        throw new Error(data.error || "Failed to load sheets");
      }
      sheets = data.sheets || [];
      renderSheetsList();
    } catch (e) {
      console.error(e);
      sheetsListEl.innerHTML =
        '<p class="text-[11px] text-red-400 px-2">Failed to load sheets. Check that you are logged in.</p>';
    }
  }

  function renderTable() {
    sheetThead.innerHTML = "";
    sheetTbody.innerHTML = "";

    if (!header.length) {
      return;
    }

    const trHead = document.createElement("tr");

    const thAction = document.createElement("th");
    thAction.className =
      "px-2 py-1 border-b border-slate-800 text-left font-medium text-slate-200 sticky top-0 bg-slate-900";
    thAction.textContent = "";
    trHead.appendChild(thAction);

    header.forEach((col) => {
      const th = document.createElement("th");
      th.className =
        "px-2 py-1 border-b border-slate-800 text-left font-medium text-slate-200 sticky top-0 bg-slate-900";
      const wrapper = document.createElement("div");
      wrapper.className = "flex items-center gap-1";

      const labelSpan = document.createElement("span");
      labelSpan.textContent = col;
      wrapper.appendChild(labelSpan);

      const colControls = document.createElement("div");
      colControls.className = "flex items-center gap-1 text-[10px] text-slate-400";

      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.className =
        "px-1 rounded bg-slate-800 hover:bg-slate-700 text-[9px] text-slate-300";
      renameBtn.textContent = "✎";
      renameBtn.title = "Rename column";
      renameBtn.addEventListener("click", () => {
        // Inline rename: turn the label into a focused input
        if (!header.includes(col)) return;

        const input = document.createElement("input");
        input.type = "text";
        input.value = col;
        input.className =
          "bg-transparent border border-slate-600 rounded px-1 py-0.5 text-[11px] text-slate-100 focus:outline-none focus:ring-1 focus:ring-emerald-500 focus:border-emerald-500";

        let committed = false;

        const commit = () => {
          if (committed) return;
          committed = true;
          const newNameRaw = input.value || "";
          const newName = newNameRaw.trim();
          if (!newName || newName === col) {
            wrapper.replaceChild(labelSpan, input);
            return;
          }
          renameColumn(col, newName);
        };

        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            committed = true;
            wrapper.replaceChild(labelSpan, input);
          }
        });

        input.addEventListener("blur", () => {
          commit();
        });

        wrapper.replaceChild(input, labelSpan);
        input.focus();
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className =
        "px-1 rounded bg-slate-800 hover:bg-red-600 text-[9px] text-slate-300";
      deleteBtn.textContent = "×";
      deleteBtn.title = "Delete column";
      deleteBtn.addEventListener("click", () => {
        deleteColumn(col);
      });

      colControls.appendChild(renameBtn);
      colControls.appendChild(deleteBtn);
      wrapper.appendChild(colControls);

      th.appendChild(wrapper);
      trHead.appendChild(th);
    });
    sheetThead.appendChild(trHead);

    rows.forEach((row, rowIndex) => {
      const tr = document.createElement("tr");
      tr.className = rowIndex % 2 === 0 ? "bg-slate-950" : "bg-slate-950/70";

      const tdAction = document.createElement("td");
      tdAction.className =
        "px-2 py-1 border-b border-slate-900 text-slate-400 align-middle text-center";
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className =
        "inline-flex items-center justify-center w-5 h-5 rounded-full bg-slate-800 hover:bg-red-500 text-[11px] text-slate-200 hover:text-slate-50 transition-colors";
      delBtn.textContent = "×";
      delBtn.title = "Delete row";
      delBtn.addEventListener("click", () => {
        rows.splice(rowIndex, 1);
        renderTable();
      });
      tdAction.appendChild(delBtn);
      tr.appendChild(tdAction);
      header.forEach((col, colIndex) => {
        const td = document.createElement("td");
        td.className =
          "px-2 py-1 border-b border-slate-900 text-slate-100 align-middle";

        const input = document.createElement("input");
        input.type = "text";
        input.className =
          "w-full bg-transparent text-[11px] text-slate-100 placeholder:text-slate-500 focus:outline-none";
        const val = row[col];
        input.value = val === undefined || val === null ? "" : String(val);
        input.dataset.rowIndex = String(rowIndex);
        input.dataset.colKey = col;

        input.addEventListener("change", (e) => {
          const rIdx = Number(e.target.dataset.rowIndex);
          const key = e.target.dataset.colKey;
          if (!Number.isNaN(rIdx) && key) {
            rows[rIdx][key] = e.target.value;
          }
        });

        td.appendChild(input);
        tr.appendChild(td);
      });
      sheetTbody.appendChild(tr);
    });
  }

  function refreshChartSelectors() {
    chartXSelect.innerHTML = '<option value="">X axis column…</option>';
    chartYSelect.innerHTML = "";

    if (chartSortSelect) {
      chartSortSelect.innerHTML = '<option value="">Sort by…</option>';
    }

    if (chartBarangayFilter) {
      chartBarangayFilter.innerHTML = '<option value="">Select barangay…</option>';
      chartBarangayFilter.disabled = true;
    }

    header.forEach((col) => {
      const optX = document.createElement("option");
      optX.value = col;
      optX.textContent = col;
      chartXSelect.appendChild(optX);

      const optY = document.createElement("option");
      optY.value = col;
      optY.textContent = col;
      chartYSelect.appendChild(optY);

      if (chartSortSelect) {
        const optS = document.createElement("option");
        optS.value = col;
        optS.textContent = col;
        chartSortSelect.appendChild(optS);
      }
    });
  }

  async function loadSheet(safeName) {
    try {
      sheetTitleEl.textContent = "Loading sheet…";
      sheetMetaEl.textContent = "";
      sheetHelpEl.textContent = "";
      setButtonsEnabled(false);

      const res = await fetch(`/api/sheets/${encodeURIComponent(safeName)}`);
      if (!res.ok) {
        throw new Error("Failed to load sheet");
      }
      const data = await res.json();
      if (!data.success) {
        throw new Error(data.error || "Failed to load sheet");
      }

      activeSheetSafeName = safeName;
      activeSheetMeta = data.sheet || null;
      header = Array.isArray(data.header) ? data.header.slice() : [];
      rows = Array.isArray(data.rows) ? data.rows.map((r) => ({ ...r })) : [];

      sheetTitleEl.textContent = activeSheetMeta.sheet_name || activeSheetMeta.safe_name;
      sheetMetaEl.textContent = `${rows.length} rows · ${header.length} columns`;
      sheetHelpEl.textContent =
        "Edit cells directly. Use Save to write back to CSV. These sheets can feed statistics and other visualizations.";

      renderSheetsList();
      renderTable();
      refreshChartSelectors();
      setButtonsEnabled(true);
      chartStatusEl.textContent = "Select X axis and one or more numeric columns, then Update chart.";

      try {
        const cfgRes = await fetch(
          `/api/sheets/${encodeURIComponent(safeName)}/config`,
        );
        if (cfgRes.ok) {
          const cfgData = await cfgRes.json();
          const cfg = cfgData && cfgData.config ? cfgData.config : {};
          if (cfg.chart_type && chartTypeSelect) {
            chartTypeSelect.value = cfg.chart_type;
          }
          if (cfg.x_column && chartXSelect) {
            chartXSelect.value = cfg.x_column;
          }
          if (cfg.y_columns && Array.isArray(cfg.y_columns) && chartYSelect) {
            const first = cfg.y_columns[0];
            if (first) {
              chartYSelect.value = first;
            }
          }
          if (chartRowFilterMode && cfg.row_mode) {
            chartRowFilterMode.value = cfg.row_mode;
          }
          if (chartSortSelect && cfg.sort_column) {
            chartSortSelect.value = cfg.sort_column;
          }
          if (chartBarangayFilter && cfg.filter_value && cfg.filter_mode === "x_equals") {
            // Options will be populated when X column is set and chart is updated
            // so we only stash the desired value for now
            chartBarangayFilter.dataset.pendingValue = cfg.filter_value;
          }
          if (chartExposeToggle && Object.prototype.hasOwnProperty.call(cfg, "expose_in_statistics")) {
            chartExposeToggle.checked = Boolean(cfg.expose_in_statistics);
          } else if (chartExposeToggle) {
            chartExposeToggle.checked = false;
          }
          if (cfg.x_column && cfg.y_columns && cfg.y_columns.length) {
            await updateChart();
          }
        }
      } catch (e) {
        console.error("Failed to load chart config", e);
      }
    } catch (e) {
      console.error(e);
      sheetTitleEl.textContent = "Failed to load sheet";
      sheetMetaEl.textContent = "";
      sheetHelpEl.textContent =
        "Check that the sheet still exists in sheets_saved_summary.csv and that you are logged in.";
      header = [];
      rows = [];
      renderTable();
      setButtonsEnabled(false);
    }
  }

  function addRow() {
    if (!header.length) return;
    const newRow = {};
    header.forEach((col) => {
      newRow[col] = "";
    });
    rows.push(newRow);
    renderTable();
  }

  function addColumn() {
    // Auto-generate a simple name; user can rename via the header ✎ control.
    const base = "New Column";
    let idx = 1;
    let name = `${base} ${idx}`;
    while (header.includes(name)) {
      idx += 1;
      name = `${base} ${idx}`;
    }

    header.push(name);
    rows.forEach((row) => {
      if (!(name in row)) {
        row[name] = "";
      }
    });
    renderTable();
    refreshChartSelectors();
    sheetHelpEl.textContent = `Added column "${name}". Use the ✎ icon in the header to rename it.`;
  }

  function renameColumn(oldName, name) {
    if (!name || name === oldName) return;

    if (header.includes(name)) {
      const proceed = window.confirm(
        "Another column already has this name. Continue and merge values into that column?",
      );
      if (!proceed) return;
    }

    header = header.map((c) => (c === oldName ? name : c));
    rows.forEach((row) => {
      if (Object.prototype.hasOwnProperty.call(row, oldName)) {
        const existing = row[name];
        const value = row[oldName];
        // If the target column already exists, prefer its value and keep the
        // old one only if the target is empty.
        if (existing === undefined || existing === null || existing === "") {
          row[name] = value;
        }
        delete row[oldName];
      }
    });
    renderTable();
    refreshChartSelectors();
  }

  function deleteColumn(colName) {
    if (!header.includes(colName)) return;
    if (header.length <= 1) {
      sheetHelpEl.textContent =
        "Cannot delete the last remaining column. Add another column before deleting this one.";
      return;
    }

    header = header.filter((c) => c !== colName);
    rows.forEach((row) => {
      if (Object.prototype.hasOwnProperty.call(row, colName)) {
        delete row[colName];
      }
    });
    renderTable();
    refreshChartSelectors();
    sheetHelpEl.textContent = `Column "${colName}" deleted from all rows.`;
  }

  async function saveSheet() {
    if (!activeSheetSafeName || !header.length) return;
    saveSheetBtn.disabled = true;
    saveSheetBtn.textContent = "Saving…";
    try {
      const res = await fetch(`/api/sheets/${encodeURIComponent(activeSheetSafeName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ header, rows }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      await loadSheets();
      chartStatusEl.textContent = "Sheet saved. You can now re-use this CSV in statistics pipelines.";
      showSheetSaveNotice("Update Succesfully", false);
    } catch (e) {
      console.error(e);
      chartStatusEl.textContent = `Failed to save sheet: ${e.message}`;
      showSheetSaveNotice("Error. Something Happened..", true);
    } finally {
      saveSheetBtn.disabled = false;
      saveSheetBtn.textContent = "Save changes";
    }
  }

  function toCsvText(headerArr, rowObjs) {
    const escapeCell = (value) => {
      if (value === null || value === undefined) return "";
      const str = String(value);
      if (str.includes("\"") || str.includes(",") || str.includes("\n")) {
        return '"' + str.replace(/"/g, '""') + '"';
      }
      return str;
    };

    const lines = [];
    lines.push(headerArr.map(escapeCell).join(","));
    rowObjs.forEach((row) => {
      const line = headerArr.map((col) => escapeCell(row[col]));
      lines.push(line.join(","));
    });
    return lines.join("\n");
  }

  function exportSheet() {
    if (!header.length || !rows.length) {
      const metaName = (activeSheetMeta && (activeSheetMeta.sheet_name || activeSheetMeta.safe_name)) || "sheet";
      const blob = new Blob([toCsvText(header, rows)], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${metaName || "sheet"}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      chartStatusEl.textContent = "CSV exported.";
      return;
    }

    const metaName = (activeSheetMeta && (activeSheetMeta.sheet_name || activeSheetMeta.safe_name)) || "sheet";
    const blob = new Blob([toCsvText(header, rows)], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${metaName || "sheet"}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    chartStatusEl.textContent = "CSV exported.";
  }

  function destroyChart() {
    if (chart && typeof chart.destroy === "function") {
      chart.destroy();
      chart = null;
    }
  }

  async function updateChart() {
    if (!chartCanvas || typeof Chart === "undefined") return;
    if (!header.length || !rows.length) {
      chartStatusEl.textContent = "No data to visualize.";
      destroyChart();
      return;
    }

    const xCol = chartXSelect.value;
    const yVal = chartYSelect.value;
    const ySelected = yVal ? [yVal] : [];
    const type = chartTypeSelect.value || "bar";

    if (!xCol || !ySelected.length) {
      chartStatusEl.textContent = "Select an X axis column and at least one Y column.";
      destroyChart();
      if (activeSheetSafeName) {
        try {
          await fetch(`/api/sheets/${encodeURIComponent(activeSheetSafeName)}/config`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ chart_type: type, x_column: "", y_columns: [] }),
          });
        } catch (e) {
          console.error("Failed to clear chart config", e);
        }
      }
      return;
    }

    // Apply row limiting / barangay filter before building chart data
    let workingRows = Array.isArray(rows) ? rows.slice() : [];
    const sortCol = chartSortSelect ? chartSortSelect.value : "";

    // First, exclude TOTAL rows (commonly used as grand total) so that
    // "Top 5" and other limits work on real barangays only.
    workingRows = workingRows.filter((r) => {
      const v = r[xCol];
      const label = v === null || v === undefined ? "" : String(v).trim().toUpperCase();
      return label !== "TOTAL";
    });

    if (!workingRows.length) {
      chartStatusEl.textContent = "No data to visualize after filtering.";
      destroyChart();
      return;
    }

    let rowMode = "top5";
    if (chartRowFilterMode) {
      const mode = chartRowFilterMode.value || "top5";
      rowMode = mode;

      if (mode === "single" && chartBarangayFilter && chartBarangayFilter.value && xCol) {
        const target = chartBarangayFilter.value;
        workingRows = workingRows.filter((r) => {
          const v = r[xCol];
          return (v === null || v === undefined ? "" : String(v)) === target;
        });
      } else {
        if (sortCol) {
          workingRows.sort((a, b) => {
            const av = Number(a[sortCol]);
            const bv = Number(b[sortCol]);
            const aNum = Number.isFinite(av) ? av : -Infinity;
            const bNum = Number.isFinite(bv) ? bv : -Infinity;
            return bNum - aNum; // descending
          });
        }

        if (mode === "top5") {
          workingRows = workingRows.slice(0, 5);
        } else if (mode === "all") {
          // keep all rows after sort
        } else {
          workingRows = workingRows.slice(0, 5);
        }
      }
    } else {
      if (sortCol) {
        workingRows.sort((a, b) => {
          const av = Number(a[sortCol]);
          const bv = Number(b[sortCol]);
          const aNum = Number.isFinite(av) ? av : -Infinity;
          const bNum = Number.isFinite(bv) ? bv : -Infinity;
          return bNum - aNum;
        });
      }
      workingRows = workingRows.slice(0, 5);
    }

    const labels = workingRows.map((r) => {
      const v = r[xCol];
      return v === null || v === undefined ? "" : String(v);
    });

    const basePalette = ["#22c55e", "#0ea5e9", "#eab308", "#f97316", "#a855f7", "#ef4444", "#6366f1", "#ec4899"];

    const datasets = ySelected.map((col, idx) => {
      const data = workingRows.map((r) => {
        const raw = r[col];
        const num = Number(raw);
        return Number.isFinite(num) ? num : null;
      });

      // If there is only one metric selected, give each point its own color
      // for easier differentiation (bar, line, or pie).
      if (ySelected.length === 1) {
        const colors = workingRows.map((_, i) => basePalette[i % basePalette.length]);
        return {
          label: col,
          data,
          backgroundColor: colors,
          borderColor: colors,
          borderWidth: type === "line" ? 2 : 0,
          tension: 0.25,
        };
      }

      // Multiple series: keep one color per series, but still vary by metric
      const seriesColor = basePalette[idx % basePalette.length];
      return {
        label: col,
        data,
        backgroundColor: seriesColor,
        borderColor: seriesColor,
        borderWidth: type === "line" ? 2 : 0,
        tension: 0.25,
      };
    });

    destroyChart();
    chart = new Chart(chartCanvas.getContext("2d"), {
      type: type === "pie" ? "pie" : type,
      data: {
        labels: type === "pie" && datasets.length === 1 ? labels : labels,
        datasets:
          type === "pie" && datasets.length === 1
            ? [
                {
                  ...datasets[0],
                  borderWidth: 0,
                },
              ]
            : datasets,
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

    chartStatusEl.textContent =
      "Chart updated. Adjust columns or type to explore different views of this sheet.";

    if (activeSheetSafeName) {
      try {
        const sortCol = chartSortSelect ? chartSortSelect.value || "" : "";
        let filterMode = null;
        let filterValue = null;
        if (chartRowFilterMode && chartRowFilterMode.value === "single" && chartBarangayFilter) {
          filterMode = "x_equals";
          filterValue = chartBarangayFilter.value || null;
        }
        const expose = chartExposeToggle ? !!chartExposeToggle.checked : undefined;

        await fetch(`/api/sheets/${encodeURIComponent(activeSheetSafeName)}/config`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chart_type: type,
            x_column: xCol,
            y_columns: ySelected,
            row_mode: rowMode,
            sort_column: sortCol || undefined,
            filter_mode: filterMode,
            filter_value: filterValue,
            // Only send expose_in_statistics when the toggle exists so that
            // older configs without this field remain unchanged unless the
            // user explicitly toggles it.
            ...(chartExposeToggle ? { expose_in_statistics: expose } : {}),
          }),
        });
      } catch (e) {
        console.error("Failed to save chart config", e);
      }
    }
  }

  function openNewSheetModal() {
    newSheetErrorEl.classList.add("hidden");
    newSheetErrorEl.textContent = "";
    newSheetNameInput.value = "";
    newSheetColumnsInput.value = "";
    newSheetModal.classList.remove("hidden");
  }

  function closeNewSheetModal() {
    newSheetModal.classList.add("hidden");
  }

  async function createNewSheet() {
    const name = newSheetNameInput.value.trim();
    const colsRaw = newSheetColumnsInput.value.trim();

    if (!name) {
      newSheetErrorEl.textContent = "Sheet name is required.";
      newSheetErrorEl.classList.remove("hidden");
      return;
    }

    const cols = colsRaw
      ? colsRaw
          .split(",")
          .map((c) => c.trim())
          .filter((c) => c.length > 0)
      : [];

    if (!cols.length) {
      newSheetErrorEl.textContent = "Provide at least one column name.";
      newSheetErrorEl.classList.remove("hidden");
      return;
    }

    newSheetCreateBtn.disabled = true;
    try {
      const res = await fetch("/api/sheets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sheet_name: name, header: cols, rows: [] }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      closeNewSheetModal();
      await loadSheets();
      if (data.sheet && data.sheet.safe_name) {
        await loadSheet(data.sheet.safe_name);
      }
    } catch (e) {
      newSheetErrorEl.textContent = `Failed to create sheet: ${e.message}`;
      newSheetErrorEl.classList.remove("hidden");
    } finally {
      newSheetCreateBtn.disabled = false;
    }
  }

  function openUploadSheetDialog() {
    if (!uploadSheetInput) return;
    uploadSheetInput.value = "";
    uploadSheetInput.click();
  }

  async function handleUploadSheetChange(event) {
    const input = event.target;
    if (!input || !input.files || !input.files.length) return;
    const file = input.files[0];
    if (!file) return;

    const defaultName = file.name.replace(/\.[^.]+$/, "") || "Uploaded sheet";
    const name = defaultName.trim();

    if (uploadSheetBtn) uploadSheetBtn.disabled = true;
    if (newSheetBtn) newSheetBtn.disabled = true;

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("sheet_name", name);

      const res = await fetch("/api/sheets/upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      await loadSheets();
      if (data.sheet && data.sheet.safe_name) {
        await loadSheet(data.sheet.safe_name);
      }
      const displayName = (data.sheet && (data.sheet.sheet_name || data.sheet.safe_name)) || name;
      sheetHelpEl.textContent = `Sheet "${displayName}" uploaded. You can now edit it or wire it into statistics and other charts.`;
    } catch (e) {
      console.error(e);
      sheetHelpEl.textContent = `Failed to upload sheet: ${e.message}`;
    } finally {
      if (uploadSheetBtn) uploadSheetBtn.disabled = false;
      if (newSheetBtn) newSheetBtn.disabled = false;
      input.value = "";
    }
  }

  async function deleteSheet() {
    if (!activeSheetSafeName) return;
    const name =
      (activeSheetMeta && (activeSheetMeta.sheet_name || activeSheetMeta.safe_name)) ||
      activeSheetSafeName;

    // First click arms the delete with a clear inline warning; second click confirms.
    if (!pendingDeleteSheet) {
      pendingDeleteSheet = true;
      if (deleteSheetBtn) {
        deleteSheetBtn.textContent = "Confirm delete";
        deleteSheetBtn.classList.add("bg-red-600");
      }
      sheetHelpEl.textContent =
        `Press "Confirm delete" again to permanently remove the sheet "${name}" from the server.`;
      if (pendingDeleteTimer) {
        clearTimeout(pendingDeleteTimer);
      }
      pendingDeleteTimer = setTimeout(() => {
        pendingDeleteSheet = false;
        pendingDeleteTimer = null;
        if (deleteSheetBtn) {
          deleteSheetBtn.textContent = "Delete sheet";
          deleteSheetBtn.classList.remove("bg-red-600");
        }
        sheetHelpEl.textContent =
          "Delete cancelled. Your sheets remain unchanged.";
      }, 8000);
      return;
    }

    // Second click: actually delete
    pendingDeleteSheet = false;
    if (pendingDeleteTimer) {
      clearTimeout(pendingDeleteTimer);
      pendingDeleteTimer = null;
    }
    if (deleteSheetBtn) {
      deleteSheetBtn.disabled = true;
      deleteSheetBtn.textContent = "Deleting…";
    }

    try {
      const res = await fetch(`/api/sheets/${encodeURIComponent(activeSheetSafeName)}`, {
        method: "DELETE",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }

      activeSheetSafeName = null;
      activeSheetMeta = null;
      header = [];
      rows = [];
      renderTable();
      setButtonsEnabled(false);
      sheetTitleEl.textContent = "No sheet selected";
      sheetMetaEl.textContent = "";
      sheetHelpEl.textContent =
        "Sheet deleted. Select another sheet or create a new one.";
      await loadSheets();
    } catch (e) {
      console.error(e);
      chartStatusEl.textContent = `Failed to delete sheet: ${e.message}`;
    } finally {
      if (deleteSheetBtn) {
        deleteSheetBtn.disabled = false;
        deleteSheetBtn.textContent = "Delete sheet";
        deleteSheetBtn.classList.remove("bg-red-600");
      }
    }
  }

  function initEvents() {
    if (addColumnBtn) {
      addColumnBtn.addEventListener("click", addColumn);
    }
    addRowBtn.addEventListener("click", addRow);
    saveSheetBtn.addEventListener("click", saveSheet);
    exportSheetBtn.addEventListener("click", exportSheet);
    updateChartBtn.addEventListener("click", () => {
      updateChart();
    });

    if (uploadSheetBtn && uploadSheetInput) {
      uploadSheetBtn.addEventListener("click", openUploadSheetDialog);
      uploadSheetInput.addEventListener("change", handleUploadSheetChange);
    }

    if (chartSortSelect) {
      chartSortSelect.addEventListener("change", () => {
        updateChart();
      });
    }

    if (chartRowFilterMode) {
      chartRowFilterMode.addEventListener("change", () => {
        if (chartRowFilterMode.value === "single") {
          refreshBarangayFilterOptions();
        } else if (chartBarangayFilter) {
          chartBarangayFilter.disabled = true;
        }
      });
    }

    if (chartXSelect && chartBarangayFilter) {
      chartXSelect.addEventListener("change", () => {
        refreshBarangayFilterOptions();
      });
    }

    if (deleteSheetBtn) {
      deleteSheetBtn.addEventListener("click", deleteSheet);
    }

    newSheetBtn.addEventListener("click", openNewSheetModal);
    newSheetCancelBtn.addEventListener("click", closeNewSheetModal);
    newSheetCreateBtn.addEventListener("click", createNewSheet);

    newSheetModal.addEventListener("click", (e) => {
      if (e.target === newSheetModal) {
        closeNewSheetModal();
      }
    });
  }

  (async function bootstrap() {
    await ensureAuthenticated();
    initEvents();
    await loadSheets();
  })();
})();
