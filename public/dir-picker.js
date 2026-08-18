/**
 * 目录树选择器（通过 /api/browse 懒加载）
 */
const DirPicker = (() => {
  const modal = document.getElementById("dirModal");
  const treeEl = document.getElementById("dirTree");
  const breadcrumbEl = document.getElementById("dirBreadcrumb");
  const currentLabel = document.getElementById("dirCurrentPath");
  const confirmBtn = document.getElementById("dirConfirm");
  const titleEl = document.getElementById("dirModalTitle");

  let targetInput = null;
  let selectedPath = "";
  let currentPath = "";
  let expanded = new Set();

  async function fetchBrowse(path) {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    const res = await fetch(`/api/browse${q}`);
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || "无法读取目录");
    }
    return data;
  }

  function setSelected(path) {
    selectedPath = path || "";
    confirmBtn.disabled = !selectedPath;
    currentLabel.textContent = selectedPath
      ? `已选：${selectedPath}`
      : "请在左侧选择文件夹";
    treeEl.querySelectorAll(".dir-node").forEach((el) => {
      el.classList.toggle("selected", el.dataset.path === selectedPath);
    });
  }

  function renderBreadcrumb(data) {
    breadcrumbEl.innerHTML = "";
    const items = [];

    const rootBtn = document.createElement("button");
    rootBtn.type = "button";
    rootBtn.className = "crumb";
    rootBtn.textContent = "此电脑";
    rootBtn.addEventListener("click", () => navigateTo(""));
    items.push(rootBtn);

    if (data.current) {
      const parts = data.current.split(/[/\\]/).filter(Boolean);
      let acc = "";
      const isWin = /^[A-Za-z]:/.test(data.current);
      parts.forEach((part, i) => {
        if (isWin && i === 0) {
          acc = `${part}\\`;
        } else if (i === 0 && !isWin) {
          acc = `/${part}`;
        } else {
          acc = pathJoin(acc, part);
        }
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "crumb";
        btn.textContent = part.replace(/\\$/, "");
        const target = acc;
        btn.addEventListener("click", () => navigateTo(target));
        items.push(btn);
      });
    }

    items.forEach((btn, i) => {
      breadcrumbEl.appendChild(btn);
      if (i < items.length - 1) {
        const sep = document.createElement("span");
        sep.className = "crumb-sep";
        sep.textContent = "›";
        breadcrumbEl.appendChild(sep);
      }
    });
  }

  function pathJoin(a, b) {
    if (!a) return b;
    const sep = a.includes("\\") ? "\\" : "/";
    return a.endsWith(sep) ? a + b : a + sep + b;
  }

  function createNode(item, depth) {
    const li = document.createElement("li");
    li.className = "dir-tree-item";

    const row = document.createElement("div");
    row.className = "dir-node";
    row.dataset.path = item.path;
    row.style.paddingLeft = `${8 + depth * 16}px`;

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "dir-toggle";
    toggle.textContent = expanded.has(item.path) ? "▼" : "▶";
    toggle.title = "展开/收起";

    const label = document.createElement("button");
    label.type = "button";
    label.className = "dir-label";
    label.textContent = item.name;

    row.appendChild(toggle);
    row.appendChild(label);
    li.appendChild(row);

    const childUl = document.createElement("ul");
    childUl.className = "dir-children";
    childUl.hidden = !expanded.has(item.path);
    li.appendChild(childUl);

    label.addEventListener("click", () => {
      setSelected(item.path);
      navigateTo(item.path);
    });

    toggle.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (expanded.has(item.path)) {
        expanded.delete(item.path);
        childUl.hidden = true;
        toggle.textContent = "▶";
        return;
      }
      expanded.add(item.path);
      toggle.textContent = "▼";
      childUl.hidden = false;
      if (!childUl.dataset.loaded) {
        childUl.innerHTML =
          '<li class="dir-loading">加载中…</li>';
        try {
          const data = await fetchBrowse(item.path);
          childUl.innerHTML = "";
          data.children.forEach((child) => {
            childUl.appendChild(createNode(child, depth + 1));
          });
          if (!data.children.length) {
            childUl.innerHTML =
              '<li class="dir-empty">没有子文件夹</li>';
          }
          childUl.dataset.loaded = "1";
        } catch (err) {
          childUl.innerHTML = `<li class="dir-error">${err.message}</li>`;
        }
      }
    });

    return li;
  }

  async function renderTree(data) {
    currentPath = data.current || "";
    renderBreadcrumb(data);
    treeEl.innerHTML = "";

    if (!data.current) {
      data.children.forEach((item) => {
        treeEl.appendChild(createNode(item, 0));
      });
      setSelected("");
      return;
    }

    const rootLi = document.createElement("li");
    rootLi.className = "dir-tree-item";
    const row = document.createElement("div");
    row.className = "dir-node selected";
    row.dataset.path = data.current;
    row.style.paddingLeft = "8px";
    const label = document.createElement("button");
    label.type = "button";
    label.className = "dir-label dir-label-root";
    label.textContent = `📁 ${data.name}`;
    row.appendChild(label);
    rootLi.appendChild(row);

    const childUl = document.createElement("ul");
    childUl.className = "dir-children";
    data.children.forEach((item) => {
      childUl.appendChild(createNode(item, 0));
    });
    rootLi.appendChild(childUl);
    treeEl.appendChild(rootLi);

    setSelected(data.current);
  }

  async function navigateTo(path) {
    const data = await fetchBrowse(path);
    expanded = new Set();
    if (data.current) expanded.add(data.current);
    await renderTree(data);
  }

  function openPicker(inputEl, title) {
    targetInput = inputEl;
    titleEl.textContent = title;
    selectedPath = inputEl.value.trim();
    modal.hidden = false;
    document.body.classList.add("modal-open");
    navigateTo(selectedPath || "").catch((err) => {
      currentLabel.textContent = err.message;
    });
  }

  function closePicker() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    targetInput = null;
  }

  function normalizeSelectedPath(p) {
    if (!p) return p;
    return p.replace(/\//g, "\\");
  }

  function confirm() {
    if (targetInput && selectedPath) {
      targetInput.value = normalizeSelectedPath(selectedPath);
      targetInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    closePicker();
  }

  modal.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", closePicker);
  });
  confirmBtn.addEventListener("click", confirm);

  document.addEventListener("keydown", (e) => {
    if (modal.hidden) return;
    if (e.key === "Escape") closePicker();
  });

  return { openPicker, closePicker };
})();
