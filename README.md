# AI 自适应错题本

AI 驱动的智能错题本系统，支持试卷拍照识别、错题自动归集、自适应难度推荐练习。包含前端原型（电脑端 + 手机端）和后端识别服务。

## 快速开始

### 1. 在线体验前端（无需安装）

直接访问部署好的页面：
- **电脑端**：https://lymeow233-wq.github.io/cuotiben/
- **手机端**：https://lymeow233-wq.github.io/cuotiben/m.html

可以直接使用内置的演示数据（初三数学）体验错题本、练习、学习报告等功能。

### 2. 本地运行后端（试卷识别）

```bash
# 克隆仓库
git clone https://github.com/lymeow233-wq/cuotiben
cd cuotiben/backend

# 安装依赖
pip install -r requirements.txt

# （可选）配置 OpenAI API 以启用 AI 识别
export OPENAI_API_KEY=sk-your-key

# 启动服务
bash run.sh
```

启动后访问 http://127.0.0.1:8000，上传 `samples/试卷照片.jpg` 测试完整识别流程。

---

## 在线访问

部署在 GitHub Pages，push 到 `main` 分支后约 1 分钟自动更新，**链接永久固定**。

| 端 | 地址 |
|---|---|
| 电脑 | https://lymeow233-wq.github.io/cuotiben/ |
| 手机 | https://lymeow233-wq.github.io/cuotiben/m.html |

仓库：https://github.com/lymeow233-wq/cuotiben

Pages 已启用「Deploy from a branch」模式，监听 `main` 分支根目录。

## 后端服务（试卷识别）

位于 `backend/` 目录，提供试卷拍照/文本导入识别功能。

### 快速启动

```bash
cd backend
pip install -r requirements.txt

# 可选：配置 OpenAI API（不配则使用规则兜底）
export OPENAI_API_KEY=sk-your-key
export OPENAI_BASE_URL=https://api.openai.com/v1  # 可选
export OPENAI_MODEL=gpt-4o-mini                   # 可选

bash run.sh
# 服务启动在 http://127.0.0.1:8000
```

访问 http://127.0.0.1:8000 即可打开测试页面，上传 `samples/试卷照片.jpg` 体验完整识别流程。

### 测试数据

- **样例试卷照片**：`backend/samples/试卷照片.jpg` - 真实初中数学试卷
- **识别流程**：上传照片 → AI/OCR 识别 → 逐题确认编辑 → 导入错题本 → 智能推荐练习题

### 功能

- **文本导入**：`POST /api/import/text` - 纯文本试卷识别
- **图片导入**：`POST /api/import/image` - 试卷拍照 OCR/VLM 识别  
- **智能抽题**：`POST /api/recommend` - 基于错题画像推荐练习题
- **元数据**：`GET /api/meta` - 获取 111 知识点 / 12 错误机制 / 3 难度档

详见 `backend/README.md`。

## 文件结构

```
cuotiben/
├── index.html           电脑端原型
├── m.html               手机端原型
├── knowledge-points.js  知识点配置（111个初中数学知识点）
├── backend/             后端识别服务
│   ├── server.py        FastAPI 服务入口
│   ├── app/
│   │   ├── paper_import_agent.py  试卷识别核心
│   │   ├── engine.py              自适应抽题引擎
│   │   └── config.py              知识点/错因/参数配置
│   ├── static/          接入版前端页面
│   ├── samples/         样例试卷照片（试卷照片.jpg）
│   └── requirements.txt
└── sync.sh              一键同步脚本
```

两个原型文件都是单文件：HTML + CSS + JS 全部内联，**无任何外部依赖、无构建步骤**，双击即可在浏览器打开。

## 功能范围

### 前端原型

- **年级 × 科目分区**：首页可切换年级（初一/初二/初三）与科目（数学/语文/英语/物理/化学）。错题、练习抽题、学习报告、练习日历、导出打印全部按当前组合隔离，互不串数据；选择记录在本地浏览器，刷新后保持。
- **练习日历**：按月展示每日练习量，有练习的日期带星标。
- **练习量直方图**：近 14 天每日练习题目数。
- **错题本**：按知识点归集，含掌握度标记与筛选。
- **练习**：基于薄弱知识点抽题，支持作答与结果反馈。
- **学习报告**：统计作答量、累计错题、连续练习天数。
- **导入 / 导出**：导入试卷错题，导出/打印当前范围的错题。

### 后端服务

- **多模态导入**：支持试卷文本粘贴、拍照识别（VLM/OCR 双路径）
- **智能标注**：自动提取题干、知识点（111 个初中数学知识点）、错误机制（12 种）、难度分级（3 档）
- **自适应引擎**：基于 Elo-like 算法动态调整难度，优先推送薄弱知识点
- **规则兜底**：无 API key 时自动降级到关键词匹配+规则打分，保证可用性

## 修改后上线

在本仓库目录下：

```bash
bash sync.sh "这次改了什么"
```

脚本会自动 `git add` → `commit` → `push`，随后 GitHub Pages 自动部署前端。不写说明时自动用时间戳。没有改动时提示无需同步。

**注意**：后端代码修改也会同步到仓库，但后端服务需要单独部署（默认本地 `127.0.0.1:8000`）。

## 协作约定（重要）

**主页在这里，不在团队空间。** 团队空间「wer帮你学习」里的同名原型已**停止维护、不再同步**，仅作为早期归档保留。

后续所有改动请直接改本仓库的 `index.html`（电脑端）和 `m.html`（手机端）。

### 开新会话时怎么用

新会话的 `/workspace` 是空的，需要先把仓库拉下来。把下面这段**整段复制**粘到新窗口即可：

```
原型主页已经迁到 GitHub，请以它为主进行操作，不要再动团队空间。

仓库：https://github.com/lymeow233-wq/cuotiben
分支：main
文件：index.html（电脑端）、m.html（手机端）
线上：https://lymeow233-wq.github.io/cuotiben/  （手机端加 /m.html）

请先执行：
  git clone https://github.com/lymeow233-wq/cuotiben /workspace
  cd /workspace

然后按我说的改对应文件。改完后：
  bash sync.sh "改动说明"

注意：
- 根目录即站点根，index.html 是电脑端主页，m.html 是手机端。
- 两个文件都是单文件原型，HTML/CSS/JS 内联，无外部依赖、无构建步骤。
- 团队空间那份已冻结，任何情况下都不要从它拉取覆盖本地。
```

## 数据说明

内置演示数据仅覆盖「数学 · 初三」一组，其余 14 种组合为空。切换空组合时会显示引导卡片，导入试卷后会自动归集到当前年级和科目下。

所有数据存放在浏览器 `localStorage`，不上传服务器、不做账号同步。

## 本地预览

```bash
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000/
```
