# 部署计划：ai-journey-copilot 公网分享

## Context

用户希望把 ai-journey-copilot 从只能本地 localhost 访问，变成可以通过公网链接分享给其他人访问。采用 **Vercel 前端 + Render 后端** 的双服务部署方案，使用平台自带域名。

## 架构概览

```
用户浏览器 → Vercel (前端静态页面)
                 ↓ WebSocket / HTTPS API
            Render (FastAPI 后端)
                 ↓
            Claude API / 高德地图 / 火山语音 / Tavily
```

## 需要修改的代码

### 1. 后端：生产就绪改造

**文件**: `backend/app/main.py`
- `__main__` 里的 `reload=True` 改为仅在 debug 模式开启（生产环境不需要热重载）
- 添加环境变量控制是否注册测试路由

**文件**: `backend/app/api/__init__.py`
- 添加 `ENABLE_TEST_ROUTERS` 环境变量开关，生产环境不注册 test routers（`claude_test`, `amap_test`, `voice_test`, `tavily_test`, `skill_test`）

**文件**: `backend/requirements.txt`
- 添加 `gunicorn` 作为生产 WSGI server（Render 推荐 gunicorn + uvicorn workers）

### 2. 前端：Vercel 部署配置

**文件**: `frontend/vercel.json` (新建)
- Vercel 配置，指定构建命令、输出目录、SPA rewrite

**文件**: `frontend/package.json`
- 添加 `engines` 字段指定 Node 版本（Vercel 默认可能是 20.x，与项目一致即可）

### 3. 部署配置文件（可选）

**文件**: `render.yaml` (项目根目录新建，可选)
- Render Blueprint 配置，一键定义后端服务
- 纯手动部署可跳过

## 部署步骤（执行顺序）

### Phase 1: 代码改造
1. 后端：生产启动模式 + test router 开关 + gunicorn
2. 前端：vercel.json 配置
3. 提交并推送到 GitHub

### Phase 2: 部署后端到 Render
1. 在 render.com 注册/登录
2. New → Web Service → 连接 GitHub 仓库
3. 配置：
   - **Name**: ai-journey-copilot-backend
   - **Region**: Singapore（离国内近，延迟低）
   - **Branch**: main
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT`
   - **Instance Type**: Free（先用免费的，不够再升）
4. 添加环境变量（Advanced → Environment Variables）：
   - `ANTHROPIC_API_KEY` = 你的 Claude key
   - `AMAP_KEY` = 你的高德 Web 服务 key
   - `VOLCENGINE_API_KEY` = 火山引擎 key
   - `VOLCENGINE_ASR_API_KEY` = ASR key（或复用上面的）
   - `VOLCENGINE_TTS_API_KEY` = TTS key（或复用上面的）
   - `TAVILY_API_KEY` = Tavily key
   - `CORS_ORIGINS` = 先填 `*`（等 Vercel 域名确定后再收紧）
   - `JOURNEY_DEMO_SIMULATION` = `1`
   - `LOG_LEVEL` = `info`
5. 点击 Create Web Service，等待部署完成
6. 拿到 `xxx.onrender.com` 域名，验证健康检查：`https://xxx.onrender.com/api/health`

### Phase 3: 部署前端到 Vercel
1. 在 vercel.com 注册/登录
2. Add New → Project → 导入 GitHub 仓库
3. 配置：
   - **Framework Preset**: Vite
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
4. 添加 Environment Variables：
   - `VITE_API_BASE_URL` = `https://xxx.onrender.com`（Phase 2 拿到的 Render 域名）
   - `VITE_WS_BASE_URL` = `wss://xxx.onrender.com`
   - `VITE_AMAP_JS_KEY` = 你的高德 JS key
5. 点击 Deploy，等待构建完成
6. 拿到 `xxx.vercel.app` 域名

### Phase 4: 收尾 & 验证
1. **收紧 CORS**：回到 Render 后台，把 `CORS_ORIGINS` 从 `*` 改成 `https://xxx.vercel.app`，重新部署
2. **高德域名白名单**：在高德控制台 → 我的应用 → JS API key 的安全设置里，添加 Vercel 域名到白名单（如果之前只配置了 localhost）
3. **完整测试**：
   - 打开 `https://xxx.vercel.app`，页面正常加载
   - 高德地图正常显示（无 key 报错弹窗）
   - 输入"去机场"，能看到思考过程、推荐方案
   - WebSocket 连接正常（DevTools → Network → WS，状态 101）
   - 语音功能测试（如需要）

## 关键文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/main.py` | 修改 | 生产启动模式，移除 reload=True |
| `backend/app/api/__init__.py` | 修改 | test router 环境开关 |
| `backend/requirements.txt` | 修改 | 添加 gunicorn |
| `frontend/vercel.json` | 新建 | Vercel 部署配置（SPA rewrite） |
| `frontend/package.json` | 修改 | engines 字段 |
| `README.md` | 修改 | 添加部署说明 |

## 注意事项

- **Render 免费套餐有冷启动**: 第一次访问需要 30-60 秒等待后端唤醒，后续正常。升级到 Starter（$7/月）可消除冷启动。
- **WebSocket 必须 wss://**: Vercel 页面是 HTTPS，浏览器会阻止混合内容，所以后端必须支持 HTTPS/WSS（Render 自带 SSL，没问题）
- **高德 JS key 安全**: 如果 key 开启了域名白名单，必须把 Vercel 域名加入白名单，否则地图不显示
- **API 费用**: 公开分享后其他人会消耗你的 Claude / 高德 / 火山 / Tavily 额度，注意监控用量
- **Demo 模式**: 当前 `JOURNEY_DEMO_SIMULATION=1` 开启，公开部署时可考虑保持（方便演示）或关闭
- **免费额度参考**: Render 免费版 750 小时/月，512MB RAM；Vercel 免费版 100GB 带宽/月，都够初期演示用
