# MS-Mail-Nexus | 微软邮箱与验证码管理系统

## 项目概览
本项目是一个基于 Python FastAPI 后端和 React 前端的轻量级管理系统，主要用于自动化管理大量微软邮箱（Outlook/Hotmail）账号，提取验证码，并追踪第三方服务（如 Perplexity, Tavily）的月度使用情况。

## 核心架构
- **后端**: `backend/main.py` (FastAPI)
  - 使用 Microsoft Graph API 刷新 Token 并读取邮件。
  - 自动识别邮件正文或主题中的 4-7 位数字验证码。
  - 维护账号的使用状态（Perplexity/Tavily 的最后使用日期）。
- **前端**: `frontend/index.html` (React + Tailwind CSS)
  - 单文件前端，通过 CDN 加载 React 和 Babel。
  - 实时计算并显示账号的可用状态（基于 30 天周期）。
- **数据存储**: `accounts_v2.json`
  - 存储邮箱、密码、Client ID、Refresh Token 以及各服务的使用日期。

## 关键功能逻辑
### 1. 验证码提取
- 正则表达式: `\b\d{4,7}\b`
- 逻辑: 自动抓取收件箱最新一封邮件的主题和正文摘要。

### 2. 月度使用追踪 (30天周期)
- **支持服务**: Perplexity, Tavily。
- **计算逻辑**: `剩余天数 = 30 - (当前日期 - 标记日期)`。
- **UI 状态**:
  - `可用`: 未标记或距离上次标记超过 30 天。
  - `已占用`: 标记日期在 30 天内。
- **操作**: 点击状态按钮可切换状态（设置为“今天”或“清除”）。

### 3. 批量导入格式
`邮箱----密码----Client_ID----Refresh_Token`

## 开发记录
- **2026-03-15**: 增加 Tavily 使用情况统计功能。
  - 后端新增 `/mark_tavily_used` 接口。
  - 前端 UI 增加 Tavily (30d) 列，并重构了状态计算组件 `getStatusByDate`。
  - 系统版本升级至 V3.1。
