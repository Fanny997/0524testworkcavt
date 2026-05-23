# 前端模型调用按钮改造说明

本文说明本次前端如何增加一个简单按钮，让用户在 Task 页面选择模型并触发后端推理。

## 1. 本次改动文件

已修改：

```text
cvat-ui/src/components/task-page/top-bar.tsx
```

新增效果：

- 在 Task 详情页顶部 `Actions` 按钮旁边新增 `Run model` 按钮。
- 点击 `Run model` 后打开现有 `Automatic annotation` 弹窗。
- 弹窗中可以选择 detector/re-id 模型、设置阈值、设置标签映射。
- 点击 `Annotate` 后前端调用后端推理接口，后端执行推理并写回标注。
- 如果该 Task 已有推理任务在运行，按钮会禁用。

重要限制：

当前按钮复用的是已有 `Automatic annotation` 弹窗，而这个弹窗当前代码仍然走旧的 `/api/lambda/*` Nuclio 模型接口。

如果 DINO 是按 `ai-models/detector/dino_onnx` 注册成 native function，它不会自动出现在这个旧弹窗里。要让 `Run model` 直接选择 `ai-models` 注册的模型，还需要把前端模型接口从 `/api/lambda/functions` 改为 `/api/functions`，并用 native function 队列接口提交请求。

## 2. 当前复用的现有前端链路

按钮只负责打开已有模型运行弹窗：

```ts
dispatch(modelsActions.showRunModelDialog(taskInstance))
```

后续流程复用已有组件：

- `cvat-ui/src/components/model-runner-modal/model-runner-dialog.tsx`
- `cvat-ui/src/components/model-runner-modal/detector-runner.tsx`
- `cvat-ui/src/actions/models-actions.ts`
- `cvat-core/src/lambda-manager.ts`
- `cvat-core/src/server-proxy.ts`

调用链路：

```text
Run model button
  -> modelsActions.showRunModelDialog(task)
  -> ModelRunnerDialog
  -> DetectorRunner
  -> startInferenceAsync(taskId, model, body)
  -> core.lambda.run()
  -> POST /api/lambda/requests
```

这是旧 Nuclio 链路。

## 3. 对接 `ai-models` native function 的下一步

要对接 `ai-models` 注册的 DINO 模型，需要新增或改造以下前端 API：

```text
GET /api/functions
POST /api/functions/queues/function:{function_id}/requests
GET /api/functions/queues/function:{function_id}/requests/{request_id}
```

具体接口字段需要以后端 `/api/functions` 实现为准。已有 `cvat-cli function run-agent` 会处理这些队列请求。

## 4. 关键代码

`top-bar.tsx` 中新增了 Redux dispatch 和运行中状态判断：

```ts
const dispatch = useDispatch();
const activeInference = useSelector((state: CombinedState) => state.models.inferences[taskInstance.id]);
const inferenceIsRunning = activeInference &&
    ![RQStatus.FAILED, RQStatus.FINISHED].includes(activeInference.status);
```

按钮：

```tsx
<Button
    size='middle'
    icon={<ThunderboltOutlined />}
    disabled={inferenceIsRunning}
    onClick={() => dispatch(modelsActions.showRunModelDialog(taskInstance))}
>
    Run model
</Button>
```

## 5. 当前按钮使用方法

1. 部署或注册后端能被当前模型接口列出的 detector。
2. 启动 CVAT 后端、RQ worker 和前端。
3. 打开一个已有 Task。
4. 点击顶部 `Run model`。
5. 在弹窗中选择模型。
6. 确认标签映射和阈值。
7. 点击 `Annotate`。

推理完成后，检测框会以自动标注的方式写回当前 Task。

## 6. 注意事项

- Task 的标签必须能和模型标签映射，否则 `Annotate` 按钮不会可用。
- 如果模型标签名和 Task 标签名一致，弹窗会自动生成映射。
- 如果走旧 `/api/lambda/*` 链路，后端需要运行 `annotation` RQ worker。
- 如果走 `ai-models` native function 链路，需要运行 `cvat-cli function run-agent <FUNCTION_ID>`。
