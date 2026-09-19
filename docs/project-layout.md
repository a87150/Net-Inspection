# 项目目录说明

本项目保留两个 Django 应用：`net` 负责数据、业务和外部连接，`index`
负责页面、表单、模板与 URL。业务代码只使用下列规范路径，不再维护旧模块别名。

## 后端

- `net/models/`：人员、域控、设备、记录、任务、报警和集成配置模型。
- `net/people/`：人员导入、目录适配、同步和后台执行。
- `net/domain/`：域控连接、同步、密钥、批量操作和任务执行。
- `net/devices/`：PC、服务器、网络设备和安防设备的采集与解析。
- `net/inspections/`：任务队列、调度、Worker、巡检执行和记录汇总。
- `net/alerts/`：报警策略、消息、飞书、钉钉和邮件发送。
- `net/data_exchange/`：CSV 与设备配置导入导出。
- `net/infrastructure/`：通用 SSH、HTTP、采集结果和脱敏工具。
- `net/devices/pc/`：PC API 上报、日志入库、留存、分析及终端数据解析。
- `net/scripts/`：页面下载的 Windows/macOS PC 采集脚本模板与生成器。

## 页面与静态资源

`index/` 按 `dashboard`、`people`、`domain`、`devices`、`inspections`、
`alerts` 和 `common` 分类。模板使用相同目录名。业务静态资源位于
`static/app/`，Bootstrap 位于 `static/vendor/bootstrap/`；`staticfiles/`
是 `collectstatic` 生成目录，不应手工编辑或提交。

## 采集端、部署和测试

- `agents/pc/windows/`：PC 信息采集脚本及其 DLL。
- `agents/server/windows/`：Windows 服务器巡检 HTTP 服务与安装脚本。
- `deploy/windows/`：Windows Web/Worker 服务示例。
- `deploy/linux/systemd/`：Linux systemd 双服务示例。
- `tests/`：按业务域组织的 Django、前端和 PowerShell 测试。
- `config/examples/`：不含凭据的配置格式示例。
- `tools/`：不参与 Django 启动的独立运维客户端。

新增设备采集器时放入对应 `net/devices/<类型>/`，通用协议能力放入
`net/infrastructure/`；新增页面放入对应 `index/<类型>/` 并在
`index/urls.py` 注册；新增后台任务放入 `net/inspections/`；新增测试放入
匹配的 `tests/<类型>/`；新增终端脚本放入 `agents/` 的对应平台目录。

运行数据库、终端本地日志、生成配置、`demo-runtime/`、`.venv/`、
`.worktrees/` 和 `staticfiles/` 都是运行或生成数据，不参与源码整理。
