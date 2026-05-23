# DINO ONNX 模型注册到 CVAT 的方法

你说得对：在当前项目结构里，新的自动标注模型应该优先放在 `ai-models/` 下。

本次已按 `ai-models` 机制新增 DINO ONNX 目标检测函数：

```text
ai-models/detector/dino_onnx/
  func.py
  requirements.txt
  README.md
```

这套机制和上游 CVAT 旧的 `serverless/` Nuclio 函数不同：

- `ai-models/`：CVAT SDK/CLI native auto-annotation function，通过 `cvat-cli function create-native` 注册。
- `serverless/`：Nuclio 函数，通过 `/api/lambda/functions` 暴露给旧的 lambda manager。

本项目里已有 `ai-models/detector/yolo`、`ai-models/detector/transformers`，DINO ONNX 应该和它们同级。

## 1. 安装依赖

进入函数目录：

```bash
cd ai-models/detector/dino_onnx
pip install -r requirements.txt
```

## 2. 准备 ONNX 文件

假设你的模型路径是：

```text
/models/dino/model.onnx
```

如果在 Windows 本地执行，把路径换成实际绝对路径，例如：

```text
C:\models\dino\model.onnx
```

## 3. 注册函数

使用 `cvat-cli function create-native` 注册：

```bash
cvat-cli --server-host http://localhost:7000 function create-native \
  "DINO ONNX Detector" \
  --function-file ai-models/detector/dino_onnx/func.py \
  -p model=str:/models/dino/model.onnx \
  -p labels=str:person,car \
  -p output_layout=str:logits_boxes \
  -p input_size=int:800
```

参数说明：

- `model`：ONNX 模型文件路径。
- `labels`：类别名，逗号分隔，顺序必须和模型输出类别 id 一致。
- `output_layout`：模型输出格式。
- `input_size`：输入缩放尺寸，默认 `800`。
- `device`：可选，`cpu` 或 `cuda`，默认 `cpu`。

GPU 示例：

```bash
cvat-cli --server-host http://localhost:7000 function create-native \
  "DINO ONNX Detector" \
  --function-file ai-models/detector/dino_onnx/func.py \
  -p model=str:/models/dino/model.onnx \
  -p labels=str:person,car \
  -p output_layout=str:logits_boxes \
  -p input_size=int:800 \
  -p device=str:cuda
```

注册成功后，命令最后一行会输出函数 ID，例如：

```text
17
```

## 4. 启动 agent

native function 注册后，还需要一个 agent 持续处理后端分发的推理请求：

```bash
cvat-cli --server-host http://localhost:7000 function run-agent 17 \
  --function-file ai-models/detector/dino_onnx/func.py \
  -p model=str:/models/dino/model.onnx \
  -p labels=str:person,car \
  -p output_layout=str:logits_boxes \
  -p input_size=int:800
```

如果 agent 没启动，函数虽然已注册，但推理请求不会被执行。

## 5. 输出格式说明

`func.py` 默认支持两种 ONNX 输出。

默认：

```text
output_layout=logits_boxes
```

适用于 DETR/DINO 常见输出：

```text
outputs[0] = logits, shape: [1, num_queries, num_classes] 或 [1, num_queries, num_classes + 1]
outputs[1] = boxes,  shape: [1, num_queries, 4], normalized cxcywh
```

如果你的 ONNX 已经包含后处理/NMS，输出是：

```text
outputs[0] = boxes,  shape: [N, 4], xyxy
outputs[1] = scores, shape: [N]
outputs[2] = labels, shape: [N]
```

注册和运行 agent 时使用：

```bash
-p output_layout=str:boxes_scores_labels
```

## 6. 验证

注册后可以用 API 验证函数是否存在：

```bash
curl http://localhost:7000/api/functions
```

或者在 CVAT 的函数/自动标注页面查看是否出现 `DINO ONNX Detector`。

