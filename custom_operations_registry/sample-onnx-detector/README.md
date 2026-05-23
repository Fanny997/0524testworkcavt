# 示例：本地 ONNX 目标检测

这是一个可直接部署的 Nuclio + ONNX 目标检测示例。

## 文件结构

```text
sample-onnx-detector/
  manifest.json
  nuclio/
    function.yaml
    main.py
    models/
      foreground_detector.onnx
```

## 基础镜像

先在项目根目录构建一次基础镜像：

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

`function.yaml` 已经引用：

```yaml
baseImage: cvat/nuclio-onnxruntime:dev
```

所以后续部署这个 function 时不会在 function 构建阶段重复下载 `onnxruntime`。

## 功能

- 输入：一个图片集合。
- 参数：亮度阈值、最小面积占比。
- 模型：`nuclio/models/foreground_detector.onnx`。
- 推理引擎：`onnxruntime`。
- 输出：每张图片一个 JSON 检测结果文件。

## 模型说明

这个示例的 ONNX 是一个极小的检测头模型，输入形状为 `[1, 5]`，输出形状为 `[1, 5]`：

```text
[x1, y1, x2, y2, score] -> ONNX -> [x1, y1, x2, y2, score]
```

`main.py` 会先用亮度阈值从图片中提取一个前景候选框，再把候选框特征送入本地 ONNX 模型，最后把 ONNX 输出转换成检测结果。

这个模型的目的不是追求检测精度，而是给你一个真正包含本地 `.onnx` 文件、`onnxruntime.InferenceSession`、前处理、推理、后处理和工作流输出的完整模板。后续替换真实 YOLO/RT-DETR/自研模型时，主要改 `nuclio/models/*.onnx` 和 `nuclio/main.py`。

## 部署

在项目根目录执行：

```powershell
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"
C:\ProgramData\anaconda3\envs\rgbtsar\python.exe manage.py deploycustomoperations --nuctl C:\tools\nuclio\nuctl.exe --only sample-onnx-detector
```
