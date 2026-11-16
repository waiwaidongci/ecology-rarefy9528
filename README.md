# 松际 · 生态样线研究档案

在同一调查期、共同个体采样量下比较稀释期望，把观察差异与田野边界一起交付。

松际公园生态研究组有13位调查分析人员。现有流程按样线总物种数做排行，再手工计算多样性；三个痛点是个体采样量不同不可直接比丰富度、稀有种被总体读数盖住、区间缺少采样假设。这里承接“匿名样线物种计数→共同采样努力校正/多样性描述→观察建议→负责人复核→CSV”。统计结果不自动决定保护措施，也不承诺因果保护效果。

## 快速启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [研究档案](http://localhost:18218)，[API文档](http://localhost:19218/docs)，数据库映射17718。首次启动自动seed，失败只记录日志、API继续；脚本可独立重复。初始3个调查期、18条物理样线在各期重复记录，共54条样线期次、24个物种×54=1296个计数行（包含零），秋季SJ-18为真实全零空样线；固定种子149。

| 账号 | 密码 | 权限 |
|---|---|---|
| admin@songji.demo | Songji2026! | admin生态研究负责人：阅读、录入、运行、建立建议、审核建议；仅获批建议可导出 |
| analyst@songji.demo | Songji2026! | analyst样线研究员：阅读、录入、运行、建立建议；审核403；仅获批建议可导出 |
| viewer@songji.demo | Songji2026! | viewer观察记录员：只读与获批CSV；写入403 |

## 一天的研究剧本

1. 06:00 林荞以analyst登录，打开秋季样线调查（2026-10-14），源文件 `sj-transects-2026-3.csv`。
2. 核对SJ-01原N588、17种，并读取24种逐项计数；秋季SJ-18全零保留。
3. 选择同一调查期18条样线，共同m=100；非空样线最小N169，17条有效、1条空线单列。
4. 设置bootstrap200次、seed20261114，把研究送入独立Celery队列。
5. worker按不放回超几何期望计算共同m及共同范围曲线；按原始N组成计算Shannon/Simpson。
6. 阅读SJ-01 E[S100]约12.433，条件区间约11.36–12.90；原N Shannon约1.926、Simpson约.769只做描述，不直接跨努力量排优。
7. 查看低计数物种与漏检边界，建立“林缘稀有种复查”建议，说明下一期保持协议。
8. 顾芩以admin复核观察边界；季苔以viewer下载获批CSV。

## 实体与页面

五个核心实体Survey、Transect、SpeciesCount、DiversityRun、ConservationNote贯穿表/model/schema/service/route/API/store/UI。六页为研究首页、调查与样线、共同量配置、多样性结果、观察建议、田野留痕。RarefactionCurve、DiversitySummary、RunTable跨页复用，同时有共享Table/Badge/Notice/Empty。

调查期内样线reference唯一，样线内species唯一并有数据库非负CHECK；API对重复物种行明确422，要求上游先汇总，不静默覆盖。S01—S24为允许物种；未知编码、负数、非整数计数拒绝，未提供物种按零计算。单种每行上限100000、样线总N上限2400000；研究最多选择同一期30条样线。支持新调查期和JSON计数录入，不提供CSV解析器。提交研究需客户端幂等键`request_key`（8–64位字母数字/下划线/连字符），重复提交不新建研究。

## 算法口径

`services/rarefaction.py`：个体型稀释不放回，E[S_m]=Σ_i(1−C(N−n_i,m)/C(N,m))，只对n_i>0求和。m为0..N的整数；m=0返回0、m=1返回1、m=N返回观测物种数。一般情形用gammaln算log组合数，再用−expm1(log比值)避免稀有种接近零时消减误差；N−n_i<m时该物种必然被抽中，贡献1。

共同m只用于同一调查期的所选非空样线；每条需N≥m，任一不足则整个研究failed，不自动删除不合资格的非空线。空线N=0单独标记，E/H/D与区间不定义，不被当作“低多样性”；全空研究失败。曲线在0到非空最小N的共同区间取最多41个等距整数节点并加入精确m，连线仅用于展示；每个整数m的核心计算是上式，不是拟合曲线。

`services/diversity.py`：p_i=n_i/N，Shannon H=−Σp_i ln(p_i)，使用自然对数；Simpson采用Gini–Simpson D=1−Σp_i²。两者均是**原始N下的plug-in描述指数**，不是共同m下的指数。结果页按样线编号排列，不按原始S、H、D排优。主横向对比字段是共同m的E[S_m]。

`services/bootstrap.py`：固定seed和调查期/样线键派生确定性子种子，重复B次 Multinomial(N,观测p)；每次用原N重采样组成计算H/D，并在该组成上再算同一m的稀释期望，分别报告2.5%/97.5%分位区间。默认B200，允许50–1000。它是**条件于已观测组成的重采样区间**，不能覆盖未发现物种、空间相关、物种漏检或完整田野抽样不确定性；未见物种概率仍为零，区间不是生态总体置信保证。原N指数区间与共同m期望区间分别标注，稀释期望曲线也不是bootstrap轨迹。

输入指纹按样线reference稳定排序，包含调查期id/name/date/filename、样线id/期次/reference/name/habitat、全部24种计数（缺列规范为零）、共同m/重复数/seed/选择样线集合、算法版本；选择ID顺序和输入行序变化不改指纹，计数/元数据/seed改变会改。结果保存全部参数、原N/S、资格、期望曲线、三组条件区间、低计数1–2个体物种和指纹，worker写共享JSON并落库，可复算。

失败态包括未知物种、负/非整数/非有限计数、重复物种、跨调查期、重复样线、共同m超非空最小N、所有样线为空、非法bootstrap和seed。观察差异不证明保护动作有因果效果，田野协议与漏检需要另行复核。

