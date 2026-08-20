# SmartHire-Agent

AI 驱动的端到端招聘自动化系统。基于 LangGraph 多智能体架构，将简历筛选、AI 电话预筛选、面试安排、入职通知串联为一条自动化流水线，同时在关键决策点设置人工审核门控（HITL），确保 HR 掌握最终决策权。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 智能简历解析 | PDF/DOCX 简历自动提取结构化信息 |
| 多维度评估 | 100 分制 5 维度评分（硬性要求/技能/经验/教育/加分项） |
| AI 电话预筛选 | Twilio 外呼 + MiMo 语音（可选），或 LLM 模拟数据 |
| 面试安排 | 飞书日历自动创建事件（可选） |
| 入职通知 | QQ 邮箱自动发送（可选） |
| HITL 门控 | 3 个人工审核节点，HR 掌握最终决策权 |

---

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM | DeepSeek / Qwen / GPT（OpenAI 兼容接口） |
| 工作流 | LangGraph StateGraph |
| 后端 | FastAPI + SQLite |
| 前端 | 纯 HTML/CSS/JS |
| 语音 | 小米 MiMo ASR/TTS（可选） |
| 通话 | Twilio（可选） |
| 日历 | 飞书开放平台（可选） |
| 邮件 | QQ 邮箱 SMTP（可选） |

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/LittleOooou-Ok/SmartHire-Agent.git
cd SmartHire-Agent
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填入 LLM API Key：

```env
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动

```bash
python main.py
```

访问 http://localhost:8000

---

## 工作流程

```
简历 → 简历解析 → 候选人评估 → Top N → 人工审核
                                         ↓
                               AI电话预筛选（模拟/Twilio）
                                         ↓
                                    人工审核
                                         ↓
                                   面试安排（飞书/模拟）
                                         ↓
                                    入职选择
                                         ↓
                                    流程完成
```

---

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | ✅ | LLM API Key |
| `LLM_BASE_URL` | ❌ | API 地址（默认 DeepSeek） |
| `LLM_MODEL` | ❌ | 模型名称 |
| `FEISHU_APP_ID` | ❌ | 飞书日历 |
| `FEISHU_APP_SECRET` | ❌ | 飞书日历 |
| `FEISHU_USER_ID` | ❌ | 飞书日历 |
| `SMTP_SENDER_EMAIL` | ❌ | QQ 邮箱 |
| `SMTP_AUTH_CODE` | ❌ | QQ 邮箱授权码 |
| `HR_EMAIL` | ❌ | HR 邮箱 |
| `TWILIO_ACCOUNT_SID` | ❌ | Twilio 通话 |
| `TWILIO_AUTH_TOKEN` | ❌ | Twilio 通话 |

---

## Docker 部署

```bash
docker-compose up -d
```

---

## 项目结构

```
SmartHire-Agent/
├── agents/                  # AI Agent 实现
├── api/                     # FastAPI 路由
├── config/                  # 配置管理
├── core/                    # 日志、异常、可观测性
├── db/                      # SQLite 数据层
├── frontend/                # 前端页面
├── graph/                   # LangGraph 工作流
├── hitl/                    # HITL 门控
├── models/                  # 数据模型
├── tools/                   # LLM、邮件、日历工具
├── voice/                   # Twilio 通话模块
├── main.py                  # 入口
├── requirements.txt         # Python 依赖
├── Dockerfile               # Docker 配置
└── docker-compose.yml       # Docker Compose
```

---

## License

MIT
