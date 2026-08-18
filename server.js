const http = require("http");
const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const PORT = process.env.PORT || 3000;
const PUBLIC_DIR = path.join(__dirname, "public");

function serveStatic(req, res) {
  let filePath = path.join(PUBLIC_DIR, req.url === "/" ? "index.html" : req.url);

  // 安全：不允许访问 PUBLIC_DIR 之外的文件
  const normalized = path.normalize(filePath);
  if (!normalized.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      // 对于目录，返回 index.html
      if (stat && stat.isDirectory()) {
        filePath = path.join(filePath, "index.html");
        fs.readFile(filePath, (err2, data) => {
          if (err2) {
            res.writeHead(404);
            res.end("Not Found");
            return;
          }
          res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
          res.end(data);
        });
        return;
      }
      res.writeHead(404);
      res.end("Not Found");
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const mimeMap = {
      ".html": "text/html",
      ".css": "text/css",
      ".js": "application/javascript",
      ".json": "application/json",
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".svg": "image/svg+xml",
    };
    const contentType = mimeMap[ext] || "application/octet-stream";

    // SPA fallback: 对于非 API 路由，返回 index.html
    if (
      req.url.startsWith("/api/")
    ) {
      // 不处理非 API 的 SPA fallback；仅当静态资源存在时才返回
    }

    fs.readFile(filePath, (err2, data) => {
      if (err2) {
        res.writeHead(404);
        res.end("Not Found");
        return;
      }
      res.writeHead(200, { "Content-Type": `${contentType}; charset=utf-8` });
      res.end(data);
    });
  });
}

function runPython(config, onStdout, onStderr, onClose) {
  const pythonCmd = process.env.PYTHON_CMD || "python";
  const child = execFile(
    pythonCmd,
    ["compress_images.py"],
    { cwd: __dirname, maxBuffer: 1024 * 1024 * 10 },
    (err, stdout, stderr) => {
      if (stderr) {
        onStderr(stderr);
      }
      onClose(err ? err.code || 1 : 0, stdout);
    },
  );

  // 将配置通过 stdin 传进去
  const configJson = JSON.stringify(config);
  child.stdin.write(configJson);
  child.stdin.end();

  // 流式读取 stdout（NDJSON）
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString("utf-8");
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed);
        onStdout(msg);
      } catch (_) {
        // 忽略非 JSON 输出
      }
    }
  });

  child.stdout.on("end", () => {
    if (buffer.trim()) {
      try {
        const msg = JSON.parse(buffer.trim());
        onStdout(msg);
      } catch (_) {}
    }
  });
}

