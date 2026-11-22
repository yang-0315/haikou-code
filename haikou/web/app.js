(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const form = $("#askForm");
  const questionInput = $("#question");
  const asOfInput = $("#asOf");
  const questionCount = $("#questionCount");
  const submitButton = $("#submitButton");
  const resultRegion = $("#resultRegion");
  const loadingState = $("#loadingState");
  const loadingTitle = $("#loadingTitle");
  const loadingDetail = $("#loadingDetail");
  const errorState = $("#errorState");
  const errorMessage = $("#errorMessage");
  const answerState = $("#answerState");
  const answerEyebrow = $("#answerEyebrow");
  const answerTitle = $("#answerTitle");
  const answerMeta = $("#answerMeta");
  const answerText = $("#answerText");
  const refusalBox = $("#refusalBox");
  const refusalText = $("#refusalText");
  const sourcesSection = $("#sourcesSection");
  const sourceCount = $("#sourceCount");
  const sourceList = $("#sourceList");
  const traceDetails = $("#traceDetails");
  const traceBody = $("#traceBody");
  const copyButton = $("#copyButton");
  const retryButton = $("#retryButton");
  const newQuestionButton = $("#newQuestionButton");
  const serviceStatus = $("#serviceStatus");
  const serviceStatusText = $("#serviceStatusText");
  const corpusSummary = $("#corpusSummary");
  const fileNotice = $("#fileNotice");

  let currentJobId = null;
  let pollTimer = null;
  let currentAnswer = "";
  const openedAsFile = window.location.protocol === "file:";

  const routeLabels = {
    article_exact: "精确条文",
    single_doc_tree: "沿树检索",
    single_doc_flat: "单文件检索",
    multi_doc: "多文件比对",
    default_flat: "综合检索",
    catalog_item: "事项目录",
    out_of_scope: "范围校验",
  };

  const stageLabels = {
    scope: "问题超出当前语料范围",
    recall: "没有找到可回答的政策条文",
    route: "当前问题不在本服务覆盖范围",
    model: "候选条文不足以支持确定回答",
    select_parse: "证据选择结果无法解析",
    select_empty: "所选条文在候选文件中不存在",
    llm_error: "模型服务暂不可用",
  };

  function updateCounter() {
    questionCount.textContent = `${questionInput.value.length} / 800`;
  }

  function clearPoll() {
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function setBusy(busy) {
    form.setAttribute("aria-busy", String(busy));
    submitButton.disabled = busy;
    submitButton.classList.toggle("is-loading", busy);
    submitButton.querySelector(".button-label").textContent =
      busy ? "正在处理" : "开始检索";
    resultRegion.setAttribute("aria-busy", String(busy));
  }

  function showOnly(panel) {
    resultRegion.hidden = false;
    [loadingState, errorState, answerState].forEach((item) => {
      item.hidden = item !== panel;
    });
  }

  function showError(message) {
    clearPoll();
    setBusy(false);
    showOnly(errorState);
    errorMessage.textContent = message;
    resultRegion.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function setLoading(job) {
    showOnly(loadingState);
    if (job.status === "queued") {
      const ahead = Number(job.queue_ahead || 0);
      loadingTitle.textContent = ahead > 0 ? "正在等待处理" : "正在启动检索";
      loadingDetail.textContent = ahead > 0
        ? `前面还有 ${ahead} 个问题，系统会按顺序处理。`
        : "系统正在准备候选政策文件。";
    } else {
      loadingTitle.textContent = "正在核对政策依据";
      loadingDetail.textContent = "正在召回条文、判断效力并组织可追溯回答。";
    }
  }

  async function parseResponse(response) {
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || `服务返回错误（${response.status}）`);
    }
    return data;
  }

  async function submitQuestion() {
    const question = questionInput.value.trim();
    if (openedAsFile) {
      showError("当前是直接打开的 HTML 文件，无法连接后端。"
        + "请先启动服务，再访问 http://127.0.0.1:8787/。");
      return;
    }
    if (!question) {
      questionInput.focus();
      showError("请输入要核实的政策问题。");
      return;
    }

    clearPoll();
    currentJobId = null;
    currentAnswer = "";
    setBusy(true);
    showOnly(loadingState);
    loadingTitle.textContent = "正在提交问题";
    loadingDetail.textContent = "正在建立本次政策检索任务。";
    resultRegion.scrollIntoView({ behavior: "smooth", block: "start" });

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          as_of: asOfInput.value.trim(),
        }),
      });
      const job = await parseResponse(response);
      currentJobId = job.id;
      setLoading(job);
      pollJob();
    } catch (error) {
      showError(error.message || "无法连接问答服务。");
    }
  }

  async function pollJob() {
    if (!currentJobId) {
      return;
    }
    try {
      const response = await fetch(`/api/ask/${currentJobId}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const job = await parseResponse(response);
      if (job.status === "queued" || job.status === "running") {
        setLoading(job);
        pollTimer = window.setTimeout(pollJob, 1200);
        return;
      }
      if (job.status === "failed") {
        showError(job.error || "服务处理失败，请稍后重试。");
        return;
      }
      if (job.status === "completed" && job.result) {
        setBusy(false);
        renderResult(job.result, job.duration_ms);
        return;
      }
      showError("任务状态异常，请重新提交问题。");
    } catch (error) {
      showError(error.message || "读取问答结果失败。");
    }
  }

  function addMetaPill(text, className = "") {
    const pill = document.createElement("span");
    pill.className = `meta-pill ${className}`.trim();
    pill.textContent = text;
    answerMeta.appendChild(pill);
  }

  function addParagraphs(container, text) {
    const blocks = String(text || "")
      .split(/\n\s*\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!blocks.length) {
      container.textContent = "未返回文字内容。";
      return;
    }
    blocks.forEach((block) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = block;
      container.appendChild(paragraph);
    });
  }

  function statusClass(status) {
    if (status === "现行有效") {
      return "status-current";
    }
    if (status === "已废止" || status === "条文已废止") {
      return "status-repealed";
    }
    if (status === "效力存疑" || status === "条文部分失效") {
      return "status-questionable";
    }
    if (status === "系列已废止") {
      return "status-series";
    }
    return "";
  }

  function renderSources(sources, forceSummaries) {
    sourceList.replaceChildren();
    const forceByDoc = new Map(
      (forceSummaries || []).map((item) => [item.doc, item]));
    if (!sources || !sources.length) {
      sourcesSection.hidden = true;
      return;
    }
    sourcesSection.hidden = false;
    sourceCount.textContent = `${sources.length} 条`;

    sources.forEach((source) => {
      const force = forceByDoc.get(source.doc) || {};
      const item = document.createElement("article");
      item.className = "source-item";

      const main = document.createElement("div");
      main.className = "source-main";
      const doc = document.createElement("div");
      doc.className = "source-doc";
      const title = document.createElement("strong");
      title.textContent = `《${source.doc || "未命名文件"}》${source.article || ""}`;
      doc.appendChild(title);
      if (source.doc_number) {
        const number = document.createElement("small");
        number.textContent = source.doc_number;
        doc.appendChild(number);
      }
      main.appendChild(doc);

      const badge = document.createElement("span");
      badge.className = `status-badge ${statusClass(source.status)}`.trim();
      badge.textContent = source.status || "状态未知";
      main.appendChild(badge);
      item.appendChild(main);

      const reasonText = source.reason || force.reason
        || force.caveats?.[0] || "";
      if (reasonText) {
        const reason = document.createElement("div");
        reason.className = "source-force";
        reason.textContent = reasonText;
        item.appendChild(reason);
      }
      sourceList.appendChild(item);
    });
  }

  function appendTraceBlock(title, content) {
    if (!content) {
      return;
    }
    const block = document.createElement("section");
    block.className = "trace-block";
    const heading = document.createElement("h4");
    heading.textContent = title;
    block.appendChild(heading);
    if (Array.isArray(content)) {
      const list = document.createElement("ul");
      content.filter(Boolean).forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        list.appendChild(li);
      });
      block.appendChild(list);
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = content;
      block.appendChild(paragraph);
    }
    traceBody.appendChild(block);
  }

  function renderTrace(result) {
    traceBody.replaceChildren();
    const route = result.route || {};
    const stage = result.refusal_stage
      ? (stageLabels[result.refusal_stage] || result.refusal_stage)
      : "";
    const asOf = result.as_of || {};
    appendTraceBlock(
      "检索策略",
      route.strategy
        ? `${routeLabels[route.strategy] || route.strategy}${route.reason ? `：${route.reason}` : ""}`
        : "使用系统默认的综合检索策略。");
    if (stage) {
      appendTraceBlock("未形成确定性回答的原因", stage);
    }
    appendTraceBlock("选条说明", result.reason);
    appendTraceBlock("效力取舍", result.force_note);
    appendTraceBlock("处理说明", result.notes || []);
    if (asOf.label || asOf.as_of) {
      appendTraceBlock(
        "查询时点",
        `${asOf.label || asOf.source || asOf.as_of}；系统按该时点判断效力。`);
    }
    const forceCaveats = (result.force_summary || [])
      .flatMap((item) => item.caveats || [])
      .filter(Boolean);
    appendTraceBlock("使用提醒", [...new Set(forceCaveats)]);
    traceDetails.hidden = traceBody.childElementCount === 0;
  }

  function renderResult(result, durationMs) {
    currentAnswer = result.answer || "";
    showOnly(answerState);
    answerMeta.replaceChildren();

    const answered = Boolean(result.has_answer && result.answer);
    answerEyebrow.textContent = answered ? "政策回答" : "检索结论";
    answerTitle.textContent = answered ? "回答" : "未形成确定性回答";
    copyButton.hidden = !answered;

    if (result.route?.strategy) {
      addMetaPill(routeLabels[result.route.strategy] || result.route.strategy);
    }
    if (result.as_of?.label || result.as_of?.as_of) {
      addMetaPill(
        `时点 ${result.as_of.label || result.as_of.source || result.as_of.as_of}`,
        "is-time");
    }
    if (Number.isFinite(durationMs)) {
      addMetaPill(`耗时 ${(durationMs / 1000).toFixed(1)} 秒`);
    }
    if (result.sources?.length) {
      addMetaPill(`引用 ${result.sources.length} 条`);
    }

    answerText.replaceChildren();
    if (answered) {
      addParagraphs(answerText, result.answer);
      refusalBox.hidden = true;
    } else {
      answerText.hidden = true;
      refusalBox.hidden = false;
      refusalText.textContent = result.refusal
        || stageLabels[result.refusal_stage]
        || "候选证据不足，系统没有生成确定性回答。";
    }
    if (answered) {
      answerText.hidden = false;
    }

    renderSources(result.sources || [], result.force_summary || []);
    renderTrace(result);
    resultRegion.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function copyAnswer() {
    if (!currentAnswer) {
      return;
    }
    const label = copyButton.querySelector("span");
    try {
      await navigator.clipboard.writeText(currentAnswer);
      label.textContent = "已复制";
    } catch {
      label.textContent = "复制失败";
    }
    window.setTimeout(() => {
      label.textContent = "复制回答";
    }, 1800);
  }

  function retry() {
    if (questionInput.value.trim()) {
      submitQuestion();
    } else {
      questionInput.focus();
    }
  }

  function resetQuestion() {
    clearPoll();
    currentJobId = null;
    resultRegion.hidden = true;
    questionInput.focus();
    questionInput.select();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadHealth() {
    try {
      const response = await fetch("/api/health", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const health = await parseResponse(response);
      serviceStatus.dataset.state = health.status;
      if (health.status === "ok") {
        serviceStatusText.textContent = "服务正常";
      } else if (!health.provider?.configured) {
        serviceStatusText.textContent = "模型未配置";
      } else {
        serviceStatusText.textContent = "服务降级";
      }
      const files = Number(health.database?.corpus_files || 0);
      corpusSummary.textContent = files
        ? `已收录 ${files.toLocaleString("zh-CN")} 份政策正文`
        : "语料状态暂不可用";
    } catch {
      serviceStatus.dataset.state = "offline";
      serviceStatusText.textContent = "服务未连接";
      corpusSummary.textContent = "无法读取语料状态";
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuestion();
  });
  questionInput.addEventListener("input", updateCounter);
  questionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submitQuestion();
    }
  });
  document.querySelectorAll(".example-chip").forEach((button) => {
    button.addEventListener("click", () => {
      questionInput.value = button.dataset.question || "";
      asOfInput.value = button.dataset.asOf || "";
      updateCounter();
      questionInput.focus();
    });
  });
  copyButton.addEventListener("click", copyAnswer);
  retryButton.addEventListener("click", retry);
  newQuestionButton.addEventListener("click", resetQuestion);

  updateCounter();
  if (openedAsFile) {
    fileNotice.hidden = false;
    document.documentElement.classList.add("file-mode");
  }
  loadHealth();
})();
