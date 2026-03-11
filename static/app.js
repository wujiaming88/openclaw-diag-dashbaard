/* OpenClaw 诊断面板 v2.0 — 前端逻辑 */
(function () {
'use strict';

// ============================================================
// 状态
// ============================================================
var currentDate = '';
var autoTimer = null;
var autoInterval = 30000;
var openRuns = {};
var currentPage = 1;
var perPage = 20;
var timelinePage = 1;
var timelinePerPage = 50;
var timelineFilters = {
  webhook: true, message: true, queue: true,
  session: true, heartbeat: false, error: true
};

// ============================================================
// 工具函数
// ============================================================
function $(sel) { return document.querySelector(sel); }

function fmtMs(ms) {
  if (ms === 0 || ms === undefined || ms === null) return '0ms';
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return (ms / 60000).toFixed(1) + 'm';
}

function fmtTok(n) {
  if (!n) return '0';
  if (n < 1000) return n.toString();
  if (n < 1000000) return (n / 1000).toFixed(1) + 'k';
  return (n / 1000000).toFixed(2) + 'M';
}

function speedClass(ms) {
  if (ms > 5000) return 'slow';
  if (ms > 1000) return 'medium';
  return '';
}

function statusClass(s) { return 'status-' + s; }

function statusIcon(s) {
  var m = { ok: '✅', error: '❌', aborted: '⚠️', running: '🔄' };
  return m[s] || s;
}

function escHtml(s) {
  if (!s) return '';
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function shortModel(m) {
  if (!m) return '';
  var parts = m.split('.');
  var last = parts[parts.length - 1];
  return last.replace(/-v\d+$/, '');
}

function eventTagHtml(category) {
  var labels = {
    webhook: 'Webhook', message: 'Message', queue: 'Queue',
    session: 'Session', model: 'Model', error: 'Error',
    heartbeat: 'Heartbeat'
  };
  var label = labels[category] || category;
  return '<span class="event-tag ' + escHtml(category) + '">' + escHtml(label) + '</span>';
}

// ============================================================
// API 请求
// ============================================================
function api(path, cb) {
  var x = new XMLHttpRequest();
  x.open('GET', path);
  x.onload = function () {
    if (x.status === 200) {
      try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); }
    } else { cb(null); }
  };
  x.onerror = function () { cb(null); };
  x.send();
}

function fetchSystemInfo() {
  api('/api/system_info', function (info) {
    renderSystemInfo(info);
  });
}

function fetchDates() {
  api('/api/dates', function (dates) {
    var sel = $('#dateSelect');
    sel.innerHTML = '';
    if (!dates || dates.length === 0) {
      sel.innerHTML = '<option>无数据</option>';
      showEmpty();
      return;
    }
    dates.forEach(function (d) {
      var o = document.createElement('option');
      o.value = d; o.textContent = d;
      sel.appendChild(o);
    });
    currentDate = dates[0];
    sel.value = currentDate;
    loadData();
  });
}

function fetchSummary(date) {
  api('/api/summary?date=' + date, function (summary) {
    renderSummary(summary);
  });
}

function fetchEventsSummary(date) {
  api('/api/events?date=' + date, function (data) {
    renderEventsSummary(data);
    renderPipeline(data);
  });
}

function fetchWebhooks(date) {
  api('/api/events/webhooks?date=' + date, function (data) {
    renderWebhooks(data);
  });
}

function fetchErrors(date) {
  api('/api/events/errors?date=' + date, function (data) {
    renderErrors(data);
  });
}

function fetchTimeline(date, page, pp) {
  var cats = [];
  for (var k in timelineFilters) {
    if (timelineFilters[k]) cats.push(k);
  }
  var catParam = cats.length > 0 ? '&category=' + cats.join(',') : '';
  api('/api/events/timeline?date=' + date + '&page=' + page + '&per_page=' + pp + catParam, function (data) {
    renderTimeline(data);
  });
}

function fetchRuns(date, page, pp) {
  api('/api/runs?date=' + date + '&page=' + page + '&per_page=' + pp, function (data) {
    renderRunList(data);
  });
}

function fetchRunDetail(rid, el) {
  el.innerHTML = '<div class="loading"><span class="spinner"></span>加载详情...</div>';
  api('/api/run/' + rid + '?date=' + currentDate, function (d) {
    if (!d) { el.innerHTML = '<p>加载失败</p>'; return; }
    renderRunDetail(d, el);
  });
}

// ============================================================
// 渲染 — 系统信息
// ============================================================
function renderSystemInfo(info) {
  if (!info) return;
  var bar = $('#sysInfoBar');
  bar.style.display = 'block';
  var ver = info.openclaw_version || '?';
  var modelShort = shortModel(info.default_model || '?');
  var channels = (info.channels || []).join(', ') || '-';
  var host = info.hostname || '?';

  var html = '<div class="sysinfo-header" onclick="toggleSysInfo(this)">';
  html += '<div class="sysinfo-summary">';
  html += '<span>🟢 OpenClaw <strong>' + escHtml(ver) + '</strong></span>';
  html += '<span class="sep">|</span>';
  html += '<span>Model: <strong>' + escHtml(modelShort) + '</strong></span>';
  html += '<span class="sep">|</span>';
  html += '<span>Channels: <strong>' + escHtml(channels) + '</strong></span>';
  html += '<span class="sep">|</span>';
  html += '<span>Host: <strong>' + escHtml(host) + '</strong></span>';
  html += '</div>';
  html += '<span class="toggle-icon">▼</span>';
  html += '</div>';
  html += '<div class="sysinfo-detail"><div class="sysinfo-grid">';
  var items = [
    ['版本', info.openclaw_version],
    ['配置文件', info.openclaw_config_path],
    ['诊断', info.diagnostics_enabled ? '已开启' : '未开启'],
    ['日志级别', info.logging_level],
    ['默认模型', info.default_model],
    ['Agents', (info.agents || []).join(', ')],
    ['Channels', (info.channels || []).join(', ')],
    ['Python', info.python_version],
    ['平台', info.platform],
    ['主机名', info.hostname],
    ['CPU', info.cpu_count + ' 核'],
    ['内存', info.memory_used_mb + 'MB / ' + info.memory_total_mb + 'MB'],
    ['日志目录', info.log_dir],
    ['日志文件数', info.log_file_count],
    ['会话目录数', info.sessions_dir_count],
    ['会话文件数', info.session_file_count],
  ];
  items.forEach(function (it) {
    html += '<div class="si-item"><span class="si-label">' + escHtml(it[0]) + '</span><span class="si-value">' + escHtml(String(it[1] || '-')) + '</span></div>';
  });
  html += '</div></div>';
  bar.innerHTML = html;
}

// ============================================================
// 渲染 — 摘要卡片 (两行)
// ============================================================
function renderSummary(s) {
  if (!s || s.total_runs === 0) {
    $('#summaryCards').innerHTML = '';
    $('#summaryCards2').innerHTML = '';
    return;
  }
  var html = '';
  html += '<div class="card"><div class="label">Run 总数</div><div class="value">' + s.total_runs + '</div></div>';
  html += '<div class="card"><div class="label">平均耗时</div><div class="value">' + fmtMs(s.avg_duration_ms) + '</div></div>';
  html += '<div class="card"><div class="label">推理占比</div><div class="value">' + s.infer_ratio + '%</div><div class="ratio-bar"><div class="fill-infer" style="width:' + s.infer_ratio + '%"></div><div class="fill-tool" style="width:' + (100 - s.infer_ratio) + '%"></div></div></div>';
  html += '<div class="card"><div class="label">总输出 Token</div><div class="value">' + fmtTok(s.total_tokens_output) + '</div></div>';
  var errCls = s.error_count > 0 ? ' error' : '';
  html += '<div class="card' + errCls + '"><div class="label">错误数</div><div class="value">' + s.error_count + '</div></div>';
  $('#summaryCards').innerHTML = html;
}

function renderEventsSummary(data) {
  if (!data || !data.summary) {
    $('#summaryCards2').innerHTML = '';
    return;
  }
  var s = data.summary;
  var mu = data.model_usage || {};
  var ms_stats = data.message_stats || {};
  var ss = data.session_stats || {};

  var html2 = '';
  html2 += '<div class="card"><div class="label">Webhook 数</div><div class="value">' + s.webhooks_received + '</div></div>';
  html2 += '<div class="card"><div class="label">消息处理</div><div class="value">' + s.messages_processed + '</div></div>';
  html2 += '<div class="card"><div class="label">平均处理时间</div><div class="value">' + fmtMs(ms_stats.avg_process_time_ms || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">平均队列等待</div><div class="value">' + fmtMs(ms_stats.avg_queue_wait_ms || 0) + '</div></div>';
  var stuckCls = ss.stuck_count > 0 ? ' warn' : '';
  html2 += '<div class="card' + stuckCls + '"><div class="label">会话卡住</div><div class="value">' + (ss.stuck_count || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">总事件数</div><div class="value">' + s.total_events + '</div></div>';
  $('#summaryCards2').innerHTML = html2;
}

// ============================================================
// 渲染 — 消息处理流水线
// ============================================================
function renderPipeline(data) {
  var sec = $('#pipelineSection');
  var body = $('#pipelineBody');
  if (!data || !data.summary) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = 'block';
  var s = data.summary;
  var ms_stats = data.message_stats || {};
  var wh_stats = data.webhook_stats || {};

  var stages = [
    { name: 'Webhook\nReceived', count: s.webhooks_received, detail: '', icon: '📡' },
    { name: 'Message\nQueued', count: s.messages_queued, detail: '', icon: '📥' },
    { name: 'Queue\nEnqueue', count: s.queue_enqueues, detail: '', icon: '📋' },
    { name: 'Queue\nDequeue', count: s.queue_dequeues, detail: 'avg wait: ' + fmtMs(ms_stats.avg_queue_wait_ms || 0), icon: '📤' },
    { name: 'Run\nExecution', count: s.runs, detail: '', icon: '⚡' },
    { name: 'Message\nProcessed', count: s.messages_processed, detail: 'avg: ' + fmtMs(ms_stats.avg_process_time_ms || 0), icon: '✅' },
  ];

  var html = '<div class="pipeline">';
  stages.forEach(function (st, i) {
    if (i > 0) html += '<div class="pipeline-arrow">→</div>';
    var errCls = (st.name.indexOf('Error') >= 0 && st.count > 0) ? ' has-error' : '';
    html += '<div class="pipeline-stage' + errCls + '">';
    html += '<div class="stage-name">' + escHtml(st.icon + ' ' + st.name) + '</div>';
    html += '<div class="stage-count">' + st.count + '</div>';
    if (st.detail) html += '<div class="stage-detail">' + escHtml(st.detail) + '</div>';
    html += '</div>';
  });
  // Show webhook errors separately
  if (s.webhook_errors > 0) {
    html += '<div class="pipeline-arrow" style="color:var(--red)">⚠</div>';
    html += '<div class="pipeline-stage has-error">';
    html += '<div class="stage-name">❌ Webhook\nErrors</div>';
    html += '<div class="stage-count">' + s.webhook_errors + '</div>';
    html += '</div>';
  }
  html += '</div>';

  // Model usage summary
  var mu = data.model_usage || {};
  if (mu.total_output_tokens > 0 || mu.total_input_tokens > 0) {
    html += '<div style="margin-top:12px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:8px;font-size:13px;color:var(--text2)">';
    html += '📊 <strong style="color:var(--text)">Token 用量:</strong> ';
    html += 'Input: <strong style="color:var(--text)">' + fmtTok(mu.total_input_tokens) + '</strong> ';
    html += 'Output: <strong style="color:var(--text)">' + fmtTok(mu.total_output_tokens) + '</strong> ';
    html += 'Cache Read: <strong style="color:var(--text)">' + fmtTok(mu.total_cache_read) + '</strong> ';
    html += 'Cache Write: <strong style="color:var(--text)">' + fmtTok(mu.total_cache_write) + '</strong>';
    if (mu.total_cache_read > 0 && mu.total_input_tokens > 0) {
      var hitRate = Math.round(mu.total_cache_read / (mu.total_cache_read + mu.total_input_tokens) * 100);
      html += ' | 缓存命中率: <strong style="color:var(--green)">' + hitRate + '%</strong>';
    }
    html += '</div>';
  }

  body.innerHTML = html;
}

// ============================================================
// 渲染 — Webhook 监控
// ============================================================
function renderWebhooks(data) {
  var sec = $('#webhookSection');
  var body = $('#webhookBody');
  var badge = $('#webhookBadge');
  if (!data || !data.webhooks || data.webhooks.length === 0) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = 'block';
  badge.textContent = data.total + ' events';

  var received = 0, errors = 0;
  data.webhooks.forEach(function (w) {
    if (w.type === 'webhook.received') received++;
    if (w.type === 'webhook.error') errors++;
  });

  var html = '<div class="webhook-stats">';
  html += '<div class="webhook-stat"><div class="stat-val">' + received + '</div><div class="stat-lbl">Received</div></div>';
  html += '<div class="webhook-stat"><div class="stat-val" style="color:' + (errors > 0 ? 'var(--red)' : 'var(--green)') + '">' + errors + '</div><div class="stat-lbl">Errors</div></div>';
  html += '<div class="webhook-stat"><div class="stat-val">' + (received > 0 ? Math.round((received - errors) / received * 100) : 100) + '%</div><div class="stat-lbl">Success Rate</div></div>';
  html += '</div>';

  html += '<div class="webhook-list"><table class="detail-table"><thead><tr><th>时间</th><th>类型</th><th>渠道</th><th>详情</th></tr></thead><tbody>';
  // Show last 50 webhooks
  var shown = data.webhooks.slice(-50).reverse();
  shown.forEach(function (w) {
    var rowCls = w.type === 'webhook.error' ? ' class="error-row"' : '';
    html += '<tr' + rowCls + '>';
    html += '<td class="mono">' + escHtml(w.time) + '</td>';
    html += '<td>' + eventTagHtml(w.type === 'webhook.error' ? 'error' : 'webhook') + '</td>';
    html += '<td>' + escHtml(w.channel || '') + '</td>';
    html += '<td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml((w.detail || '').substring(0, 120)) + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  body.innerHTML = html;
}

// ============================================================
// 渲染 — 错误日志
// ============================================================
function renderErrors(data) {
  var sec = $('#errorSection');
  var body = $('#errorBody');
  var badge = $('#errorBadge');
  if (!data) {
    sec.style.display = 'none';
    return;
  }
  var errors = data.errors || [];
  if (errors.length === 0) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = 'block';
  badge.textContent = errors.length + ' errors';
  badge.className = 'section-badge error';

  // Auto-expand if errors
  var header = sec.querySelector('.section-header');
  var bodyEl = sec.querySelector('.section-body');
  if (!bodyEl.classList.contains('open')) {
    bodyEl.classList.add('open');
    if (header) header.classList.add('open');
  }

  var html = '<table class="detail-table"><thead><tr><th>时间</th><th>类型</th><th>渠道</th><th>详情</th></tr></thead><tbody>';
  errors.forEach(function (e) {
    html += '<tr class="error-row">';
    html += '<td class="mono">' + escHtml(e.time) + '</td>';
    html += '<td>' + eventTagHtml('error') + ' ' + escHtml(e.type || '') + '</td>';
    html += '<td>' + escHtml(e.channel || '') + '</td>';
    html += '<td class="error-text" style="max-width:500px;word-break:break-all">' + escHtml(e.detail || '') + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  body.innerHTML = html;
}

// ============================================================
// 渲染 — 事件时间线
// ============================================================
function renderTimeline(data) {
  var sec = $('#timelineSection');
  var badge = $('#timelineBadge');
  if (!data) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = 'block';
  badge.textContent = data.total + ' events';

  // Render filters
  var filtersHtml = '';
  var filterDefs = [
    { key: 'webhook', label: '🌐 Webhook' },
    { key: 'message', label: '💬 Message' },
    { key: 'queue', label: '📋 Queue' },
    { key: 'session', label: '🔮 Session' },
    { key: 'error', label: '❌ Error' },
    { key: 'heartbeat', label: '💓 Heartbeat' },
  ];
  filterDefs.forEach(function (f) {
    var checked = timelineFilters[f.key] ? ' checked' : '';
    filtersHtml += '<label><input type="checkbox" data-filter="' + f.key + '"' + checked + ' onchange="toggleTimelineFilter(this)"> ' + f.label + '</label>';
  });
  $('#timelineFilters').innerHTML = filtersHtml;

  // Render events
  var events = data.events || [];
  var html = '<div class="timeline-list"><table class="detail-table"><thead><tr><th>时间</th><th>类型</th><th>事件</th><th>详情</th></tr></thead><tbody>';
  events.forEach(function (e) {
    var rowCls = e.category === 'error' ? ' class="error-row"' : '';
    html += '<tr' + rowCls + '>';
    html += '<td class="mono">' + escHtml(e.time) + '</td>';
    html += '<td>' + eventTagHtml(e.category) + '</td>';
    html += '<td>' + escHtml(e.type) + '</td>';
    html += '<td style="max-width:500px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(e.detail || '') + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  $('#timelineContent').innerHTML = html;

  // Pagination
  var page = data.page || 1;
  var totalPages = data.total_pages || 1;
  var total = data.total || 0;
  var pagHtml = '';
  pagHtml += '<button onclick="goTimelinePage(' + (page - 1) + ')"' + (page <= 1 ? ' disabled' : '') + '>◀ 上一页</button>';
  pagHtml += '<span class="page-info">第 ' + page + ' / ' + totalPages + ' 页 (共 ' + total + ' 条)</span>';
  pagHtml += '<button onclick="goTimelinePage(' + (page + 1) + ')"' + (page >= totalPages ? ' disabled' : '') + '>下一页 ▶</button>';
  $('#timelinePagination').innerHTML = pagHtml;
}

// ============================================================
// 渲染 — Run 列表
// ============================================================
function renderRunList(data) {
  if (!data) {
    $('#content').innerHTML = '<div class="empty"><div class="icon">📭</div><p>加载失败</p></div>';
    return;
  }
  var runs = data.runs || [];
  var total = data.total || 0;
  var page = data.page || 1;
  var pp = data.per_page || 20;
  var totalPages = data.total_pages || 1;
  currentPage = page;

  if (total === 0) {
    $('#content').innerHTML = '<div class="empty"><div class="icon">📭</div><p>该日期暂无 Run 数据</p></div>';
    return;
  }
  var colSpan = 12;
  var html = '<div class="table-wrap"><div class="runs-scroll" id="runsScroll"><table><thead><tr>';
  html += '<th>开始</th><th>结束</th><th>Run ID</th><th>模型</th><th>通道</th><th>端到端</th><th>推理</th><th>工具</th><th>工具数</th><th>输出Token</th><th>状态</th>';
  html += '</tr></thead><tbody>';
  runs.forEach(function (r) {
    var durCls = speedClass(r.duration_ms);
    var short_id = r.run_id.substring(0, 8);
    html += '<tr class="clickable" data-runid="' + escHtml(r.run_id) + '" onclick="toggleRun(this)">';
    html += '<td class="mono">' + escHtml(r.start) + '</td>';
    html += '<td class="mono">' + escHtml(r.end || '-') + '</td>';
    html += '<td class="mono" title="' + escHtml(r.run_id) + '">' + escHtml(short_id) + '</td>';
    html += '<td>' + escHtml(shortModel(r.model)) + '</td>';
    html += '<td>' + escHtml(r.channel) + '</td>';
    html += '<td class="' + durCls + '">' + fmtMs(r.duration_ms) + '</td>';
    html += '<td>' + fmtMs(r.infer_ms) + '</td>';
    html += '<td>' + fmtMs(r.tool_ms) + '</td>';
    html += '<td>' + r.tool_count + '</td>';
    html += '<td>' + fmtTok(r.token_output) + '</td>';
    html += '<td class="' + statusClass(r.status) + '">' + statusIcon(r.status) + '</td>';
    html += '</tr>';
    html += '<tr class="detail-row"><td colspan="' + colSpan + '"><div class="run-detail" id="detail-' + escHtml(r.run_id) + '"></div></td></tr>';
  });
  html += '</tbody></table></div>';

  // 分页
  html += '<div class="pagination">';
  html += '<button onclick="goPage(' + (page - 1) + ')"' + (page <= 1 ? ' disabled' : '') + '>◀ 上一页</button>';
  html += '<span class="page-info">第 ' + page + ' / ' + totalPages + ' 页 (共 ' + total + ' 条)</span>';
  html += '<button onclick="goPage(' + (page + 1) + ')"' + (page >= totalPages ? ' disabled' : '') + '>下一页 ▶</button>';
  html += '<select onchange="changePerPage(this.value)">';
  [20, 50, 100].forEach(function (n) {
    html += '<option value="' + n + '"' + (n === pp ? ' selected' : '') + '>' + n + ' 条/页</option>';
  });
  html += '</select>';
  html += '</div></div>';

  $('#content').innerHTML = html;

  // 恢复展开的详情
  Object.keys(openRuns).forEach(function (rid) {
    var el = document.getElementById('detail-' + rid);
    if (el) {
      el.classList.add('open');
      fetchRunDetail(rid, el);
    }
  });
}

// ============================================================
// 渲染 — Run 详情
// ============================================================
function renderRunDetail(d, el) {
  var html = '';

  // 时间信息
  html += '<div style="margin-bottom:12px;font-size:13px;color:var(--text2)">';
  html += '开始: <strong style="color:var(--text)">' + escHtml(d.start) + '</strong>';
  html += ' &nbsp;结束: <strong style="color:var(--text)">' + escHtml(d.end || '-') + '</strong>';
  html += ' &nbsp;输出速率: <strong style="color:var(--text)">' + (d.overall_tok_per_s || 0) + ' tok/s</strong>';
  html += '</div>';

  // 甘特图
  html += '<div class="gantt-legend"><span><span class="dot infer"></span>推理</span><span><span class="dot tool"></span>工具</span><span style="margin-left:auto;font-size:11px;color:var(--text2)">总耗时: ' + fmtMs(d.duration_ms) + '</span></div>';
  html += '<div class="gantt">';
  if (d.gantt) {
    d.gantt.forEach(function (g) {
      var cls = g.type === 'infer' ? 'infer' : 'tool';
      var w = Math.max(g.width_pct, 0.5);
      html += '<div class="gantt-bar ' + cls + '" style="left:' + g.offset_pct + '%;width:' + w + '%">';
      if (w > 5) html += '<span style="padding:0 4px;overflow:hidden;text-overflow:ellipsis">' + escHtml(g.label) + '</span>';
      html += '<div class="gantt-tooltip">' + escHtml(g.label) + ' — ' + fmtMs(g.duration_ms) + '</div>';
      html += '</div>';
    });
  }
  html += '</div>';

  html += '<div class="detail-grid">';

  // 推理分段
  html += '<div class="detail-section"><h4>推理分段</h4>';
  if (d.infer_segments && d.infer_segments.length > 0) {
    html += '<table class="detail-table"><thead><tr><th>阶段</th><th>耗时</th><th>输出 Token</th><th>速率</th></tr></thead><tbody>';
    d.infer_segments.forEach(function (s) {
      var dc = speedClass(s.duration_ms);
      html += '<tr><td>' + escHtml(s.label) + '</td><td class="' + dc + '">' + fmtMs(s.duration_ms) + '</td><td>' + s.output_tokens + '</td><td>' + (s.tok_per_s > 0 ? s.tok_per_s + ' tok/s' : '-') + '</td></tr>';
    });
    html += '</tbody></table>';
  } else {
    html += '<p style="color:var(--text2)">无推理数据</p>';
  }
  html += '</div>';

  // 工具调用
  html += '<div class="detail-section"><h4>工具调用 (' + d.tool_count + ')</h4>';
  if (d.tools && d.tools.length > 0) {
    html += '<table class="detail-table"><thead><tr><th>工具</th><th>参数</th><th>耗时</th></tr></thead><tbody>';
    d.tools.forEach(function (t) {
      var dc = speedClass(t.duration_ms);
      var argText = t.arguments_summary || '';
      var lines = argText.split('\n');
      var needCollapse = lines.length > 2;
      html += '<tr><td><strong>' + escHtml(t.tool) + '</strong></td><td class="tool-args">';
      if (argText) {
        html += '<div class="tool-args-wrap' + (needCollapse ? ' collapsed' : '') + '">';
        html += '<pre>' + escHtml(argText) + '</pre>';
        if (needCollapse) html += '<span class="tool-args-toggle" onclick="toggleArgs(this)">▼ 展开</span>';
        html += '</div>';
      }
      html += '</td><td class="' + dc + '">' + fmtMs(t.duration_ms) + '</td></tr>';
    });
    html += '</tbody></table>';
  } else {
    html += '<p style="color:var(--text2)">无工具调用</p>';
  }
  html += '</div>';

  html += '</div>';

  // 汇总条
  html += '<div class="summary-bar">';
  html += '<div class="item"><div class="val">' + fmtMs(d.duration_ms) + '</div><div class="lbl">端到端</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.infer_ms) + '</div><div class="lbl">推理总耗时</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.tool_ms) + '</div><div class="lbl">工具总耗时</div></div>';
  html += '<div class="item"><div class="val">' + fmtTok(d.total_tokens_output) + '</div><div class="lbl">输出 Token</div></div>';
  html += '<div class="item"><div class="val">' + (d.overall_tok_per_s || 0) + ' tok/s</div><div class="lbl">输出速率</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.model) + '</div><div class="lbl">模型</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.channel) + '</div><div class="lbl">通道</div></div>';
  html += '</div>';

  // Token 摘要
  if (d.token_summary) {
    var ts = d.token_summary;
    html += '<div style="margin-top:8px;font-size:12px;color:var(--text2)">';
    html += 'Token: input=' + fmtTok(ts.input) + ' output=' + fmtTok(ts.output) + ' cacheRead=' + fmtTok(ts.cacheRead) + ' cacheWrite=' + fmtTok(ts.cacheWrite);
    html += '</div>';
  }

  // Prompt 信息
  if (d.prompt_info && d.prompt_info.messages) {
    html += '<div style="margin-top:4px;font-size:12px;color:var(--text2)">';
    html += 'Prompt: messages=' + escHtml(d.prompt_info.messages);
    if (d.prompt_info.historyTextChars) html += ' historyChars=' + escHtml(d.prompt_info.historyTextChars);
    if (d.prompt_info.systemPromptChars) html += ' sysPromptChars=' + escHtml(d.prompt_info.systemPromptChars);
    html += '</div>';
  }

  el.innerHTML = html;
}

// ============================================================
// 页面状态
// ============================================================
function showEmpty() {
  $('#summaryCards').innerHTML = '';
  $('#summaryCards2').innerHTML = '';
  $('#content').innerHTML = '<div class="empty"><div class="icon">📭</div><p>暂无诊断数据</p><p style="margin-top:8px;font-size:13px">等待 OpenClaw 生成日志后自动显示</p></div>';
}

function showLoading() {
  $('#content').innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
}

function loadData() {
  showLoading();
  var d = currentDate;
  fetchSummary(d);
  fetchEventsSummary(d);
  fetchWebhooks(d);
  fetchErrors(d);
  fetchRuns(d, currentPage, perPage);
  fetchTimeline(d, timelinePage, timelinePerPage);
}

// ============================================================
// 自动刷新
// ============================================================
function initAutoRefresh(ms) {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  autoInterval = ms;
  if (ms > 0) {
    autoTimer = setInterval(function () { loadData(); }, ms);
  }
}

// ============================================================
// 全局事件处理 (onclick handlers)
// ============================================================
window.toggleSysInfo = function (el) {
  el.classList.toggle('open');
  var detail = el.nextElementSibling;
  if (detail) detail.classList.toggle('open');
};

window.toggleSection = function (el) {
  el.classList.toggle('open');
  var body = el.nextElementSibling;
  if (body) body.classList.toggle('open');
};

window.toggleArgs = function (el) {
  var wrap = el.parentElement;
  if (wrap.classList.contains('collapsed')) {
    wrap.classList.remove('collapsed');
    el.textContent = '▲ 收起';
  } else {
    wrap.classList.add('collapsed');
    el.textContent = '▼ 展开';
  }
};

window.toggleRun = function (tr) {
  var rid = tr.getAttribute('data-runid');
  var el = document.getElementById('detail-' + rid);
  if (!el) return;
  if (el.classList.contains('open')) {
    el.classList.remove('open');
    delete openRuns[rid];
  } else {
    el.classList.add('open');
    openRuns[rid] = true;
    fetchRunDetail(rid, el);
  }
};

window.goPage = function (p) {
  currentPage = p;
  var sc = $('#runsScroll');
  if (sc) sc.scrollTop = 0;
  fetchRuns(currentDate, currentPage, perPage);
};

window.changePerPage = function (v) {
  perPage = parseInt(v) || 20;
  currentPage = 1;
  fetchRuns(currentDate, 1, perPage);
};

window.goTimelinePage = function (p) {
  timelinePage = p;
  fetchTimeline(currentDate, timelinePage, timelinePerPage);
};

window.toggleTimelineFilter = function (el) {
  var key = el.getAttribute('data-filter');
  timelineFilters[key] = el.checked;
  timelinePage = 1;
  fetchTimeline(currentDate, 1, timelinePerPage);
};

window.refresh = function () { loadData(); };

window.scrollToRun = function (rid) {
  var row = document.querySelector('tr[data-runid="' + rid + '"]');
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.style.background = 'var(--bg3)';
    setTimeout(function () { row.style.background = ''; }, 2000);
  }
};

// ============================================================
// 初始化
// ============================================================
$('#dateSelect').addEventListener('change', function () {
  currentDate = this.value;
  currentPage = 1;
  timelinePage = 1;
  openRuns = {};
  loadData();
});

$('#autoRefreshSelect').addEventListener('change', function () {
  initAutoRefresh(parseInt(this.value) || 0);
});

// 启动
fetchSystemInfo();
fetchDates();
initAutoRefresh(autoInterval);

})();
