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
var currentDate = '';

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
  x.setRequestHeader('Accept-Encoding', 'gzip, deflate');
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
    var skeleton = $('#skeletonCards');
    if (skeleton) skeleton.style.display = 'none';
    renderSummary(summary);
  });
}

function fetchEventsSummary(date) {
  api('/api/events?date=' + date, function (data) {
    renderEventsSummary(data);
    renderPipeline(data);
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
    ['模型调用数', (info.model_calls_total >= 0) ? info.model_calls_total : '加载中...'],
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
// 指标解读提示
var TIPS = {
  // 摘要卡片
  runs: 'Agent 处理用户消息的总次数。每次用户发消息或系统触发都算一次 Run',
  avgDur: '从收到消息到回复完成的平均端到端耗时。包含推理等待和工具执行',
  inferRatio: '推理（等待模型响应）占总耗时的比例。越高说明瓶颈在模型侧；越低说明工具执行耗时多',
  totalTokens: '模型生成的输出 Token 总数。Token 数量直接决定 API 费用',
  errors: '处理失败的 Run 数。常见原因：模型超时、工具报错、会话异常',
  // Run 详情汇总条
  e2e: '端到端耗时 = 从 Agent 收到消息开始，到最终回复发送完毕的总时间',
  inferTotal: '所有推理段的总耗时。模型每次决定调用工具或生成回复前都需要推理',
  toolTotal: '所有工具执行的总耗时。包括 exec、read、write、web_search 等',
  outputToken: '本次 Run 模型输出的总 Token 数。包含工具调用指令和最终回复文本',
  tokPerS: '模型输出速率（Token/秒）。Opus 通常 20-50 tok/s，Sonnet 50-100 tok/s，Haiku 100-200 tok/s',
  model: '本次 Run 使用的模型。不同模型在速度、质量、费用之间有不同权衡',
  channel: '消息来源渠道（如 Telegram、Discord）。影响消息格式和传输延迟',
  // Token 摘要
  tokenInput: 'Input Token: 发送给模型的输入 Token 数（不含缓存部分）。通常很少，因为大部分被缓存命中',
  tokenOutput: 'Output Token: 模型生成的输出 Token 数。这是主要的费用来源',
  tokenCacheRead: 'Cache Read: 从缓存中读取的 Token 数。命中缓存可节省 90% 的输入费用',
  tokenCacheWrite: 'Cache Write: 写入缓存的 Token 数。首次对话或上下文变化时产生',
  // 推理分段
  inferSeg: '模型的每次推理过程。第一次推理决定要做什么（调用工具或直接回复），后续推理处理工具结果',
  outputTokens: '该段推理中模型输出的 Token 数。越多说明回复越长或工具调用参数越复杂',
  // Prompt
  promptMsg: 'Prompt 中的消息条数。包含系统提示、对话历史、工具定义等',
  promptHistory: '对话历史的字符数。越大说明上下文越长，推理越慢',
  promptSys: '系统提示的字符数。包含人设、规则、技能定义等固定内容',
  cacheHit: '缓存命中率。高命中率说明上下文被有效缓存，可降低延迟和费用',
  webhooks: '收到的 Webhook 请求数。来自 Telegram 等渠道的原始消息推送',
  msgProcessed: '成功处理完成的消息数。包含排队、推理、回复全流程',
  avgProcessTime: '消息从入队到处理完成的平均耗时。反映整体响应速度',
  avgQueueWait: '消息在队列中等待的平均时间。过长说明并发处理能力不足',
  sessionStuck: '会话卡住的次数。表示某个会话长时间未完成，可能需要人工介入',
  // Session 级推理统计
  sessionInferMs: '基于 session 消息时间戳计算的平均推理延迟。精确测量每次模型调用从请求到响应的耗时',
  sessionTps: '基于 session 消息时间戳计算的平均 Token 吞吐量（输出 Token 数 / 推理耗时）',
};

function tipAttr(key) {
  return TIPS[key] ? ' data-tip="' + escHtml(TIPS[key]) + '"' : '';
}

function tipIcon(key) {
  return TIPS[key] ? ' <span class="tip-icon"' + tipAttr(key) + '>?</span>' : '';
}

function renderSummary(s) {
  if (!s || s.total_runs === 0) {
    $('#summaryCards').innerHTML = '';
    $('#summaryCards2').innerHTML = '';
    return;
  }
  // 第一行：核心性能指标
  var html = '';
  html += '<div class="card"><div class="label">Run 总数' + tipIcon('runs') + '</div><div class="value">' + s.total_runs + '</div></div>';
  html += '<div class="card"><div class="label">平均耗时' + tipIcon('avgDur') + '</div><div class="value">' + fmtMs(s.avg_duration_ms) + '</div></div>';
  html += '<div class="card"><div class="label">推理占比' + tipIcon('inferRatio') + '</div><div class="value">' + s.infer_ratio + '%</div><div class="ratio-bar"><div class="fill-infer" style="width:' + s.infer_ratio + '%"></div><div class="fill-tool" style="width:' + (100 - s.infer_ratio) + '%"></div></div></div>';
  html += '<div class="card"><div class="label">平均速率' + tipIcon('tokPerS') + '</div><div class="value">' + (s.avg_tok_per_s || 0) + ' tok/s</div></div>';
  // 新增：Session 级精确推理延迟
  html += '<div class="card inference-card"><div class="label">平均推理延迟' + tipIcon('sessionInferMs') + '</div><div class="value">' + fmtMs(s.session_avg_inference_ms || 0) + '</div><div class="sub-value">' + (s.session_inference_count || 0) + ' 次调用</div></div>';
  // 新增：Session 级精确 Token 吞吐量
  html += '<div class="card inference-card"><div class="label">Token 吞吐量' + tipIcon('sessionTps') + '</div><div class="value">' + (s.session_avg_tokens_per_sec || 0) + ' tok/s</div><div class="sub-value">总推理 ' + fmtMs(s.session_total_inference_ms || 0) + '</div></div>';
  var errCls = s.error_count > 0 ? ' error' : '';
  html += '<div class="card' + errCls + '"><div class="label">错误数' + tipIcon('errors') + '</div><div class="value">' + s.error_count + '</div></div>';
  $('#summaryCards').innerHTML = html;

  // 第二行：Token 消耗指标
  var html2 = '';
  html2 += '<div class="card"><div class="label">输出 Token' + tipIcon('tokenOutput') + '</div><div class="value">' + fmtTok(s.total_tokens_output) + '</div></div>';
  html2 += '<div class="card"><div class="label">输入 Token' + tipIcon('tokenInput') + '</div><div class="value">' + fmtTok(s.total_tokens_input || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">缓存读取' + tipIcon('tokenCacheRead') + '</div><div class="value">' + fmtTok(s.total_cache_read || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">缓存写入' + tipIcon('tokenCacheWrite') + '</div><div class="value">' + fmtTok(s.total_cache_write || 0) + '</div></div>';
  var hitCls = (s.cache_hit_ratio || 0) > 80 ? '' : ' warn';
  html2 += '<div class="card' + hitCls + '"><div class="label">缓存命中率' + tipIcon('cacheHit') + '</div><div class="value">' + (s.cache_hit_ratio || 0) + '%</div></div>';
  $('#summaryCards2').innerHTML = html2;
}

// 也在 showEmpty 中清除
// 渲染 — Run 列表
// ============================================================
function renderEventsSummary(data) {
  var el = $('#summaryCards3');
  if (!data || !data.summary) { el.innerHTML = ''; return; }
  var s = data.summary;
  var ms_stats = data.message_stats || {};
  var ss = data.session_stats || {};
  // 只在有任何数据时显示（不仅看 webhook）
  var hasData = (s.messages_processed || 0) > 0 || (s.total_events || 0) > 0 || (ss.stuck_count || 0) > 0;
  if (!hasData) { el.innerHTML = ''; return; }
  var html = '';
  html += '<div class="card"><div class="label">消息处理' + tipIcon('msgProcessed') + '</div><div class="value">' + (s.messages_processed || 0) + '</div></div>';
  html += '<div class="card"><div class="label">平均处理时间' + tipIcon('avgProcessTime') + '</div><div class="value">' + fmtMs(ms_stats.avg_process_time_ms || 0) + '</div></div>';
  html += '<div class="card"><div class="label">平均队列等待' + tipIcon('avgQueueWait') + '</div><div class="value">' + fmtMs(ms_stats.avg_queue_wait_ms || 0) + '</div></div>';
  var stuckCls = (ss.stuck_count || 0) > 0 ? ' warn' : '';
  html += '<div class="card' + stuckCls + '"><div class="label">会话卡住' + tipIcon('sessionStuck') + '</div><div class="value">' + (ss.stuck_count || 0) + '</div></div>';
  el.innerHTML = html;
}

function renderPipeline(data) {
  var sec = $('#pipelineSection');
  var body = $('#pipelineBody');
  if (!sec || !body) return;
  if (!data || !data.summary) { sec.style.display = 'none'; return; }
  var s = data.summary;
  var ms_stats = data.message_stats || {};
  // 只要有消息处理数据就显示
  if ((s.messages_queued || 0) === 0 && (s.messages_processed || 0) === 0) {
    sec.style.display = 'none'; return;
  }
  sec.style.display = 'block';

  var stages = [
    { icon: '📥', name: 'Message\nQueued', count: s.messages_queued || 0 },
    { icon: '📋', name: 'Queue\nEnqueue', count: s.queue_enqueues || 0 },
    { icon: '📤', name: 'Queue\nDequeue', count: s.queue_dequeues || 0, detail: 'avg wait: ' + fmtMs(ms_stats.avg_queue_wait_ms || 0) },
    { icon: '⚡', name: 'Run\nExecution', count: s.runs || 0 },
    { icon: '✅', name: 'Message\nProcessed', count: s.messages_processed || 0, detail: 'avg: ' + fmtMs(ms_stats.avg_process_time_ms || 0) },
  ];

  var html = '<div class="pipeline">';
  stages.forEach(function (st, i) {
    if (i > 0) html += '<div class="pipeline-arrow">→</div>';
    html += '<div class="pipeline-stage">';
    html += '<div class="stage-icon">' + st.icon + '</div>';
    html += '<div class="stage-count">' + st.count + '</div>';
    html += '<div class="stage-name">' + escHtml(st.name) + '</div>';
    if (st.detail) html += '<div class="stage-detail">' + escHtml(st.detail) + '</div>';
    html += '</div>';
  });
  html += '</div>';

  // Token 用量摘要
  var mu = data.model_usage || {};
  if ((mu.total_output_tokens || 0) > 0) {
    html += '<div style="margin-top:12px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:8px;font-size:13px;color:var(--text2)">';
    html += '📊 <strong style="color:var(--text)">Token 用量:</strong> ';
    html += 'Input: <strong>' + fmtTok(mu.total_input_tokens) + '</strong> ';
    html += 'Output: <strong>' + fmtTok(mu.total_output_tokens) + '</strong> ';
    html += 'Cache Read: <strong>' + fmtTok(mu.total_cache_read) + '</strong> ';
    html += 'Cache Write: <strong>' + fmtTok(mu.total_cache_write) + '</strong>';
    if (mu.total_cache_read > 0 && mu.total_input_tokens > 0) {
      var hitRate = Math.round(mu.total_cache_read / (mu.total_cache_read + mu.total_input_tokens) * 100);
      html += ' | 缓存命中率: <strong style="color:var(--green,#4caf50)">' + hitRate + '%</strong>';
    }
    html += '</div>';
  }
  body.innerHTML = html;
}

window.toggleModelCallDetail = function (idx) {
  var el = document.getElementById('mc-detail-' + idx);
  if (el) el.classList.toggle('open');
};

window.toggleMcRunDetail = function (id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('open');
};

// ============================================================
// 渲染 — 错误列表
// ============================================================
function renderErrors(data) {
  var sec = $('#errorsSection');
  var body = $('#errorsBody');
  var badge = $('#errorCount');
  if (!sec || !body) return;
  if (!data || !data.errors || data.errors.length === 0) {
    sec.style.display = 'none';
    if (badge) badge.textContent = '';
    return;
  }
  sec.style.display = 'block';
  if (badge) badge.textContent = data.total || data.errors.length;

  var errors = data.errors;
  var html = '<div class="errors-scroll"><table class="errors-table"><thead><tr>';
  html += '<th>时间</th><th>级别</th><th>类型</th><th>子系统</th><th>错误信息</th><th>来源</th>';
  html += '</tr></thead><tbody>';
  errors.forEach(function (e, idx) {
    var sevCls = (e.severity === 'error') ? 'error-row' : 'warn-row';
    var tagCls = (e.severity === 'error') ? 'error' : 'warn';
    var sevLabel = (e.severity === 'error') ? '🔴 Error' : '🟡 Warn';
    var detail = e.detail || '';
    var shortDetail = detail.length > 120 ? detail.substring(0, 120) + '...' : detail;

    html += '<tr class="' + sevCls + '" onclick="toggleErrorDetail(' + idx + ')" style="cursor:pointer">';
    html += '<td style="white-space:nowrap">' + escHtml(e.time || '') + '</td>';
    html += '<td><span class="error-type-tag ' + tagCls + '">' + sevLabel + '</span></td>';
    html += '<td><span class="error-type-tag">' + escHtml(e.type || '') + '</span></td>';
    html += '<td class="error-subsystem">' + escHtml(e.subsystem || '') + '</td>';
    html += '<td>' + escHtml(shortDetail) + '</td>';
    html += '<td class="error-source">' + escHtml(e.source_file || '') + '</td>';
    html += '</tr>';

    // 展开完整错误
    if (detail.length > 120) {
      html += '<tr><td colspan="6" style="padding:0"><div class="error-detail-full" id="err-detail-' + idx + '">' + escHtml(detail) + '</div></td></tr>';
    }
  });
  html += '</tbody></table></div>';
  body.innerHTML = html;
}

window.toggleErrorDetail = function (idx) {
  var el = document.getElementById('err-detail-' + idx);
  if (el) el.classList.toggle('open');
};

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
  html += ' &nbsp;输出速率: <strong style="color:var(--text)">' + (d.overall_tok_per_s || 0) + ' tok/s' + tipIcon('tokPerS') + '</strong>';
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

  var runUid = d.run_id ? d.run_id.substring(0, 8) : 'x';
  html += '<div class="detail-grid">';

  // 推理分段
  html += '<div class="detail-section"><h4>推理分段</h4>';
  if (d.infer_segments && d.infer_segments.length > 0) {
    html += '<table class="detail-table"><thead><tr><th>阶段' + tipIcon('inferSeg') + '</th><th>耗时</th><th>输出 Token' + tipIcon('outputTokens') + '</th><th>速率' + tipIcon('tokPerS') + '</th></tr></thead><tbody>';
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
    html += '<table class="detail-table"><thead><tr><th>工具</th><th>参数</th><th>耗时</th><th>结果</th></tr></thead><tbody>';
    d.tools.forEach(function (t, ti) {
      var dc = speedClass(t.duration_ms);
      var argText = t.arguments_summary || '';
      var lines = argText.split('\n');
      var needCollapse = lines.length > 4;
      var hasArgsFull = t.arguments_full && Object.keys(t.arguments_full).length > 0;
      var hasResult = t.result && (t.result.text || t.result.text_preview);
      html += '<tr><td><strong>' + escHtml(t.tool) + '</strong></td><td class="tool-args">';
      if (argText) {
        html += '<div class="tool-args-wrap' + (needCollapse ? ' collapsed' : '') + '">';
        html += '<pre>' + escHtml(argText) + '</pre>';
        if (needCollapse) html += '<span class="tool-args-toggle" onclick="toggleArgs(this)">▼ 展开 (' + lines.length + ' 行)</span>';
        html += '</div>';
      }
      if (hasArgsFull) {
        var afid = 'tool-args-full-' + runUid + '-' + ti;
        html += '<div style="margin-top:4px"><span class="collapse-toggle" onclick="toggleBlock(\'' + afid + '\', this)">📋 完整参数 JSON</span>';
        html += '<div class="collapsible-content" id="' + afid + '"><pre>' + escHtml(JSON.stringify(t.arguments_full, null, 2)) + '</pre></div></div>';
      }
      html += '</td><td class="' + dc + '">' + fmtMs(t.duration_ms) + '</td>';
      // 结果列
      html += '<td>';
      if (hasResult) {
        var isErr = t.result.isError;
        var resultIcon = isErr ? '❌' : '✅';
        var trid = 'tool-result-' + runUid + '-' + ti;
        html += resultIcon + ' <span class="collapse-toggle" onclick="toggleBlock(\'' + trid + '\', this)">查看结果</span>';
        html += '<div class="collapsible-content" id="' + trid + '">';
        html += '<div class="tool-result' + (isErr ? ' error' : '') + '">' + escHtml(t.result.text || t.result.text_preview || '') + '</div></div>';
      } else {
        html += '<span style="color:var(--text2)">-</span>';
      }
      html += '</td></tr>';
    });
    html += '</tbody></table>';
  } else {
    html += '<p style="color:var(--text2)">无工具调用</p>';
  }
  html += '</div>';

  html += '</div>';

  // 汇总条
  html += '<div class="summary-bar">';
  html += '<div class="item"><div class="val">' + fmtMs(d.duration_ms) + '</div><div class="lbl">端到端' + tipIcon('e2e') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.infer_ms) + '</div><div class="lbl">推理总耗时' + tipIcon('inferTotal') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.tool_ms) + '</div><div class="lbl">工具总耗时' + tipIcon('toolTotal') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtTok(d.total_tokens_output) + '</div><div class="lbl">输出 Token' + tipIcon('outputToken') + '</div></div>';
  html += '<div class="item"><div class="val">' + (d.overall_tok_per_s || 0) + ' tok/s</div><div class="lbl">输出速率' + tipIcon('tokPerS') + '</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.model) + '</div><div class="lbl">模型' + tipIcon('model') + '</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.channel) + '</div><div class="lbl">通道' + tipIcon('channel') + '</div></div>';
  html += '</div>';

  // Token 摘要
  if (d.token_summary) {
    var ts = d.token_summary;
    html += '<div style="margin-top:8px;font-size:12px;color:var(--text2)">';
    html += 'Token: <span class="tip-wrap">input=' + fmtTok(ts.input) + tipIcon('tokenInput') + '</span>';
    html += ' <span class="tip-wrap">output=' + fmtTok(ts.output) + tipIcon('tokenOutput') + '</span>';
    html += ' <span class="tip-wrap">cacheRead=' + fmtTok(ts.cacheRead) + tipIcon('tokenCacheRead') + '</span>';
    html += ' <span class="tip-wrap">cacheWrite=' + fmtTok(ts.cacheWrite) + tipIcon('tokenCacheWrite') + '</span>';
    html += '</div>';
  }

  // 模型调用详情 (嵌入 Run 内)
  if (d.model_calls && d.model_calls.length > 0) {
    html += '<div class="detail-section" style="margin-top:12px"><h4>🤖 模型调用 (' + d.model_calls.length + ' 次)</h4>';
    html += '<table class="model-calls-table"><thead><tr>';
    html += '<th>时间</th><th>模型</th><th>推理耗时</th><th>输入</th><th>输出</th><th>tok/s</th><th>缓存</th><th>费用</th><th>停止</th><th></th>';
    html += '</tr></thead><tbody>';
    var runUid = d.run_id ? d.run_id.substring(0, 8) : 'x';
    d.model_calls.forEach(function (mc, mcIdx) {
      var mcTs = mc.timestamp || '';
      if (mcTs.indexOf('T') > -1) {
        mcTs = mcTs.split('T')[1] || mcTs;
        if (mcTs.length > 12) mcTs = mcTs.substring(0, 12);
      }
      var mcU = mc.usage || {};
      var mcCost = mc.cost || {};
      var stopCls = (mc.stop_reason === 'stop') ? 'stop' : (mc.stop_reason === 'toolUse' ? 'toolUse' : '');
      var costStr = mcCost.total ? ('$' + mcCost.total.toFixed(6)) : '-';
      var cacheStr = fmtTok(mcU.cacheRead || 0);
      if ((mcU.cacheRead || 0) > 0 && (mcU.input || 0) > 0) {
        var hitPct = Math.round(mcU.cacheRead / (mcU.cacheRead + mcU.input) * 100);
        cacheStr += ' (' + hitPct + '%)';
      }
      var inferMs = mc.inference_ms || 0;
      var inferCls = speedClass(inferMs);
      var tps = mc.tokens_per_sec || 0;
      var detailId = 'mc-run-' + runUid + '-' + mcIdx;
      html += '<tr onclick="toggleMcRunDetail(\'' + detailId + '\')" style="cursor:pointer">';
      html += '<td>' + escHtml(mcTs) + '</td>';
      html += '<td class="model-name">' + escHtml(shortModel(mc.model || '')) + '</td>';
      html += '<td class="' + inferCls + '">' + (inferMs > 0 ? fmtMs(inferMs) : '-') + '</td>';
      html += '<td>' + fmtTok(mcU.input || 0) + '</td>';
      html += '<td>' + fmtTok(mcU.output || 0) + '</td>';
      html += '<td>' + (tps > 0 ? tps + ' tok/s' : '-') + '</td>';
      html += '<td>' + cacheStr + '</td>';
      html += '<td class="cost">' + costStr + '</td>';
      html += '<td><span class="stop-tag ' + stopCls + '">' + escHtml(mc.stop_reason || '-') + '</span></td>';
      html += '<td style="font-size:11px;color:var(--text2)">▶</td>';
      html += '</tr>';

      // 展开详情
      var cs = mc.content_summary || {};
      var prompt = mc.prompt || {};
      html += '<tr><td colspan="10" style="padding:0"><div class="mc-detail" id="' + detailId + '">';

      // Prompt (用户输入)
      if (prompt.text) {
        var pid = detailId + '-prompt';
        html += '<div class="mc-content-block"><strong>📝 Prompt (用户输入):</strong>';
        html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + pid + '\', this)">▶ 展开 (' + prompt.text.length + ' 字)</span>';
        html += '<div class="collapsible-content" id="' + pid + '"><pre>' + escHtml(prompt.text) + '</pre></div></div>';
      }
      // Thinking
      if (cs.has_thinking) {
        var tid = detailId + '-think';
        var thinkText = cs.thinking_full || cs.thinking_preview || '';
        if (thinkText) {
          html += '<div class="mc-content-block"><strong>💭 Thinking:</strong>';
          html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + tid + '\', this)">▶ 展开 (' + thinkText.length + ' 字)</span>';
          html += '<div class="collapsible-content" id="' + tid + '"><pre>' + escHtml(thinkText) + '</pre></div></div>';
        }
      }
      // Output
      if (cs.has_text) {
        var oid = detailId + '-out';
        var outText = cs.text_full || cs.text_preview || '';
        if (outText) {
          html += '<div class="mc-content-block"><strong>💬 Output:</strong>';
          html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + oid + '\', this)">▶ 展开 (' + outText.length + ' 字)</span>';
          html += '<div class="collapsible-content" id="' + oid + '"><pre>' + escHtml(outText) + '</pre></div></div>';
        }
      }
      // 工具调用列表
      if (cs.tool_calls && cs.tool_calls.length > 0) {
        html += '<div class="mc-content-block"><strong>🔧 工具调用:</strong><div style="margin-top:4px">';
        cs.tool_calls.forEach(function (tc) {
          html += '<span class="tool-item">' + escHtml(tc.name);
          if (tc.args_summary) html += ': ' + escHtml(tc.args_summary);
          html += '</span>';
        });
        html += '</div></div>';
      }
      html += '</div></td></tr>';
    });
    html += '</tbody></table></div>';
  }

  // Prompt 信息
  if (d.prompt_info && d.prompt_info.messages) {
    html += '<div style="margin-top:4px;font-size:12px;color:var(--text2)">';
    html += '<span class="tip-wrap">Prompt: messages=' + escHtml(d.prompt_info.messages) + tipIcon('promptMsg') + '</span>';
    if (d.prompt_info.historyTextChars) html += ' <span class="tip-wrap">historyChars=' + escHtml(d.prompt_info.historyTextChars) + tipIcon('promptHistory') + '</span>';
    if (d.prompt_info.systemPromptChars) html += ' <span class="tip-wrap">sysPromptChars=' + escHtml(d.prompt_info.systemPromptChars) + tipIcon('promptSys') + '</span>';
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
  $('#summaryCards3').innerHTML = '';
  $('#content').innerHTML = '<div class="empty"><div class="icon">📭</div><p>暂无诊断数据</p><p style="margin-top:8px;font-size:13px">等待 OpenClaw 生成日志后自动显示</p></div>';
}

function showLoading() {
  $('#content').innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
}

function loadData() {
  showLoading();
  var d = currentDate;
  // 使用批量接口 /api/dashboard 一次获取所有数据，减少 HTTP 请求
  api('/api/dashboard?date=' + d + '&page=' + currentPage + '&per_page=' + perPage, function (data) {
    // 移除骨架屏
    var skeleton = $('#skeletonCards');
    if (skeleton) skeleton.style.display = 'none';
    if (!data) {
      // 回退到分离请求
      fetchSummary(d);
      fetchEventsSummary(d);
      fetchRuns(d, currentPage, perPage);
      return;
    }
    renderSummary(data.summary);
    renderEventsSummary(data.events);
    renderPipeline(data.events);
    renderRunList(data.runs);
  });
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

window.toggleCollapsible = function (id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('expanded');
};

window.toggleBlock = function (id, btn) {
  var el = document.getElementById(id);
  if (!el) return;
  var isOpen = el.classList.toggle('expanded');
  if (btn) {
    btn.classList.toggle('open', isOpen);
    var text = btn.textContent;
    if (isOpen) {
      btn.textContent = text.replace('▶', '▼').replace('展开', '收起');
    } else {
      btn.textContent = text.replace('▼', '▶').replace('收起', '展开');
    }
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
