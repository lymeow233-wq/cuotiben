# 试卷导入 Agent · 接入版

把资料库「wer帮你学习」里的 `paper_import_agent.py` 接到真实页面上：**拍照 / 粘贴文本 → 真实识别 → 逐题确认 → 入库 → 画像 → 抽题**，全链路有网络请求，不再读内置 `IMP` 常量。

对应看板 **P0-2「试卷识别完全没接」**。

---

## 1. 目录

```
paper_import_agent/
├── server.py                    # FastAPI 服务：包住 agent，暴露 4 个接口
├── app/
│   ├── paper_import_agent.py    # 空间原件，一行未改
│   ├── engine.py                # 空间原件（抽题引擎），一行未改
│   └── config.py                # 空间原件（111 知识点 / 12 错因 / 先验参数）
├── static/
│   ├── index.html               # 接入版页面（4 步：来源 → 识别 → 确认 → 结果）
│   └── sample.txt               # 样例卷，页面里「载入样例卷」用
├── samples/
│   ├── 九年级一元二次方程期中复习卷.txt
│   └── 真实试卷照片1.jpg
├── screenshots/                 # 实测截图
├── run.sh
└── README.md
```

前端不内置任何题目；`grep -c "fetch(" static/index.html` 为 3（上传、抽题、健康检查），全部打到本机 `/api/*`。

## 2. 跑起来

```bash
cd paper_import_agent
export OPENAI_API_KEY=sk-...            # 可选，不配就走规则兜底
export OPENAI_BASE_URL=https://.../v1   # 可选，中转 / 自建 vLLM / Ollama
export OPENAI_MODEL=gpt-4o-mini         # 可选
bash run.sh                             # → http://127.0.0.1:8000
```

依赖：`fastapi`、`uvicorn`、`python-multipart`、`requests`、`pillow`、`numpy`；照片 OCR 兜底另需 `tesseract` + `chi_sim` 语言包。

## 3. 接口

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/api/health` | — | 是否配了模型、模型名、OCR 语言包、题库条数、各项上限 |
| GET | `/api/meta` | — | 111 知识点 / 12 错误机制 / 3 难度档 |
| POST | `/api/import/text` | `{text, exam_date, results?}` | `{mode, attempts[], dropped[], stats}` |
| POST | `/api/import/image` | `multipart: file, exam_date` | 同上，`mode` 为 `vlm` 或 `ocr` |
| POST | `/api/recommend` | `{attempts, originals?, n, seed?}` | `{recs[], profile[], prior}` |

`mode` 是诚实标记：`llm` / `vlm` 表示模型真的返回了标注，`rule` / `ocr` 表示走了兜底。页面顶部徽标同步显示，不把兜底伪装成模型结果。

`attempts[]` 字段与 `engine.recommend()` 完全对齐：`qid / kp / mech / tier / tier_difficulty / text / days_ago / correct / needs_confirm`，另附 `kp_name / em_name` 供前端展示。

## 4. 服务层做了什么（agent 本体零改动）

| 处理 | 原因 |
|---|---|
| 砍掉第一题之前的卷头（标题 / 来源 / 考试说明） | 原 `parse_paper()` 会把卷头当成第 0 题；实测样卷多出 1 条废题 |
| 单份最多 60 题、文本 2 万字、图片 10MB | 防止一次请求把额度烧光 / 撑爆内存 |
| 模型调用失败 → 显式回退并在 `note` 里说明 | 原代码 `except: None` 静默降级，现场看不出是没额度还是模型笨 |
| 图片超过 10MB / 空文件 / 解析 0 题 → 明确报错文案 | 避免「导入成功但 0 题」的黑箱 |

超时沿用 agent 原值：文本每题 30s，视觉整页 120s。

## 5. 实测结论（2026-10-31）

| 项 | 结果 |
|---|---|
| 文本导入（22 题，规则兜底） | ✅ 22 条 attempts，7 条白名单外丢弃，耗时 < 1s |
| 文本导入（接模型，mock 端点） | ✅ `mode=llm`，29 题全部被打标，逐题请求成功 |
| 照片导入（接视觉模型，mock 端点） | ✅ `mode=vlm`，整页一次请求返回 3 题 |
| 照片导入（仅 OCR，真实 A4 多栏卷） | ❌ 0 题：多栏 + 密排，OCR 打不出「题号+题干」结构，已返回明确提示 |
| 抽题 | ✅ 5 题含原题池配额，理由带「错误率 / 间隔天数 / 复发率」 |
| 额度耗尽 | ⚠️ 未实测，代码路径为 `except → None → 规则兜底`，`mode` 会变 `rule` |

> 视觉模型那条用的是本地假模型端点（只验证**接线**：请求格式、白名单校验、结构转换、模式标记）。真模型效果需配 key 后重跑，不能拿现在的结果当准确率。

## 6. 已知缺口

1. **答案没采集**：agent 只产出 `text/kp/mech/tier/correct`，不产出「你的答案 / 正确答案」。确认卡里留了输入框但默认空，没编造。
2. **OCR 兜底对真实照片基本不可用**：这张 A4 双栏卷实测 0 题。主路径必须是视觉模型。
3. **没有持久化**：入库只存在页面内存 + 传给引擎，刷新即失。要落库需接资料库数据库或本地 SQLite。
4. **只在本机**：`127.0.0.1:8000`。要挂到资料库页面（https）需公网部署，浏览器不允许 https 页面调 http 本地端口。

## 7. 下一步（按优先级）

1. 配 key 跑一遍真模型，把「识别准确率」这个 P2 数字补上
2. 5 张带日期样卷（P0-3）—— 现在只有 1 张真实照片 + 1 份文本卷
3. 持久化（P0-4）：attempts 落 SQLite 或资料库表，刷新不丢
4. 部署到公网后，把资料库「AI自适应错题本_交付包」页面的 `doImport()` 指过来
