# 自定义操作注册目录

这是 CVAT 默认扫描的 Nuclio-only 自定义操作注册目录。

每个操作创建一个子目录：

```text
custom_operations_registry/
  my-yolo-detector/
    manifest.json
    nuclio/
      function.yaml
      main.py
    models/
      yolo.onnx
```

子目录名称会作为默认操作名称和默认 `nuclio_function` 值。

如果操作依赖 ONNX Runtime、OpenCV、NumPy、Pillow，先构建统一基础镜像：

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

之后在每个 `nuclio/function.yaml` 里使用：

```yaml
baseImage: cvat/nuclio-onnxruntime:dev
```

完整 manifest 字段、Nuclio 自动部署方式、输入集合规则和返回格式见：

```text
docs/自定义操作工作流注册说明.md
```