function runPythonPreview(config) {
  return new Promise((resolve, reject) => {
    const pythonCmd = process.env.PYTHON_CMD || "python";
    const child = execFile(
      pythonCmd,
      ["compress_images.py", "--preview"],
      { cwd: __dirname, maxBuffer: 1024 * 1024 * 50 },
      (err, stdout, stderr) => {
        if (err && !stdout) {
          reject(new Error(stderr || err.message));
          return;
        }
        try {
          const result = JSON.parse(stdout.trim());
          resolve(result);
        } catch (e) {
          reject(new Error(`解析预览结果失败: ${e.message}\n输出: ${stdout}`));
        }
      },
    );

    child.stdin.write(JSON.stringify(config));
    child.stdin.end();

    child.stderr.on("data", (data) => {
      // 忽略 stderr 中的警告信息
    });
  });
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 10 * 1024 * 1024) {
        req.destroy();
        reject(new Error("请求体过大"));
      }
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(new Error("无效的 JSON 请求体"));
      }
    });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  // API 路由
  if (req.method === "POST" && req.url === "/api/compress") {
    let config;
    try {
      config = await parseBody(req);
    } catch (e) {
      res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
      return;
    }

    res.writeHead(200, {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Transfer-Encoding": "chunked",
    });

    let completed = false;

    runPython(
      config,
      (msg) => {
        // 流式发送每个 JSON 消息
        res.write(JSON.stringify(msg) + "\n");
        if (msg.event === "done") {
          completed = true;
          res.end();
        }
      },
      (stderr) => {
        // stderr 只记录但不发送给客户端
        // 如果已经完成则不重复处理
      },
      (code, stdout) => {
        if (!completed) {
          // 子进程退出但没有发送 done 事件
          res.write(
            JSON.stringify({
              ok: false,
              event: "done",
              error: stdout || `子进程退出码: ${code}`,
              total: 0,
              success: 0,
              failed: 0,
              results: [],
            }) + "\n",
          );
          res.end();
        }
      },
    );

    // 超时处理
    const timeout = setTimeout(() => {
      if (!completed) {
        completed = true;
        try {
          res.write(
            JSON.stringify({
              ok: false,
              event: "done",
              error: "压缩超时（5 分钟）",
              total: 0,
              success: 0,
              failed: 0,
              results: [],
            }) + "\n",
          );
        } catch (_) {}
        res.end();
      }
    }, 5 * 60 * 1000);

    req.on("close", () => {
      clearTimeout(timeout);
    });

    return;
  }

  // 获取输入路径中的第一张图片
  if (req.method === "POST" && req.url === "/api/get-first-image") {
    let body;
    try {
      body = await parseBody(req);
    } catch (e) {
      res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
      return;
    }

    const inputPath = body.input_path;
    if (!inputPath) {
      res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: "缺少 input_path 参数" }));
      return;
    }

    const fsSync = require("fs");
    const pathSync = require("path");
    const absPath = pathSync.resolve(inputPath);

    if (!fsSync.existsSync(absPath)) {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: `输入路径不存在: ${inputPath}` }));
      return;
    }

    const stat = fsSync.statSync(absPath);
    if (!stat.isDirectory()) {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: `输入路径不是文件夹: ${inputPath}` }));
      return;
    }

    const imgExts = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"];
    let firstImage = null;
    try {
      const files = fsSync.readdirSync(absPath);
      for (const f of files.sort()) {
        const ext = pathSync.extname(f).toLowerCase();
        if (imgExts.includes(ext)) {
          const fullPath = pathSync.join(absPath, f);
          const s = fsSync.statSync(fullPath);
          if (s.isFile()) {
            firstImage = fullPath;
            break;
          }
        }
      }
    } catch (e) {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: `读取目录失败: ${e.message}` }));
      return;
    }

    if (!firstImage) {
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: "该文件夹中没有找到图片文件" }));
      return;
    }

    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: true, file_path: firstImage }));
    return;
  }

  // 预览 API
  if (req.method === "POST" && req.url === "/api/preview") {
    let body;
    try {
      body = await parseBody(req);
    } catch (e) {
      res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
      return;
    }

    if (!body.file_path) {
      res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: "缺少 file_path 参数" }));
      return;
    }

    try {
      const result = await runPythonPreview({
        file_path: body.file_path,
        encoder: body.encoder || "mozjpeg",
        quality: body.quality || 85,
        scale_percent: body.scale_percent || 100,
      });

      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(result));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }

    return;
  }

  // 浏览目录 API（供目录树选择器使用）
  if (req.method === "GET" && req.url.startsWith("/api/browse")) {
    const urlObj = new URL(req.url, `http://localhost:${PORT}`);
    const queryPath = urlObj.searchParams.get("path") || "";
    const fsSync = require("fs");
    const pathSync = require("path");

    try {
      let currentPath = "";
      let name = "";
      let children = [];

      if (!queryPath) {
        // 根级别：列出驱动器（Windows）
        const isWin = process.platform === "win32";
        if (isWin) {
          const { execSync } = require("child_process");
          try {
            const result = execSync("wmic logicaldisk get name", { encoding: "utf-8", timeout: 5000 });
            const drives = result
              .split("\n")
              .map((l) => l.trim())
              .filter((l) => /^[A-Za-z]:$/.test(l))
              .map((d) => ({ path: d + "\\", name: d + "\\" }));
            children = drives;
          } catch {
            for (let c = 65; c <= 90; c++) {
              const letter = String.fromCharCode(c);
              const p = letter + ":\\";
              try {
                if (fsSync.existsSync(p)) {
                  children.push({ path: p, name: p });
                }
              } catch {}
            }
          }
        } else {
          children = [{ path: "/", name: "/" }];
        }
      } else {
        currentPath = pathSync.resolve(queryPath);

        if (!fsSync.existsSync(currentPath)) {
          res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
          res.end(JSON.stringify({ ok: false, error: `路径不存在: ${queryPath}` }));
          return;
        }

        const stat = fsSync.statSync(currentPath);
        if (!stat.isDirectory()) {
          res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
          res.end(JSON.stringify({ ok: false, error: `不是文件夹: ${queryPath}` }));
          return;
        }

        name = pathSync.basename(currentPath) || currentPath;

        const entries = fsSync.readdirSync(currentPath, { withFileTypes: true });
        children = entries
          .filter((d) => d.isDirectory() && !d.name.startsWith(".") && !d.name.startsWith("$"))
          .map((d) => ({
            path: pathSync.join(currentPath, d.name),
            name: d.name,
          }))
          .sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN", { sensitivity: "base" }));
      }

      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({
        ok: true,
        current: currentPath || undefined,
        name: name || undefined,
        children,
      }));
    } catch (e) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // 静态资源
  serveStatic(req, res);
});

server.listen(PORT, () => {
  console.log(`图片压缩服务已启动: http://localhost:${PORT}`);
});