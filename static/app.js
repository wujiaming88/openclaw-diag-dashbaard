/* OpenClaw 诊断面板 v4.0.0 — 前端逻辑 (多节点支持) */
(function () {
'use strict';

// ============================================================
// 状态
// ============================================================
var currentDate = '';
var currentNode = '';  // '' = legacy mode (no node), 'local' = local, 'xxx' = remote
var nodesList = [];    // [{node_id, node_name, last_report_at, status}]
var autoTimer = null;
var autoInterval = 30000;
var openRuns = {};
var currentPage = 1;
var perPage = 20;
var mcPage = 1;
var mcPerPage = 50;
var dashboardMode = 'standard'; // 'standard' | 'advanced'
var conversationLoaded = false;

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

function fmtChars(n) {
  if (!n) return '0';
  if (n < 1000) return n.toString();
  if (n < 1000000) return (n / 1000).toFixed(1) + 'k';
  return (n / 1000000).toFixed(1) + 'M';
}

function fmtCost(v) {
  if (!v || v === 0) return '$0';
  if (v < 0.01) return '$' + v.toFixed(6);
  if (v < 1) return '$' + v.toFixed(4);
  return '$' + v.toFixed(2);
}

function fmtDowntime(sec) {
  if (sec === null || sec === undefined) return '-';
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm' + (sec % 60) + 's';
  return Math.floor(sec / 3600) + 'h' + Math.floor((sec % 3600) / 60) + 'm';
}

function fmtShortTs(isoStr) {
  if (!isoStr) return '-';
  try {
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) throw 'invalid';
    var bj = new Date(d.getTime() + 8 * 3600000);
    var mo = String(bj.getUTCMonth() + 1).padStart(2, '0');
    var dd = String(bj.getUTCDate()).padStart(2, '0');
    var hh = String(bj.getUTCHours()).padStart(2, '0');
    var mm = String(bj.getUTCMinutes()).padStart(2, '0');
    var ss = String(bj.getUTCSeconds()).padStart(2, '0');
    return mo + '-' + dd + ' ' + hh + ':' + mm + ':' + ss;
  } catch (e) {
    var m = isoStr.match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
    if (!m) return isoStr;
    return m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] + ':' + m[6];
  }
}

function fmtTime(isoStr) {
  if (!isoStr) return '';
  try {
    // 解析为 Date 对象（自动处理 Z / +00:00 / +08:00 等时区）
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) throw 'invalid';
    // 转为北京时间显示（UTC+8）
    var bj = new Date(d.getTime() + 8 * 3600000);
    var hh = String(bj.getUTCHours()).padStart(2, '0');
    var mm = String(bj.getUTCMinutes()).padStart(2, '0');
    var ss = String(bj.getUTCSeconds()).padStart(2, '0');
    var ms = String(bj.getUTCMilliseconds()).padStart(3, '0');
    return hh + ':' + mm + ':' + ss + '.' + ms;
  } catch (e) {
    // fallback: 原始截取
    if (isoStr.indexOf('T') > -1) {
      var t = isoStr.split('T')[1] || isoStr;
      return t.length > 12 ? t.substring(0, 12) : t;
    }
    return isoStr;
  }
}

function fmtDateTime(isoStr) {
  if (!isoStr) return '';
  try {
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) throw 'invalid';
    var bj = new Date(d.getTime() + 8 * 3600000);
    var yyyy = bj.getUTCFullYear();
    var mo = String(bj.getUTCMonth() + 1).padStart(2, '0');
    var dd = String(bj.getUTCDate()).padStart(2, '0');
    var hh = String(bj.getUTCHours()).padStart(2, '0');
    var mm = String(bj.getUTCMinutes()).padStart(2, '0');
    var ss = String(bj.getUTCSeconds()).padStart(2, '0');
    return yyyy + '-' + mo + '-' + dd + ' ' + hh + ':' + mm + ':' + ss;
  } catch (e) {
    return isoStr;
  }
}

function speedClass(ms) {
  if (ms > 5000) return 'slow';
  if (ms > 1000) return 'medium';
  return '';
}

function statusClass(s) { return 'status-' + s; }

