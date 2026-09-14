# Windows Server 部署

在项目根目录打开管理员 Windows PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Install-NetInspection.ps1
```

使用系统自带计划任务管理独立 Web 和 Worker，无需 NSSM。需先安装 64 位 Python 3.12+，准备 MariaDB/MySQL 数据库。

首次引导、运行账号/共享目录权限、现有数据迁移、升级与排障统一见 [一键部署说明](../README.md#windows-server)。原手工 NSSM 服务不会被脚本覆盖或删除，须先明确迁移原启动方式。
