# 培养方案问答与选课规划

一个面向高校培养方案的本地演示项目：**LangChain** 负责文档切分、向量检索与生成，**LangGraph** 负责意图路由（问答 / 选课规划分流），**确定性 Python 代码**负责学分缺口与选课计算（LLM 不改数值），**LangSmith** 用于全链路追踪。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # 填入 DeepSeek / LangSmith 密钥
streamlit run main.py
```

## 使用流程（四阶段向导）

1. **步骤 A · 培养方案解析**：上传培养方案 PDF，自动识别学院 / 专业 / 课程表（结果缓存在 `data/cache/`）。
2. **步骤 B · 学生信息**：选择学院、专业，录入入学年份，系统推算当前学期。
3. **步骤 C · 过往学分**：手动填写各平台学分，或上传成绩单（PDF / Excel / CSV / TXT）自动解析，含不及格课程（补考 / 重修）处理与缺失学期提示。
4. **步骤 D · 问答 / 规划**：输入问题，LangGraph 自动路由——选课规划走确定性计算，培养方案问答走 RAG。

## 目录结构

```
class/
├── main.py                     # Streamlit 入口
├── app/
│   ├── main.py                 # 页面框架 + 四阶段导航
│   ├── state.py                # 会话状态
│   ├── core/
│   │   ├── extract_courses.py  # 培养方案课程表结构化抽取
│   │   ├── rag.py              # Chroma 索引 + RAG 问答
│   │   ├── graph.py            # LangGraph 状态图（意图路由）
│   │   ├── calc.py             # 学分缺口 / 选课规划（确定性）
│   │   ├── transcript.py       # 成绩单解析
│   │   └── prerequisite.py     # 先修建议（非强制）
│   └── views/                  # 四个阶段视图
├── data/
│   ├── raw/                    # 培养方案 PDF
│   ├── demo_courses.json       # 演示用结构化课程表
│   └── prerequisites/          # 先修关系 YAML
└── tests/
```

## 设计边界

- 学分、补考和重修是否计入学分由 `app/core/calc.py` 确定性计算，模型不能修改数值。
- 先修关系只是建议（`app/core/prerequisite.py`），不会阻断选课；可在 `data/prerequisites/<专业>.yaml` 中人工维护。
- 未配置模型或索引时，问答会明确提示，不会编造答案；规划功能不依赖模型。

## 质量检查

```powershell
pytest tests/
ruff check app/
```