function statusIcon(s) {
  var m = { ok: '✅', error: '❌', aborted: '⚠️', running: '🔄', virtual: '🔮' };
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

function isAdvanced() { return dashboardMode === 'advanced'; }

// ============================================================
// API 请求
// ============================================================
function nodeApiPath(path) {
  // 如果选中了节点，将 /api/xxx 路由改为 /api/node/<node_id>/xxx
  if (currentNode) {
    // 把 /api/xxx 变成 /api/node/<node_id>/xxx
    if (path.startsWith('/api/')) {
      var rest = path.substring(5);  // 去掉 /api/
      return '/api/node/' + encodeURIComponent(currentNode) + '/' + rest;
    }
  }
  return path;
}

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

function fetchMode(cb) {
  var path = currentNode ? nodeApiPath('/api/mode') : '/api/mode';
  api(path, function (data) {
    if (data && data.mode) {
      dashboardMode = data.mode;
    }
    renderModeIndicator();
    if (cb) cb();
  });
}

function fetchNodes(cb) {
  api('/api/nodes', function (nodes) {
    if (!nodes || !Array.isArray(nodes) || nodes.length === 0) {
      // No nodes = legacy single-node mode
      nodesList = [];
      currentNode = '';
      renderNodeSelector();
      if (cb) cb();
      return;
    }
    nodesList = nodes;
    // Default: select 'local' if present, otherwise first
    if (!currentNode) {
      var local = nodes.find(function(n) { return n.node_id === 'local'; });
      currentNode = local ? 'local' : nodes[0].node_id;
    }
    renderNodeSelector();
    if (cb) cb();
  });
}

function renderNodeSelector() {
  var sel = $('#nodeSelect');
  if (!sel) return;
  sel.innerHTML = '';
  if (nodesList.length === 0) {
    // Hide selector in legacy mode
    var wrap = sel.closest('.node-selector-wrap');
    if (wrap) wrap.style.display = 'none';
    return;
  }
  var wrap = sel.closest('.node-selector-wrap');
  if (wrap) wrap.style.display = '';
  nodesList.forEach(function(n) {
    var o = document.createElement('option');
    o.value = n.node_id;
    var statusIcon = '🔴';
    if (n.status === 'local') statusIcon = '🔵';
    else if (n.status === 'online') statusIcon = '🟢';
    o.textContent = statusIcon + ' ' + (n.node_name || n.node_id);
    sel.appendChild(o);
  });
  sel.value = currentNode;
}

function fetchSystemInfo() {
  var path = currentNode ? nodeApiPath('/api/system_info') : '/api/system_info';
  api(path, function (info) {
    renderSystemInfo(info);
  });
}

function fetchDates() {
  var path = currentNode ? nodeApiPath('/api/dates') : '/api/dates';
  api(path, function (dates) {
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
  var path = currentNode ? nodeApiPath('/api/summary') : '/api/summary';
  api(path + '?date=' + date, function (summary) {
    var skeleton = $('#skeletonCards');
    if (skeleton) skeleton.style.display = 'none';
    renderSummary(summary);
  });
}

function fetchEventsSummary(date) {
  var path = currentNode ? nodeApiPath('/api/events') : '/api/events';
  api(path + '?date=' + date, function (data) {
    renderEventsSummary(data);
    renderPipeline(data);
  });
}

function fetchRuns(date, page, pp) {
  var path = currentNode ? nodeApiPath('/api/runs') : '/api/runs';
  api(path + '?date=' + date + '&page=' + page + '&per_page=' + pp, function (data) {
    renderRunList(data);
  });
}

function fetchRunDetail(rid, el) {
  el.innerHTML = '<div class="loading"><span class="spinner"></span>加载详情...</div>';
  var path = currentNode ? nodeApiPath('/api/run/' + rid) : '/api/run/' + rid;
  api(path + '?date=' + currentDate, function (d) {
    if (!d) { el.innerHTML = '<p>加载失败</p>'; return; }
    renderRunDetail(d, el);
  });
}

function fetchModelCalls(date, page, pp) {
  var path = currentNode ? nodeApiPath('/api/model_calls') : '/api/model_calls';
  api(path + '?date=' + date + '&page=' + page + '&per_page=' + pp, function (data) {
    renderModelCallsList(data);
  });
}

// ============================================================
// 渲染 — 模式指示器
// ============================================================
function renderModeIndicator() {
  var el = $('#modeIndicator');
  if (!el) return;
  if (isAdvanced()) {
    el.innerHTML = '<span class="mode-badge advanced">🔵 高级诊断模式</span>';
  } else {
    el.innerHTML = '<span class="mode-badge standard">🟢 标准模式</span>';
  }
  el.style.display = 'inline-block';
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
// 渲染 — 摘要卡片
// ============================================================
var TIPS = {
  runs: 'Agent 处理用户消息的总次数。每次用户发消息或系统触发都算一次 Run',
  avgDur: '从收到消息到回复完成的平均端到端耗时。包含推理等待和工具执行',
  inferRatio: '推理（等待模型响应）占总耗时的比例。越高说明瓶颈在模型侧；越低说明工具执行耗时多',
  totalTokens: '模型生成的输出 Token 总数。Token 数量直接决定 API 费用',
  errors: '处理失败的 Run 数。常见原因：模型超时、工具报错、会话异常',
  e2e: '端到端耗时 = 从 Agent 收到消息开始，到最终回复发送完毕的总时间',
  inferTotal: '所有推理段的总耗时。模型每次决定调用工具或生成回复前都需要推理',
  toolTotal: '所有工具执行的总耗时。包括 exec、read、write、web_search 等',
  outputToken: '本次 Run 模型输出的总 Token 数。包含工具调用指令和最终回复文本',
  tokPerS: '模型输出速率（Token/秒）。Opus 通常 20-50 tok/s，Sonnet 50-100 tok/s，Haiku 100-200 tok/s',
  model: '本次 Run 使用的模型。不同模型在速度、质量、费用之间有不同权衡',
  channel: '消息来源渠道（如 Telegram、Discord）。影响消息格式和传输延迟',
  tokenInput: 'Input Token: 发送给模型的输入 Token 数（不含缓存部分）。通常很少，因为大部分被缓存命中',
  tokenOutput: 'Output Token: 模型生成的输出 Token 数。这是主要的费用来源',
  tokenCacheRead: 'Cache Read: 从缓存中读取的 Token 数。命中缓存可节省 90% 的输入费用',
  tokenCacheWrite: 'Cache Write: 写入缓存的 Token 数。首次对话或上下文变化时产生',
  inferSeg: '模型的每次推理过程。第一次推理决定要做什么（调用工具或直接回复），后续推理处理工具结果',
  outputTokens: '该段推理中模型输出的 Token 数。越多说明回复越长或工具调用参数越复杂',
  promptMsg: 'Prompt 中的消息条数。包含系统提示、对话历史、工具定义等',
  promptHistory: '对话历史的字符数。越大说明上下文越长，推理越慢',
  promptSys: '系统提示的字符数。包含人设、规则、技能定义等固定内容',
  cacheHit: '缓存命中率。高命中率说明上下文被有效缓存，可降低延迟和费用',
  webhooks: '收到的 Webhook 请求数。来自 Telegram 等渠道的原始消息推送',
  msgProcessed: '成功处理完成的消息数。包含排队、推理、回复全流程',
  avgProcessTime: '消息从入队到处理完成的平均耗时。反映整体响应速度',
  avgQueueWait: '消息在队列中等待的平均时间。过长说明并发处理能力不足',
  sessionStuck: '会话卡住的次数。表示某个会话长时间未完成，可能需要人工介入',
  sessionInferMs: '基于 session 消息时间戳计算的平均推理延迟。精确测量每次模型调用从请求到响应的耗时',
  sessionTps: '基于 session 消息时间戳计算的平均 Token 吞吐量（输出 Token 数 / 推理耗时）',
  restarts: 'Gateway 重启次数。包括手动重启、配置变更重启和崩溃重启',
  modelCalls: '当日模型调用总次数。每次 assistant 消息计为一次调用',
  totalCost: '当日模型调用总费用。基于各模型的 Token 单价计算',
  toolCalls: '工具调用总次数。包括 exec、read、write、edit、web_search 等所有工具',
  toolErrors: '工具执行失败次数。包括 isError=true 或 exec exitCode!=0',
  toolAvgMs: '工具执行的平均耗时（毫秒）。基于 details.durationMs 计算',
  thinkingChars: '模型 thinking 内容的总字符数。反映推理链的深度和复杂度',
  thinkingRatio: '平均 thinking 占比 = thinking字符 / (thinking + output字符)。越高说明模型在更多内部思考',
  thinkingCalls: '有 thinking 内容的模型调用次数',
};

function tipAttr(key) {
  return TIPS[key] ? ' data-tip="' + escHtml(TIPS[key]) + '"' : '';
}

function tipIcon(key) {
  return TIPS[key] ? ' <span class="tip-icon"' + tipAttr(key) + '>?</span>' : '';
}

function renderSummary(s) {
  if (!s) {
    $('#summaryCards').innerHTML = '';
    $('#summaryCards2').innerHTML = '';
    $('#summaryCards4').innerHTML = '';
    return;
  }

  var html = '';

  if (isAdvanced()) {
    html += '<div class="card"><div class="label">Run 总数' + tipIcon('runs') + '</div><div class="value">' + (s.total_runs || 0) + '</div></div>';
    html += '<div class="card"><div class="label">平均耗时' + tipIcon('avgDur') + '</div><div class="value">' + fmtMs(s.avg_duration_ms) + '</div></div>';
    html += '<div class="card"><div class="label">推理占比' + tipIcon('inferRatio') + '</div><div class="value">' + (s.infer_ratio || 0) + '%</div><div class="ratio-bar"><div class="fill-infer" style="width:' + (s.infer_ratio || 0) + '%"></div><div class="fill-tool" style="width:' + (100 - (s.infer_ratio || 0)) + '%"></div></div></div>';
    html += '<div class="card"><div class="label">平均速率' + tipIcon('tokPerS') + '</div><div class="value">' + (s.avg_tok_per_s || 0) + ' tok/s</div></div>';
    var errCls = (s.error_count || 0) > 0 ? ' error' : '';
    html += '<div class="card' + errCls + '"><div class="label">错误数' + tipIcon('errors') + '</div><div class="value">' + (s.error_count || 0) + '</div></div>';
  }

  html += '<div class="card"><div class="label">模型调用数' + tipIcon('modelCalls') + '</div><div class="value">' + (s.session_model_call_count || 0) + '</div></div>';
  html += '<div class="card inference-card"><div class="label">平均推理延迟' + tipIcon('sessionInferMs') + '</div><div class="value">' + fmtMs(s.session_avg_inference_ms || 0) + '</div><div class="sub-value">' + (s.session_inference_count || 0) + ' 次调用</div></div>';
  html += '<div class="card inference-card"><div class="label">Token 吞吐量' + tipIcon('sessionTps') + '</div><div class="value">' + (s.session_avg_tokens_per_sec || 0) + ' tok/s</div><div class="sub-value">总推理 ' + fmtMs(s.session_total_inference_ms || 0) + '</div></div>';

  var restartCls = (s.restart_count || 0) > 0 ? ' warn' : '';
  html += '<div class="card restart-card' + restartCls + '"><div class="label">Gateway 重启' + tipIcon('restarts') + '</div><div class="value">' + (s.restart_count || 0) + '</div>';
  if ((s.total_downtime_sec || 0) > 0) {
    html += '<div class="sub-value">停机 ' + fmtDowntime(s.total_downtime_sec) + '</div>';
  }
  html += '</div>';
  $('#summaryCards').innerHTML = html;

  // 第二行：Token 消耗指标
  var html2 = '';
  html2 += '<div class="card"><div class="label">输出 Token' + tipIcon('tokenOutput') + '</div><div class="value">' + fmtTok(s.total_tokens_output) + '</div></div>';
  html2 += '<div class="card"><div class="label">输入 Token' + tipIcon('tokenInput') + '</div><div class="value">' + fmtTok(s.total_tokens_input || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">缓存读取' + tipIcon('tokenCacheRead') + '</div><div class="value">' + fmtTok(s.total_cache_read || 0) + '</div></div>';
  html2 += '<div class="card"><div class="label">缓存写入' + tipIcon('tokenCacheWrite') + '</div><div class="value">' + fmtTok(s.total_cache_write || 0) + '</div></div>';
  var hitCls = (s.cache_hit_ratio || 0) > 80 ? '' : ' warn';
  html2 += '<div class="card' + hitCls + '"><div class="label">缓存命中率' + tipIcon('cacheHit') + '</div><div class="value">' + (s.cache_hit_ratio || 0) + '%</div></div>';
  html2 += '<div class="card"><div class="label">总费用' + tipIcon('totalCost') + '</div><div class="value">' + fmtCost(s.total_model_cost || 0) + '</div></div>';
  $('#summaryCards2').innerHTML = html2;

  // 第四行：工具统计 + Thinking 统计
  var html4 = '';
  var toolCallCount = s.tool_call_count || 0;
  var toolErrorCount = s.tool_error_count || 0;
  var toolAvgMs = s.tool_avg_duration_ms || 0;
  var toolErrCls = toolErrorCount > 0 ? ' error' : '';
  html4 += '<div class="card tool-card"><div class="label">工具调用' + tipIcon('toolCalls') + '</div><div class="value">' + toolCallCount + '</div></div>';
  html4 += '<div class="card tool-card' + toolErrCls + '"><div class="label">工具错误' + tipIcon('toolErrors') + '</div><div class="value">' + toolErrorCount + '</div></div>';
  html4 += '<div class="card tool-card"><div class="label">工具平均耗时' + tipIcon('toolAvgMs') + '</div><div class="value">' + fmtMs(toolAvgMs) + '</div></div>';

  // Thinking stats
  var thinkingChars = s.thinking_total_chars || 0;
  var thinkingCalls = s.thinking_calls_count || 0;
  var thinkingRatio = s.thinking_avg_ratio || 0;
  if (thinkingCalls > 0) {
    html4 += '<div class="card thinking-card"><div class="label">Thinking 字符' + tipIcon('thinkingChars') + '</div><div class="value">' + fmtChars(thinkingChars) + '</div><div class="sub-value">' + thinkingCalls + ' 次调用</div></div>';
    html4 += '<div class="card thinking-card"><div class="label">Thinking 占比' + tipIcon('thinkingRatio') + '</div><div class="value">' + (thinkingRatio * 100).toFixed(1) + '%</div></div>';
  }
  $('#summaryCards4').innerHTML = html4;
}

// ============================================================
// 渲染 — 事件摘要卡片 (仅高级模式)
// ============================================================
function renderEventsSummary(data) {
  var el = $('#summaryCards3');
  if (!el) return;
  if (!isAdvanced() || !data || !data.summary) { el.innerHTML = ''; return; }
  var s = data.summary;
  var ms_stats = data.message_stats || {};
  var ss = data.session_stats || {};
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
  if (!isAdvanced() || !data || !data.summary) { sec.style.display = 'none'; return; }
  var s = data.summary;
  var ms_stats = data.message_stats || {};
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
// 渲染 — 工具 details 标签
// ============================================================
function renderToolDetails(details, toolName) {
  if (!details || typeof details !== 'object') return '';
  var html = '';
  if (toolName === 'exec' || details.exitCode !== undefined) {
    var ec = details.exitCode;
    if (ec !== undefined && ec !== null) {
      var ecCls = (ec === 0) ? 'exit-ok' : 'exit-err';
      html += '<span class="tool-detail-tag ' + ecCls + '">exit:' + ec + '</span>';
    }
    if (details.durationMs) {
      html += '<span class="tool-detail-tag duration">' + fmtMs(details.durationMs) + '</span>';
    }
  } else if (toolName === 'edit' || details.diff !== undefined) {
    if (details.status) {
      html += '<span class="tool-detail-tag exit-ok">' + escHtml(details.status) + '</span>';
    }
    if (details.diff) {
      var diffPreview = details.diff.substring(0, 80);
      html += '<span class="tool-detail-tag diff" title="' + escHtml(details.diff.substring(0, 300)) + '">diff: ' + escHtml(diffPreview) + '</span>';
    }
  } else if (toolName === 'web_fetch' || details.tookMs !== undefined) {
    if (details.tookMs) {
      html += '<span class="tool-detail-tag fetch">' + fmtMs(details.tookMs) + '</span>';
    }
    if (details.contentType) {
      html += '<span class="tool-detail-tag duration">' + escHtml(details.contentType) + '</span>';
    }
  } else if (toolName === 'sessions_spawn' || details.childSessionKey !== undefined) {
    if (details.status) {
      html += '<span class="tool-detail-tag exit-ok">' + escHtml(details.status) + '</span>';
    }
    if (details.modelApplied) {
      html += '<span class="tool-detail-tag duration">' + escHtml(shortModel(details.modelApplied)) + '</span>';
    }
  } else {
    // Generic: show durationMs if present
    if (details.durationMs) {
      html += '<span class="tool-detail-tag duration">' + fmtMs(details.durationMs) + '</span>';
    }
  }
  return html;
}

// ============================================================
// 渲染 — 错误列表 (仅高级模式)
// ============================================================
function renderErrors(data) {
  var sec = $('#errorsSection');
  var body = $('#errorsBody');
  var badge = $('#errorCount');
  if (!sec || !body) return;
  if (!isAdvanced() || !data || !data.errors || data.errors.length === 0) {
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
    html += '<td style="white-space:nowrap">' + fmtTime(e.time || '') + '</td>';
    html += '<td><span class="error-type-tag ' + tagCls + '">' + sevLabel + '</span></td>';
    html += '<td><span class="error-type-tag">' + escHtml(e.type || '') + '</span></td>';
    html += '<td class="error-subsystem">' + escHtml(e.subsystem || '') + '</td>';
    html += '<td>' + escHtml(shortDetail) + '</td>';
    html += '<td class="error-source">' + escHtml(e.source_file || '') + '</td>';
    html += '</tr>';

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

// ============================================================
// 渲染 — Gateway 重启历史
// ============================================================
function renderRestarts(data) {
  var sec = $('#restartsSection');
  var body = $('#restartsBody');
  if (!sec || !body) return;
  if (!data || !data.restarts || data.restarts.length === 0) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = 'block';
  var restarts = data.restarts;
  var html = '';

  if (data.current_pid) {
    html += '<div class="restart-current-info">';
    html += '🟢 当前进程: PID <strong>' + escHtml(data.current_pid) + '</strong>';
    if (data.current_since) {
      html += ' &nbsp;启动于: <strong>' + escHtml(data.current_since) + '</strong>';
    }
    html += '</div>';
  }

  html += '<div class="restarts-scroll"><table class="restarts-table"><thead><tr>';
  html += '<th>#</th><th>停机时间</th><th>恢复时间</th><th>停机时长</th><th>类型</th><th>原因</th>';
  html += '</tr></thead><tbody>';
  restarts.forEach(function (r) {
    var typeCls = r.type === 'CRASH' ? 'restart-crash' : 'restart-sigterm';
    var typeIcon = r.type === 'CRASH' ? '💥' : '🔄';
    var recovered = r.startup_utc !== null;
    html += '<tr class="' + typeCls + '">';
    html += '<td>' + r.num + '</td>';
    html += '<td class="mono">' + fmtShortTs(r.shutdown_utc) + '</td>';
    html += '<td class="mono">' + (recovered ? fmtShortTs(r.startup_utc) : '<span class="not-recovered">NOT RECOVERED</span>') + '</td>';
    html += '<td>' + (r.downtime_sec !== null ? fmtDowntime(r.downtime_sec) : '-') + '</td>';
    html += '<td>' + typeIcon + ' ' + escHtml(r.type) + '</td>';
    html += '<td class="restart-reason">' + escHtml(r.reason) + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  html += '<div class="restart-summary">共 <strong>' + data.total + '</strong> 次重启</div>';
  body.innerHTML = html;
}

// ============================================================
// 渲染 — 模型调用列表 (标准模式核心表格)
// ============================================================
function renderModelCallsList(data) {
  var sec = $('#modelCallsSection');
  var body = $('#modelCallsBody');
  if (!sec || !body) return;
  sec.style.display = 'block';

  // Show conversation section
  var convSec = $('#conversationSection');
  if (convSec) convSec.style.display = 'block';

  if (!data || !data.model_calls || data.model_calls.length === 0) {
    body.innerHTML = '<div class="empty" style="padding:24px"><div class="icon">📭</div><p>该日期暂无模型调用数据</p></div>';
    return;
  }

  var calls = data.model_calls;
  var total = data.total || 0;
  var page = data.page || 1;
  var pp = data.per_page || 50;
  var totalPages = data.total_pages || 1;
  mcPage = page;

  var html = '<div class="model-calls-scroll"><table class="model-calls-table"><thead><tr>';
  html += '<th>时间</th><th>模型</th><th>推理耗时</th><th>tok/s</th><th>输入</th><th>输出</th><th>缓存</th><th>费用</th><th>💭</th><th>停止原因</th><th></th>';
  html += '</tr></thead><tbody>';

  calls.forEach(function (mc, idx) {
    var mcTs = fmtTime(mc.timestamp);
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
    var detailId = 'mc-list-' + idx;

    // Thinking column
    var thinkChars = mc.thinking_chars || 0;
    var thinkHtml = '-';
    if (thinkChars > 0) {
      var barW = Math.min(60, Math.max(4, Math.round(thinkChars / 500)));
      thinkHtml = '<span class="thinking-bar" style="width:' + barW + 'px" title="' + thinkChars + ' chars (' + ((mc.thinking_ratio || 0) * 100).toFixed(0) + '%)"></span> ' + fmtChars(thinkChars);
    }

    html += '<tr onclick="toggleMcRunDetail(\'' + detailId + '\')" style="cursor:pointer">';
    html += '<td class="mono">' + escHtml(mcTs) + '</td>';
    html += '<td class="model-name">' + escHtml(shortModel(mc.model || '')) + '</td>';
    html += '<td class="' + inferCls + '">' + (inferMs > 0 ? fmtMs(inferMs) : '-') + '</td>';
    html += '<td>' + (tps > 0 ? tps + ' tok/s' : '-') + '</td>';
    html += '<td>' + fmtTok(mcU.input || 0) + '</td>';
    html += '<td>' + fmtTok(mcU.output || 0) + '</td>';
    html += '<td>' + cacheStr + '</td>';
    html += '<td class="cost">' + costStr + '</td>';
    html += '<td style="font-size:11px">' + thinkHtml + '</td>';
    html += '<td><span class="stop-tag ' + stopCls + '">' + escHtml(mc.stop_reason || '-') + '</span></td>';
    html += '<td style="font-size:11px;color:var(--text2)">▶</td>';
    html += '</tr>';

    // 展开详情
    var cs = mc.content_summary || {};
    var prompt = mc.prompt || {};
    html += '<tr><td colspan="11" style="padding:0"><div class="mc-detail" id="' + detailId + '">';

    if (prompt.text) {
      var pid = detailId + '-prompt';
      html += '<div class="mc-content-block"><strong>📝 Prompt (用户输入):</strong>';
      html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + pid + '\', this)">▶ 展开 (' + prompt.text.length + ' 字)</span>';
      html += '<div class="collapsible-content" id="' + pid + '"><pre>' + escHtml(prompt.text) + '</pre></div></div>';
    }
    if (cs.has_thinking) {
      var tid = detailId + '-think';
      var thinkText = cs.thinking_full || cs.thinking_preview || '';
      if (thinkText) {
        html += '<div class="mc-content-block"><strong>💭 Thinking (' + fmtChars(thinkChars) + ', ' + ((mc.thinking_ratio || 0) * 100).toFixed(0) + '%):</strong>';
        html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + tid + '\', this)">▶ 展开 (' + thinkText.length + ' 字)</span>';
        html += '<div class="collapsible-content" id="' + tid + '"><pre>' + escHtml(thinkText) + '</pre></div></div>';
      }
    }
    if (cs.has_text) {
      var oid = detailId + '-out';
      var outText = cs.text_full || cs.text_preview || '';
      if (outText) {
        html += '<div class="mc-content-block"><strong>💬 Output:</strong>';
        html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + oid + '\', this)">▶ 展开 (' + outText.length + ' 字)</span>';
        html += '<div class="collapsible-content" id="' + oid + '"><pre>' + escHtml(outText) + '</pre></div></div>';
      }
    }
    if (cs.tool_calls && cs.tool_calls.length > 0) {
      html += '<div class="mc-content-block"><strong>🔧 工具调用:</strong><div style="margin-top:4px">';
      cs.tool_calls.forEach(function (tc) {
        html += '<div class="tool-item" style="display:block;margin:4px 0">' + escHtml(tc.name);
        if (tc.args_summary) html += ': ' + escHtml(tc.args_summary);
        // Render details
        var detailsHtml = renderToolDetails(tc.details, tc.name);
        if (detailsHtml) html += ' ' + detailsHtml;
        html += '</div>';
      });
      html += '</div></div>';
    }
    html += '</div></td></tr>';
  });
  html += '</tbody></table></div>';

  // 分页
  html += '<div class="pagination">';
  html += '<button onclick="goMcPage(' + (page - 1) + ')"' + (page <= 1 ? ' disabled' : '') + '>◀ 上一页</button>';
  html += '<span class="page-info">第 ' + page + ' / ' + totalPages + ' 页 (共 ' + total + ' 条)</span>';
  html += '<button onclick="goMcPage(' + (page + 1) + ')"' + (page >= totalPages ? ' disabled' : '') + '>下一页 ▶</button>';
  html += '<select onchange="changeMcPerPage(this.value)">';
  [20, 50, 100].forEach(function (n) {
    html += '<option value="' + n + '"' + (n === pp ? ' selected' : '') + '>' + n + ' 条/页</option>';
  });
  html += '</select>';
  html += '</div>';

  body.innerHTML = html;
}

window.goMcPage = function (p) {
  mcPage = p;
  fetchModelCalls(currentDate, mcPage, mcPerPage);
};

window.changeMcPerPage = function (v) {
  mcPerPage = parseInt(v) || 50;
  mcPage = 1;
  fetchModelCalls(currentDate, 1, mcPerPage);
};

// ============================================================
// 渲染 — 锁定提示 (标准模式下高级功能)
// ============================================================
function renderLockedSections() {
  if (isAdvanced()) {
    var lockRuns = $('#lockedRunsSection');
    var lockEvents = $('#lockedEventsSection');
    if (lockRuns) lockRuns.style.display = 'none';
    if (lockEvents) lockEvents.style.display = 'none';
    return;
  }
  var lockRuns = $('#lockedRunsSection');
  var lockEvents = $('#lockedEventsSection');
  if (lockRuns) lockRuns.style.display = 'block';
  if (lockEvents) lockEvents.style.display = 'block';
}

// ============================================================
// 渲染 — Run 列表 (仅高级模式)
// ============================================================
function renderRunList(data) {
  if (!isAdvanced()) return;
  var content = $('#content');
  if (!data) {
    content.innerHTML = '<div class="empty"><div class="icon">📭</div><p>加载失败</p></div>';
    return;
  }
  var runs = data.runs || [];
  var total = data.total || 0;
  var page = data.page || 1;
  var pp = data.per_page || 20;
  var totalPages = data.total_pages || 1;
  currentPage = page;

  if (total === 0) {
    content.innerHTML = '<div class="empty"><div class="icon">📭</div><p>该日期暂无 Run 数据</p></div>';
    return;
  }
  var colSpan = 12;
  var html = '<div class="table-wrap"><div class="runs-scroll" id="runsScroll"><table><thead><tr>';
  html += '<th>开始</th><th>结束</th><th>Run ID</th><th>模型</th><th>通道</th><th>端到端</th><th>推理</th><th>工具</th><th>工具数</th><th>输出Token</th><th>状态</th>';
  html += '</tr></thead><tbody>';
  runs.forEach(function (r) {
    var durCls = speedClass(r.duration_ms);
    var short_id = r.run_id.substring(0, 8);
    var virtualTag = r.virtual ? '<span class="virtual-tag">[虚拟]</span>' : '';
    html += '<tr class="clickable" data-runid="' + escHtml(r.run_id) + '" onclick="toggleRun(this)">';
    html += '<td class="mono">' + fmtTime(r.start) + '</td>';
    html += '<td class="mono">' + (r.end ? fmtTime(r.end) : '-') + '</td>';
    html += '<td class="mono" title="' + escHtml(r.run_id) + '">' + virtualTag + escHtml(short_id) + '</td>';
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

  content.innerHTML = html;

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

  html += '<div style="margin-bottom:12px;font-size:13px;color:var(--text2)">';
  html += '开始: <strong style="color:var(--text)">' + fmtDateTime(d.start) + '</strong>';
  html += ' &nbsp;结束: <strong style="color:var(--text)">' + (d.end ? fmtDateTime(d.end) : '-') + '</strong>';
  html += ' &nbsp;输出速率: <strong style="color:var(--text)">' + (d.overall_tok_per_s || 0) + ' tok/s' + tipIcon('tokPerS') + '</strong>';
  html += '</div>';

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
      html += '<tr><td><strong>' + escHtml(t.tool) + '</strong>';
      // Show details tags
      if (t.result && t.result.details) {
        html += '<div style="margin-top:2px">' + renderToolDetails(t.result.details, t.tool) + '</div>';
      }
      html += '</td><td class="tool-args">';
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

  html += '<div class="summary-bar">';
  html += '<div class="item"><div class="val">' + fmtMs(d.duration_ms) + '</div><div class="lbl">端到端' + tipIcon('e2e') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.infer_ms) + '</div><div class="lbl">推理总耗时' + tipIcon('inferTotal') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtMs(d.tool_ms) + '</div><div class="lbl">工具总耗时' + tipIcon('toolTotal') + '</div></div>';
  html += '<div class="item"><div class="val">' + fmtTok(d.total_tokens_output) + '</div><div class="lbl">输出 Token' + tipIcon('outputToken') + '</div></div>';
  html += '<div class="item"><div class="val">' + (d.overall_tok_per_s || 0) + ' tok/s</div><div class="lbl">输出速率' + tipIcon('tokPerS') + '</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.model) + '</div><div class="lbl">模型' + tipIcon('model') + '</div></div>';
  html += '<div class="item"><div class="val">' + escHtml(d.channel) + '</div><div class="lbl">通道' + tipIcon('channel') + '</div></div>';
  html += '</div>';

  if (d.token_summary) {
    var ts = d.token_summary;
    html += '<div style="margin-top:8px;font-size:12px;color:var(--text2)">';
    html += 'Token: <span class="tip-wrap">input=' + fmtTok(ts.input) + tipIcon('tokenInput') + '</span>';
    html += ' <span class="tip-wrap">output=' + fmtTok(ts.output) + tipIcon('tokenOutput') + '</span>';
    html += ' <span class="tip-wrap">cacheRead=' + fmtTok(ts.cacheRead) + tipIcon('tokenCacheRead') + '</span>';
    html += ' <span class="tip-wrap">cacheWrite=' + fmtTok(ts.cacheWrite) + tipIcon('tokenCacheWrite') + '</span>';
    html += '</div>';
  }

  if (d.model_calls && d.model_calls.length > 0) {
    html += '<div class="detail-section" style="margin-top:12px"><h4>🤖 模型调用 (' + d.model_calls.length + ' 次)</h4>';
    html += '<table class="model-calls-table"><thead><tr>';
    html += '<th>时间</th><th>模型</th><th>推理耗时</th><th>输入</th><th>输出</th><th>tok/s</th><th>缓存</th><th>费用</th><th>💭</th><th>停止</th><th></th>';
    html += '</tr></thead><tbody>';
    var rUid = d.run_id ? d.run_id.substring(0, 8) : 'x';
    d.model_calls.forEach(function (mc, mcIdx) {
      var mcTs = fmtTime(mc.timestamp);
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
      var detailId = 'mc-run-' + rUid + '-' + mcIdx;

      var thinkChars = mc.thinking_chars || 0;
      var thinkHtml = '-';
      if (thinkChars > 0) {
        var barW = Math.min(60, Math.max(4, Math.round(thinkChars / 500)));
        thinkHtml = '<span class="thinking-bar" style="width:' + barW + 'px"></span> ' + fmtChars(thinkChars);
      }

      html += '<tr onclick="toggleMcRunDetail(\'' + detailId + '\')" style="cursor:pointer">';
      html += '<td>' + escHtml(mcTs) + '</td>';
      html += '<td class="model-name">' + escHtml(shortModel(mc.model || '')) + '</td>';
      html += '<td class="' + inferCls + '">' + (inferMs > 0 ? fmtMs(inferMs) : '-') + '</td>';
      html += '<td>' + fmtTok(mcU.input || 0) + '</td>';
      html += '<td>' + fmtTok(mcU.output || 0) + '</td>';
      html += '<td>' + (tps > 0 ? tps + ' tok/s' : '-') + '</td>';
      html += '<td>' + cacheStr + '</td>';
      html += '<td class="cost">' + costStr + '</td>';
      html += '<td style="font-size:11px">' + thinkHtml + '</td>';
      html += '<td><span class="stop-tag ' + stopCls + '">' + escHtml(mc.stop_reason || '-') + '</span></td>';
      html += '<td style="font-size:11px;color:var(--text2)">▶</td>';
      html += '</tr>';

      var cs = mc.content_summary || {};
      var prompt = mc.prompt || {};
      html += '<tr><td colspan="11" style="padding:0"><div class="mc-detail" id="' + detailId + '">';
      if (prompt.text) {
        var pid = detailId + '-prompt';
        html += '<div class="mc-content-block"><strong>📝 Prompt:</strong>';
        html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + pid + '\', this)">▶ 展开 (' + prompt.text.length + ' 字)</span>';
        html += '<div class="collapsible-content" id="' + pid + '"><pre>' + escHtml(prompt.text) + '</pre></div></div>';
      }
      if (cs.has_thinking) {
        var tid = detailId + '-think';
        var thinkText = cs.thinking_full || cs.thinking_preview || '';
        if (thinkText) {
          html += '<div class="mc-content-block"><strong>💭 Thinking (' + fmtChars(thinkChars) + '):</strong>';
          html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + tid + '\', this)">▶ 展开 (' + thinkText.length + ' 字)</span>';
          html += '<div class="collapsible-content" id="' + tid + '"><pre>' + escHtml(thinkText) + '</pre></div></div>';
        }
      }
      if (cs.has_text) {
        var oid = detailId + '-out';
        var outText = cs.text_full || cs.text_preview || '';
        if (outText) {
          html += '<div class="mc-content-block"><strong>💬 Output:</strong>';
          html += '<span class="collapse-toggle" onclick="toggleBlock(\'' + oid + '\', this)">▶ 展开 (' + outText.length + ' 字)</span>';
          html += '<div class="collapsible-content" id="' + oid + '"><pre>' + escHtml(outText) + '</pre></div></div>';
        }
      }
      if (cs.tool_calls && cs.tool_calls.length > 0) {
        html += '<div class="mc-content-block"><strong>🔧 工具调用:</strong><div style="margin-top:4px">';
        cs.tool_calls.forEach(function (tc) {
          html += '<div class="tool-item" style="display:block;margin:4px 0">' + escHtml(tc.name);
          if (tc.args_summary) html += ': ' + escHtml(tc.args_summary);
          var detailsHtml = renderToolDetails(tc.details, tc.name);
          if (detailsHtml) html += ' ' + detailsHtml;
          html += '</div>';
        });
        html += '</div></div>';
      }
      html += '</div></td></tr>';
    });
    html += '</tbody></table></div>';
  }

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
// 渲染 — 会话浏览器 (功能 4/5/6)
// ============================================================
window.toggleConversationSection = function (el) {
  el.classList.toggle('open');
  var body = el.nextElementSibling;
  if (body) {
    var wasOpen = body.classList.contains('open');
    body.classList.toggle('open');
    if (!wasOpen && !conversationLoaded) {
      loadSessionsList();
    }
  }
};

function loadSessionsList() {
  var date = currentDate;
  var path = currentNode ? nodeApiPath('/api/sessions') : '/api/sessions';
  api(path + '?date=' + (date || ''), function (sessions) {
    var sel = $('#sessionSelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">选择会话...</option>';
    if (!sessions || sessions.length === 0) {
      sel.innerHTML += '<option disabled>暂无会话数据</option>';
      return;
    }
    sessions.forEach(function (s) {
      var o = document.createElement('option');
      o.value = s.session_id;
      var label = (s.agent ? s.agent + '/' : '') + s.session_id.substring(0, 8);
      label += ' (' + s.message_count + ' msgs';
      if (s.model) label += ', ' + shortModel(s.model);
      label += ')';
      o.textContent = label;
      sel.appendChild(o);
    });
    conversationLoaded = true;
  });
}

window.loadConversationTree = function () {
  var sel = $('#sessionSelect');
  var tree = $('#conversationTree');
  var info = $('#sessionInfo');
  if (!sel || !tree) return;
  var sid = sel.value;
  if (!sid) {
    tree.innerHTML = '';
    if (info) info.textContent = '';
    return;
  }
  tree.innerHTML = '<div class="loading"><span class="spinner"></span>加载会话...</div>';
  var convPath = currentNode ? nodeApiPath('/api/conversation_tree') : '/api/conversation_tree';
  api(convPath + '?session_id=' + encodeURIComponent(sid) + '&date=' + (currentDate || ''), function (messages) {
    if (!messages || messages.length === 0) {
      tree.innerHTML = '<div class="empty" style="padding:24px"><div class="icon">📭</div><p>暂无消息</p></div>';
      return;
    }
    if (info) info.textContent = messages.length + ' 条消息';

    // Build parent->children map for indentation
    var parentMap = {};
    var idSet = {};
    messages.forEach(function (m) { idSet[m.id] = true; });

    var html = '';
    messages.forEach(function (m, idx) {
      var role = m.role || '';
      var mType = m.type || '';
      var icon = '📄';
      var cls = 'conv-message';
      var indentCls = '';

      if (mType === 'custom_message') {
        icon = '🔄';
        cls += ' system-event';
      } else if (mType === 'model-snapshot') {
        icon = '📸';
        cls += ' model-snapshot';
      } else if (role === 'user') {
        icon = '👤';
      } else if (role === 'assistant') {
        icon = '🤖';
      } else if (role === 'toolResult') {
        icon = '🔧';
        indentCls = ' conv-message-indent';
      }

      // Indentation based on parentId
      if (m.parentId && idSet[m.parentId]) {
        if (role !== 'toolResult') {
          indentCls = ' conv-message-indent';
        } else {
          indentCls = ' conv-message-indent-2';
        }
      }

      var time = fmtTime(m.timestamp);
      var preview = m.preview || '';

      html += '<div class="' + cls + indentCls + '" onclick="toggleConvMsg(' + idx + ')">';
      html += '<div class="conv-message-icon">' + icon + '</div>';
      html += '<div class="conv-message-body">';
      html += '<span class="conv-message-time">' + escHtml(time) + '</span>';
      html += '<span class="conv-message-preview">' + escHtml(preview) + '</span>';

      // Meta info for assistant
      if (role === 'assistant') {
        var meta = '';
        if (m.inference_ms) meta += '<span class="inference">' + fmtMs(m.inference_ms) + '</span> ';
        if (m.tokens_per_sec) meta += '<span class="inference">' + m.tokens_per_sec + ' tok/s</span> ';
        if (m.model) meta += escHtml(shortModel(m.model)) + ' ';
        if (m.has_thinking) meta += '💭 ';
        if (m.tool_count > 0) meta += '🔧×' + m.tool_count;
        if (meta) html += '<div class="conv-message-meta">' + meta + '</div>';
      }

      // Full content (hidden by default)
      if (m.full_text) {
        html += '<div class="conv-message-full" id="conv-full-' + idx + '">' + escHtml(m.full_text) + '</div>';
      }

      html += '</div></div>';
    });
    tree.innerHTML = html;
  });
};

window.toggleConvMsg = function (idx) {
  var fullEl = document.getElementById('conv-full-' + idx);
  if (!fullEl) return;
  var parent = fullEl.closest('.conv-message');
  if (parent) parent.classList.toggle('expanded');
};

// ============================================================
// 页面状态
// ============================================================
function showEmpty() {
  $('#summaryCards').innerHTML = '';
  $('#summaryCards2').innerHTML = '';
  $('#summaryCards3').innerHTML = '';
  $('#summaryCards4').innerHTML = '';
  $('#content').innerHTML = '<div class="empty"><div class="icon">📭</div><p>暂无诊断数据</p><p style="margin-top:8px;font-size:13px">等待 OpenClaw 生成日志后自动显示</p></div>';
}

function showLoading() {
  if (isAdvanced()) {
    $('#content').innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
  }
}

function loadData() {
  showLoading();
  renderLockedSections();
  conversationLoaded = false;
  var d = currentDate;
  var dashPath = currentNode ? nodeApiPath('/api/dashboard') : '/api/dashboard';
  api(dashPath + '?date=' + d + '&page=' + currentPage + '&per_page=' + perPage + '&mc_page=' + mcPage + '&mc_per_page=' + mcPerPage, function (data) {
    var skeleton = $('#skeletonCards');
    if (skeleton) skeleton.style.display = 'none';
    if (!data) {
      fetchSummary(d);
      if (isAdvanced()) {
        fetchEventsSummary(d);
        fetchRuns(d, currentPage, perPage);
      }
      fetchModelCalls(d, mcPage, mcPerPage);
      return;
    }
    renderSummary(data.summary);
    renderRestarts(data.restarts);
    renderModelCallsList(data.model_calls);

    if (isAdvanced()) {
      if (data.events) {
        renderEventsSummary(data.events);
        renderPipeline(data.events);
      }
      if (data.runs) {
        renderRunList(data.runs);
      }
      if (data.errors) {
        renderErrors(data.errors);
      }
    }
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
// 全局事件处理
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
// 探测控制面板
// ============================================================
var probesList = [];
var probeResults = {};

function fetchProbes() {
  // Probes only available for local mode (not node-scoped)
  api('/api/probes', function (data) {
    if (data && data.probes) {
      probesList = data.probes;
      renderProbeCards();
    }
  });
}

function renderProbeCards() {
  var el = $('#probeCards');
  if (!el || !probesList.length) return;
  var html = '';
  probesList.forEach(function (p) {
    var state = probeResults[p.name];
    var cls = '';
    var statusHtml = '';
    if (state === 'running') {
      cls = ' running';
      statusHtml = '<div class="probe-status">⏳ 执行中...</div>';
    } else if (state && state.ok !== undefined) {
      cls = state.ok ? ' success' : ' error';
      var durStr = state.duration_ms ? (state.duration_ms / 1000).toFixed(1) + 's' : '';
      statusHtml = '<div class="probe-status">' + (state.ok ? '✅ 成功' : '❌ 失败') + (durStr ? ' (' + durStr + ')' : '') + '</div>';
    }
    var disabled = state === 'running' ? ' disabled' : '';
    html += '<div class="probe-card' + cls + '" id="probe-card-' + escHtml(p.name) + '">';
    html += '<div class="probe-icon">' + escHtml(p.icon || '🔍') + '</div>';
    html += '<div class="probe-label">' + escHtml(p.label) + '</div>';
    html += '<div class="probe-desc">' + escHtml(p.description) + '</div>';
    html += '<button class="probe-btn" onclick="runProbe(\'' + escHtml(p.name) + '\')"' + disabled + '>▶ 执行</button>';
    html += statusHtml;
    html += '</div>';
  });
  el.innerHTML = html;
}

function renderProbeResults() {
  var el = $('#probeResults');
  if (!el) return;
  var html = '';
  var hasResults = false;
  probesList.forEach(function (p) {
    var state = probeResults[p.name];
    if (!state || state === 'running') return;
    hasResults = true;
    var isOk = state.ok;
    var durStr = state.duration_ms ? (state.duration_ms / 1000).toFixed(1) + 's' : '';
    var resultId = 'probe-result-' + p.name;
    html += '<div class="probe-result-item">';
    html += '<div class="probe-result-header" onclick="toggleProbeResult(\'' + resultId + '\')">';
    html += '<span class="result-icon">' + escHtml(p.icon || '🔍') + '</span>';
    html += '<span class="result-label">' + escHtml(p.label) + '</span>';
    html += '<span class="result-status ' + (isOk ? 'ok' : 'fail') + '">' + (isOk ? '✅ 通过' : '❌ 失败') + '</span>';
    html += '<span class="result-dur">' + durStr + '</span>';
    html += '</div>';
    html += '<div class="probe-result-body open" id="' + resultId + '">';
    if (state.error) {
      html += '<pre style="color:var(--red)">' + escHtml(state.error) + '</pre>';
    } else if (state.output) {
      if (state.output.data) {
        html += '<pre>' + escHtml(JSON.stringify(state.output.data, null, 2)) + '</pre>';
      } else if (state.output.raw) {
        html += '<pre>' + escHtml(state.output.raw) + '</pre>';
      }
    }
    if (state.stderr) {
      html += '<pre style="color:var(--yellow);margin-top:6px">[stderr] ' + escHtml(state.stderr) + '</pre>';
    }
    html += '</div>';
    html += '</div>';
  });
  if (!hasResults) {
    html = '<div style="padding:16px;text-align:center;color:var(--text2);font-size:13px">点击探测卡片或"全部执行"按钮查看结果</div>';
  }
  el.innerHTML = html;
}

function apiPost(path, cb) {
  var x = new XMLHttpRequest();
  x.open('POST', path);
  x.setRequestHeader('Content-Type', 'application/json');
  x.onload = function () {
    if (x.status === 200) {
      try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); }
    } else {
      try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); }
    }
  };
  x.onerror = function () { cb(null); };
  x.send('{}');
}

window.runProbe = function (name) {
  probeResults[name] = 'running';
  renderProbeCards();
  apiPost('/api/probe/' + name, function (result) {
    if (result) {
      probeResults[name] = result;
    } else {
      probeResults[name] = { ok: false, error: '请求失败' };
    }
    renderProbeCards();
    renderProbeResults();
  });
};

window.runAllProbes = function () {
  probesList.forEach(function (p) {
    probeResults[p.name] = 'running';
  });
  renderProbeCards();
  renderProbeResults();
  apiPost('/api/probe/all', function (result) {
    if (result && result.probes) {
      Object.keys(result.probes).forEach(function (name) {
        probeResults[name] = result.probes[name];
      });
    } else {
      probesList.forEach(function (p) {
        if (probeResults[p.name] === 'running') {
          probeResults[p.name] = { ok: false, error: '请求失败' };
        }
      });
    }
    renderProbeCards();
    renderProbeResults();
  });
};

window.toggleProbeResult = function (id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('open');
};

// ============================================================
// 初始化
// ============================================================
$('#dateSelect').addEventListener('change', function () {
  currentDate = this.value;
  currentPage = 1;
  mcPage = 1;
  openRuns = {};
  conversationLoaded = false;
  loadData();
});

$('#nodeSelect').addEventListener('change', function () {
  currentNode = this.value;
  currentPage = 1;
  mcPage = 1;
  openRuns = {};
  conversationLoaded = false;
  // Reload everything for new node
  fetchMode(function () {
    fetchSystemInfo();
    fetchDates();
  });
});

$('#autoRefreshSelect').addEventListener('change', function () {
  initAutoRefresh(parseInt(this.value) || 0);
});

// Initial load: fetch nodes first, then mode/dates
fetchNodes(function () {
  fetchMode(function () {
    fetchSystemInfo();
    fetchProbes();
    fetchDates();
    initAutoRefresh(autoInterval);
  });
});

})();
