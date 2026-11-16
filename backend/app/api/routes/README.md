# 接口文件

已实现 `health.py`（`GET /api/v1/health`）与 `chat.py`：

- `GET /api/v1/model`：只返回模型名、配置齐全与否、超时秒数；不探测中转、不返回密钥。
- `POST /api/v1/chat`：接收交替排列的 user/assistant 消息，最后一条必须为 user；服务端添加 NBA 分析要求，调用中转站 Responses 接口。成功返回完整回答与 token 用量；错误返回脱敏提示，不自动重试。

`health.py` 另有 `GET /api/v1/health/storage`（PostgreSQL、Milvus、嵌入的就绪状态，存储断开时仍 200 + `degraded`）。

`memory.py`（数据库接入 P3，只读）：

- `GET /api/v1/memory`：返回 `{parents, children, log}`，字段与前端 `types/memory.ts` 一致；默认不返回已忘记条目；数据库不可用时 503。
- `GET /api/v1/memory/children/{id}`：单条子块，不存在时 404。

编辑、停用、忘记、恢复接口在 P6.4。后续按业务新增 `games.py`、`predictions.py`、`tasks.py`，并在上一层 `router.py` 注册。不使用返回假成功的占位接口。

接口层负责检查输入、调用 service、返回结果；外部服务请求、分析流程和数据库操作不要直接写在这里。
