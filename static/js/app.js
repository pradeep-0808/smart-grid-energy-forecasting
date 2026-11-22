/* Smart Grid Energy Analytics — front-end controller.
   Every number rendered here comes from a FastAPI response. Nothing is mocked. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    datasets: [],
    activeId: null,
    grain: null,
    info: null,
    run: null,          // /model-comparison/ payload
    predModel: 'Random Forest',
    cmpMetric: 'MAE',
    histInterval: 'daily',
    histRange: { start: '', end: '' },
    liveTimer: null,
    dashInterval: 'daily',
    forecast: null,
    locData: null,
  };

  const charts = {};
  const PALETTE = {
    emerald: '#22c99b', emeraldDim: 'rgba(34,201,155,.16)',
    cyan: '#38bcd8', blue: '#5b93ea', amber: '#e0a34a', red: '#e06060',
    grey: '#8ba6b9', purple: '#a78bfa',
  };

  /* ---------------------------------------------------------------- helpers */
  function toast(msg, type) {
    const el = document.createElement('div');
    el.className = 'toast ' + (type || '');
    el.innerHTML = msg;
    $('toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 3600);
    setTimeout(() => el.remove(), 4000);
  }

  
const API_BASE = 'https://smart-grid-energy-forecasting.onrender.com';

async function api(path, opts) {
    const res = await fetch(API_BASE + path, opts);
    const ctype = res.headers.get('content-type') || '';
    const body = ctype.includes('application/json') ? await res.json() : await res.text();

    if (!res.ok) {
      const detail = (body && body.detail) || (typeof body === 'string' ? body : 'Request failed');
      const err = new Error(Array.isArray(detail) ? detail.map(d => d.msg).join('; ') : detail);
      err.status = res.status;
      err.body = body;
      throw err;
    }

    return body;
}


  function download(url, name) {
    const a = document.createElement('a');
    a.href = url; a.download = name || '';
    document.body.appendChild(a); a.click(); a.remove();
  }

  const fmt = (n, d) => (n === null || n === undefined || isNaN(n)) ? '—'
    : Number(n).toLocaleString('en-US', { maximumFractionDigits: d === undefined ? 1 : d });
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const bytes = (n) => n > 1024 ? (n / 1024).toFixed(1) + ' KB' : n + ' B';

  Chart.defaults.color = '#8ba6b9';
  Chart.defaults.font.family = 'Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.borderColor = 'rgba(27,47,63,.9)';
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = '#0b1b28';
  Chart.defaults.plugins.tooltip.borderColor = '#24404f';
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 9;
  Chart.defaults.plugins.tooltip.titleColor = '#e8f1f7';
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = false;   // deterministic paints, no distracting motion

  const grid = { color: 'rgba(27,47,63,.7)', drawTicks: false };
  const axis = { grid, ticks: { color: '#7d95a9', padding: 6 }, border: { display: false } };

  function draw(id, cfg) {
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart($(id), cfg);
    return charts[id];
  }

  function lineCfg(labels, datasets, extra) {
    return Object.assign({
      type: 'line',
      data: { labels, datasets },
      options: {
        interaction: { mode: 'index', intersect: false },
        scales: { x: Object.assign({}, axis, { ticks: Object.assign({}, axis.ticks, { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }) }), y: axis },
        elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2, tension: .25 } },
      },
    }, extra || {});
  }

  function barCfg(labels, datasets, extra) {
    return Object.assign({
      type: 'bar',
      data: { labels, datasets },
      options: {
        scales: { x: Object.assign({}, axis, { ticks: Object.assign({}, axis.ticks, { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }) }), y: axis },
        elements: { bar: { borderRadius: 3, maxBarThickness: 34 } },
      },
    }, extra || {});
  }

  /* ---------------------------------------------------------------- routing */
  const TITLES = {
    home: ['Home', 'Smart Grid Energy Consumption Forecasting & Analysis'],
    dashboard: ['Dashboard', 'Consolidated view of the active dataset'],
    location: ['Location Explorer', 'Real country-level electricity data'],
    upload: ['Dataset Upload', 'CSV / Excel ingestion, profiling and preprocessing'],
    predictions: ['ML Predictions', 'Hold-out actual vs predicted'],
    comparison: ['Model Comparison', 'Linear Regression · Decision Tree · Random Forest'],
    historical: ['Historical Analysis', 'Hourly to yearly aggregation'],
    live: ['Live Monitoring', 'Smart meter readings stored in SQLite'],
    forecast: ['Future Forecast', 'Recursive multi-step predictions'],
    reports: ['Reports', 'CSV and PDF exports'],
    about: ['About Project', 'Pipeline, sources and limitations'],
  };

  function goto(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
    document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === page));
    const t = TITLES[page] || ['', ''];
    $('pageTitle').textContent = t[0]; $('pageCrumb').textContent = t[1];
    $('side').classList.remove('open');
    window.scrollTo({ top: 0 });
    if (page === 'dashboard') loadDashboard();
    if (page === 'predictions' || page === 'comparison') loadRun();
    if (page === 'historical') loadHistorical();
    if (page === 'live') loadLive();
    if (page === 'about') renderAbout();
    if (page === 'reports') renderEndpoints();
  }

  document.querySelectorAll('.nav-item').forEach(b => b.addEventListener('click', () => goto(b.dataset.page)));
  document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => goto(b.dataset.goto)));
  $('menuBtn').addEventListener('click', () => $('side').classList.toggle('open'));
  document.addEventListener('click', (e) => {
    if (window.innerWidth > 980) return;
    if ($('side').classList.contains('open') && !$('side').contains(e.target) && e.target !== $('menuBtn')) {
      $('side').classList.remove('open');
    }
  });

  /* ---------------------------------------------------------------- datasets */
  async function refreshDatasets(selectId) {
    try {
      const res = await api('/datasets');
      state.datasets = res.datasets;
      const sel = $('datasetSelect');
      sel.innerHTML = state.datasets.length
        ? state.datasets.map(d => `<option value="${d.id}">${esc(d.filename)} · ${d.rows} rows${d.trained ? ' · trained' : ''}</option>`).join('')
        : '<option value="">no dataset yet</option>';
      const trainedDs = state.datasets.find(d => d.trained);
      const target = selectId || state.activeId ||
        (trainedDs ? trainedDs.id : (state.datasets[0] && state.datasets[0].id));
      if (target && state.datasets.some(d => d.id === target)) {
        sel.value = target;
        await setActive(target);
      } else if (!state.datasets.length) {
        state.activeId = null; state.run = null;
        $('grainChip').textContent = 'no data';
        $('sfDataset').textContent = 'none selected';
      }
      $('homeLiveCount').textContent = '—';
    } catch (e) { toast('Could not list datasets: ' + e.message, 'err'); }
  }

  async function setActive(id) {
    state.activeId = id;
    state.run = null;
    try {
      state.info = await api('/dataset/info?dataset_id=' + encodeURIComponent(id));
      state.grain = state.info.grain || null;
      $('grainChip').textContent = state.grain ? state.grain + ' grain' : 'not preprocessed';
      $('sfDataset').textContent = state.info.filename;
      if (state.info.date_range) {
        const s0 = state.info.date_range.start.slice(0, 10);
        const e0 = state.info.date_range.end.slice(0, 10);
        state.histRange = { start: s0, end: e0 };
        // keep every date filter aligned with the active dataset
        $('histStart').value = s0; $('histEnd').value = e0;
        $('dashStart').value = s0; $('dashEnd').value = e0;
      }
      try {
        state.run = await api('/model-comparison/?dataset_id=' + encodeURIComponent(id));
      } catch (e) { /* not trained yet */ }
    } catch (e) { toast(e.message, 'err'); }
    if (state.run) { state.predModel = state.run.best_model; }
  }

  $('datasetSelect').addEventListener('change', async (e) => {
    await setActive(e.target.value);
    const active = document.querySelector('.page.active').id.replace('page-', '');
    if (active === 'dashboard') loadDashboard();
    if (active === 'predictions' || active === 'comparison') loadRun();
    if (active === 'historical') loadHistorical();
    if (active === 'forecast') $('fcOut').innerHTML = '<div class="empty">Dataset changed — generate a new forecast.</div>';
  });

  /* ---------------------------------------------------------------- upload */

