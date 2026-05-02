# Smart-Notifier

基于 FastAPI + PostgreSQL + APScheduler + python-telegram-bot 的智能提醒系统。

## 功能
- Telegram 纯内联主菜单（新建提醒 / 我的任务 / 备份恢复）
- 单次提醒 + 周期提醒（Cron）
- 支持自然语言时间解析（如“明天下午3点”）
- Cron 严格合法性校验（非法输入会拦截）
- 提醒消息带 Done / Snooze 回调按钮
- `/cancel` 可中断创建流程
- 发送 `backup.json` 可按 `id` 执行 Upsert 恢复（新增/更新统计）
- 简易 Web 管理端（Vue3 + Element Plus CDN）
- Web 支持状态筛选、完成任务、删除任务
- Web 页面与 `/api` 路由均启用 HTTP Basic Auth 保护

## 小白操作流程（推荐）

1. Telegram 里先发 `/start`。
2. 点击 `📝 新建提醒`，先选任务类型。
3. 单次提醒可直接点快捷时间（10分钟后/30分钟后/1小时后），也可输入自然语言时间。
4. 按提示输入提醒内容和备注，即创建完成。
5. 收到提醒后，点 `✅ 已完成` 或 `⏳ 稍后提醒`。

常用机器人指令：
- `/start` 或 `/menu`：打开主菜单
- `/new`：直接新建提醒
- `/myid`：查看 Chat ID（Web 过滤会用到）
- `/ping`：检查机器人与数据库连接
- `/cancel`：取消当前流程
- `/help`：查看帮助

## 快速启动

1. 复制环境变量：

```bash
cp .env.example .env
```

2. 填写 `.env` 中的 `TELEGRAM_BOT_TOKEN`

3. 运行：

```bash
docker-compose up --build
```

4. 访问：
- Web: [http://localhost:8000](http://localhost:8000)
- Telegram: 给机器人发送 `/start` 或 `/menu`

## 1Panel 部署用环境变量（逐条）

在 1Panel 的容器编排里，为 `app` 容器逐条添加以下环境变量：

1. `APP_NAME`
- 示例：`Smart Notifier`
- 说明：应用显示名称，仅用于服务标识。

2. `APP_HOST`
- 示例：`0.0.0.0`
- 说明：FastAPI 监听地址，容器内建议固定为 `0.0.0.0`。

3. `APP_PORT`
- 示例：`8000`
- 说明：FastAPI 监听端口，需与容器映射端口一致。

4. `APP_DEBUG`
- 示例：`false`
- 说明：是否开启调试模式，生产环境建议 `false`。

5. `DATABASE_URL`
- 示例：`postgresql+asyncpg://postgres:postgres@db:5432/smart_notifier`
- 说明：PostgreSQL 异步连接串（必须 `postgresql+asyncpg`）。
- 格式：`postgresql+asyncpg://<user>:<password>@<host>:<port>/<dbname>`

6. `TELEGRAM_BOT_TOKEN`
- 示例：`1234567890:AA...your_bot_token`
- 说明：BotFather 分配给 Telegram 机器人的 Token。

7. `TELEGRAM_POLL_INTERVAL`
- 示例：`1.0`
- 说明：Telegram 轮询间隔（秒），一般 `1.0` 即可。

8. `SCHEDULER_TIMEZONE`
- 示例：`Asia/Shanghai`
- 说明：APScheduler 时区，影响定时触发基准。

9. `WEB_USERNAME`
- 示例：`admin`
- 说明：Web 页面和 `/api` 的 HTTP Basic Auth 用户名。

10. `WEB_PASSWORD`
- 示例：`change_me_please`
- 说明：Web 页面和 `/api` 的 HTTP Basic Auth 密码，部署时必须改强密码。

## 1Panel 部署建议

1. 数据库容器建议单独部署 PostgreSQL，并创建数据库 `smart_notifier`。
2. `DATABASE_URL` 中主机名请填 1Panel 内可达地址（如同网络容器名）。
3. `app` 容器对外暴露 `8000` 端口。
4. 首次启动会自动执行 `alembic upgrade head` 初始化表结构。

## 提醒不触发排查

1. 容器环境变量 `SCHEDULER_TIMEZONE` 与你的使用时区一致（如 `Asia/Shanghai`）。
2. 新建单次任务时使用未来时间。
3. 在机器人输入 `/ping`，确认机器人与数据库联通正常。
4. 在 Web 点“刷新任务”查看任务是否为 `pending`。

## 本地导出镜像（给 1Panel 导入）

```bash
cd /Users/cwzs/Desktop/Smart-Notifier
docker build -t smart-notifier:phase3 .
docker save -o /Users/cwzs/Desktop/smart-notifier-phase3.tar smart-notifier:phase3
```

然后在 1Panel 中导入 `/Users/cwzs/Desktop/smart-notifier-phase3.tar`。

## 主要目录

- `app/main.py`: FastAPI 入口与生命周期管理
- `app/bot/handlers.py`: Telegram 内联交互与状态机
- `app/services/scheduler_service.py`: 调度与推送
- `app/models/task.py`: Task 模型
- `alembic/`: 数据迁移
