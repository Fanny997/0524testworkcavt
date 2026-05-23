# Windows 本地开发从零部署说明

本文面向 Windows 本机开发和单机调试。Windows 版本使用 `dev_start.py` 一键启动本地 Django 后端、前端 dev server、Docker 基础服务和 Nuclio 自定义操作。

以下命令默认在项目根目录执行：

```powershell
C:\Users\Administrator\Desktop\cvat-develop
```

## 1. 准备软件

需要安装：

| 软件 | 用途 |
| --- | --- |
| Docker Desktop | 运行 PostgreSQL、Redis、Nuclio、ClickHouse 等容器 |
| Git for Windows | 拉代码，也给 `nuctl` 提供部分 shell 能力 |
| Anaconda / Miniconda | 创建 Python 环境 |
| Node.js / Corepack | 前端 Yarn 依赖 |
| Visual Studio Build Tools | 某些 Python 包构建时需要 |

确认 Docker 可用：

```powershell
docker version
docker compose version
```

## 2. 获取代码

```powershell
cd C:\Users\Administrator\Desktop
git clone <你的仓库地址> cvat-develop
cd C:\Users\Administrator\Desktop\cvat-develop
```

如果已经有代码，直接进入目录即可。

## 3. 创建 Python 环境

推荐 Python 3.11：

```powershell
conda create -n rgbtsar python=3.11 -y
conda activate rgbtsar
python -m pip install --upgrade pip setuptools wheel
```

安装后端依赖：

```powershell
python -m pip install -r cvat\requirements\development.txt
```

Windows 上如果 `python-ldap` 安装失败，使用 conda-forge：

```powershell
conda install -n rgbtsar -c conda-forge python-ldap=3.4.3 -y
```

如果缺少单个包，按报错补装即可。不要安装废弃的 `azure` 元包，需要 Azure 时安装具体包，例如 `azure-storage-blob`。

## 4. 安装 nuctl

创建目录并下载 Windows 版 `nuctl`：

```powershell
New-Item -ItemType Directory -Force C:\tools\nuclio

Invoke-WebRequest `
  -Uri "https://github.com/nuclio/nuclio/releases/download/1.15.9/nuctl-1.15.9-windows-amd64" `
  -OutFile "C:\tools\nuclio\nuctl.exe"

Unblock-File C:\tools\nuclio\nuctl.exe
```

加入用户 PATH：

```powershell
$env:Path = "C:\tools\nuclio;$env:Path"
[Environment]::SetEnvironmentVariable(
  "Path",
  "C:\tools\nuclio;" + [Environment]::GetEnvironmentVariable("Path", "User"),
  "User"
)

C:\tools\nuclio\nuctl.exe version
```

Windows 下执行 `nuctl` 前建议设置：

```powershell
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"
```

`dev_start.py` 会自动设置这些变量。

## 5. 构建 Nuclio 基础镜像

先构建一次基础镜像，后续新增 ONNX 模型不用重复下载 `onnxruntime`。

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

验证：

```powershell
docker run --rm --entrypoint python cvat/nuclio-onnxruntime:dev -c "import onnxruntime, cv2, numpy, PIL; print('ok')"
```

如果构建时访问 Docker Hub 失败，确认 Dockerfile 第一行没有 `# syntax=docker/dockerfile:1`。当前仓库已经去掉。

## 6. 确认 Nuclio 辅助镜像

`deploycustomoperations` 默认会给 `nuctl deploy` 添加：

```text
--offline --no-pull
```

所以需要本地已有这些镜像：

```powershell
docker image ls cvat/nuclio-onnxruntime:dev
docker image ls quay.io/nuclio/handler-builder-python-onbuild:1.15.9-amd64
docker image ls gcr.io/iguazio/uhttpc:0.0.3-amd64
```

如果缺少 Nuclio 辅助镜像，需要先拉取或从其他机器 `docker save` / `docker load` 导入。