async function uploadFile(fileList) {
  const files = Array.from(fileList || []).filter(file =>
    file.name.toLowerCase().endsWith('.csv')
  );

  if (!files.length) {
    toast('Please select CSV files only.', 'err');
    return;
  }

  let lastDatasetId = null;
  let successCount = 0;

  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);

    toast('Uploading ' + esc(file.name) + '…');

    try {
      const res = await api('/upload-dataset/', {
        method: 'POST',
        body: fd
      });

      successCount++;
      lastDatasetId = res.dataset.id;

      toast(
        'Uploaded <b>' + esc(res.dataset.filename) +
        '</b> · ' + res.dataset.rows + ' rows × ' +
        res.dataset.columns + ' cols'
      );

    } catch (e) {
      toast('Failed: ' + esc(file.name) + ' · ' + e.message, 'err');
    }
  }

  if (successCount > 0) {
    await refreshDatasets(lastDatasetId);
    renderPreview(lastDatasetId);
    goto('upload');
  }

  toast(successCount + ' of ' + files.length + ' files uploaded.');
}

function wireDrop(zoneId, inputId) {
  const zone = $(zoneId);
  const input = $(inputId);

  input.multiple = true;
  input.accept = '.csv';

  zone.addEventListener('click', () => input.click());

  input.addEventListener('change', () => {
    uploadFile(input.files);
    input.value = '';
  });

  ['dragenter', 'dragover'].forEach(ev =>
    zone.addEventListener(ev, e => {
      e.preventDefault();
      zone.classList.add('drag');
    })
  );

  ['dragleave', 'drop'].forEach(ev =>
    zone.addEventListener(ev, e => {
      e.preventDefault();
      zone.classList.remove('drag');
    })
  );

  zone.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) {
      uploadFile(e.dataTransfer.files);
    }
  });
}

