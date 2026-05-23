# Nuclio ONNX 基础镜像

这个镜像用于 Workflows 自定义操作里的 Nuclio function。先构建一次基础镜像，后续新增数据增强或 ONNX 推理操作时，`function.yaml` 只需要引用这个镜像，不需要每个 function 都重新下载 `onnxruntime`、`opencv-python-headless` 等依赖。

## 构建

在项目根目录执行：

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

这个 Dockerfile 不使用 `# syntax=docker/dockerfile:1`，避免 Docker 在国内网络下去 Docker Hub 拉取 `docker/dockerfile` 构建前端。

如果需要换 pip 源：

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple `
  --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

## 验证

```powershell
docker run --rm --entrypoint python cvat/nuclio-onnxruntime:dev -c "import onnxruntime, cv2, numpy, PIL; print('ok')"
```

## 使用

在 Nuclio function 的 `function.yaml` 里写：

```yaml
spec:
  build:
    image: cvat.my-detector
    baseImage: cvat/nuclio-onnxruntime:dev
```

`deploycustomoperations` 默认会给 `nuctl deploy` 追加 `--offline --no-pull`，所以部署时会使用本地已有镜像，不会因为访问 `gcr.io`、Docker Hub 或 Quay 超时而失败。第一次部署前请确认这些镜像已经在本地：

```powershell
docker image ls cvat/nuclio-onnxruntime:dev
docker image ls quay.io/nuclio/handler-builder-python-onbuild:1.15.9-amd64
docker image ls gcr.io/iguazio/uhttpc:0.0.3-amd64
```

模型文件建议放到当前 function 目录，例如：

```text
nuclio/
  main.py
  function.yaml
  models/
    model.onnx
```