## 7. 启动项目

激活环境：

```powershell
conda activate rgbtsar
cd C:\Users\Administrator\Desktop\cvat-develop
```

一键启动：

```powershell
python dev_start.py
```

启动完成后会显示：

```text
Backend: http://localhost:7000
Frontend: http://localhost:3000
```

访问：

```text
http://localhost:3000
```

不要直接访问 `7000` 作为主页面。`7000` 是 Django API 后端，前端页面走 `3000`。

## 8. dev_start.py 做了什么

`dev_start.py` 会按顺序执行：

```text
1. 启动 Docker 基础服务：PostgreSQL、Redis、Kvrocks、OPA、ClickHouse、Vector
2. 启动辅助 cvat_server，供 OPA / Nuclio 内部规则使用，不暴露宿主机端口
3. 执行 Django migrations
4. 执行 Redis migrations
5. 同步周期任务
6. collectstatic
7. 启动 Nuclio dashboard
8. 部署 custom_operations_registry 中的 Nuclio functions
9. 同步 custom operation manifest 到数据库
10. 启动本地 Django 后端 127.0.0.1:7000
11. 启动前端 dev server 127.0.0.1:3000
```

如果 Nuclio 已经部署成功，只想重启前后端：

```powershell
python dev_start.py --skip-custom-operations-deploy
```

只同步数据库，不重新部署 Nuclio：

```powershell
python dev_start.py --sync-custom-operations-only
```

指定 `nuctl`：

```powershell
python dev_start.py --nuctl C:\tools\nuclio\nuctl.exe
```

## 9. 验证自定义操作

查看 Nuclio functions：

```powershell
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"
C:\tools\nuclio\nuctl.exe get functions --platform local
```

应看到：

```text
sample-blur-augmentation   ready
sample-onnx-detector       ready
```

同步数据库：

```powershell
python manage.py synccustomoperations
```

前端打开“工作流”页面，应看到：

```text
模型：示例：本地 ONNX 目标检测
数据增强：示例：图片模糊增强
```

## 10. 新增模型

新增目录：

```text
custom_operations_registry/my-detector/
  manifest.json
  nuclio/
    function.yaml
    main.py
    models/
      model.onnx
```

`function.yaml` 使用统一基础镜像：

```yaml
spec:
  build:
    image: cvat.my-detector
    baseImage: cvat/nuclio-onnxruntime:dev
```

部署单个操作：

```powershell
python manage.py deploycustomoperations --nuctl C:\tools\nuclio\nuctl.exe --only my-detector
```

或者直接重新一键启动：

```powershell
python dev_start.py
```

## 11. 常见问题

`localhost:5432 refused`：

```text
cvat_db 可能被 base compose 启成了无宿主机端口映射。重新运行 python dev_start.py，它会重建基础服务并恢复 127.0.0.1:5432->5432。
```

检查端口：

```powershell
docker ps --filter name=cvat_db --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Nuclio 拉 `gcr.io` 超时：

```text
确认本地已有 gcr.io/iguazio/uhttpc:0.0.3-amd64，并使用当前代码中的 --offline --no-pull。
```

工作流报 `Unknown input field(s): _output_path, _save_outputs, _step_index`：

```text
刷新浏览器 Ctrl+F5，并确认本地后端和前端 dev server 都是最新代码。
```

确认 7000 端口是谁占用：

```powershell
Get-NetTCPConnection -LocalPort 7000 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

停止旧后端：

```powershell
Stop-Process -Id <PID> -Force
```

前端 3000 空白：

```text
查看前端 dev server 窗口是否编译成功；浏览器访问 http://localhost:3000；不要把 http://localhost:7000 当成前端入口。
```

Docker 里的 `cvat_server` 是否影响访问：

```powershell
docker ps --filter name=cvat_server --format "table {{.Names}}\t{{.Ports}}"
```

正常辅助 `cvat_server` 不应该暴露宿主机端口。
