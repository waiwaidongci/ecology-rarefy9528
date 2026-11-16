# gb-0014 发布单元验证记录

环境：本机无 Docker/PostgreSQL/Redis，后端测试与场景脚本使用 SQLite（`Base.metadata.create_all`）与临时共享目录；前端用 `tsc --noEmit` + `vite build` 验证。未在真实 PostgreSQL/compose 栈上复跑，属已知差异。pip 通过 `/tmp/get-pip.py --user --break-system-packages` 安装后建 venv，未改动仓库内依赖声明。

## 自动化测试

`backend/tests/`，14 项全部通过（`backend-pytest.log`）：

| 场景 | 断言要点 |
|---|---|
| 归档后中断 | 归档写入、发布事务未提交：研究保持 running、result 为空、无 artifact 引用；租约过期转 interrupted；无主文件被清理 |
| 接管与迟到写回 | 旧尝试中断后接管为 A2；A2 发布后旧尝试写回返回 late，指纹/归档不变，旧尝试标 superseded，A1 归档被删，留痕 `diversity.late_discarded` |
| 重复执行 | 同一尝试二次投递返回 duplicate，不重新计算、不新增归档；`request_key` 重复提交只建一个研究/尝试，参数冲突 409 |
| 归档核对 | 篡改文件后 verify 失败；导出遇到不一致返回 409；正常导出带 `X-Publication-Attempt`/`X-Archive-SHA256` |
| 清理 | 未引用且超宽限期的文件删除；被引用文件与宽限期内新文件保留 |
| 清理并发 | 扫描线程与发布事务交错，归档仍存在且 verify 通过；零宽限期后续清理仍保留引用 |
| 恢复幂等 | 健康租约不被误判；过期恢复一次，第二次不再重复记录 |
| 接管/重试 API | analyst 接管 403；负责人接管 201 且旧尝试 interrupted；已发布接管 409；失败研究可重试 |
| 计算失败 | 失败尝试无归档、run 为 failed 且 result 为空，可重试 |

## 场景脚本

`scenario_check.py` → `scenario-report.json`（seed 1296 计数行）：

1. seed 研究发布 A1，verify 通过；
2. 归档写完后进程中断：无发布结果，租约过期转 interrupted，无主归档被清理；
3. 接管生成 A2 并发布，verify 通过；
4. A1 迟到结果被拒，指纹/归档不变，A1 标 superseded 且 A1 归档不存在；
5. A2 重复投递返回 duplicate，run 2 只有一个归档；
6. 清理线程与发布并发：A3 归档保留，被引用的 A1/A2/A3 全部保留。

## 未覆盖

- 真实 Celery/Redis 下的 `task_acks_late` 重投、真实 PostgreSQL 行锁与 `with_for_update`；
- 浏览器截图与真实网络下 CSV 下载；
- 不删任何既有数据：迁移为补列，seed 的既有研究通过发布路径生成。