## 发布单元：尝试、归档与恢复

一次研究是一个稳定的发布槽，每次执行是`run_attempts`里的一条尝试（编号递增）。**结果、完成状态、发布尝试和导出依据（归档路径、SHA-256、字节数、发布时间）在同一事务提交**；归档先写临时文件、fsync、再原子改名，然后才提交。若提交前中断，文件不成为任何记录的引用，只是无主文件。

- **仅当前有效尝试可发布**：guarded UPDATE要求`current_attempt_id`匹配、状态`running`、租约未过期，行数不为1即拒绝。旧尝试的迟到结果被标`superseded`（页面显示"迟到·未采用"），其刚写的归档当场删除，库内结果与归档SHA-256保持不变，另记`diversity.late_discarded`留痕。
- **中断可接管**：worker持租约心跳（`RUN_LEASE_SECONDS`，默认120秒）。worker失联后租约过期，`POST /runs/recover`把运行标为`interrupted`、尝试结束；`POST /runs/{id}/attempts`由负责人接管，新增尝试并用`recovered_from_attempt_id`串起链路，旧尝试不会复活。失败研究用`POST /runs/{id}/retry`重试。已发布研究不可接管（409）。
- **重复任务不增加归档**：worker只认`(run_id, attempt_id)`，重复投递拿不到`queued→running`的原子声明即返回`duplicate`，不再计算也不再写文件。提交侧`request_key`唯一：同键同参返回既有研究（HTTP 200），不新建研究/尝试/归档；同键不同参数返回409。
- **导出以发布为依据**：CSV仅对已发布研究导出，导出前重新核对归档（存在性、SHA-256、字节数、指纹、发布尝试状态），任何不一致返回409而不发数字；导出响应头携带`X-Publication-Attempt`与`X-Archive-SHA256`。`GET /runs/{id}/verify`返回同一份核对报告。
- **清理不误删被引用文件**：`POST /runs/maintenance/sweep?dry_run=true`扫描共享卷，只删除未被任何`artifact_path`引用且早于`ARTIFACT_GRACE_SECONDS`（默认86400秒）的文件；引用判定来自数据库而非文件名或目录顺序，宽限期覆盖"改名完成、事务未提交"的短暂窗口。归档文件名含尝试号（`diversity-{run}-a{n}.json`），迟到尝试物理上也无法覆盖新尝试的文件。

研究页"发布单元 / 尝试与恢复"展示发布尝试、归档名/SHA-256/字节数与发布时间、每次尝试的执行者与起止时间/说明、中断与接管记录，并提供接管、重试、扫描失联、核对归档入口；研究索引显示尝试数与发布尝试。迁移见`migrations/002_publication_unit.sql`，启动时对旧库做窄范围幂等补列。后端口径测试`backend/tests/`（14项）与场景脚本在批次根`test-artifacts/gb-0014`；场景覆盖归档后中断、接管后迟到写回、重复投递与提交、清理并发。

## 工程与开发

| 层 | 技术 |
|---|---|
| 前端 | React18.3 / TypeScript5.7 / Vite6.4.6 / Nginx1.27 |
| API | Python3.11 / FastAPI / Pydantic / SQLAlchemy2 |
| 计算 | NumPy2.2.1 / SciPy1.15 |
| 数据和队列 | PostgreSQL16 / Redis7 / Celery5.4 + Kombu5.4.2 |
| 认证 | PBKDF2-SHA256 210000轮 / JWT HS256 8小时 / 后端角色鉴权 |

恰好frontend/backend/worker/db/redis五服务，全部健康探针/健康依赖；Nginx使用127.0.0.1探针，worker使用定向Celery ping。db-data/redis-data/artifacts命名卷分别保存数据库、队列与共享计算JSON（发布归档与未引用文件同卷，清理按数据库引用判定）。Nginx剥离/api/前缀，API不重复前缀。Kombu锁定5.4.2以兼容Celery5.4 Redis依赖。

环境变量见.env.example：COMPOSE_PROJECT_NAME/FRONTEND_PORT/BACKEND_PORT/DB_PORT隔离项目与端口；POSTGRES_DB/USER/PASSWORD配置库；JWT_SECRET签名；SEED_DEMO控制自动数据。演示口令只用于本机，部署时替换环境变量。

```sh
# 先配置本机可达 DATABASE_URL / REDIS_URL / JWT_SECRET
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 19218
# 另一个终端，同环境变量
celery -A src.worker.app worker --concurrency=1
# 前端代理已指向127.0.0.1:19218
cd frontend
npm ci
npm run dev
```

目录：backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}；backend/scripts/seed_demo.py；backend/migrations/；frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。metadata首次建表，migrations记录结构，未配置生产增量迁移。seed按自然键插入，已有数据/用户修改不覆盖，基准运行仅缺失时计算。请求ID/全局错误两个中间件；AuditLog记录登录、录入、研究、建议复核；CSV对公式前缀转义。

共享枚举位置：backend/src/constants/enums.py的Role/RunStatus/NoteStatus/TransectStatus；frontend/src/constants/enums.ts对应角色/运行/建议/资格标签；使用于鉴权、服务/schema与Badge/Layout/页面。75个职责源文件，不计__init__及配置。全部测试脚本、真实截图、CSV、日志和报告仅位于批次根test-artifacts/gb-0014；TESTING.md列真实结果与已知差异。

## 范围与License

合成数据不是松际公园真实生态结论；没有外部种群验证、自动保护决策、生产压测或完整田野不确定性估计。Google字体需联网，离线浏览器回退。收尾 `docker compose down -v --remove-orphans` 清理本栈演示卷。

MIT，见LICENSE。
