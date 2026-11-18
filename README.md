# 单队列竞争风险分析 API（30 天再入院累计发生概率）

面向公共卫生队列数据员：评估出院患者 **30 天内再住院的累计发生概率（CIF）**，
**死亡作为竞争结局**（不当作普通删失）。技术栈：FastAPI + SQLite + 后台串行重算，
提供 Docker Compose 一键启动与 Swagger 操作入口。

## 1. 统计方法

逐人记录结局发生日（1~30）与结局（再入院 / 死亡 / 随访截止），按 **Aalen-Johansen
乘积极限估计**计算竞争风险 CIF：

- 日初在险人数 `Y_t`：第 t 天开始时尚未发生任何事件、未删失的人数；
  **同日再入院与死亡共用同一个日初在险人数**。
- 原因别危险：`h1_t = d1_t / Y_t`（再入院）、`h2_t = d2_t / Y_t`（死亡）。
- 当日生存因子：`S_t = S_{t-1} × (1 − (d1_t + d2_t) / Y_t)`。
- CIF 增量：`ΔCIF_k(t) = S_{t-1} × h_k(t)`，逐日累加。
- **随访截止（CENSORED）者在当日事件计数之后才退出风险集**，不进入当日危险分母；
  **死亡进入事件计数并压低生存因子**，绝不按普通删失处理。
- 恒等式：`CIF_再入院 + CIF_死亡 + S(30) = 1`（测试中逐日校验）。

## 2. CSV 格式

UTF-8 编码、必含三列（表头大小写不敏感、列序不限）：

| 列名 | 说明 |
| --- | --- |
| `subject_id` | 每人唯一编号，非空、不可重复 |
| `day` | 结局发生日，1~30 整数 |
| `outcome` | `READMISSION` / `DEATH` / `CENSORED`，兼容中文 `再入院` / `死亡` / `随访截止` 及常见别名 |

示例见 [`samples/`](samples/)。

## 3. 修订与一致性规则

- 修订版本号为**递增整数**，每次上传为**整体更换**；下一可受理版本 = 当前最大版号 + 1。
- **同版同内容**（按原始字节 SHA-256 判定）重送：幂等，不产生任何改动。
- **同版异内容**或跳号：HTTP 409 拒收。
- 新修订在单个 `BEGIN IMMEDIATE` 事务内受理：旧 CURRENT 立即置 `SUPERSEDED`，
  其未完成分析立即置 `SUPERSEDED`；新修订写入 `PENDING`。事务提交后任何查询
  都不可能再把旧结论当作现行结论返回。
- 重算由**单后台守护线程串行**执行；落库前再次校验该修订仍为 CURRENT，
  计算期间被取代则结果丢弃、状态转 `SUPERSEDED`，**绝不发布旧修订结论**。
- 重算失败落 `FAILED`、服务重启后遗留的 `PENDING` 自动恢复重算；两种状态下
  `/api/analyses/current` 均返回 409 状态提示而非旧结论，可用
  `POST /api/analyses/recompute` 手动重试。
- 显式查询某历史版本 `/api/analyses/{revision}`：若该版本已完成则返回结果并带
  `is_current=false`（审计留痕）；若其在计算中被取代（从未发布）则返回 409。

## 4. API 一览（Swagger：`/docs`，OpenAPI：`/openapi.json`）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/api/revisions/upload` | 表单字段 `revision` + 文件 `file`，上传完整 CSV |
| GET | `/api/revisions` | 修订清单与分析状态 |
| GET | `/api/analyses/current` | 现行修订逐日结论（在险人数/两类事件数/两类 CIF/生存概率） |
| GET | `/api/analyses/{revision}` | 指定修订结论（历史版本标注 `is_current=false`） |
| POST | `/api/analyses/recompute` | 手动重算现行修订（可带 `?revision=`） |

逐日响应字段：`day, at_risk, readmission_count, death_count, censored_count,
readmission_cif, death_cif, survival`（覆盖 1~30 全天，无事件日 CIF 不变）。

## 5. 启动方式

### Docker Compose（推荐）

```bash
docker compose up --build -d
# Swagger: http://localhost:8000/docs
```

SQLite 数据库与暂存目录保存在命名卷 `cohort-data`（容器内 `/data`），
重启不丢数据；自带 healthcheck。

### 本地直接运行

```bash
python3 -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

可用环境变量（见 `.env.example`）：`COHORT_DB_PATH`、`COHORT_UPLOAD_DIR`、
`COHORT_HORIZON_DAYS`（默认 30）、`COHORT_MAX_UPLOAD_BYTES`（默认 50 MB）。

### curl 示例

```bash
curl -X POST http://localhost:8000/api/revisions/upload \
  -F revision=1 -F "file=@samples/cohort_rev1.csv"
curl http://localhost:8000/api/analyses/current
```

## 6. 自测

```bash
python3 -m pip install pytest httpx
python3 -m pytest tests/ -v
```

15 个用例覆盖：Aalen-Johansen 手算对照、同日事件共用风险集、删失当日事件后退出、
死亡不被当作删失、上传幂等、同版异内容拒收、版号递增、CSV 校验、
**修订交错（计算中被取代绝不发布）**、**重启恢复与 FAILED 重试不返回旧结论**。

## 7. 目录结构

```
app/            FastAPI 应用
  main.py         路由与统一错误处理（lifespan 启动恢复）
  analysis.py     Aalen-Johansen 竞争风险 CIF
  csv_parser.py   CSV 解析/校验/SHA-256
  database.py     SQLite 模式、IMMEDIATE 事务、版本受理与落库
  services.py     后台串行重算线程与启动恢复
  schemas.py      Pydantic 模型
  config.py       环境变量配置
samples/        示例 CSV（含中文结局版本）
tests/          pytest 测试
Dockerfile / docker-compose.yml / .env.example
```

## 8. 说明与限制

- 单进程部署：串行化依赖进程内写锁 + SQLite 事务；多副本水平扩展需换用
  外部队列/选主（当前需求为单队列 API，未实现）。
- 时间尺度固定为 1~30 天的离散日尺度；每人一条终局记录。
- 原始 CSV 解析后即删除暂存文件，仅持久化规范化记录与分析结果。
