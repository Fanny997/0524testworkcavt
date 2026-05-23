# 后端模型推理链路说明

当前项目里有两套自动标注模型机制，需要区分清楚。

## 1. `ai-models` native function 机制

这是当前项目新增模型时应该优先使用的机制。

代码位置：

```text
ai-models/
```

注册方式：

```bash
cvat-cli function create-native ...
```

运行方式：

```bash
cvat-cli function run-agent <FUNCTION_ID> ...
```

后端 API 形态：

```text
POST /api/functions
GET  /api/functions
POST /api/functions/queues/{queue_id}/requests/{request_id}/update
POST /api/functions/queues/{queue_id}/requests/{request_id}/complete
```

`ai-models/detector/dino_onnx/func.py` 实现的是一个 CVAT SDK `DetectionFunction`。agent 拉取请求后，会对 Task 图片执行推理，并把结果提交回 CVAT。

## 2. 旧 `lambda_manager` / Nuclio 机制

当前前端已有的 `Automatic annotation` 弹窗走的是这套旧链路：

```text
GET  /api/lambda/functions
POST /api/lambda/requests
GET  /api/lambda/requests/{id}
```

对应后端文件：

- `cvat/apps/lambda_manager/urls.py`
- `cvat/apps/lambda_manager/views.py`
- `cvat/apps/lambda_manager/serializers.py`

这套机制消费的是 Nuclio/serverless 函数，不是 `ai-models` native function。

## 3. 本次 DINO 后端实现

本次 DINO 模型实现已放在：

```text
ai-models/detector/dino_onnx/
```

它通过 `cvat-cli function create-native` 注册，通过 `cvat-cli function run-agent` 执行推理。

没有新增 Django 数据表，也不需要数据库迁移。

## 4. 后端调用流程

```text
用户在 CVAT 创建 native function 推理请求
  -> 后端把请求放入 function 队列
  -> cvat-cli run-agent 拉取请求
  -> agent 调用 ai-models/detector/dino_onnx/func.py
  -> func.py 使用 ONNX Runtime 推理
  -> agent 把 annotations 提交回 CVAT
  -> CVAT 写回 Task 标注
```

## 5. 注意前端差异

当前我加在 Task 顶部的 `Run model` 按钮复用了现有 `Automatic annotation` 弹窗。这个弹窗当前代码仍然调用 `/api/lambda/*`，所以它能直接选择 Nuclio/serverless 模型。

如果你要让该按钮选择 `ai-models` 注册的 native function，需要继续把前端模型 API 从 `/api/lambda/functions` 改到 `/api/functions`，并按 native function 的请求队列接口提交任务。

也可以走兼容方案：后端新增一个 bridge endpoint，把 `/api/lambda/*` 风格请求转发到 native function 队列。但这属于下一步改造，不是单纯新增模型文件。

## 6. DINO 函数返回格式

DINO native function 不直接返回 JSON 给 Django view，而是返回 CVAT SDK annotation 对象：

```python
cvataa.rectangle(class_id, [xtl, ytl, xbr, ybr])
```

标签规格来自 `DetectionFunctionSpec`，由 `labels` 参数生成。