wireDrop('upZone', 'upFile');
wireDrop('homeUpload', 'homeFile');


  /* ---------------------------------------------------------------- preview */
  async function renderPreview(datasetId, tsCol, valCol) {
    let p;
    try {
      const qs = new URLSearchParams({ dataset_id: datasetId });
      if (tsCol) qs.set('timestamp_column', tsCol);
      if (valCol) qs.set('consumption_column', valCol);
      p = await api('/dataset/preview?' + qs.toString());
    } catch (e) { toast('Preview failed: ' + e.message, 'err'); return; }

    $('upProfileCard').style.display = '';
    $('upFileName').textContent = p.dataset.filename;
    $('upProfileSub').textContent = p.dataset.rows + ' rows × ' + p.dataset.columns + ' columns';

    $('upStats').innerHTML = [
      ['Rows', fmt(p.dataset.rows, 0)],
      ['Columns', fmt(p.dataset.columns, 0)],
      ['Missing cells', fmt(p.total_missing_cells, 0)],
      ['Duplicate rows', fmt(p.duplicate_rows, 0)],
    ].map(([k, v], i) => `<div class="metric ${['', 'cyan', 'amber', 'blue'][i]}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

    const tsOpts = p.columns.map(c => `<option value="${esc(c)}" ${c === p.selected.timestamp_column ? 'selected' : ''}>${esc(c)}</option>`).join('');
    const valOpts = p.columns.map(c => `<option value="${esc(c)}" ${c === p.selected.consumption_column ? 'selected' : ''}>${esc(c)}</option>`).join('');
    $('upTsCol').innerHTML = tsOpts;
    $('upValCol').innerHTML = valOpts;

    $('upCols').innerHTML = '<thead><tr><th>Column</th><th>Type</th></tr></thead><tbody>' +
      Object.entries(p.dtypes).map(([c, t]) => `<tr><td>${esc(c)}</td><td class="mono" style="font-size:11px;color:#93aec1">${esc(t)}</td></tr>`).join('') + '</tbody>';

    const cols = p.columns;
    $('upHead').innerHTML = '<thead><tr>' + cols.map(c => `<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
      p.head.map(r => '<tr>' + cols.map(c => `<td>${esc(r[c])}</td>`).join('') + '</tr>').join('') + '</tbody>';

    const det = p.detection.confident;
    $('upDetectNote').innerHTML = det
      ? `<div class="note">Detected timestamp <b>${esc(p.selected.timestamp_column)}</b> and consumption
         <b>${esc(p.selected.consumption_column)}</b>.${p.date_range ? ' Range: ' + esc(p.date_range.start) + ' → ' + esc(p.date_range.end) : ''}
         Change the selects above if this is wrong, then preprocess again.</div>`
      : `<div class="note warn">Column mapping is uncertain — please confirm the timestamp and consumption
         columns manually. Numeric columns found: ${p.numeric_columns.map(esc).join(', ') || 'none'}.</div>`;
    $('upPreprocessOut').innerHTML = '';
    state.uploadId = datasetId;
  }

  $('upPreprocess').addEventListener('click', () => runPreprocess(true));
  $('upRetrain').addEventListener('click', () => runPreprocess(true).then(() => trainModels()));

  async function runPreprocess(render) {
    const id = state.uploadId || state.activeId;
    if (!id) { toast('Upload a dataset first.', 'warn'); return; }
    const btn = $('upPreprocess');
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Preprocessing';
    try {
      const res = await api('/preprocess/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_id: id,
          timestamp_column: $('upTsCol').value || null,
          consumption_column: $('upValCol').value || null,
          cumulative_meter: $('upCumulative').value === 'true',
          duplicate_policy: $('upDup').value,
        }),
      });
      await setActive(id);
      const r = res.report;
      if (render) {
        $('upPreprocessOut').innerHTML = `
          <div class="note"><b>Preprocessing complete.</b> Grain detected: <b>${res.grain}</b> ·
            ${fmt(res.rows, 0)} valid records → ${fmt(res.base_rows, 0)} continuous observations.
            ${r.duplicate_timestamps ? 'Collapsed ' + fmt(r.duplicate_timestamps, 0) + ' duplicate timestamps (' + esc(r.duplicate_policy) + '). ' : ''}
            ${r.gaps_interpolated ? fmt(r.gaps_interpolated, 0) + ' missing periods interpolated. ' : ''}
            ${r.invalid_timestamps || r.invalid_values ? 'Dropped ' + (r.invalid_timestamps + r.invalid_values) + ' invalid rows. ' : ''}
            ${res.grain === 'annual' ? 'Annual data: at most 19 feature rows remain, so 3-model training will be refused as too sparse.' : ''}
          </div>`;
      }
      return res;
    } catch (e) {
      $('upPreprocessOut').innerHTML = `<div class="note err"><b>Preprocessing failed.</b> ${esc(e.message)}</div>`;
      toast('Preprocessing failed: ' + e.message, 'err');
    } finally { btn.disabled = false; btn.textContent = 'Preprocess & validate'; }
  }

  async function trainModels() {
    const id = state.uploadId || state.activeId;
    if (!id) { toast('Upload a dataset first.', 'warn'); return; }
    const btn = $('upRetrain');
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Training';
    try {
      const res = await api('/train-models/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_id: id }),
      });
      const m = res.run.metrics, best = res.run.best_model;
      toast('Trained 3 models · best: <b>' + best + '</b> (MAE ' + m[best].MAE.toFixed(2) + ' kWh)');
      await refreshDatasets(id);
      $('upPreprocessOut').insertAdjacentHTML('beforeend', `
        <div class="note" style="margin-top:8px"><b>Models trained.</b> Best: <b>${best}</b> ·
          MAE ${m[best].MAE.toFixed(3)} · RMSE ${m[best].RMSE.toFixed(3)} · R² ${m[best].R2.toFixed(4)}.
          Open <a href="#" data-goto="comparison" style="color:#6ee7bd;text-decoration:underline">Model Comparison</a>
          or <a href="#" data-goto="predictions" style="color:#6ee7bd;text-decoration:underline">ML Predictions</a>.</div>`);
      document.querySelectorAll('[data-goto]').forEach(b => { if (!b._wired) { b.addEventListener('click', () => goto(b.dataset.goto)); b._wired = true; } });
    } catch (e) {
      $('upPreprocessOut').innerHTML = `<div class="note err"><b>Training refused.</b> ${esc(e.message)}</div>`;
      toast('Training failed: ' + e.message, 'err');
    } finally { btn.disabled = false; btn.textContent = 'Train 3 models'; }
  }

  /* ---------------------------------------------------------------- run pages */
  async function loadRun() {
    const empty = (id, msg) => { $(id).style.display = ''; $(id).textContent = msg; };
    if (!state.activeId) { empty('predEmpty', 'Select or upload a dataset first.'); empty('cmpEmpty', 'Select or upload a dataset first.'); return; }
    try {
      state.run = await api('/model-comparison/?dataset_id=' + encodeURIComponent(state.activeId));
    } catch (e) {
      state.run = null;
      empty('predEmpty', 'No trained models for this dataset yet. Use Dataset Upload → Train 3 models.');
      empty('cmpEmpty', 'No trained models for this dataset yet. Use Dataset Upload → Train 3 models.');
      $('predBody').style.display = 'none'; $('cmpBody').style.display = 'none';
      return;
    }
    $('predEmpty').style.display = 'none'; $('cmpEmpty').style.display = 'none';
    $('predBody').style.display = ''; $('cmpBody').style.display = '';
    renderPredictions(); renderComparison();
  }

  function renderPredictions() {
    const run = state.run;
    if (!run) return;
    if (!run.predictions.models[state.predModel]) state.predModel = run.best_model;
    document.querySelectorAll('#predTabs button').forEach(b => b.classList.toggle('on', b.dataset.model === state.predModel));

    const met = run.metrics[state.predModel];
    $('predStats').innerHTML = [
      ['Model', state.predModel, 'cyan', run.best_model === state.predModel ? 'best on test MAE' : 'MAE ' + fmt(met.MAE, 2)],
      ['MAE', fmt(met.MAE, 2), '', 'kWh · test set'],
      ['RMSE', fmt(met.RMSE, 2), 'amber', 'kWh · test set'],
      ['R²', met.R2.toFixed(4), 'blue', met.n_test + ' test rows'],
    ].map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div><div class="v">${esc(v)}</div><div class="hint">${esc(h)}</div></div>`).join('');

    $('predSub').textContent = run.split.test_period.start + ' → ' + run.split.test_period.end;

    const dates = run.predictions.dates;
    draw('chPred', lineCfg(dates, [
      { label: 'Actual', data: run.predictions.actual, borderColor: PALETTE.emerald, backgroundColor: 'transparent' },
      { label: 'Predicted · ' + state.predModel, data: run.predictions.models[state.predModel], borderColor: PALETTE.cyan, borderDash: [5, 4], backgroundColor: 'transparent' },
    ]));

    const errs = run.predictions.actual.map((a, i) => Math.abs(a - run.predictions.models[state.predModel][i]));
    draw('chErr', barCfg(['Linear Regression', 'Decision Tree', 'Random Forest'],
      [{
        label: 'Mean absolute error (kWh)',
        data: ['Linear Regression', 'Decision Tree', 'Random Forest'].map(m => run.metrics[m].MAE),
        backgroundColor: ['Linear Regression', 'Decision Tree', 'Random Forest'].map(m => m === state.predModel ? PALETTE.emerald : 'rgba(34,201,155,.28)'),
      }], { options: { plugins: { legend: { display: false } }, scales: { x: axis, y: axis } } }));

    const res = run.predictions.residuals[state.predModel];
    const bins = 22, mn = Math.min.apply(null, res), mx = Math.max.apply(null, res);
    const width = (mx - mn) / bins || 1;
    const counts = new Array(bins).fill(0);
    res.forEach(v => { let i = Math.floor((v - mn) / width); if (i >= bins) i = bins - 1; if (i < 0) i = 0; counts[i]++; });
    const labels = counts.map((_, i) => (mn + i * width).toFixed(0));
    draw('chResid', barCfg(labels, [{
      label: 'Residual count', data: counts, backgroundColor: 'rgba(56,188,216,.55)',
    }], { options: { plugins: { legend: { display: false } }, scales: { x: Object.assign({}, axis, { title: { display: true, text: 'actual − predicted (kWh)', color: '#7d95a9', font: { size: 10 } } }), y: axis } } }));
    $('residSub').textContent = state.predModel;

    const rows = dates.slice(-80).map((d, i) => {
      const idx = dates.length - Math.min(80, dates.length) + i;
      const a = run.predictions.actual[idx], p = run.predictions.models[state.predModel][idx];
      return `<tr><td>${esc(d)}</td><td class="r">${fmt(a, 2)}</td><td class="r">${fmt(p, 2)}</td>
        <td class="r">${fmt(a - p, 2)}</td><td class="r">${fmt(Math.abs(a - p), 2)}</td></tr>`;
    }).join('');
    $('predTable').innerHTML = '<thead><tr><th>Period</th><th class="r">Actual kWh</th><th class="r">Predicted kWh</th><th class="r">Residual</th><th class="r">|Error|</th></tr></thead><tbody>' + rows + '</tbody>';
  }

  document.querySelectorAll('#predTabs button').forEach(b => b.addEventListener('click', () => {
    state.predModel = b.dataset.model; renderPredictions();
  }));
  $('predCsv').addEventListener('click', () => {
    if (!state.run) return;
    const run = state.run;
    const head = ['Period', 'Actual kWh', 'Linear Regression', 'Decision Tree', 'Random Forest'];
    const lines = [head.join(',')].concat(run.predictions.dates.map((d, i) =>
      [d, run.predictions.actual[i], run.predictions.models['Linear Regression'][i],
       run.predictions.models['Decision Tree'][i], run.predictions.models['Random Forest'][i]].join(',')));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    download(URL.createObjectURL(blob), 'holdout_predictions.csv');
  });

  function renderComparison() {
    const run = state.run;
    if (!run) return;
    const models = ['Linear Regression', 'Decision Tree', 'Random Forest'];
    const best = run.best_model;
    const bm = run.metrics[best];

    $('cmpStats').innerHTML = [
      ['Best model', best, '', 'lowest test MAE'],
      ['Best MAE', fmt(bm.MAE, 2), 'cyan', 'kWh'],
      ['Best RMSE', fmt(bm.RMSE, 2), 'amber', 'kWh'],
      ['Best R²', bm.R2.toFixed(4), 'blue', run.split.test_rows + ' test rows'],
    ].map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div><div class="v" style="${String(v).length > 14 ? 'font-size:15px' : ''}">${esc(v)}</div><div class="hint">${esc(h)}</div></div>`).join('');

    $('cmpTable').innerHTML = '<thead><tr><th>Model</th><th class="r">MAE</th><th class="r">RMSE</th><th class="r">R²</th><th class="r">MAPE</th></tr></thead><tbody>' +
      models.map(m => {
        const x = run.metrics[m];
        return `<tr class="${m === best ? 'best' : ''}"><td>${m}${m === best ? ' <span class="badge best">BEST</span>' : ''}</td>
          <td class="r">${fmt(x.MAE, 3)}</td><td class="r">${fmt(x.RMSE, 3)}</td>
          <td class="r">${x.R2.toFixed(4)}</td><td class="r">${x.MAPE === null ? '—' : fmt(x.MAPE, 2) + '%'}</td></tr>`;
      }).join('') + '</tbody>';

    renderCmpChart();

    const fi = run.feature_importance['Random Forest'].slice(0, 10);
    draw('chFI', { type: 'bar', data: { labels: fi.map(f => f.feature), datasets: [{ label: 'Importance', data: fi.map(f => +(f.importance * 100).toFixed(2)), backgroundColor: 'rgba(34,201,155,.6)' }] },
      options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: Object.assign({}, axis, { ticks: { color: '#7d95a9', callback: v => v + '%' } }), y: Object.assign({}, axis, { ticks: { color: '#9fb4c4', font: { size: 10.5 } } }) } } });
    $('fiSub').textContent = 'Random Forest · ' + run.predictions.dates.length + ' test points';

    $('cmpSetup').innerHTML = [
      ['Dataset', esc(state.info ? state.info.filename : '—')],
      ['Granularity', esc(run.split ? state.grain || '—' : '—')],
      ['Feature rows', fmt(run.split.train_rows + run.split.test_rows, 0)],
      ['Train rows', fmt(run.split.train_rows, 0)],
      ['Test rows', fmt(run.split.test_rows, 0)],
      ['Train period', esc(run.split.train_period.start) + ' → ' + esc(run.split.train_period.end)],
      ['Test period', esc(run.split.test_period.start) + ' → ' + esc(run.split.test_period.end)],
      ['Trained at', esc(run.trained_at)],
      ['Split policy', 'chronological 80/20, no shuffling'],
    ].map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join('') +
      `<div class="note" style="margin-top:10px">${esc(run.split.note)}</div>`;
  }

  function renderCmpChart() {
    const run = state.run;
    const models = ['Linear Regression', 'Decision Tree', 'Random Forest'];
    const metric = state.cmpMetric;
    const values = models.map(m => run.metrics[m][metric]);
    const bestVal = Math.min.apply(null, values);
    draw('chCmp', barCfg(models, [{
      label: metric, data: values,
      backgroundColor: models.map(m => run.metrics[m][metric] === bestVal ? PALETTE.emerald : 'rgba(34,201,155,.28)'),
    }], { options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => metric + ': ' + c.parsed.y.toFixed(4) } } }, scales: { x: Object.assign({}, axis, { ticks: { color: '#9fb4c4', font: { size: 10.5 } } }), y: axis } } }));
  }

  document.querySelectorAll('#cmpTabs button').forEach(b => b.addEventListener('click', () => {
    state.cmpMetric = b.dataset.metric;
    document.querySelectorAll('#cmpTabs button').forEach(x => x.classList.toggle('on', x === b));
    if (state.run) renderCmpChart();
  }));

  /* ---------------------------------------------------------------- dashboard */
  async function loadDashboard() {
    try {
      const res = await api('/dashboard/summary' + (state.activeId ? '?dataset_id=' + encodeURIComponent(state.activeId) : ''));
      const liveStat = res.live || { n: 0, m: 0 };
      $('homeLiveCount').textContent = fmt(liveStat.n, 0);
      $('dashLiveChip').textContent = liveStat.n ? liveStat.m + ' meter(s) · ' + liveStat.n + ' readings' : 'no readings';

      if (!res.summary) {
        $('dashMetrics').innerHTML = '<div class="empty" style="grid-column:1/-1">No dataset yet — upload one or load the bundled CEEW data from Home.</div>';
        return;
      }
      const s = res.summary;
      const best = s.best_model;
      let forecastAvg = null;
      try {
        const fc = await api('/forecast/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: s.dataset_id, horizon: 7, interval: 'daily' }) });
        if (fc.available) forecastAvg = fc.summary.average_predicted_kwh;
      } catch (e) { /* training missing */ }

      $('dashMetrics').innerHTML = [
        ['Total consumption', fmt(s.total_kwh, 0) + '<small>kWh</small>', '', s.records + ' recorded intervals · ' + esc(s.grain) + ' grain', ''],
        ['Average per interval', fmt(s.average_kwh, 2) + '<small>kWh</small>', 'cyan', 'mean of the continuous series'],
        ['Peak interval', fmt(s.peak.kwh, 2) + '<small>kWh</small>', 'amber', esc(s.peak.dt)],
        ['Best ML model', best || 'not trained', 'blue', best && s.metrics ? 'MAE ' + fmt(s.metrics[best].MAE, 2) + ' · R² ' + s.metrics[best].R2.toFixed(3) : 'train from Dataset Upload'],
        ['Latest live reading', liveStat.n ? fmt((res.meters[0] || {}).latest ? res.meters[0].latest.energy_consumption : 0, 2) + '<small>kWh</small>' : '—', '', liveStat.n ? 'meter ' + esc(res.meters[0].meter_id) : 'no readings yet'],
        ['Forecast next 7 days', forecastAvg !== null ? fmt(forecastAvg, 2) + '<small>kWh/day</small>' : '—', 'cyan', forecastAvg !== null ? 'average predicted daily consumption' : 'train a model first'],
        ['Active meters', fmt(liveStat.m, 0), '', 'distinct meter IDs in SQLite'],
        ['Dataset', esc(s.filename), 'blue', s.period.start + ' → ' + s.period.end],
      ].map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div>
        <div class="v" style="${String(v).length > 16 ? 'font-size:14px' : ''}">${v}</div>
        <div class="hint">${h}</div></div>`).join('');

      if (s.period) {
        if (!$('dashStart').value) { $('dashStart').value = s.period.start; $('dashEnd').value = s.period.end; }
      }
      await renderDashChart();

      $('dashHvFSub').textContent = best ? best + ' · recursive 7-day' : 'no trained model';
      if (best) {
        try {
          const hvf = await api('/forecast/history-vs-prediction?dataset_id=' + s.dataset_id + '&model=best&horizon=7&interval=daily&tail=45');
          const labels = hvf.history.map(h => h.label).concat(hvf.forecast.points.map(p => p.label));
          const hist = hvf.history.map(h => h.kwh).concat(new Array(hvf.forecast.points.length).fill(null));
          const fut = new Array(hvf.history.length ? hvf.history.length - 1 : 0).fill(null)
            .concat([hvf.history.length ? hvf.history[hvf.history.length - 1].kwh : null])
            .concat(hvf.forecast.points.map(p => p.predicted_kwh));
          draw('chDashHvF', lineCfg(labels, [
            { label: 'Historical', data: hist, borderColor: PALETTE.emerald, backgroundColor: PALETTE.emeraldDim, fill: true },
            { label: 'Predicted', data: fut, borderColor: PALETTE.amber, borderDash: [5, 4], spanGaps: true },
          ]));
        } catch (e) { $('dashHvFSub').textContent = 'forecast unavailable'; }
      } else {
        if (charts['chDashHvF']) { charts['chDashHvF'].destroy(); delete charts['chDashHvF']; }
      }
      await renderDashLive();
    } catch (e) { toast('Dashboard error: ' + e.message, 'err'); }
  }

  async function renderDashChart() {
    if (!state.activeId) return;
    const qs = new URLSearchParams({ dataset_id: state.activeId });
    if ($('dashStart').value) qs.set('start', $('dashStart').value);
    if ($('dashEnd').value) qs.set('end', $('dashEnd').value);
    try {
      const agg = await api('/historical/' + state.dashInterval + '?' + qs.toString());
      if (!agg.available) {
        if (charts['chDash']) { charts['chDash'].destroy(); delete charts['chDash']; }
        toast(agg.message, 'warn');
        return;
      }
      const labels = agg.buckets.map(b => b.label);
      const data = agg.buckets.map(b => b.total_kwh);
      const isBar = ['weekly', 'monthly', 'yearly'].includes(state.dashInterval);
      const cfg = isBar
        ? barCfg(labels, [{ label: 'Total kWh', data, backgroundColor: 'rgba(34,201,155,.55)' }], { options: { plugins: { legend: { display: false } }, scales: { x: axis, y: axis } } })
        : lineCfg(labels, [{ label: 'Total kWh', data, borderColor: PALETTE.emerald, backgroundColor: PALETTE.emeraldDim, fill: true }]);
      draw('chDash', cfg);
    } catch (e) { toast('Chart error: ' + e.message, 'err'); }
  }

  document.querySelectorAll('#dashTabs button').forEach(b => b.addEventListener('click', () => {
    state.dashInterval = b.dataset.iv;
    document.querySelectorAll('#dashTabs button').forEach(x => x.classList.toggle('on', x === b));
    renderDashChart();
  }));
  $('dashApply').addEventListener('click', () => { loadDashboard(); });

  async function renderDashLive() {
    try {
      const res = await api('/live-api/readings?limit=60');
      if (!res.readings.length) {
        $('dashLiveEmpty').style.display = '';
        if (charts['chDashLive']) { charts['chDashLive'].destroy(); delete charts['chDashLive']; }
        return;
      }
      $('dashLiveEmpty').style.display = 'none';
      const rows = res.readings.slice().reverse();
      draw('chDashLive', lineCfg(rows.map(r => r.timestamp.replace('T', ' ').replace('Z', '')), [{
        label: 'Live kWh', data: rows.map(r => r.energy_consumption),
        borderColor: PALETTE.cyan, backgroundColor: 'rgba(56,188,216,.14)', fill: true,
      }]));
    } catch (e) { /* ignore */ }
  }

  /* ---------------------------------------------------------------- historical */
  document.querySelectorAll('#histTabs button').forEach(b => b.addEventListener('click', () => {
    state.histInterval = b.dataset.iv;
    document.querySelectorAll('#histTabs button').forEach(x => x.classList.toggle('on', x === b));
    loadHistorical();
  }));
  $('histApply').addEventListener('click', () => loadHistorical());

  async function loadHistorical() {
    if (!state.activeId) { $('histBody').innerHTML = '<div class="empty">Select a dataset first.</div>'; return; }
    const iv = state.histInterval;
    $('histBody').innerHTML = '<div class="empty">Aggregating…</div>';
    const qs = new URLSearchParams({ dataset_id: state.activeId });
    if ($('histStart').value) qs.set('start', $('histStart').value);
    if ($('histEnd').value) qs.set('end', $('histEnd').value);
    try {
      const agg = await api('/historical/' + iv + '?' + qs.toString());
      if (!agg.available) {
        $('histBody').innerHTML = `<div class="note warn">${esc(agg.message)}</div>`;
        if (charts['chHist2']) { charts['chHist2'].destroy(); delete charts['chHist2']; }
        if (charts['chPeak']) { charts['chPeak'].destroy(); delete charts['chPeak']; }
        $('histTable').innerHTML = ''; return;
      }
      const s = agg.summary;
      $('histBody').innerHTML = `
        <div class="grid g4" style="margin-bottom:12px">
          ${[['Buckets', fmt(s.buckets, 0), iv + ' periods'],
             ['Total', fmt(s.total_kwh, 0) + '<small>kWh</small>', 'sum of interval energy'],
             ['Average', fmt(s.average_kwh, 2) + '<small>kWh</small>', 'per bucket'],
             ['Peak bucket', fmt(s.peak.total_kwh, 0) + '<small>kWh</small>', esc(s.peak.label)]]
            .map(([k, v, h], i) => `<div class="metric ${['', 'cyan', 'blue', 'amber'][i]}"><div class="k">${k}</div>
              <div class="v" style="font-size:${String(v).length > 14 ? 15 : 21}px">${v}</div><div class="hint">${h}</div></div>`).join('')}
        </div>
        <div class="chart-box h-300"><canvas id="chHist1"></canvas></div>
        <div class="sub" style="font-size:11.5px;color:var(--dim);margin-top:8px">${esc(agg.note)}</div>`;

      const labels = agg.buckets.map(b => b.label);
      const data = agg.buckets.map(b => b.total_kwh);
      const isBar = ['weekly', 'monthly', 'yearly'].includes(iv);
      const cfg = isBar
        ? barCfg(labels, [{ label: 'Total kWh', data, backgroundColor: 'rgba(34,201,155,.55)' }], { options: { plugins: { legend: { display: false } }, scales: { x: axis, y: axis } } })
        : lineCfg(labels, [{ label: 'Total kWh', data, borderColor: PALETTE.emerald, backgroundColor: PALETTE.emeraldDim, fill: true }]);
      draw('chHist1', cfg);

      const dist = await api('/historical/distribution?dataset_id=' + encodeURIComponent(state.activeId) + '&bins=24');
      draw('chHist2', barCfg(dist.bins.slice(0, -1).map((b, i) => b.toFixed(0) + '–' + dist.bins[i + 1].toFixed(0)),
        [{ label: 'Count', data: dist.counts, backgroundColor: 'rgba(91,147,234,.55)' }],
        { options: { plugins: { legend: { display: false } }, scales: { x: Object.assign({}, axis, { ticks: { color: '#7d95a9', maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } }), y: axis } } }));

      draw('chPeak', barCfg(['Lowest', 'Average', 'Peak'], [{
        label: 'kWh', data: [s.lowest.total_kwh, s.average_kwh, s.peak.total_kwh],
        backgroundColor: ['rgba(139,166,185,.5)', 'rgba(34,201,155,.5)', 'rgba(224,163,74,.6)'],
      }], { options: { plugins: { legend: { display: false } }, scales: { x: axis, y: axis } } }));
      $('histCmpSub').textContent = iv + ' buckets';
      $('histTableSub').textContent = s.buckets + ' ' + iv + ' buckets · ' + esc(agg.grain) + ' source grain';
      $('histTable').innerHTML = '<thead><tr><th>Period</th><th class="r">Total kWh</th><th class="r">Average kWh</th><th class="r">Records</th></tr></thead><tbody>' +
        agg.buckets.slice().reverse().map(b => `<tr><td>${esc(b.label)}</td><td class="r">${fmt(b.total_kwh, 2)}</td>
          <td class="r">${fmt(b.avg_kwh, 2)}</td><td class="r">${b.records}</td></tr>`).join('') + '</tbody>';
      state.histAgg = agg;
    } catch (e) {
      $('histBody').innerHTML = `<div class="note err">${esc(e.message)}</div>`;
    }
  }

  $('histCsv').addEventListener('click', () => {
    if (!state.activeId) return;
    const qs = new URLSearchParams({ dataset_id: state.activeId, interval: state.histInterval, fmt: 'csv' });
    if ($('histStart').value) qs.set('start', $('histStart').value);
    if ($('histEnd').value) qs.set('end', $('histEnd').value);
    download('/reports/historical?' + qs.toString());
  });
  $('histPdf').addEventListener('click', () => {
    if (!state.activeId) return;
    const qs = new URLSearchParams({ dataset_id: state.activeId, interval: state.histInterval, fmt: 'pdf' });
    download('/reports/historical?' + qs.toString());
  });

  /* ---------------------------------------------------------------- live */
  async function loadLive() {
    try {
      const [res, meters] = await Promise.all([api('/live-api/readings?limit=100'), api('/live-api/meters')]);
      const stats = meters.stats || {};
      const last = res.readings[0];
      const latestMeter = meters.meters[0];
      $('liveStats').innerHTML = [
        ['Stored readings', fmt(stats.n || 0, 0), '', 'SQLite · live_readings'],
        ['Active meters', fmt(stats.m || 0, 0), 'cyan', 'distinct meter IDs'],
        ['Latest reading', last ? fmt(last.energy_consumption, 2) + '<small>kWh</small>' : '—', 'blue', last ? esc(last.meter_id) : 'none yet'],
        ['Last received', last ? esc(last.timestamp.replace('T', ' ').replace('Z', '')) : '—', 'amber', last ? esc(last.status) : 'waiting'],
      ].map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div>
        <div class="v" style="font-size:${String(v).length > 14 ? 14 : 21}px">${v}</div><div class="hint">${h}</div></div>`).join('');

      const filter = $('liveMeterFilter').value;
      const sel = $('liveMeterFilter');
      const current = sel.value;
      sel.innerHTML = '<option value="">All meters</option>' +
        meters.meters.map(m => `<option value="${esc(m.meter_id)}">${esc(m.meter_id)}</option>`).join('');
      sel.value = current;

      const rows = res.readings.filter(r => !filter || r.meter_id === filter).slice().reverse();
      $('liveEmpty').style.display = rows.length ? 'none' : '';
      if (rows.length) {
        draw('chLive', lineCfg(rows.map(r => r.timestamp.replace('T', ' ').replace('Z', '')),
          [{ label: 'kWh', data: rows.map(r => r.energy_consumption), borderColor: PALETTE.cyan, backgroundColor: 'rgba(56,188,216,.14)', fill: true }],
          { options: { plugins: { legend: { display: false } }, scales: { x: Object.assign({}, axis, { ticks: { color: '#7d95a9', maxRotation: 0, autoSkip: true, maxTicksLimit: 6 } }), y: axis } } }));
      } else if (charts['chLive']) { charts['chLive'].destroy(); delete charts['chLive']; }

      $('liveLast').textContent = last
        ? 'Last successful fetch: ' + new Date().toLocaleTimeString() + ' · newest reading ' + last.timestamp
        : 'No readings stored yet.';

      $('liveMeters').innerHTML = '<thead><tr><th>Meter</th><th class="r">Readings</th><th class="r">Latest kWh</th><th>Last seen</th></tr></thead><tbody>' +
        (meters.meters.length ? meters.meters.map(m => `<tr><td class="mono">${esc(m.meter_id)}</td>
          <td class="r">${m.readings}</td><td class="r">${m.latest ? fmt(m.latest.energy_consumption, 2) : '—'}</td>
          <td>${esc((m.last_seen || '').replace('T', ' ').replace('Z', ''))}</td></tr>`).join('')
          : '<tr><td colspan="4" style="color:var(--dim)">No meters yet.</td></tr>') + '</tbody>';

      $('liveHistSub').textContent = res.readings.length + ' shown';
      $('liveTable').innerHTML = '<thead><tr><th>Meter</th><th class="r">kWh</th><th>Timestamp</th><th>Status</th></tr></thead><tbody>' +
        (res.readings.length ? res.readings.map(r => `<tr><td class="mono">${esc(r.meter_id)}</td>
          <td class="r">${fmt(r.energy_consumption, 2)}</td>
          <td>${esc(r.timestamp.replace('T', ' ').replace('Z', ''))}</td>
          <td><span class="badge ${r.status === 'FAULT' ? 'err' : r.status === 'IDLE' ? 'warn' : 'best'}">${esc(r.status)}</span></td></tr>`).join('')
          : '<tr><td colspan="4" style="color:var(--dim)">No readings.</td></tr>') + '</tbody>';

      $('apiChip').className = 'chip ok'; $('apiChip').textContent = 'API · connected';
      $('sfDot').className = 'dot-live';
      $('sfStatus').textContent = 'API connected · ' + (stats.n || 0) + ' live readings';
    } catch (e) {
      $('apiChip').className = 'chip err'; $('apiChip').textContent = 'API · offline';
      $('sfDot').className = 'dot-live off'; $('sfStatus').textContent = 'API unreachable';
    }
  }

  $('livePost').addEventListener('click', async () => {
    const meter = $('liveMeter').value.trim();
    const kwh = parseFloat($('liveKwh').value);
    if (!meter || isNaN(kwh) || kwh < 0) { toast('Enter a meter ID and a non-negative kWh value.', 'warn'); return; }
    const btn = $('livePost'); btn.disabled = true;
    try {
      const res = await api('/live-api/readings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ meter_id: meter, energy_consumption: kwh, status: $('liveStatus').value }),
      });
      $('livePostOut').innerHTML = `<div class="note">Stored reading #${res.data.id} · ${esc(res.data.meter_id)} · ${fmt(res.data.energy_consumption, 2)} kWh at ${esc(res.data.timestamp)}</div>`;
      $('liveKwh').value = '';
      await loadLive();
    } catch (e) {
      $('livePostOut').innerHTML = `<div class="note err">${esc(e.message)}</div>`;
    } finally { btn.disabled = false; }
  });

  $('liveDelete').addEventListener('click', async () => {
    if (!confirm('Delete all live readings from SQLite?')) return;
    try { await api('/live-api/readings', { method: 'DELETE' }); toast('Live readings cleared.'); await loadLive(); }
    catch (e) { toast(e.message, 'err'); }
  });
  $('liveRefresh').addEventListener('click', loadLive);
  $('liveMeterFilter').addEventListener('change', loadLive);
  setInterval(() => {
    if ($('page-live').classList.contains('active') && $('liveAuto').checked) loadLive();
  }, 5000);
  
  // Dataset Live Simulator
  let simRunning = false;
  let simCurrent = 0;
  let simTotal = 0;
  let simTimer = null;

  $('simStart').addEventListener('click', async () => {
    console.log("START BUTTON CLICKED!");
    const datasetId = $('simDatasetId').value.trim();

    if (!datasetId) {
      toast('Enter a dataset ID.', 'warn');
      return;
    }

    const btn = $('simStart');
    btn.disabled = true;

    try {
      const result = await api(
        '/live-api/simulator/start?dataset_id=' + encodeURIComponent(datasetId),
        { method: 'POST' }
      );

      simCurrent = 0;
      simTotal = result.records;
      simRunning = true;

      $('simStatus').textContent =
        'Simulator running · ' + result.filename +
        ' · Historical data simulation';

      $('simProgressText').textContent = '0 / ' + simTotal;
      $('simProgressBar').style.width = '0%';

      $('simStart').disabled = true;
      $('simStop').disabled = false;

      simTimer = setInterval(async () => {
        if (!simRunning) return;

        try {
          const next = await api(
            '/live-api/simulator/next?dataset_id=' +
            encodeURIComponent(datasetId),
            { method: 'POST' }
          );

          if (next.status === 'completed') {
            clearInterval(simTimer);
            simTimer = null;
            simRunning = false;
            $('simStart').disabled = false;
            $('simStop').disabled = true;
            $('simStatus').textContent =
              'Simulation completed · ' + simTotal + ' records.';
            return;
          }

          if (next.status === 'success') {
            simCurrent = next.progress.current;

            $('simProgressText').textContent =
              simCurrent + ' / ' + simTotal;

            $('simProgressBar').style.width =
              (simCurrent / simTotal * 100) + '%';

            $('simStatus').textContent =
              'Historical simulation running · ' +
              next.data.energy_consumption.toFixed(2) + ' kWh';

            await loadLive();
          }
        } catch (error) {
          clearInterval(simTimer);
          simTimer = null;
          simRunning = false;
          $('simStart').disabled = false;
          $('simStop').disabled = true;
          $('simStatus').textContent = 'Simulator error: ' + error.message;
        }
      }, 5000);

    } catch (error) {
      $('simStatus').textContent = 'Error: ' + error.message;
      $('simStart').disabled = false;
    }
  });

  $('simStop').addEventListener('click', () => {
    simRunning = false;

    if (simTimer) {
      clearInterval(simTimer);
      simTimer = null;
    }

    $('simStart').disabled = false;
    $('simStop').disabled = true;
    $('simStatus').textContent =
      'Simulation paused · ' + simCurrent + ' / ' + simTotal;
  });


  /* ---------------------------------------------------------------- forecast */
  $('fcRun').addEventListener('click', runForecast);

  async function runForecast() {
    if (!state.activeId) { $('fcOut').innerHTML = '<div class="empty">Select a dataset first.</div>'; return; }
    const btn = $('fcRun'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Running';
    try {
      const res = await api('/forecast/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_id: state.activeId, model: $('fcModel').value,
          horizon: parseInt($('fcHorizon').value, 10), interval: $('fcInterval').value,
        }),
      });
      if (!res.available) {
        $('fcOut').innerHTML = `<div class="note warn"><b>Forecast not available.</b> ${esc(res.message)}</div>`;
        state.forecast = null; return;
      }
      state.forecast = res;
      const s = res.summary;
      const hist = await api('/historical/' + res.interval + '?dataset_id=' + encodeURIComponent(state.activeId));
      const histPoints = hist.available ? hist.buckets.slice(-40) : [];
      $('fcOut').innerHTML = `
        <div class="grid g4" style="margin-bottom:12px">
          ${[['Model', res.model, '', 'trained on ' + esc(res.grain) + ' grain'],
             ['Forecast points', fmt(s.horizon_points, 0), 'cyan', esc(res.interval) + ' interval'],
             ['Total predicted', fmt(s.total_predicted_kwh, 0) + '<small>kWh</small>', 'blue', 'sum over horizon'],
             ['Expected peak', fmt(s.peak.predicted_kwh, 0) + '<small>kWh</small>', 'amber', esc(s.peak.label)]]
            .map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div>
              <div class="v" style="font-size:${String(v).length > 14 ? 14 : 21}px">${v}</div><div class="hint">${h}</div></div>`).join('')}
        </div>
        <div class="card" style="margin-bottom:12px">
          <div class="card-h"><h3>Historical vs future</h3><span class="sub">last ${histPoints.length} ${esc(res.interval)} buckets + ${s.horizon_points} predicted</span>
            <div class="right"><button class="btn sm ghost" id="fcCsv">CSV</button><button class="btn sm ghost" id="fcPdf">PDF</button></div></div>
          <div class="card-b"><div class="chart-box h-300"><canvas id="chFc"></canvas></div>
            <div class="note" style="margin-top:10px">${esc(res.method)}</div></div>
        </div>
        <div class="card">
          <div class="card-h"><h3>Forecast table</h3><span class="sub">${esc(s.change_vs_recent_pct)} % vs recent average (${esc(s.recent_actual_average_kwh)} kWh)</span></div>
          <div class="card-b tbl-wrap" style="max-height:340px">
            <table class="tbl"><thead><tr><th>Period</th><th class="r">Predicted kWh</th><th class="r">Share of total</th></tr></thead>
            <tbody>${res.points.map(p => `<tr><td>${esc(p.label)}</td><td class="r">${fmt(p.predicted_kwh, 2)}</td>
              <td class="r">${fmt(p.predicted_kwh / s.total_predicted_kwh * 100, 1)}%</td></tr>`).join('')}</tbody></table>
          </div>
        </div>`;

      const labels = histPoints.map(h => h.label).concat(res.points.map(p => p.label));
      const histData = histPoints.map(h => h.total_kwh).concat(new Array(res.points.length).fill(null));
      const futData = new Array(Math.max(0, histPoints.length - 1)).fill(null)
        .concat([histPoints.length ? histPoints[histPoints.length - 1].total_kwh : null])
        .concat(res.points.map(p => p.predicted_kwh));
      draw('chFc', lineCfg(labels, [
        { label: 'Historical', data: histData, borderColor: PALETTE.emerald, backgroundColor: PALETTE.emeraldDim, fill: true },
        { label: 'Predicted · ' + res.model, data: futData, borderColor: PALETTE.amber, borderDash: [5, 4], spanGaps: true },
      ]));
      $('fcCsv').addEventListener('click', () => downloadForecast('csv'));
      $('fcPdf').addEventListener('click', () => downloadForecast('pdf'));
    } catch (e) {
      $('fcOut').innerHTML = `<div class="note err"><b>Forecast refused.</b> ${esc(e.message)}</div>`;
    } finally { btn.disabled = false; btn.textContent = 'Generate forecast'; }
  }

  function downloadForecast(fmt) {
    if (!state.forecast) { toast('Generate a forecast first.', 'warn'); return; }
    const qs = new URLSearchParams({
      dataset_id: state.activeId, model: state.forecast.model, interval: state.forecast.interval,
      horizon: ($('fcInterval').value === 'hourly' && state.grain === 'hourly') ? parseInt($('fcHorizon').value, 10) : parseInt($('fcHorizon').value, 10),
      fmt,
    });
    download('/reports/predictions?' + qs.toString());
  }

  /* ---------------------------------------------------------------- location */
  async function searchLocation(q, host) {
    const box = typeof host === 'string' ? $(host) : host;
    if (!q || q.length < 2) { box.innerHTML = '<div class="note warn">Type at least two characters.</div>'; return; }
    box.innerHTML = '<div class="sub" style="color:var(--dim);font-size:12px">Searching…</div>';
    try {
      const res = await api('/location/search?q=' + encodeURIComponent(q));
      if (!res.results.length) {
        box.innerHTML = '<div class="note warn">No country match in the OWID energy dataset. Try the full country name.</div>';
        return;
      }
      box.innerHTML = res.results.map(r => `
        <button class="btn sm ghost" data-country="${esc(r.country)}" style="margin:0 6px 6px 0">
          ${esc(r.country)} <span class="badge dim">${r.first_year}–${r.last_year}</span>
        </button>`).join('');
      box.querySelectorAll('[data-country]').forEach(b => b.addEventListener('click', () => loadLocation(b.dataset.country)));
    } catch (e) { box.innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
  }

  async function loadLocation(country) {
    $('locQuery').value = country;
    const metric = $('locMetric').value;
    const qs = new URLSearchParams({ country, metric });
    if ($('locStart').value) qs.set('start_year', $('locStart').value);
    if ($('locEnd').value) qs.set('end_year', $('locEnd').value);
    try {
      const d = await api('/location/energy-data?' + qs.toString());
      state.locData = d;
      $('locDataCard').style.display = '';
      $('locTableCard').style.display = '';
      $('locDataTitle').textContent = d.location.country + ' — ' + d.metric_label;
      $('locDataSub').textContent = d.coverage.first_year + '–' + d.coverage.last_year + ' · ' + d.unit;

      const vals = d.series.map(s => s.value);
      $('locStats').innerHTML = [
        ['Observations', fmt(d.coverage.observations, 0), '', 'annual values published'],
        ['Latest', fmt(vals[vals.length - 1], 2) + '<small>' + d.unit + '</small>', 'cyan', d.series[d.series.length - 1].year],
        ['Average', fmt(vals.reduce((a, b) => a + b, 0) / vals.length, 2) + '<small>' + d.unit + '</small>', 'blue', 'over the period'],
        ['Peak year', fmt(Math.max.apply(null, vals), 2) + '<small>' + d.unit + '</small>', 'amber', d.series[vals.indexOf(Math.max.apply(null, vals))].year],
      ].map(([k, v, c, h]) => `<div class="metric ${c}"><div class="k">${k}</div><div class="v" style="font-size:${String(v).length > 14 ? 14 : 21}px">${v}</div><div class="hint">${esc(h)}</div></div>`).join('');

      draw('chLoc', lineCfg(d.series.map(s => s.year), [{
        label: d.metric_label + ' (' + d.unit + ')', data: d.series.map(s => s.value),
        borderColor: PALETTE.cyan, backgroundColor: 'rgba(56,188,216,.13)', fill: true,
      }]));
      $('locNote').innerHTML = d.note
        ? `<div class="note warn">${esc(d.note)}</div>`
        : `<div class="note">Source: ${esc(d.source.name)} — ${esc(d.source.detail)} (${esc(d.source.licence)}).</div>`;
      $('locTable').innerHTML = '<thead><tr><th>Year</th><th class="r">' + esc(d.metric_label) + ' (' + esc(d.unit) + ')</th></tr></thead><tbody>' +
        d.series.slice().reverse().map(s => `<tr><td>${s.year}</td><td class="r">${fmt(s.value, 3)}</td></tr>`).join('') + '</tbody>';
    } catch (e) {
      $('locDataCard').style.display = 'none';
      toast(e.message, 'err');
    }
  }

  $('locSearchBtn').addEventListener('click', () => searchLocation($('locQuery').value, 'locResults'));
  $('locQuery').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); searchLocation($('locQuery').value, 'locResults'); } });
  $('locClearBtn').addEventListener('click', () => {
    $('locQuery').value = ''; $('locResults').innerHTML = '';
    $('locDataCard').style.display = 'none'; $('locTableCard').style.display = 'none';
  });
  $('locMetric').addEventListener('change', () => { if (state.locData) loadLocation(state.locData.location.country); });

  $('locImportBtn').addEventListener('click', async () => {
    if (!state.locData) return;
    const btn = $('locImportBtn'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Importing';
    try {
      const res = await api('/location/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ country: state.locData.location.country, metric: state.locData.metric }),
      });
      await refreshDatasets(res.dataset_id);
      if (res.trainable) {
        toast('Imported <b>' + esc(state.locData.location.country) + '</b> and trained 3 models.');
        goto('comparison');
      } else {
        toast('Imported as dataset, but ML training was refused: ' + esc(res.train_error).slice(0, 120), 'warn');
        $('locNote').innerHTML = `<div class="note warn"><b>Imported, but not trainable.</b> ${esc(res.train_error)}</div>`;
        goto('upload');
        renderPreview(res.dataset_id);
      }
    } catch (e) { toast(e.message, 'err'); }
    finally { btn.disabled = false; btn.textContent = 'Import as dataset'; }
  });

  $('homeLocBtn').addEventListener('click', () => {
    searchLocation($('homeLocInput').value, 'homeLocResults');
  });
  $('homeLocInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('homeLocBtn').click(); }
  });

  /* ---------------------------------------------------------------- seed loads */
  async function loadSeed(kind) {
    try {
      const res = await api('/seed-dataset?kind=' + kind, { method: 'POST' });
      toast('Loaded ' + esc(res.dataset.filename) + ' (' + res.dataset.rows + ' rows)');
      await refreshDatasets(res.dataset.id);
      await setActive(res.dataset.id);
      if (res.run) toast('Trained 3 models · best: <b>' + res.run.best_model + '</b>');
      goto('dashboard');
    } catch (e) { toast(e.message, 'err'); }
  }
  $('loadSeedBtn').addEventListener('click', () => loadSeed('mathura'));
  $('loadSeedAllBtn').addEventListener('click', () => loadSeed('combined'));

  /* ---------------------------------------------------------------- reports */
  document.querySelectorAll('[data-report]').forEach(b => b.addEventListener('click', () => {
    const kind = b.dataset.report, f = b.dataset.fmt;
    if (kind === 'live') { download('/reports/live?fmt=' + f); return; }
    if (!state.activeId) { toast('Select a dataset first.', 'warn'); return; }
    const qs = new URLSearchParams({ dataset_id: state.activeId, fmt: f });
    if (kind === 'predictions') {
      qs.set('model', $('fcModel').value);
      qs.set('interval', $('fcInterval').value);
      qs.set('horizon', $('fcHorizon').value);
    }
    download('/reports/' + kind + '?' + qs.toString());
  }));
  $('repHistCsv').addEventListener('click', () => {
    if (!state.activeId) return;
    download('/reports/historical?' + new URLSearchParams({ dataset_id: state.activeId, interval: state.histInterval, fmt: 'csv' }).toString());
  });
  $('repHistPdf').addEventListener('click', () => {
    if (!state.activeId) return;
    download('/reports/historical?' + new URLSearchParams({ dataset_id: state.activeId, interval: state.histInterval, fmt: 'pdf' }).toString());
  });

  const ENDPOINTS = [
    ['GET', '/health', 'service + SQLite state'],
    ['POST', '/upload-dataset/', 'CSV / Excel upload'],
    ['GET', '/dataset/preview', 'columns, dtypes, head, missing values'],
    ['GET', '/dataset/info', 'dataset metadata'],
    ['POST', '/preprocess/', 'clean, dedupe, resample, validate'],
    ['POST', '/train-models/', 'Linear Regression · Decision Tree · Random Forest'],
    ['GET', '/model-comparison/', 'MAE / RMSE / R² + predictions'],
    ['POST', '/predict/ · /forecast/', 'recursive forecast'],
    ['GET', '/historical/{hourly…yearly}', 'interval aggregation'],
    ['GET', '/live-api/readings', 'list live readings'],
    ['POST', '/live-api/readings', 'store a live reading'],
    ['GET', '/live-api/history · /meters', 'history & meter list'],
    ['GET', '/location/search · /energy-data', 'OWID country data'],
    ['GET', '/reports/predictions · /comparison · /historical · /dataset · /live', 'CSV + PDF'],
  ];

  function renderEndpoints() {
    $('endpointTable').innerHTML = '<thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead><tbody>' +
      ENDPOINTS.map(([m, p, s]) => `<tr><td><span class="badge ${m === 'GET' ? 'dim' : 'best'}">${m}</span></td>
        <td class="mono" style="font-size:11.5px">${esc(p)}</td><td style="color:var(--dim)">${esc(s)}</td></tr>`).join('') +
      '</tbody>';
  }

  /* ---------------------------------------------------------------- about */
  function renderAbout() {
    $('aboutPipeline').innerHTML = [
      ['1', 'Ingest', 'CSV/XLSX upload, bundled CEEW files, or an OWID country series imported as a dataset.'],
      ['2', 'Profile', 'Row/column counts, dtypes, missing cells, duplicates, date range, column auto-detection with manual override.'],
      ['3', 'Clean', 'Invalid timestamps and non-positive values dropped, duplicate timestamps collapsed (sum or mean), cumulative meters differenced.'],
      ['4', 'Resample', 'Continuous series at the dataset\'s natural frequency; gaps interpolated and counted.'],
      ['5', 'Features', 'Calendar terms plus lags and shifted rolling mean/std/max — rolling windows use shift(1) so the target is never an input.'],
      ['6', 'Train', 'Linear Regression, Decision Tree and Random Forest on the identical chronological 80/20 split.'],
      ['7', 'Evaluate', 'Linear Regression, Decision Tree and Random Forest are evaluated using MAE, RMSE, R² and MAPE on the unseen later period. The best model is selected using MAE.'],
      ['8', 'Forecast', 'Recursive one-step-ahead predictions; predicted values only, no future actuals.'],
    ].map(([n, t, d]) => `<div style="display:flex;gap:10px;padding:7px 0;border-bottom:1px solid rgba(27,47,63,.6)">
      <span class="badge dim" style="height:18px">${n}</span>
      <div><b style="color:var(--ink);font-size:12.4px">${t}</b>
      <div style="color:var(--dim);font-size:12px;margin-top:2px">${d}</div></div></div>`).join('');

    const limits = [
      'Location data is annual and country-level (OWID). It is not city or area level, and it cannot support hourly modelling.',
      'Three time-series models need at least 20 feature rows; shorter series are rejected with an explanation instead of a fabricated result.',
      'Intervals finer than the source grain (e.g. hourly from daily data) are refused, because the detail does not exist in the data.',
      'No weather, tariff or holiday features — the model uses calendar terms and consumption history only.',
      'Recursive forecast error compounds with horizon length; long horizons are indicative, not operational.',
      'Live readings and metadata use SQLite on local server storage. On hosting platforms with ephemeral storage, data may be lost after restarts or redeployments. No external physical meter integration is currently provided.',
    ];
    $('aboutLimits').innerHTML = limits.map(t => `<li style="position:relative;padding:5px 0 5px 14px">
      <span style="position:absolute;left:0;top:12px;width:4px;height:4px;border-radius:50%;background:#4b6a80"></span>${esc(t)}</li>`).join('');
  }

  /* ---------------------------------------------------------------- boot */
  async function boot() {
    try {
      const h = await api('/health');
      $('apiChip').className = 'chip ok'; $('apiChip').textContent = 'API · connected';
      $('sfDot').className = 'dot-live';
      $('sfStatus').textContent = 'API connected · ' + h.live_readings + ' live readings';
    } catch (e) {
      $('apiChip').className = 'chip err'; $('apiChip').textContent = 'API · offline';
      $('sfStatus').textContent = 'API unreachable';
    }
    await refreshDatasets();
    try {
      const live = await api('/live-api/readings?limit=2');
      $('homeLiveCount').textContent = fmt(live.meters || 0, 0);
    } catch (e) { /* ignore */ }
    if (state.activeId && state.info && state.info.date_range) {
      $('histStart').value = state.info.date_range.start.slice(0, 10);
      $('histEnd').value = state.info.date_range.end.slice(0, 10);
    }
    renderEndpoints();
    goto('home');
  }

  boot();
})();
