const $ = (id) => document.getElementById(id);

/* ---- 滑块实时显示 ---- */
$("quality").addEventListener("input", (e) => {
  $("qualityVal").textContent = e.target.value;
});

$("scalePercent").addEventListener("input", (e) => {
  $("scaleVal").textContent = e.target.value;
});

/* ---- 编码器切换时更新质量滑块提示 ---- */
$("encoder").addEventListener("change", (e) => {
  const enc = e.target.value;
  const hint = $("qualityHint");
  if (enc === "pngquant") {
    hint.textContent =
      "pngquant 自动选择最佳质量，该滑块将忽略";
    $("quality").disabled = true;
    $("qualityVal").textContent = "—";
  } else {
    hint.textContent =
      "仅 JPEG/WebP/AVIF 编码器有效，pngquant 自动选择最佳质量";
    $("quality").disabled = false;
    $("qualityVal").textContent = $("quality").value;
  }
});

/* ---- 路径选择 ---- */
$("btnPickInput").addEventListener("click", () => {
  DirPicker.openPicker($("inputPath"), "选择输入文件夹");
});

$("btnPickOutput").addEventListener("click", () => {
  DirPicker.openPicker($("outputPath"), "选择输出文件夹");
});

function setOutputPathEnabled(enabled) {
  $("outputPath").disabled = !enabled;
  $("btnPickOutput").disabled = !enabled;
  if (!enabled) $("outputPath").value = "";
}

$("overwriteOriginal").addEventListener("change", (e) => {
  setOutputPathEnabled(!e.target.checked);
});

setOutputPathEnabled(true);

/* ---- 构建配置 ---- */
function buildConfig() {
  const enc = $("encoder").value;
  return {
    input_path: $("inputPath").value.trim(),
    output_path: $("outputPath").value.trim(),
    include_subfolders: $("includeSubfolders").checked,
    overwrite_original: $("overwriteOriginal").checked,
    keep_original_name: $("keepOriginalName").checked,
    prefix: $("prefix").value,
    suffix: $("suffix").value,
    encoder: enc,
    quality: parseInt($("quality").value, 10),
    scale_percent: parseInt($("scalePercent").value, 10),
  };
}

/* ---- 日志 / 进度 ---- */
function appendLog(line) {
  const log = $("log");
  log.textContent += line + "\n";
  log.scrollTop = log.scrollHeight;
}

function setProgress(index, total, lastFile) {
  const pct = total > 0 ? Math.round((index / total) * 100) : 0;
  $("progressFill").style.width = `${pct}%`;
  const name = lastFile ? pathBasename(lastFile) : "";
  $("progressText").textContent =
    total > 0
      ? `正在处理 ${index} / ${total}${name ? `：${name}` : ""}`
      : "正在扫描图片…";
}

function pathBasename(p) {
  const parts = p.replace(/[/\\]+$/, "").split(/[/\\]/);
  return parts[parts.length - 1] || p;
}

/* ---- 流式 NDJSON 读取 ---- */
async function readNdjsonStream(response, onMessage) {
  const ct = response.headers.get("content-type") || "";
  if (!response.ok) {
    if (ct.includes("json")) {
      const err = await response.json();
      throw Object.assign(new Error(err.error || "请求失败"), { data: err });
    }
    const text = await response.text();
    throw new Error(text || `请求失败 (${response.status})`);
  }

  if (!response.body) {
    throw new Error("浏览器不支持流式响应，请使用 Chrome / Edge 最新版");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let lastDone = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const msg = JSON.parse(trimmed);
      onMessage(msg);
      if (msg.event === "done") lastDone = msg;
    }
  }

  if (buffer.trim()) {
    const msg = JSON.parse(buffer.trim());
    onMessage(msg);
    if (msg.event === "done") lastDone = msg;
  }

  return lastDone;
}

