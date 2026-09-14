# Linux / systemd 部署

在固定的项目目录（例如 `/opt/net-inspection`）执行：

```bash
sudo bash deploy/linux/install.sh
```

脚本安装系统和项目依赖，复用配置，以低权限账号运行两个 systemd 服务。准备 MariaDB/MySQL 数据库；需要发行版提供 Python 3.12+，或通过 `--python` 指定已安装解释器。

完整步骤、升级、迁移和故障处理见 [一键部署说明](../../README.md#linux--systemd)。本目录的 `.service` 与 `environment.example` 是旧手工部署参考；安装脚本根据实际目录生成 unit，不直接复制这些固定路径文件。已有手工 unit 不会被静默替换。
