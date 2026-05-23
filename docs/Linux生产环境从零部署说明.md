# Linux 生产环境从零部署说明

本文面向 Linux 服务器生产部署。生产环境不要使用 `dev_start.py`，它只用于 Windows 本地开发。生产环境使用 Docker Compose 启动 CVAT、Nuclio 和基础服务，使用 `nuctl` 部署自定义操作，再把 manifest 同步到数据库。

以下命令默认在项目根目录执行。

## 1. 准备机器

推荐环境：

| 项目 | 建议 |
| --- | --- |
| 系统 | Ubuntu 22.04 / 24.04 LTS |
| CPU / 内存 | 至少 4 核 16GB，模型较大时按需增加 |
| 磁盘 | 至少 100GB，模型镜像和数据集会占空间 |
| Docker | Docker Engine + Docker Compose v2 |
| 端口 | 80/443 给 CVAT，8070 给 Nuclio dashboard，按需限制防火墙 |

安装常用工具：

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
```

安装 Docker 后确认：

```bash
docker version
docker compose version
```

## 2. 获取代码

```bash
git clone <你的仓库地址> cvat-develop
cd cvat-develop
```

如果是部署已经改好的代码，确认这些目录存在：

```bash
ls custom_operations_registry
ls custom_operations_registry/base-images/nuclio-onnxruntime
```

## 3. 安装 nuctl

`nuctl` 版本要和 Nuclio dashboard 版本一致。当前项目使用：

```text
quay.io/nuclio/dashboard:1.15.9-amd64
```

安装 Linux 版 `nuctl`：

```bash
sudo curl -L \
  https://github.com/nuclio/nuclio/releases/download/1.15.9/nuctl-1.15.9-linux-amd64 \
  -o /usr/local/bin/nuctl

sudo chmod +x /usr/local/bin/nuctl
nuctl version
```

## 4. 构建 CVAT 镜像

生产环境建议从当前源码构建镜像，确保后端包含 Workflows、自定义操作接口和中文前端代码。

```bash
docker build -t cvat/server:dev .
docker build -t cvat/ui:dev -f Dockerfile.ui .
```

`docker-compose.yml` 默认使用：

```text
cvat/server:${CVAT_VERSION:-dev}
cvat/ui:${CVAT_VERSION:-dev}
```

所以如果你使用 `dev` 标签，上面的镜像名可以直接被 compose 使用。如果你使用私有镜像仓库，构建后自行打 tag 和 push，并同步设置 `CVAT_VERSION` 或修改 compose 镜像名。

## 5. 构建 Nuclio 基础镜像

先构建一次统一基础镜像，后续新增 ONNX 模型不需要每个 function 重复下载 `onnxruntime`。

```bash
docker build \
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile \
  -t cvat/nuclio-onnxruntime:dev \
  custom_operations_registry/base-images/nuclio-onnxruntime
```

验证：

```bash
docker run --rm --entrypoint python cvat/nuclio-onnxruntime:dev \
  -c "import onnxruntime, cv2, numpy, PIL; print('ok')"
```

如果服务器不能访问 Docker Hub 或 PyPI，先在有网络的机器构建镜像，再 `docker save` / `docker load` 到生产服务器。

## 6. 启动 CVAT 和 Nuclio

生产启动：

```bash
docker compose \
  -f docker-compose.yml \
  -f components/serverless/docker-compose.serverless.yml \
  up -d
```

确认服务：

```bash
docker ps
docker ps --filter name=nuclio
```

## 7. 初始化数据库和后台任务

生产容器内执行：

```bash
docker exec cvat_server python manage.py migrate
docker exec cvat_server python manage.py migrateredis
docker exec cvat_server python manage.py syncperiodicjobs
docker exec cvat_server python manage.py collectstatic --noinput
```

创建管理员：

```bash
docker exec -it cvat_server python manage.py createsuperuser
```

## 8. 部署 Nuclio 自定义操作

先确认 Nuclio 需要的本地镜像都存在：

```bash
docker image ls cvat/nuclio-onnxruntime:dev
docker image ls quay.io/nuclio/handler-builder-python-onbuild:1.15.9-amd64
docker image ls gcr.io/iguazio/uhttpc:0.0.3-amd64
```

如果缺少 Nuclio 辅助镜像，需要先拉取或离线导入。

创建项目：

```bash
nuctl create project cvat --platform local || true
```

部署示例数据增强：

```bash
nuctl deploy \
  --project-name cvat \
  --path custom_operations_registry/sample-blur-augmentation/nuclio \
  --file custom_operations_registry/sample-blur-augmentation/nuclio/function.yaml \
  --platform local \
  --offline \
  --no-pull \
  --platform-config '{"attributes":{"network":"cvat_cvat"}}'
```

部署示例 ONNX 检测：

```bash
nuctl deploy \
  --project-name cvat \
  --path custom_operations_registry/sample-onnx-detector/nuclio \
  --file custom_operations_registry/sample-onnx-detector/nuclio/function.yaml \
  --platform local \
  --offline \
  --no-pull \
  --platform-config '{"attributes":{"network":"cvat_cvat"}}'
```

检查：

```bash
nuctl get functions --platform local
```

应看到：

```text
sample-blur-augmentation   ready
sample-onnx-detector       ready
```

## 9. 同步操作到数据库

`cvat_server` 镜像默认不包含 `custom_operations_registry`，所以生产环境需要把注册目录挂进容器，或者把该目录复制进你自己的镜像。

推荐新增一个生产 override 文件，例如 `docker-compose.custom-operations.yml`：

```yaml
services:
  cvat_server:
    volumes:
      - ./custom_operations_registry:/home/django/custom_operations_registry:ro
```

然后重启 `cvat_server`：

```bash
docker compose \
  -f docker-compose.yml \
  -f components/serverless/docker-compose.serverless.yml \
  -f docker-compose.custom-operations.yml \
  up -d cvat_server
```

同步数据库：

```bash
docker exec cvat_server python manage.py synccustomoperations
```

看到类似输出表示成功：

```text
Synced custom operations: sample-blur-augmentation, sample-onnx-detector
```

## 10. 验证前端

访问 CVAT：

```text
http://<服务器地址>/
```

登录管理员账号，打开“工作流”页面，应能看到：

```text
模型：示例：本地 ONNX 目标检测
数据增强：示例：图片模糊增强
```

如果前端看不到操作，按顺序检查：

```bash
docker exec cvat_server python manage.py synccustomoperations
nuctl get functions --platform local
docker logs cvat_server --tail 200
```

## 11. 新增模型的生产流程

新增一个模型目录：

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

部署：

```bash
nuctl deploy \
  --project-name cvat \
  --path custom_operations_registry/my-detector/nuclio \
  --file custom_operations_registry/my-detector/nuclio/function.yaml \
  --platform local \
  --offline \
  --no-pull \
  --platform-config '{"attributes":{"network":"cvat_cvat"}}'

docker exec cvat_server python manage.py synccustomoperations
```

## 12. 常见问题

`gcr.io` 或 Docker Hub 超时：

```text
确保本地已有 Nuclio 辅助镜像，并使用 --offline --no-pull。
```

前端看不到模型：

```text
Nuclio 部署成功只代表 function ready；还必须执行 synccustomoperations 写数据库。
```

执行时报 function 不存在：

```text
检查 manifest.json 的 nuclio_function 是否等于 function.yaml 的 metadata.name。
```

执行时报数据库连接失败：

```text
生产环境不要让宿主机 Python 连接数据库。同步数据库推荐在 cvat_server 容器内执行。
```