/* ---- 开始压缩 ---- */
$("btnStart").addEventListener("click", async () => {
  const config = buildConfig();
  if (!config.input_path) {
    alert("请选择或填写输入文件夹路径");
    return;
  }
  if (!config.overwrite_original && !config.output_path) {
    alert("未勾选「覆盖原文件」时，请选择或填写输出文件夹路径");
    return;
  }

  const btn = $("btnStart");
  const panel = $("logPanel");
  btn.disabled = true;
  panel.hidden = false;
  panel.classList.remove("error");
  $("log").textContent = "";
  $("progressFill").style.width = "0%";
  $("progressText").textContent = "正在启动…";

  try {
    const res = await fetch("/api/compress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });

    const summary = await readNdjsonStream(res, (msg) => {
      if (msg.event === "start") {
        const total = msg.total ?? 0;
        $("progressText").textContent =
          total > 0 ? `共 ${total} 张图片，开始处理…` : "未找到可处理的图片";
        $("progressFill").style.width = "0%";
      }
      if (msg.event === "progress") {
        const idx = msg.index ?? 0;
        const total = msg.total ?? 0;
        setProgress(idx, total, msg.source);
      }
    });

    if (!summary) {
      panel.classList.add("error");
      $("progressText").textContent = "未收到完成结果";
      return;
    }

    if (!summary.ok) {
      panel.classList.add("error");
      $("progressFill").style.width = "100%";
      $("progressText").textContent = summary.error || "压缩失败";
      return;
    }

    const total = summary.total ?? 0;
    const success = summary.success ?? 0;
    const failed = summary.failed ?? 0;
    $("progressFill").style.width = "100%";
    $("progressText").textContent =
      `完成：共 ${total} 张，成功 ${success} 张，失败 ${failed} 张`;

    (summary.results || []).forEach((r) => {
      if (r.ok) {
        appendLog(`✓ ${r.source}\n  → ${r.output}`);
      } else {
        appendLog(`✗ ${r.source}\n  ${r.error || ""}`);
      }
    });
  } catch (err) {
    panel.classList.add("error");
    if (err.data) {
      $("progressText").textContent = err.data.error || err.message;
    } else {
      $("progressText").textContent = `请求失败: ${err.message}`;
    }
  } finally {
    btn.disabled = false;
  }
});

/* ============ 预览功能 ============ */

function openPreviewModal() {
  const modal = $("previewModal");
  modal.hidden = false;
  document.body.classList.add("modal-open");
  // 重置状态
  $("previewOriginalImg").src = "";
  $("previewCompressedImg").src = "";
  $("previewOriginalInfo").textContent = "";
  $("previewCompressedInfo").textContent = "";
  $("previewSummary").hidden = true;
  $("previewLoading").hidden = true;
  $("previewError").hidden = true;
}

function closePreviewModal() {
  $("previewModal").hidden = true;
  document.body.classList.remove("modal-open");
}

// 打开预览按钮 — 新流程
$("btnPreview").addEventListener("click", async () => {
  // 第1步：检测输入路径和参数是否已选择
  const inputPath = $("inputPath").value.trim();
  const encoder = $("encoder").value;
  const quality = $("quality").value;
  const scalePercent = $("scalePercent").value;

  const missing = [];
  if (!inputPath) missing.push("• 输入文件夹路径");
  if (!encoder) missing.push("• 编码器");
  if (!quality && quality !== "0") missing.push("• 压缩质量");
  if (!scalePercent && scalePercent !== "0") missing.push("• 尺寸比例");

  if (missing.length > 0) {
    alert("请先完成以下设置后再预览：\n" + missing.join("\n"));
    return;
  }

  // 第2步：打开弹窗并显示加载状态
  openPreviewModal();
  $("previewLoading").hidden = false;
  $("previewError").hidden = true;

  try {
    // 第3步：自动获取输入路径中的第一张图片
    const getImgRes = await fetch("/api/get-first-image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_path: inputPath }),
    });

    const getImgData = await getImgRes.json();
    if (!getImgData.ok) {
      throw new Error(getImgData.error || "获取图片列表失败");
    }

    const filePath = getImgData.file_path;
    $("previewCurrentFile").textContent = pathBasename(filePath);

    // 第4步：生成预览
    const config = buildConfig();
    const previewRes = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: filePath,
        encoder: config.encoder,
        quality: config.quality,
        scale_percent: config.scale_percent,
      }),
    });

    if (!previewRes.ok) {
      const ct = previewRes.headers.get("content-type") || "";
      let msg = `请求失败 (${previewRes.status})`;
      if (ct.includes("json")) {
        const err = await previewRes.json();
        msg = err.error || msg;
      }
      throw new Error(msg);
    }

    const result = await previewRes.json();
    if (!result.ok) {
      throw new Error(result.error || "预览生成失败");
    }

    // 显示原始图片
    if (result.original_data_url) {
      $("previewOriginalImg").src = result.original_data_url;
    }

    // 显示压缩后的图片
    $("previewCompressedImg").src = result.compressed_data_url || "";

    // 信息
    $("previewOriginalInfo").textContent = result.original_info || "";
    $("previewCompressedInfo").textContent = result.compressed_info || "";

    // 压缩比
    if (result.ratio_text) {
      $("previewRatio").textContent = result.ratio_text;
      $("previewSummary").hidden = false;
    } else {
      $("previewSummary").hidden = true;
    }

    $("previewLoading").hidden = true;
  } catch (err) {
    $("previewLoading").hidden = true;
    $("previewError").hidden = false;
    $("previewError").textContent = err.message;
  }
});

// 关闭预览弹窗（点击遮罩 / 关闭按钮）
document.querySelectorAll("#previewModal [data-close]").forEach((el) => {
  el.addEventListener("click", closePreviewModal);
});

// 按 Esc 关闭
document.addEventListener("keydown", (e) => {
  if (
    e.key === "Escape" &&
    !$("previewModal").hidden
  ) {
    closePreviewModal();
  }
});
